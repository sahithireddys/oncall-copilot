"""Deterministic signal extraction: Evidence -> Findings.

These are the observations an experienced on-call engineer makes in the first
few minutes ("traces all end in payment", "payment was deployed 11 minutes
ago", "recommendation has been OOMKilled 4 times"). They drive the drill-down
loop and give the LLM a pre-digested summary. The LLM is told these are hints
to verify, not conclusions.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..models import Evidence, Finding
from ..topology import DATASTORES, Topology

POOL_RE = re.compile(r"pool (busy|exhausted)|awaiting (response|connection)|connection from pool|too many connections", re.I)
SLOW_QUERY_RE = re.compile(r"slow query|statement timeout|lock timeout|BUSY", re.I)
DNS_RE = re.compile(r"no such host|dns error|name resolution|could not resolve", re.I)
TIMEOUT_RE = re.compile(r"deadline|timed out|timeout", re.I)
CALLING_RE = re.compile(r"failed calling ([a-z0-9-]+)", re.I)

CHANGE_WINDOW_MIN = 30.0


def _recency_weight(minutes_before: float) -> float:
    if minutes_before <= 20:
        return 1.0
    if minutes_before <= CHANGE_WINDOW_MIN:
        return 0.5
    return 0.0


def _mins(d: dict) -> float:
    v = d.get("minutes_before_alert")
    return float(v) if v is not None else 999.0


def extract_findings(evidence: list[Evidence], alert_service: str, topology: Topology) -> list[Finding]:
    f: list[Finding] = []

    def add(kind: str, service: str, score: float, summary: str, ev: Evidence, **data):
        f.append(Finding(kind=kind, service=service, score=round(score, 3), summary=summary,
                         evidence_ids=[ev.id], data=data))

    for ev in evidence:
        d = ev.data
        kind = d.get("kind")
        svc = ev.service or alert_service

        if kind == "metric_series":
            m, base, cur, peak = d["metric"], d["baseline"], d["current"], d["peak"]
            if m == "error_rate" and ev.severity != "info":
                add("error_spike", svc, 1.0 if ev.severity == "critical" else 0.6,
                    f"{svc} error rate {base*100:.1f}% → {cur*100:.1f}%", ev, baseline=base, current=cur)
            elif m == "p95_latency_ms" and ev.severity != "info":
                add("latency_spike", svc, 0.8 if ev.severity == "critical" else 0.5,
                    f"{svc} p95 {base:.0f}ms → {cur:.0f}ms", ev, baseline=base, current=cur)
            elif m == "request_rate" and ev.severity == "critical":
                add("traffic_drop", svc, 2.0, f"{svc} request rate fell {base:.1f} → {cur:.1f} req/s", ev)
            elif m == "cpu_utilization" and (cur >= 0.9 or peak >= 0.97):
                add("cpu_saturation", svc, 2.0, f"{svc} CPU at {cur*100:.0f}% of limit", ev, current=cur)
            elif m == "memory_utilization" and peak >= 0.95:
                add("memory_pressure", svc, 1.2, f"{svc} memory peaked at {peak*100:.0f}% of limit", ev, peak=peak)
            elif m == "consumer_lag" and ev.severity != "info":
                add("consumer_lag", svc, 0.8, f"{svc} consumer lag {base:,.0f} → {cur:,.0f}", ev, current=cur)

        elif kind == "trace_summary":
            total, errors = d.get("total", 0) or 1, d.get("errors", 0)
            origins: dict = d.get("error_origins", {})
            if errors / total > 0.02 and origins:
                for o, n in list(origins.items())[:3]:
                    share = n / max(errors, 1)
                    if share >= 0.1:
                        add("trace_error_origin", o, 3.5 * share,
                            f"{share*100:.0f}% of failing {ev.service} traces end in {o}", ev, share=share, count=n)
            lat: dict = d.get("latency_contributors_ms", {})
            total_lat = sum(lat.values())
            if total_lat > 100:
                for o, ms in list(lat.items())[:3]:
                    share = ms / total_lat
                    if share >= 0.15:
                        add("trace_latency_origin", o, 3.0 * share,
                            f"{o} accounts for {share*100:.0f}% of added latency in {ev.service} traces (+{ms:.0f}ms)",
                            ev, share=share, added_ms=ms)

        elif kind == "trace_sample":
            spans = d.get("spans", [])
            last = spans[-1] if spans else {}
            if last.get("kind") == "client" and last.get("peer_service"):
                peer = last["peer_service"]
                msg = last.get("message", "")
                if TIMEOUT_RE.search(msg) or d.get("slow"):
                    add("client_timeout", peer, 0.8, f"calls to {peer} time out on the client side ({last.get('duration_ms')}ms)", ev,
                        message=msg)
                elif msg:
                    add("peer_unreachable", peer, 0.8, f"{last['service']} cannot reach {peer}: {msg[:80]}", ev, message=msg)

        elif kind == "log_pattern":
            level, pattern, count = d.get("level"), d.get("pattern", ""), d.get("count", 0)
            m = CALLING_RE.search(pattern)
            if m:
                callee = m.group(1)
                add("downstream_errors", callee, 0.4, f"{svc} logs {count:,} errors calling {callee}", ev, caller=svc)
                if DNS_RE.search(pattern) and svc != callee:
                    add("dns_failure", svc, 1.5, f"{svc} cannot resolve its target: {pattern[:100]}", ev)
                continue
            if POOL_RE.search(pattern):
                add("pool_exhaustion", svc, 2.0, f"{svc}: {pattern[:100]}", ev, count=count)
            if SLOW_QUERY_RE.search(pattern):
                add("slow_queries", svc, 1.5, f"{svc}: {pattern[:100]}", ev, count=count)
            if DNS_RE.search(pattern):
                add("dns_failure", svc, 1.5, f"{svc} cannot resolve a dependency: {pattern[:100]}", ev)
            if level in ("ERROR", "FATAL") and count >= 20:
                add("error_logs", svc, 1.0 if level == "FATAL" or count > 200 else 0.6,
                    f"{count:,}× {level} in {svc}: {pattern[:90]}", ev, count=count, message=pattern)
            if "heap" in pattern.lower() or "outofmemory" in pattern.lower().replace(" ", ""):
                add("memory_pressure", svc, 0.6, f"{svc}: {pattern[:90]}", ev)

        elif kind == "pod_status":
            if d.get("waiting_reason") == "CrashLoopBackOff":
                add("crash_loop", svc, 3.0, f"{d['pod']} is in CrashLoopBackOff ({d['restarts']} restarts)", ev,
                    restarts=d["restarts"])
            elif d.get("last_termination_reason") == "OOMKilled" and d.get("restarts", 0) >= 1:
                add("oom_killed", svc, 2.5 if d["restarts"] >= 2 else 1.2,
                    f"{d['pod']} OOMKilled, {d['restarts']} restarts", ev, restarts=d["restarts"])
            elif d.get("restarts", 0) >= 1:
                add("restarts", svc, 0.3, f"{d['pod']} restarted {d['restarts']}×", ev)

        elif kind == "k8s_event":
            reason = d.get("reason", "")
            if reason == "Unhealthy":
                add("probe_failure", svc, 2.5, f"{svc}: {d.get('message')} ×{d.get('count')}", ev)
            elif reason == "CPUThrottlingHigh":
                add("cpu_throttling", svc, 1.0, f"{svc}: {d.get('message')}", ev)

        elif kind == "deploy":
            mins = _mins(d)
            w = _recency_weight(mins)
            if w:
                add("recent_deploy", svc, 1.5 * w,
                    f"{svc} rolled out {d.get('from_version')} → {d.get('to_version')} {mins:.0f}m before the alert", ev,
                    minutes_before_alert=mins, to_version=d.get("to_version"))

        elif kind == "config_change":
            mins = _mins(d)
            w = _recency_weight(mins)
            target = d.get("affects") or svc
            if w:
                add("recent_config_change", target, 1.5 * w,
                    f"{d.get('resource')}: {d.get('key')} {d.get('old')!r} → {d.get('new')!r} {mins:.0f}m before the alert",
                    ev, minutes_before_alert=mins, key=d.get("key"))

        elif kind == "scale":
            if d.get("to_replicas") == 0:
                add("scaled_to_zero", svc, 2.5, f"{svc} was scaled to 0 replicas", ev)

    return f


def score_services(findings: list[Finding]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for fd in findings:
        scores[fd.service] += fd.score
    # corroboration bonus: a change on a service is only damning if that service also shows symptoms
    by_svc: dict[str, set[str]] = defaultdict(set)
    for fd in findings:
        by_svc[fd.service].add(fd.kind)
    for svc, kinds in by_svc.items():
        changes = kinds & {"recent_deploy", "recent_config_change"}
        symptoms = kinds - {"recent_deploy", "recent_config_change", "downstream_errors"}
        if changes and not symptoms:
            scores[svc] *= 0.4
    return dict(sorted(scores.items(), key=lambda kv: -kv[1]))


def pick_suspects(findings: list[Finding], examined: set[str], limit: int = 3) -> list[str]:
    ranked = score_services(findings)
    return [s for s, sc in ranked.items() if s not in examined and sc >= 0.8][:limit]


def is_datastore(service: str) -> bool:
    return service in DATASTORES
