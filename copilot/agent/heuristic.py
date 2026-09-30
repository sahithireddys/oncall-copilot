"""Rule-based reasoner. Runs offline and is the baseline the LLM is measured against."""
from __future__ import annotations

from collections import defaultdict

from ..models import (
    AlternativeHypothesis, EvidenceRef, Finding, Hypothesis, RootCauseCategory as C, SuggestedAction,
)
from .analysis import is_datastore, score_services

NS = "otel-demo"


def classify(service: str, kinds: set[str], findings: list[Finding]) -> C:
    if "crash_loop" in kinds:
        return C.crash_loop
    if "oom_killed" in kinds:
        return C.memory_leak
    if "scaled_to_zero" in kinds:
        return C.dependency_unavailable
    if "probe_failure" in kinds:
        return C.probe_failure
    if "dns_failure" in kinds and "recent_config_change" in kinds:
        return C.config_change
    if "pool_exhaustion" in kinds:
        return C.resource_exhaustion
    if "slow_queries" in kinds or (is_datastore(service) and kinds & {"trace_latency_origin", "latency_spike", "trace_error_origin"}):
        return C.datastore_degradation
    if "cpu_saturation" in kinds or "cpu_throttling" in kinds:
        return C.cpu_saturation
    if "client_timeout" in kinds and "latency_spike" not in kinds:
        return C.network_latency
    if "recent_config_change" in kinds:
        return C.config_change
    if "recent_deploy" in kinds:
        return C.bad_deploy
    if "traffic_drop" in kinds or "peer_unreachable" in kinds:
        return C.dependency_unavailable
    if "consumer_lag" in kinds:
        return C.queue_backlog
    if "memory_pressure" in kinds:
        return C.memory_leak
    return C.unknown


def actions_for(category: C, service: str) -> list[SuggestedAction]:
    s, A = service, SuggestedAction
    table: dict[C, list[SuggestedAction]] = {
        C.bad_deploy: [A(title=f"Roll back {s}", command=f"kubectl -n {NS} rollout undo deployment/{s}", risk="low",
                         rationale="Errors began right after the rollout.")],
        C.config_change: [A(title=f"Revert the recent config change on {s}",
                            command=f"kubectl -n {NS} rollout history deployment/{s}", risk="low",
                            rationale="Diff the last two revisions / flagd values and restore the previous value.")],
        C.memory_leak: [A(title=f"Restart {s} to stop the OOM loop", command=f"kubectl -n {NS} rollout restart deployment/{s}",
                          risk="low", rationale="Buys time; the leak will return."),
                        A(title="Capture a heap profile before the next OOM", risk="low")],
        C.crash_loop: [A(title=f"Read the crash reason", command=f"kubectl -n {NS} logs deploy/{s} --previous", risk="low"),
                       A(title=f"Roll back {s}", command=f"kubectl -n {NS} rollout undo deployment/{s}", risk="low")],
        C.cpu_saturation: [A(title=f"Scale out {s}", command=f"kubectl -n {NS} scale deployment/{s} --replicas=3", risk="medium",
                             rationale="Relieves CPU pressure while the hot path is investigated.")],
        C.network_latency: [A(title="Look for network chaos / node issues", command="kubectl get networkchaos -A", risk="low")],
        C.dependency_unavailable: [A(title=f"Check {s} replicas and endpoints",
                                     command=f"kubectl -n {NS} get deploy,endpoints {s}", risk="low"),
                                   A(title=f"Scale {s} back up", command=f"kubectl -n {NS} scale deployment/{s} --replicas=1",
                                     risk="low")],
        C.datastore_degradation: [A(title=f"Find slow/blocking queries on {s}", risk="low",
                                    command=(f"kubectl -n {NS} exec deploy/{s} -- psql -U root -d otel -c "
                                             "\"SELECT pid, now()-query_start, query FROM pg_stat_activity WHERE state<>'idle'\"")
                                    if s == "postgresql" else f"kubectl -n {NS} exec deploy/{s} -- valkey-cli SLOWLOG GET 10")],
        C.resource_exhaustion: [A(title=f"Raise {s}'s connection pool and restart it",
                                  command=f"kubectl -n {NS} rollout restart deployment/{s}", risk="medium")],
        C.queue_backlog: [A(title=f"Scale {s} consumers", command=f"kubectl -n {NS} scale deployment/{s} --replicas=3",
                            risk="medium")],
        C.probe_failure: [A(title=f"Check {s} endpoints and probe config",
                            command=f"kubectl -n {NS} describe pod -l app.kubernetes.io/name={s}", risk="low")],
    }
    return table.get(category, [A(title=f"Inspect {s} dashboards and logs", risk="low")])


def heuristic_hypothesis(findings: list[Finding], alert_service: str, documents: list[dict]) -> Hypothesis:
    if not findings:
        return Hypothesis(root_cause_service=alert_service, category=C.unknown, title="No anomalous signals found",
                          summary="Metrics, traces, logs and events for the alerting service look normal. The alert may be "
                                  "noisy or the telemetry may be incomplete.", confidence=0.1, reasoner="heuristic")
    scores = score_services(findings)
    by_svc: dict[str, list[Finding]] = defaultdict(list)
    for fd in findings:
        by_svc[fd.service].append(fd)
    ranked = list(scores.items())
    top, top_score = ranked[0]
    kinds = {fd.kind for fd in by_svc[top]}
    category = classify(top, kinds, by_svc[top])
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - second) / top_score if top_score else 0
    confidence = round(min(0.95, 0.35 + 0.45 * margin + 0.05 * min(len(kinds), 3)), 2)

    top_findings = sorted(by_svc[top], key=lambda x: -x.score)[:5]
    refs = [EvidenceRef(evidence_id=fd.evidence_ids[0], why_it_matters=fd.summary) for fd in top_findings if fd.evidence_ids]
    chain = "; ".join(fd.summary for fd in top_findings[:3])
    if top != alert_service:
        summary = f"The {alert_service} alert is a symptom. Signals point to {top}: {chain}."
    else:
        summary = f"The problem is in {alert_service} itself: {chain}."

    alts = []
    for svc, _ in ranked[1:3]:
        k = {fd.kind for fd in by_svc[svc]}
        cat = classify(svc, k, by_svc[svc])
        if svc == alert_service and cat in (C.unknown,):
            continue
        alts.append(AlternativeHypothesis(root_cause_service=svc, category=cat,
                                          why_less_likely=f"weaker signal score ({scores[svc]:.1f} vs {top_score:.1f})"))
    docs = [d["id"] for d in documents if top in d.get("services", []) or d.get("category") == category.value][:3]
    return Hypothesis(
        root_cause_service=top, category=category,
        title=f"{top}: {category.value.replace('_', ' ')}",
        summary=summary, confidence=confidence, supporting_evidence=refs,
        suggested_actions=actions_for(category, top), related_documents=docs, alternatives=alts, reasoner="heuristic",
    )
