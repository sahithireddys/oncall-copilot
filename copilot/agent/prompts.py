"""Prompt construction for the Claude reasoner. Pure functions so they are unit-testable."""
from __future__ import annotations

import json

from ..models import CATEGORY_DESCRIPTIONS, Alert, Evidence, Finding
from ..topology import Topology

SYSTEM_PROMPT = """You are an experienced site reliability engineer on call for a Kubernetes microservices shop \
(the OpenTelemetry Demo). An alert has fired. An automated investigator has already collected telemetry and \
extracted signals. Your job is to name the single most likely ROOT CAUSE, not the service that is merely showing symptoms.

How to reason:
- Errors and latency propagate UPSTREAM along the call graph. The alerting service is usually a victim. Follow the \
failing/slow traces to where they originate.
- A change (deploy, config, flag, scale) is only a cause if the changed service is on the failing path AND its symptoms \
start after the change. Unrelated recent changes are common red herrings - say so explicitly when you dismiss one.
- Distinguish mechanisms: CrashLoopBackOff vs OOMKilled vs readiness-probe failures vs scaled-to-zero vs DNS/config \
errors vs client-side pool exhaustion vs a genuinely slow datastore vs network delay (client span slow, server span fast \
or missing) vs CPU throttling vs slow memory growth / GC pressure before any OOM.
- Consumer lag is a symptom; ask why the consumer is slow (its own CPU/memory, or a slow dependency). If the time is
spent in the consumer's own spans and its dependencies are healthy, the root cause is the consumer (queue_backlog), even
without a recorded change.
- Prefer what the telemetry shows over speculation: never blame a dependency whose own metrics are healthy by assuming
a "hidden" problem there.
- Which category to pick: the most specific underlying MECHANISM, not the symptom. Lag, latency and errors are
symptoms; memory growth, CPU limits, a slow datastore, a bad change are mechanisms.
- Which service to name: the service where the fault lives. For network latency or an unreachable dependency, name the
destination service whose inbound path is slow/broken (not the caller that reports timeouts). For a bad config or
deploy, name the service whose config/deploy changed.
- Use related postmortems and runbooks when the pattern matches, and cite their ids.
- Only cite evidence ids that appear in the evidence list. Never invent metrics, pods or versions.
- Do not name a root-cause service unless you have direct telemetry from it (its metrics, logs, events or its own
spans). A matching postmortem is a hint, not evidence: if the suspected dependency's own metrics look healthy, the
problem is more likely in the service itself.
- Calibrate confidence: >0.8 only when traces, metrics and a mechanism all agree AND no alternative fits the evidence;
use 0.5-0.7 when the mechanism is ambiguous (e.g. CPU saturation vs datastore degradation on a database).
- Suggested actions must be concrete (kubectl commands against namespace otel-demo where possible), start with the \
safest mitigation, and mark risk."""


def _fmt_evidence(ev: Evidence) -> str:
    d = ev.data
    kind = d.get("kind")
    head = f"[{ev.id}] ({ev.source}, {ev.severity}) {ev.title}"
    if kind == "metric_series":
        # keep a coarse shape of the series: every 5th point
        pts = d.get("series", [])[::5]
        unit = d.get("unit", "")
        vals = ", ".join(f"{t:+.0f}m:{v:.3g}" for t, v in pts)
        return f"{head}\n    shape ({unit}): {vals}"
    if kind == "trace_summary":
        return (f"{head}\n    error_origins={json.dumps(d.get('error_origins', {}))} "
                f"latency_contributors_ms={json.dumps(d.get('latency_contributors_ms', {}))} p95={d.get('p95_ms')}ms")
    if kind == "trace_sample":
        spans = " → ".join(
            f"{s['service']}:{s['operation']}({s['duration_ms']}ms{', ERROR' if s.get('error') else ''}"
            f"{', peer=' + s['peer_service'] if s.get('peer_service') else ''})" for s in d.get("spans", []))
        last_msg = next((s["message"] for s in reversed(d.get("spans", [])) if s.get("message")), "")
        return f"{head}\n    spans: {spans}\n    deepest error: {last_msg}"
    if kind == "log_pattern":
        return f"{head}\n    first seen {d.get('first_seen_min')}m, sample: {d.get('sample', '')[:220]}"
    if kind in ("k8s_event", "pod_status"):
        return f"{head}\n    {ev.detail or json.dumps({k: v for k, v in d.items() if k != 'kind'})}"
    return head + (f"\n    {ev.detail}" if ev.detail else "")


def build_user_prompt(alert: Alert, evidence: list[Evidence], findings: list[Finding],
                      documents: list[dict], topology: Topology, max_evidence: int = 70) -> str:
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    src_rank = {"traces": 0, "changes": 1, "k8s_events": 2, "logs": 3, "metrics": 4}
    ordered = sorted(evidence, key=lambda e: (sev_rank.get(e.severity, 3), src_rank.get(e.source, 5)))
    # drop healthy metrics beyond the budget but keep everything notable
    chosen = [e for e in ordered if e.severity != "info" or e.source != "metrics"][:max_evidence]
    if len(chosen) < max_evidence:
        chosen += [e for e in ordered if e not in chosen][: max_evidence - len(chosen)]

    services_seen = sorted({e.service for e in evidence if e.service})
    graph_lines = [f"  {s} -> {', '.join(topology.callees(s))}" for s in topology.services if topology.callees(s)]
    finding_lines = [f"  - ({f.score:.1f}) [{f.kind}] {f.summary}  evidence={f.evidence_ids}"
                     for f in sorted(findings, key=lambda x: -x.score)[:15]]
    doc_lines = [f"  - {d['id']} ({d['type']}, services={d.get('services')}, category={d.get('category')}): "
                 f"{d['title']}\n      {d.get('snippet', '')[:350]}" for d in documents]
    categories = "\n".join(f"  - {k}: {v}" for k, v in CATEGORY_DESCRIPTIONS.items())

    return f"""ALERT
  name: {alert.name}
  service: {alert.service}
  severity: {alert.severity}
  summary: {alert.summary}
  fired at: {alert.starts_at.isoformat()} (all times below are minutes relative to this, negative = before)

SERVICE CALL GRAPH (caller -> callees; kafka edges are async)
{chr(10).join(graph_lines)}

SERVICES WITH EVIDENCE: {', '.join(services_seen)}

AUTOMATED SIGNAL ANALYSIS (heuristic hints - verify against the evidence, they can be wrong)
{chr(10).join(finding_lines) or '  (none)'}

EVIDENCE ({len(chosen)} of {len(evidence)} items, most severe first)
{chr(10).join(_fmt_evidence(e) for e in chosen)}

RELATED KNOWLEDGE (postmortems, runbooks, confirmed past incidents)
{chr(10).join(doc_lines) or '  (none)'}

ROOT CAUSE CATEGORIES
{categories}

Return your conclusion using the provided schema. root_cause_service must be one of the service names in the call graph."""
