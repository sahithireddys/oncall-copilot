"""LangGraph investigation agent.

    triage ─► gather ─► analyze ──(new suspects & rounds left)──► gather   (drill-down loop)
                           │
                           └──► retrieve ─► hypothesize ─► report ─► END

* triage       decide the time window and the first services to look at
* gather       pull metrics / traces / logs / k8s events / changes in parallel
* analyze      turn raw evidence into findings, rank suspect services,
               and decide whether to drill into a downstream suspect
* retrieve     BM25 search over postmortems, runbooks and confirmed past incidents
* hypothesize  Claude (structured output) or the heuristic baseline
* report       validate the hypothesis against collected evidence, emit the result
"""
from __future__ import annotations

import operator
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..knowledge.store import KnowledgeBase
from ..models import Alert, Evidence, Finding, Hypothesis, RootCauseCategory
from ..providers.base import TelemetryProvider, Window
from .analysis import extract_findings, pick_suspects, score_services
from ..providers.simulated import ASYNC_EDGES
from .heuristic import heuristic_hypothesis

EmitFn = Callable[[str, str, str, dict], None]


def merge_evidence(left: dict[str, Evidence], right: dict[str, Evidence]) -> dict[str, Evidence]:
    out = dict(left or {})
    out.update(right or {})
    return out


class AgentState(TypedDict, total=False):
    investigation_id: str
    alert: Alert
    window: Window
    round: int
    pending_services: list[str]
    examined_services: Annotated[list[str], operator.add]
    evidence: Annotated[dict[str, Evidence], merge_evidence]
    findings: list[Finding]
    suspect_scores: dict[str, float]
    documents: list[dict]
    hypothesis: Hypothesis
    reasoner_used: str
    notes: Annotated[list[str], operator.add]


@dataclass
class AgentDeps:
    provider: TelemetryProvider
    kb: KnowledgeBase
    emit: EmitFn
    reasoner: str = "heuristic"              # "claude" | "heuristic"
    llm: Any = None                          # ClaudeReasoner when reasoner == "claude"
    max_rounds: int = 2
    lookback_minutes: int = 45


def _safe(fn, *args) -> list[Evidence]:
    try:
        return fn(*args)
    except Exception as exc:  # a broken telemetry backend must not kill the investigation
        return [Evidence(id=f"error:{getattr(fn, '__name__', 'source')}:{args[0] if args else ''}",
                         source="logs", title=f"telemetry source failed: {exc}", severity="info",
                         data={"kind": "source_error", "error": str(exc)})]


