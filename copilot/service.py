"""Application service: runs investigations, stores results, handles feedback and stats.

The FastAPI layer (api.py), the eval runner and the MCP server all go through this class.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .agent.graph import AgentDeps, run_investigation
from .config import Settings, get_settings
from .evals.scoring import grade
from .knowledge.store import Document, KnowledgeBase
from .models import (
    Alert, Feedback, FeedbackIn, FeedbackVerdict, Investigation, InvestigationStatus, StepEvent, utcnow,
)
from .notify.slack import post_to_slack
from .providers import make_provider
from .providers.scenarios import Scenario, get_catalog
from .store import Store

log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class CopilotService:
    def __init__(self, settings: Settings | None = None, store: Store | None = None):
        self.settings = settings or get_settings()
        self.store = store or Store(self.settings.database_path)
        self.kb = KnowledgeBase(self.settings.knowledge_path)
        self.scenarios: dict[str, Scenario] = get_catalog(self.settings.scenarios_path)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="investigation")
        self._llm = None
        self._llm_lock = threading.Lock()
        self._load_confirmed_incidents()

    # ------------------------------------------------------------------ helpers
    def _get_llm(self):
        with self._llm_lock:
            if self._llm is None and self.settings.llm_enabled:
                from .agent.llm import ClaudeReasoner
                self._llm = ClaudeReasoner(self.settings.anthropic_api_key, self.settings.anthropic_model,
                                           self.settings.llm_max_tokens)
            return self._llm

    def resolve_reasoner(self, requested: str | None) -> str:
        want = requested or self.settings.reasoner
        if want == "claude" and not self.settings.anthropic_api_key:
            return "heuristic"
        return want if want in ("claude", "heuristic") else "heuristic"

    def _emit_for(self, inv_id: str):
        def emit(node: str, status: str, message: str, data: dict) -> None:
            self.store.add_event(StepEvent(investigation_id=inv_id, node=node, status=status, message=message, data=data))
        return emit

    # ----------------------------------------------------------- investigations
    def create_investigation(self, alert: Alert, scenario_id: str | None = None, reasoner: str | None = None,
                             background: bool = True, telemetry_mode: str | None = None) -> Investigation:
        if scenario_id and scenario_id not in self.scenarios:
            raise NotFound(f"unknown scenario {scenario_id}")
        scenario_id = scenario_id or alert.labels.get("scenario_id")
        if scenario_id and scenario_id not in self.scenarios:
            scenario_id = None
        inv = Investigation(
            id=uuid.uuid4().hex[:12], alert=alert, scenario_id=scenario_id,
            reasoner=self.resolve_reasoner(reasoner), telemetry_mode=telemetry_mode or self.settings.telemetry_mode,
        )
        self.store.save_investigation(inv)
        self._emit_for(inv.id)("queued", "completed", f"Alert received: {alert.name} on {alert.service}", {})
        if background:
            self.pool.submit(self._run_safely, inv.id)
        else:
            self._run_safely(inv.id)
        return self.store.get_investigation(inv.id) or inv

    def investigate_scenario(self, scenario_id: str, reasoner: str | None = None, background: bool = True) -> Investigation:
        sc = self.scenarios.get(scenario_id)
        if not sc:
            raise NotFound(f"unknown scenario {scenario_id}")
        alert = sc.alert.model_copy(update={"starts_at": utcnow(), "labels": {**sc.alert.labels, "scenario_id": sc.id}})
        # "replay" the scenario through the simulator, regardless of TELEMETRY_MODE
        return self.create_investigation(alert, scenario_id=sc.id, reasoner=reasoner, background=background,
                                         telemetry_mode="simulated")

    def _run_safely(self, inv_id: str) -> None:
        inv = self.store.get_investigation(inv_id)
        if inv is None:
            return
        inv.status = InvestigationStatus.running
        self.store.save_investigation(inv)
        emit = self._emit_for(inv_id)
        t0 = time.perf_counter()
        try:
            scenario = self.scenarios.get(inv.scenario_id) if inv.scenario_id else None
            provider = make_provider(self.settings, scenario, mode=inv.telemetry_mode)
            deps = AgentDeps(
                provider=provider, kb=self.kb, emit=emit, reasoner=inv.reasoner,
                llm=self._get_llm() if inv.reasoner == "claude" else None,
                max_rounds=self.settings.max_drilldown_rounds,
                lookback_minutes=self.settings.investigation_window_minutes,
            )
            state = run_investigation(deps, inv.alert, inv_id)
            inv.hypothesis = state.get("hypothesis")
            inv.evidence = sorted(state.get("evidence", {}).values(), key=lambda e: (e.source, e.id))
            inv.findings = sorted(state.get("findings", []), key=lambda f: -f.score)
            inv.retrieved_documents = state.get("documents", [])
            inv.services_examined = state.get("examined_services", [])
            inv.notes = state.get("notes", [])
            inv.reasoner = state.get("reasoner_used", inv.reasoner)
            inv.status = InvestigationStatus.completed
            if scenario:
                inv.grading = grade(inv.hypothesis, scenario)
        except Exception as exc:
            log.exception("investigation %s failed", inv_id)
            inv.status = InvestigationStatus.failed
            inv.error = f"{exc.__class__.__name__}: {exc}"
            emit("report", "failed", inv.error, {})
        inv.completed_at = utcnow()
        inv.duration_ms = int((time.perf_counter() - t0) * 1000)
        if inv.status == InvestigationStatus.completed and self.settings.slack_webhook_url:
            inv.slack_posted = post_to_slack(inv, self.settings.slack_webhook_url, self.settings.public_ui_url)
            if inv.slack_posted:
                emit("notify", "completed", "Posted to Slack", {})
        self.store.save_investigation(inv)

    def get_investigation(self, inv_id: str) -> Investigation:
        inv = self.store.get_investigation(inv_id)
        if not inv:
            raise NotFound(inv_id)
        return inv

    # ------------------------------------------------------------------ feedback
    def submit_feedback(self, inv_id: str, fb_in: FeedbackIn) -> Feedback:
        inv = self.get_investigation(inv_id)
        if inv.status != InvestigationStatus.completed or inv.hypothesis is None:
            raise ValueError("feedback can only be given on completed investigations")
        fb = Feedback(investigation_id=inv_id, **fb_in.model_dump())
        self.store.save_feedback(fb)
        doc = self._incident_document(inv, fb)
        if doc:
            self.kb.add(doc)
        self._emit_for(inv_id)("feedback", "completed", f"Engineer marked this {fb.verdict.value}", fb.model_dump(mode="json"))
        return fb

    @staticmethod
    def _incident_document(inv: Investigation, fb: Feedback) -> Document | None:
        """Turn a confirmed (or corrected) investigation into a searchable past incident."""
        h = inv.hypothesis
        if h is None:
            return None
        if fb.verdict == FeedbackVerdict.correct:
            service, category = h.root_cause_service, h.category.value
        elif fb.actual_root_cause_service:
            service = fb.actual_root_cause_service
            category = (fb.actual_category.value if fb.actual_category else h.category.value)
        else:
            return None  # "wrong" with no correction: nothing trustworthy to learn from
        top_findings = [f.summary for f in inv.findings[:5]]
        messages = [e.data.get("pattern") for e in inv.evidence
                    if e.data.get("kind") == "log_pattern" and e.data.get("level") in ("ERROR", "FATAL")][:3]
        body = "\n".join([
            f"Alert {inv.alert.name} on {inv.alert.service}: {inv.alert.summary}",
            f"Confirmed root cause: {service} ({category}).",
            f"Agent summary: {h.summary}" if fb.verdict == FeedbackVerdict.correct else
            f"Agent guessed {h.root_cause_service} ({h.category.value}) but the engineer corrected it.",
            "Signals: " + "; ".join(top_findings),
            "Error messages: " + " | ".join(m for m in messages if m),
            f"Engineer notes: {fb.notes}" if fb.notes else "",
        ])
        return Document(
            id=f"INC-{inv.id}", title=f"{inv.alert.name}: {service} {category.replace('_', ' ')} (confirmed)",
            type="incident", body=body, services=sorted({service, inv.alert.service}), category=category,
            date=inv.created_at.date().isoformat(),
        )

    def _load_confirmed_incidents(self) -> None:
        for fb in self.store.list_feedback():
            inv = self.store.get_investigation(fb.investigation_id)
            if inv:
                doc = self._incident_document(inv, fb)
                if doc:
                    self.kb.docs[doc.id] = doc
        self.kb._rebuild()

    # --------------------------------------------------------------------- stats
    def stats(self) -> dict:
        invs = self.store.list_investigations(limit=10_000)
        completed = [i for i in invs if i.status == InvestigationStatus.completed]
        with_fb = [i for i in completed if i.feedback]
        verdicts = Counter(i.feedback.verdict.value for i in with_fb)
        fb_score = (verdicts["correct"] + 0.5 * verdicts["partially_correct"]) / len(with_fb) if with_fb else None
        graded = [i for i in completed if i.grading]
        durations = sorted(i.duration_ms for i in completed if i.duration_ms is not None)
        by_day: dict[str, dict] = defaultdict(lambda: {"investigations": 0, "correct": 0, "rated": 0})
        for i in invs:
            day = i.created_at.date().isoformat()
            by_day[day]["investigations"] += 1
            if i.feedback:
                by_day[day]["rated"] += 1
                by_day[day]["correct"] += i.feedback.verdict == FeedbackVerdict.correct
        return {
            "total": len(invs),
            "completed": len(completed),
            "failed": sum(1 for i in invs if i.status == InvestigationStatus.failed),
            "running": sum(1 for i in invs if i.status in (InvestigationStatus.running, InvestigationStatus.queued)),
            "median_time_to_hypothesis_ms": durations[len(durations) // 2] if durations else None,
            "feedback": {"count": len(with_fb), "verdicts": dict(verdicts),
                         "accuracy": round(fb_score, 3) if fb_score is not None else None},
            "scenario_accuracy": round(sum(1 for i in graded if i.grading["correct"]) / len(graded), 3) if graded else None,
            "scenario_runs": len(graded),
            "by_category": dict(Counter(i.hypothesis.category.value for i in completed if i.hypothesis)),
            "by_service": dict(Counter(i.hypothesis.root_cause_service for i in completed if i.hypothesis).most_common(10)),
            "by_reasoner": dict(Counter(i.reasoner for i in completed)),
            "timeline": [{"date": d, **v} for d, v in sorted(by_day.items())][-30:],
            "knowledge_documents": len(self.kb.docs),
            "learned_incidents": sum(1 for d in self.kb.docs.values() if d.type == "incident"),
        }

    def runtime_info(self) -> dict:
        s = self.settings
        return {
            "telemetry_mode": s.telemetry_mode,
            "reasoner": s.effective_reasoner,
            "llm_enabled": s.llm_enabled,
            "model": s.anthropic_model if s.llm_enabled else None,
            "slack_enabled": bool(s.slack_webhook_url),
            "namespace": s.k8s_namespace,
            "scenarios": len(self.scenarios),
            "knowledge_documents": len(self.kb.docs),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }
