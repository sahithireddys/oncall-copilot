"""Jaeger query API -> trace summary + sample traces + dependency graph."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from statistics import mean

import httpx

from ..models import Evidence
from ..topology import Topology
from .base import Window

PEER_TAGS = ("peer.service", "rpc.service", "server.address", "net.peer.name", "db.system")


def _tags(span: dict) -> dict:
    return {t["key"]: t.get("value") for t in span.get("tags", [])}


def _is_error(span: dict) -> bool:
    t = _tags(span)
    return t.get("error") in (True, "true") or t.get("otel.status_code") == "ERROR"


def _error_message(span: dict) -> str:
    t = _tags(span)
    for key in ("otel.status_description", "exception.message", "error.message", "http.status_code"):
        if t.get(key):
            return str(t[key])
    for lg in span.get("logs", []):
        fields = {f["key"]: f.get("value") for f in lg.get("fields", [])}
        if fields.get("exception.message"):
            return str(fields["exception.message"])
    return ""


def analyse_trace(trace: dict) -> dict:
    """Return per-trace structure: ordered spans with depth, error origin, self-times per service."""
    procs = {pid: p.get("serviceName", "?") for pid, p in trace.get("processes", {}).items()}
    spans = {s["spanID"]: s for s in trace.get("spans", [])}
    parent: dict[str, str | None] = {}
    children: dict[str, list[str]] = defaultdict(list)
    for sid, s in spans.items():
        p = next((r["spanID"] for r in s.get("references", []) if r.get("refType") == "CHILD_OF" and r["spanID"] in spans), None)
        parent[sid] = p
        if p:
            children[p].append(sid)
    depth: dict[str, int] = {}

    def d(sid: str) -> int:
        if sid not in depth:
            depth[sid] = 0 if parent[sid] is None else d(parent[sid]) + 1
        return depth[sid]

    for sid in spans:
        d(sid)
    self_ms: dict[str, float] = defaultdict(float)
    for sid, s in spans.items():
        child_total = sum(spans[c]["duration"] for c in children[sid])
        self_ms[procs.get(s["processID"], "?")] += max(0.0, s["duration"] - child_total) / 1000.0

    origin, origin_span = None, None
    errs = [sid for sid, s in spans.items() if _is_error(s)]
    if errs:
        deepest = max(errs, key=lambda sid: depth[sid])
        s = spans[deepest]
        svc = procs.get(s["processID"], "?")
        tags = _tags(s)
        kind = tags.get("span.kind", "")
        # an erroring client span with no child means the callee never answered -> blame the peer
        if kind == "client" and not children[deepest]:
            peer = next((str(tags[k]).split(":")[0] for k in PEER_TAGS if tags.get(k)), None)
            origin = peer or svc
        else:
            origin = svc
        origin_span = deepest

    root = next((sid for sid, p in parent.items() if p is None), None)
    chain = []
    if origin_span:
        cur: str | None = origin_span
        while cur:
            chain.append(cur)
            cur = parent[cur]
        chain.reverse()
    rendered = []
    for sid in chain:
        s = spans[sid]
        tags = _tags(s)
        rendered.append({
            "service": procs.get(s["processID"], "?"), "operation": s.get("operationName", ""),
            "duration_ms": round(s["duration"] / 1000.0, 1), "error": _is_error(s), "message": _error_message(s),
            "depth": depth[sid], "kind": tags.get("span.kind", "internal"),
            **({"peer_service": next((str(tags[k]).split(":")[0] for k in PEER_TAGS if tags.get(k)), None)}
               if tags.get("span.kind") == "client" else {}),
        })
    return {
        "trace_id": trace.get("traceID"),
        "duration_ms": spans[root]["duration"] / 1000.0 if root else 0.0,
        "error": bool(errs), "origin": origin, "chain": rendered, "self_ms": dict(self_ms),
    }


def summarise_traces(service: str, traces: list[dict], ui_base: str | None = None) -> list[Evidence]:
    analysed = [analyse_trace(t) for t in traces]
    total = len(analysed)
    if not total:
        return []
    failing = [a for a in analysed if a["error"]]
    origins = Counter(a["origin"] for a in failing if a["origin"])
    # latency attribution: mean self-time per service in the slowest 20% minus the fastest 50%
    by_dur = sorted(analysed, key=lambda a: a["duration_ms"])
    slow, fast = by_dur[int(total * 0.8):], by_dur[: max(1, total // 2)]
    contrib = {}
    for svc in {k for a in analysed for k in a["self_ms"]}:
        delta = mean(a["self_ms"].get(svc, 0.0) for a in slow) - mean(a["self_ms"].get(svc, 0.0) for a in fast)
        if delta > 5:
            contrib[svc] = round(delta, 1)
    durations = [a["duration_ms"] for a in by_dur]
    p95 = durations[int(0.95 * (len(durations) - 1))]
    link = lambda tid: f"{ui_base.rstrip('/')}/trace/{tid}" if ui_base else None  # noqa: E731
    out = [Evidence(
        id=f"traces:{service}:summary", source="traces", service=service,
        title=f"{len(failing)}/{total} sampled {service} traces failed"
              + (f"; top error origin: {origins.most_common(1)[0][0]}" if origins else ""),
        severity="critical" if len(failing) / total > 0.05 else ("warning" if len(failing) / total > 0.02 else "info"),
        link=f"{ui_base.rstrip('/')}/search?service={service}&tags=%7B%22error%22%3A%22true%22%7D" if ui_base else None,
        data={"kind": "trace_summary", "total": total, "errors": len(failing), "error_origins": dict(origins.most_common()),
              "latency_contributors_ms": dict(sorted(contrib.items(), key=lambda kv: -kv[1])), "p95_ms": round(p95, 1)},
    )]
    for origin, _ in origins.most_common(2):
        a = next(x for x in failing if x["origin"] == origin)
        out.append(Evidence(
            id=f"traces:{service}:{a['trace_id'][:12]}", source="traces", service=origin,
            title=f"Failing trace {a['trace_id'][:12]}: " + " → ".join(s["service"] for s in a["chain"]),
            detail=next((s["message"] for s in reversed(a["chain"]) if s["message"]), ""), severity="critical",
            link=link(a["trace_id"]),
            data={"kind": "trace_sample", "trace_id": a["trace_id"], "spans": a["chain"], "origin": origin}))
    return out


class JaegerTraces:
    def __init__(self, base_url: str, client: httpx.Client | None = None, limit: int = 200):
        self.base = base_url.rstrip("/")
        self.http = client or httpx.Client(timeout=20)
        self.limit = limit

    def get_traces(self, service: str, window: Window) -> list[Evidence]:
        start_us = int((window.end.timestamp() - 600) * 1e6)  # last 10 minutes before the alert
        end_us = int(window.end.timestamp() * 1e6)
        params = {"service": service, "start": start_us, "end": end_us, "limit": self.limit}
        r = self.http.get(f"{self.base}/api/traces", params=params)
        r.raise_for_status()
        traces = r.json().get("data") or []
        # make sure failing traces are represented even when sampling is sparse
        r2 = self.http.get(f"{self.base}/api/traces", params={**params, "tags": json.dumps({"error": "true"}), "limit": 50})
        if r2.status_code == 200:
            seen = {t["traceID"] for t in traces}
            traces += [t for t in (r2.json().get("data") or []) if t["traceID"] not in seen]
        return summarise_traces(service, traces, self.base)

    def get_topology(self, window: Window) -> Topology | None:
        r = self.http.get(f"{self.base}/api/dependencies",
                          params={"endTs": int(window.end.timestamp() * 1000), "lookback": 3600 * 1000})
        if r.status_code != 200:
            return None
        edges: dict[str, list[str]] = defaultdict(list)
        for dep in r.json().get("data") or []:
            if dep["parent"] != dep["child"]:
                edges[dep["parent"]].append(dep["child"])
        return Topology(dict(edges)) if edges else None