def build_graph(deps: AgentDeps):
    p, emit = deps.provider, deps.emit

    # ------------------------------------------------------------------ triage
    def triage(state: AgentState) -> dict:
        alert = state["alert"]
        window = Window(alert_time=alert.starts_at, lookback_minutes=deps.lookback_minutes)
        emit("triage", "started", f"Alert {alert.name} on {alert.service}: {alert.summary}", {})
        topo = p.get_topology()
        callees = topo.callees(alert.service)
        emit("triage", "completed",
             f"Investigating {alert.service} over the last {deps.lookback_minutes} minutes"
             + (f"; it depends on {', '.join(callees)}" if callees else ""),
             {"service": alert.service, "dependencies": callees})
        return {"window": window, "round": 0, "pending_services": [alert.service]}

    # ------------------------------------------------------------------ gather
    def gather(state: AgentState) -> dict:
        rnd, window, alert = state.get("round", 0), state["window"], state["alert"]
        services = [s for s in state.get("pending_services", []) if s not in state.get("examined_services", [])]
        emit("gather", "started",
             f"Round {rnd + 1}: collecting telemetry for {', '.join(services)}", {"round": rnd + 1, "services": services})
        jobs: list[tuple[str, str, Callable, tuple]] = []
        for s in services:
            jobs.append(("metrics", s, p.get_metrics, (s, window)))
            jobs.append(("logs", s, p.get_logs, (s, window)))
            if rnd == 0 or p.get_topology().callees(s):
                jobs.append(("traces", s, p.get_traces, (s, window)))
        if rnd == 0:
            jobs.append(("k8s_events", "*", p.get_k8s_events, (window,)))
            jobs.append(("changes", "*", p.get_changes, (window,)))
        collected: dict[str, Evidence] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [(src, s, pool.submit(_safe, fn, *args)) for src, s, fn, args in jobs]
            for src, s, fut in futures:
                items = fut.result()
                for ev in items:
                    collected[ev.id] = ev
                notable = [e for e in items if e.severity != "info"]
                emit("gather", "progress", f"{src} · {s}: {len(items)} items, {len(notable)} notable",
                     {"source": src, "service": s, "count": len(items),
                      "notable": [e.title for e in notable[:4]]})
        emit("gather", "completed", f"Collected {len(collected)} pieces of evidence", {"count": len(collected)})
        return {"evidence": collected, "examined_services": services, "round": rnd + 1}

    # ----------------------------------------------------------------- analyze
    def analyze(state: AgentState) -> dict:
        alert = state["alert"]
        evidence = list(state["evidence"].values())
        findings = extract_findings(evidence, alert.service, p.get_topology())
        scores = score_services(findings)
        examined = set(state.get("examined_services", []))
        suspects = pick_suspects(findings, examined)
        # Evidence coverage: if the leading suspect is slow/lagging but we have not looked at what it
        # depends on, check those dependencies before concluding (otherwise the reasoner may blame a
        # dependency it has no telemetry for, e.g. from a similar-looking postmortem).
        topo = p.get_topology()
        if scores:
            lead = next(iter(scores))
            lead_kinds = {f.kind for f in findings if f.service == lead}
            if lead_kinds & {"latency_spike", "consumer_lag", "trace_latency_origin", "error_spike", "trace_error_origin"}:
                for dep in topo.callees(lead):
                    if dep not in examined and dep not in suspects and (lead, dep) not in ASYNC_EDGES:
                        suspects.append(dep)
        suspects = suspects[:4]
        top = list(scores.items())[:4]
        emit("analyze", "completed",
             f"{len(findings)} signals; leading suspects: " + ", ".join(f"{s} ({v:.1f})" for s, v in top),
             {"scores": dict(top), "new_suspects": suspects,
              "findings": [f.summary for f in sorted(findings, key=lambda x: -x.score)[:6]]})
        return {"findings": findings, "suspect_scores": scores, "pending_services": suspects}

    def route_after_analyze(state: AgentState) -> str:
        if state.get("pending_services") and state.get("round", 0) < deps.max_rounds:
            emit("analyze", "progress", f"Drilling down into {', '.join(state['pending_services'])}",
                 {"drill_down": state["pending_services"]})
            return "gather"
        return "retrieve"

    # ---------------------------------------------------------------- retrieve
    def retrieve(state: AgentState) -> dict:
        alert = state["alert"]
        findings = sorted(state.get("findings", []), key=lambda x: -x.score)[:8]
        top_services = list(state.get("suspect_scores", {}))[:3]
        messages = []
        for ev in state["evidence"].values():
            if ev.data.get("kind") == "log_pattern" and ev.data.get("level") in ("ERROR", "FATAL"):
                messages.append(ev.data["pattern"])
        query = " ".join([alert.name, alert.service, alert.summary, *top_services,
                          *(f.kind.replace("_", " ") for f in findings), *(f.summary for f in findings), *messages[:5]])
        hits = deps.kb.search(query, k=5, services=top_services)
        docs = [{**d.as_dict(), "score": s} for d, s in hits]
        emit("retrieve", "completed",
             f"Found {len(docs)} related documents" + (f"; best match: {docs[0]['title']}" if docs else ""),
             {"documents": [{"id": d["id"], "title": d["title"], "score": d["score"]} for d in docs]})
        return {"documents": docs}

    # ------------------------------------------------------------- hypothesize
    def hypothesize(state: AgentState) -> dict:
        alert = state["alert"]
        findings = state.get("findings", [])
        docs = state.get("documents", [])
        notes: list[str] = []
        if deps.reasoner == "claude" and deps.llm is not None:
            emit("hypothesize", "started", "Asking Claude to weigh the evidence", {"reasoner": "claude"})
            try:
                t0 = time.time()
                hyp = deps.llm.hypothesize(alert, list(state["evidence"].values()), findings, docs,
                                           p.get_topology(), deps.kb)
                emit("hypothesize", "progress", f"Claude answered in {time.time() - t0:.1f}s", {})
                used = "claude"
            except Exception as exc:
                notes.append(f"LLM reasoning failed ({exc.__class__.__name__}: {exc}); used heuristic fallback")
                emit("hypothesize", "progress", f"Claude call failed, falling back to heuristic: {exc}", {})
                hyp = heuristic_hypothesis(findings, alert.service, docs)
                used = "heuristic"
        else:
            emit("hypothesize", "started", "Scoring suspects with the rule-based reasoner", {"reasoner": "heuristic"})
            hyp = heuristic_hypothesis(findings, alert.service, docs)
            used = "heuristic"
        emit("hypothesize", "completed",
             f"{hyp.root_cause_service} · {hyp.category.value} · confidence {hyp.confidence:.0%}",
             {"root_cause_service": hyp.root_cause_service, "category": hyp.category.value})
        return {"hypothesis": hyp, "reasoner_used": used, "notes": notes}

    # ------------------------------------------------------------------ report
    def report(state: AgentState) -> dict:
        hyp = state["hypothesis"]
        known = state["evidence"]
        topo_services = set(p.get_topology().services)
        notes = []
        # Guardrails: never report evidence the agent didn't actually collect.
        valid_refs = [r for r in hyp.supporting_evidence if r.evidence_id in known]
        if len(valid_refs) != len(hyp.supporting_evidence):
            notes.append(f"dropped {len(hyp.supporting_evidence) - len(valid_refs)} evidence refs that were not collected")
        if hyp.root_cause_service not in topo_services:
            notes.append(f"root cause service {hyp.root_cause_service!r} is not in the service graph")
            hyp = hyp.model_copy(update={"confidence": min(hyp.confidence, 0.3)})
        if not valid_refs and hyp.category != RootCauseCategory.unknown:
            hyp = hyp.model_copy(update={"confidence": min(hyp.confidence, 0.4)})
        hyp = hyp.model_copy(update={"supporting_evidence": valid_refs})
        emit("report", "completed", hyp.title, {"hypothesis": hyp.model_dump(mode="json")})
        return {"hypothesis": hyp, "notes": notes}

    g = StateGraph(AgentState)
    g.add_node("triage", triage)
    g.add_node("gather", gather)
    g.add_node("analyze", analyze)
    g.add_node("retrieve", retrieve)
    g.add_node("hypothesize", hypothesize)
    g.add_node("report", report)
    g.add_edge(START, "triage")
    g.add_edge("triage", "gather")
    g.add_edge("gather", "analyze")
    g.add_conditional_edges("analyze", route_after_analyze, {"gather": "gather", "retrieve": "retrieve"})
    g.add_edge("retrieve", "hypothesize")
    g.add_edge("hypothesize", "report")
    g.add_edge("report", END)
    return g.compile()


def run_investigation(deps: AgentDeps, alert: Alert, investigation_id: str) -> AgentState:
    graph = build_graph(deps)
    initial: AgentState = {"investigation_id": investigation_id, "alert": alert, "evidence": {},
                           "examined_services": [], "notes": []}
    return graph.invoke(initial, {"recursion_limit": 25})
