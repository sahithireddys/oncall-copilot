"""Domain models shared by the agent, API and eval harness."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RootCauseCategory(str, Enum):
    bad_deploy = "bad_deploy"
    config_change = "config_change"
    memory_leak = "memory_leak"
    crash_loop = "crash_loop"
    cpu_saturation = "cpu_saturation"
    network_latency = "network_latency"
    dependency_unavailable = "dependency_unavailable"
    datastore_degradation = "datastore_degradation"
    resource_exhaustion = "resource_exhaustion"
    queue_backlog = "queue_backlog"
    probe_failure = "probe_failure"
    unknown = "unknown"


CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "bad_deploy": "A new rollout/image version introduced errors or latency.",
    "config_change": "A ConfigMap, env var or feature-flag change altered behaviour.",
    "memory_leak": "Memory grows over time, causing GC pressure and slowdowns and eventually OOMKills (an OOM has not necessarily happened yet).",
    "crash_loop": "Container exits on start-up repeatedly (CrashLoopBackOff).",
    "cpu_saturation": "CPU pinned at limit / throttled, causing latency.",
    "network_latency": "Added network latency or packet loss on the path to a service (client spans slow, server spans fast).",
    "dependency_unavailable": "A downstream service is unreachable (connection refused/DNS).",
    "datastore_degradation": "A database, cache or broker (postgresql, valkey-cart, kafka) is slow or failing queries. Use this for datastores even when their CPU is high.",
    "resource_exhaustion": "A finite pool (connections, threads, file handles) is exhausted.",
    "queue_backlog": "A consumer is intrinsically too slow per message (its own spans are slow, dependencies healthy) and no more specific mechanism (memory leak / GC pressure, CPU saturation, crash loop) explains it.",
    "probe_failure": "Readiness/liveness probes fail, pods removed from endpoints.",
    "unknown": "Not enough evidence to classify.",
}


class Alert(BaseModel):
    name: str
    service: str
    severity: Literal["critical", "warning", "info"] = "critical"
    summary: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(default_factory=utcnow)
    fingerprint: str | None = None


class Evidence(BaseModel):
    """A single observation the agent collected from a telemetry source."""
    id: str
    source: Literal["metrics", "traces", "logs", "k8s_events", "changes", "knowledge"]
    service: str | None = None
    title: str
    detail: str = ""
    severity: Literal["info", "warning", "critical"] = "info"
    link: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


class Finding(BaseModel):
    """A signal extracted from evidence by the analysis step (e.g. 'OOMKilled x3 on recommendation')."""
    kind: str
    service: str
    score: float
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class SuggestedAction(BaseModel):
    title: str
    command: str | None = None
    risk: Literal["low", "medium", "high"] = "low"
    rationale: str = ""


class EvidenceRef(BaseModel):
    evidence_id: str
    why_it_matters: str


class AlternativeHypothesis(BaseModel):
    root_cause_service: str
    category: RootCauseCategory
    why_less_likely: str


class Hypothesis(BaseModel):
    root_cause_service: str
    category: RootCauseCategory
    title: str = Field(description="One line, e.g. 'payment v1.4.2 rollout is throwing NullPointerException'")
    summary: str = Field(description="2-4 sentences explaining the causal chain from root cause to alert.")
    reasoning: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    related_documents: list[str] = Field(default_factory=list, description="Ids of postmortems/runbooks that match.")
    alternatives: list[AlternativeHypothesis] = Field(default_factory=list)
    reasoner: str = "heuristic"


class InvestigationStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class Investigation(BaseModel):
    id: str
    alert: Alert
    status: InvestigationStatus = InvestigationStatus.queued
    scenario_id: str | None = None
    reasoner: str = "heuristic"
    telemetry_mode: str = "simulated"
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    hypothesis: Hypothesis | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    retrieved_documents: list[dict[str, Any]] = Field(default_factory=list)
    services_examined: list[str] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    # Filled only for scenario runs: how the hypothesis compares with the known root cause.
    grading: dict[str, Any] | None = None
    feedback: "Feedback | None" = None
    slack_posted: bool = False


class FeedbackVerdict(str, Enum):
    correct = "correct"
    partially_correct = "partially_correct"
    incorrect = "incorrect"


class FeedbackIn(BaseModel):
    verdict: FeedbackVerdict
    actual_root_cause_service: str | None = None
    actual_category: RootCauseCategory | None = None
    notes: str = ""
    submitted_by: str = "on-call"


class Feedback(FeedbackIn):
    investigation_id: str
    created_at: datetime = Field(default_factory=utcnow)


class StepEvent(BaseModel):
    """Progress event streamed to the UI while the agent works."""
    id: int | None = None
    investigation_id: str
    ts: datetime = Field(default_factory=utcnow)
    node: str
    status: Literal["started", "progress", "completed", "failed"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


Investigation.model_rebuild()
