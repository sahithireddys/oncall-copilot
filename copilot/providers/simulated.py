"""Scenario-driven telemetry simulator for the OpenTelemetry Demo topology.

Given a `Scenario` (one injected fault plus red-herring noise), this produces
metrics, traces, logs, Kubernetes events and change history that look like
what Prometheus/Jaeger/OpenSearch/the K8s API would return during that
incident. Faults propagate *upstream* through the call graph, so an alert on
`checkout` caused by `payment` shows errors in both, and the agent has to
follow the traces to find the origin.

Everything is deterministic per scenario id (seeded RNG), which makes the eval
set repeatable.
"""
from __future__ import annotations

import random
import zlib

from ..models import Evidence
from ..topology import DEFAULT_TOPOLOGY, Topology
from .base import Window, metric_evidence
from .scenarios import Fault, Noise, Scenario

# Healthy baselines per service: requests/s, p95 latency (ms), cpu, memory (fraction of limit).
BASELINES: dict[str, dict[str, float]] = {
    "frontend-proxy": {"rps": 48, "p95": 210, "cpu": 0.22, "mem": 0.35},
    "frontend": {"rps": 45, "p95": 180, "cpu": 0.35, "mem": 0.52},
    "checkout": {"rps": 3.2, "p95": 320, "cpu": 0.18, "mem": 0.41},
    "payment": {"rps": 3.1, "p95": 45, "cpu": 0.12, "mem": 0.38},
    "cart": {"rps": 21, "p95": 25, "cpu": 0.21, "mem": 0.44},
    "valkey-cart": {"rps": 60, "p95": 3, "cpu": 0.09, "mem": 0.31},
    "product-catalog": {"rps": 52, "p95": 18, "cpu": 0.25, "mem": 0.36},
    "currency": {"rps": 40, "p95": 6, "cpu": 0.15, "mem": 0.22},
    "shipping": {"rps": 9, "p95": 40, "cpu": 0.1, "mem": 0.28},
    "quote": {"rps": 9, "p95": 22, "cpu": 0.08, "mem": 0.25},
    "email": {"rps": 3.1, "p95": 60, "cpu": 0.07, "mem": 0.33},
    "recommendation": {"rps": 18, "p95": 55, "cpu": 0.2, "mem": 0.47},
    "ad": {"rps": 18, "p95": 30, "cpu": 0.24, "mem": 0.58},
    "kafka": {"rps": 6.4, "p95": 8, "cpu": 0.2, "mem": 0.55},
    "accounting": {"rps": 3.2, "p95": 35, "cpu": 0.11, "mem": 0.4},
    "fraud-detection": {"rps": 3.2, "p95": 28, "cpu": 0.14, "mem": 0.5},
    "postgresql": {"rps": 40, "p95": 4, "cpu": 0.12, "mem": 0.45},
    "image-provider": {"rps": 30, "p95": 12, "cpu": 0.05, "mem": 0.2},
}
DEFAULT_BASELINE = {"rps": 10, "p95": 50, "cpu": 0.15, "mem": 0.4}
BASE_ERR = 0.004          # healthy background error ratio
PROPAGATION = 0.85        # share of downstream failures that surface upstream (retries absorb the rest)
ASYNC_EDGES = {("checkout", "kafka"), ("kafka", "accounting"), ("kafka", "fraud-detection")}
KAFKA_CONSUMERS = {"accounting", "fraud-detection"}

# Faults where callers see failures even though the service itself reports little/no traffic.
CALLER_VIEW_FAULTS = {"dependency_unavailable", "crash_loop", "probe_failure", "network_latency"}

DEFAULT_MESSAGES = {
    "bad_deploy": "unhandled exception in request handler",
    "config_change": "invalid configuration value",
    "memory_leak": "java.lang.OutOfMemoryError: Java heap space",
    "crash_loop": "fatal: failed to start: required env var not set",
    "cpu_saturation": "request timed out after 5000ms",
    "network_latency": "context deadline exceeded",
    "dependency_unavailable": "dial tcp: connect: connection refused",
    "datastore_degradation": "query exceeded statement timeout",
    "resource_exhaustion": "timeout waiting for connection from pool",
    "queue_backlog": "consumer processing time exceeded poll interval",
    "probe_failure": "upstream connect error or disconnect/reset before headers",
}

BENIGN_LOGS = {
    "frontend": [("WARN", "slow render for /product/{id} (>500ms)", 14)],
    "ad": [("WARN", "GC pause 120ms (G1 Young Generation)", 9)],
    "recommendation": [("INFO", "cache miss rate 12%", 30)],
    "product-catalog": [("WARN", "product {id} not found", 6)],
    "currency": [("WARN", "rate for currency XDR is stale", 3)],
    "kafka": [("INFO", "[GroupCoordinator] rebalance completed", 2)],
    "email": [("WARN", "template cache refreshed", 4)],
}


AMBIENT_ERRORS = [
    "grpc: received message larger than max (4194304 vs. 4194304)",
    "failed to export spans: context deadline exceeded (otel-collector:4317)",
    "unhandled rejection: AbortError: The operation was aborted",
    "flagd: stream error, reconnecting",
    "ERROR could not flush metrics batch: 503 Service Unavailable",
]


def _ramp(t: float, onset: float, ramp: float = 2.0) -> float:
    if t < onset:
        return 0.0
    return min(1.0, (t - onset) / ramp) if ramp > 0 else 1.0


class SimulatedProvider:
    name = "simulated"

    def __init__(self, scenario: Scenario | None = None, topology: Topology | None = None, lookback: int = 45):
        self.sc = scenario
        self.topo = topology or DEFAULT_TOPOLOGY
        self.lookback = lookback
        self.seed = zlib.crc32((scenario.id if scenario else "healthy").encode())
        self.faults: dict[str, Fault] = {f.service: f for f in (scenario.faults if scenario else [])}
        self.times = list(range(-lookback, 1))
        self.noise = list(scenario.noise) if scenario else []
        if scenario and scenario.extra_noise:
            self.noise += self._ambient_noise()
        # memoised per-(service, t) computations
        self._err_cache: dict[tuple[str, int], float] = {}
        self._lat_cache: dict[tuple[str, int], float] = {}
        self._lag_cache: dict[str, dict[int, float]] = {}

    # ------------------------------------------------------------------ helpers
    def _ambient_noise(self) -> list[Noise]:
        """Unrelated background activity every real cluster has: deploys, restarts, noisy error logs."""
        rng = self._rng("ambient")
        fault_svcs = set(self.faults)
        candidates = [s for s in self.topo.services if s not in fault_svcs and s not in ("kafka", "postgresql")]
        out = []
        svc = rng.choice(candidates)
        out.append(Noise(type="deploy", service=svc, at_min=-rng.uniform(4, 40),
                         version_from="2.0.2", version_to="2.0.3"))
        svc = rng.choice(candidates)
        out.append(Noise(type="log_error", service=svc, at_min=-44, count=rng.randint(25, 120),
                         message=rng.choice(AMBIENT_ERRORS)))
        if rng.random() < 0.5:
            out.append(Noise(type="restart", service=rng.choice(candidates), count=1, message="exit 137"))
        return out


    def _rng(self, *parts: object) -> random.Random:
        return random.Random(zlib.crc32(("|".join(map(str, parts))).encode()) ^ self.seed)

    def _base(self, svc: str) -> dict[str, float]:
        return BASELINES.get(svc, DEFAULT_BASELINE)

    def _jitter(self, svc: str, metric: str, t: int, rel: float = 0.05) -> float:
        return 1.0 + self._rng(svc, metric, t).gauss(0, rel)

    def _fault(self, svc: str) -> Fault | None:
        return self.faults.get(svc)

    def _oom_times(self, f: Fault) -> list[float]:
        period = float(f.extra.get("period_min", 6))
        out, t = [], f.onset_min + period
        while t <= 0:
            out.append(t)
            t += period
        return out

    # --------------------------------------------------------- own (server) view
    def own_err(self, svc: str, t: int) -> float:
        f = self._fault(svc)
        e = BASE_ERR
        if not f:
            return e
        k = _ramp(t, f.onset_min)
        if f.type in ("bad_deploy", "config_change", "cpu_saturation", "datastore_degradation",
                      "resource_exhaustion", "queue_backlog"):
            e += k * f.error_rate
        elif f.type == "crash_loop" and k > 0:
            e = 1.0
        elif f.type == "memory_leak":
            if any(0 <= t - om < 1.5 for om in self._oom_times(f)):
                e += 0.25
        return min(1.0, e)

    def own_lat_added(self, svc: str, t: int) -> float:
        f = self._fault(svc)
        if not f:
            return 0.0
        k = _ramp(t, f.onset_min)
        if f.type in ("bad_deploy", "config_change", "cpu_saturation", "datastore_degradation",
                      "resource_exhaustion", "queue_backlog"):
            return k * f.latency_ms
        if f.type == "memory_leak" and k > 0:
            return f.latency_ms * self._mem_phase(f, t)
        return 0.0

    def _mem_phase(self, f: Fault, t: int) -> float:
        period = float(f.extra.get("period_min", 6))
        if t < f.onset_min:
            return 0.0
        return ((t - f.onset_min) % period) / period

    # ------------------------------------------------------ what callers observe
    def caller_view_err(self, svc: str, t: int) -> float:
        f = self._fault(svc)
        if f and _ramp(t, f.onset_min) > 0:
            k = _ramp(t, f.onset_min)
            if f.type == "dependency_unavailable":
                return k * 1.0
            if f.type == "crash_loop":
                return k * 0.9
            if f.type == "probe_failure":
                return k * (f.error_rate or 0.5)
            if f.type == "network_latency":
                return min(1.0, self.effective_err(svc, t) + k * f.error_rate)
        return self.effective_err(svc, t)

    def caller_view_lat_added(self, svc: str, t: int) -> float:
        f = self._fault(svc)
        extra = 0.0
        if f and f.type == "network_latency":
            extra = _ramp(t, f.onset_min) * f.latency_ms
        return self.effective_lat_added(svc, t) + extra

    def _propagates(self, caller: str, callee: str) -> bool:
        return (caller, callee) not in ASYNC_EDGES

    def effective_err(self, svc: str, t: int) -> float:
        key = (svc, t)
        if key in self._err_cache:
            return self._err_cache[key]
        f = self._fault(svc)
        if f and f.type == "dependency_unavailable" and _ramp(t, f.onset_min) > 0:
            self._err_cache[key] = 0.0  # no traffic reaches it
            return 0.0
        e = self.own_err(svc, t)
        for c in self.topo.callees(svc):
            if not self._propagates(svc, c):
                continue
            excess = max(0.0, self.caller_view_err(c, t) - BASE_ERR)
            e += self.topo.call_fraction(svc, c) * PROPAGATION * excess
        e = min(1.0, e)
        self._err_cache[key] = e
        return e

    def effective_lat_added(self, svc: str, t: int) -> float:
        key = (svc, t)
        if key in self._lat_cache:
            return self._lat_cache[key]
        lat = self.own_lat_added(svc, t)
        for c in self.topo.callees(svc):
            if not self._propagates(svc, c):
                continue
            lat += min(1.0, self.topo.call_fraction(svc, c) * 1.2) * self.caller_view_lat_added(c, t)
        self._lat_cache[key] = lat
        return lat

    # ------------------------------------------------------------------ metrics
    def _series(self, svc: str, metric: str) -> list[list[float]]:
        b = self._base(svc)
        f = self._fault(svc)
        out = []
        for t in self.times:
            k = _ramp(t, f.onset_min) if f else 0.0
            if metric == "request_rate":
                v = b["rps"]
                if f and f.type == "dependency_unavailable" and k > 0:
                    v = b["rps"] * (1 - k)
                elif f and f.type == "crash_loop" and k > 0:
                    v = b["rps"] * 0.04
                elif f and f.type == "probe_failure" and k > 0:
                    v = b["rps"] * (1 - 0.5 * k)
                v *= self._jitter(svc, metric, t)
            elif metric == "error_rate":
                v = self.effective_err(svc, t)
                v = max(0.0, v * self._jitter(svc, metric, t, 0.15))
            elif metric == "p95_latency_ms":
                v = (b["p95"] + self.effective_lat_added(svc, t)) * self._jitter(svc, metric, t, 0.07)
            elif metric == "cpu_utilization":
                v = b["cpu"]
                if f and k > 0:
                    if f.type == "cpu_saturation":
                        v = b["cpu"] + (0.97 - b["cpu"]) * k
                    elif f.type in ("dependency_unavailable", "crash_loop"):
                        v = 0.01
                    elif f.type == "datastore_degradation":
                        v = b["cpu"] + (float(f.extra.get("cpu", 0.8)) - b["cpu"]) * k
                    elif f.type == "memory_leak":
                        v = b["cpu"] + 0.35 * self._mem_phase(f, t)  # GC churn
                v = min(1.0, v * self._jitter(svc, metric, t))
            elif metric == "memory_utilization":
                v = b["mem"]
                if f and k > 0:
                    if f.type == "memory_leak":
                        v = b["mem"] + (1.0 - b["mem"]) * self._mem_phase(f, t)
                    elif f.type in ("dependency_unavailable",):
                        v = 0.0
                    elif f.type == "crash_loop":
                        v = 0.05
                v = min(1.0, v * self._jitter(svc, metric, t, 0.02))
            elif metric == "consumer_lag":
                v = self._consumer_lag(svc, t)
            else:
                v = 0.0
            out.append([t, round(v, 4)])
        return out

    def _consumer_degradation(self, svc: str, t: int) -> float:
        f = self._fault(svc)
        if f and _ramp(t, f.onset_min) > 0 and f.type in ("crash_loop", "dependency_unavailable"):
            return 0.95
        added = self.effective_lat_added(svc, t)
        base = self._base(svc)["p95"]
        return max(0.0, 1 - base / (base + added)) if added > 0 else 0.0

    def _lag_table(self, svc: str) -> dict[int, float]:
        if svc in self._lag_cache:
            return self._lag_cache[svc]
        produce = 3.2  # orders/s published by checkout
        lag, table = 12.0, {}
        for t in self.times:
            lag = max(12.0, lag + produce * 60 * self._consumer_degradation(svc, t) - 5)
            table[t] = lag
        self._lag_cache[svc] = table
        return table

    def _consumer_lag(self, svc: str, t: int) -> float:
        return self._lag_table(svc)[t] * self._jitter(svc, "lag", t, 0.04)

    def get_metrics(self, service: str, window: Window) -> list[Evidence]:
        metrics = ["request_rate", "error_rate", "p95_latency_ms", "cpu_utilization", "memory_utilization"]
        if service in KAFKA_CONSUMERS:
            metrics.append("consumer_lag")
        return [metric_evidence(service, m, self._series(service, m)) for m in metrics]

    # ------------------------------------------------------------------- traces
    def _err_contributions(self, svc: str, t: int = -1, depth: int = 0) -> dict[str, float]:
        """Map origin service -> share of `svc`'s error rate it causes."""
        out: dict[str, float] = {}
        own = self.own_err(svc, t) - BASE_ERR
        if own > 0.001:
            out[svc] = own
        if depth > 6:
            return out
        for c in self.topo.callees(svc):
            if not self._propagates(svc, c):
                continue
            scale = self.topo.call_fraction(svc, c) * PROPAGATION
            f = self._fault(c)
            if f and f.type in CALLER_VIEW_FAULTS and _ramp(t, f.onset_min) > 0:
                share = scale * max(0.0, self.caller_view_err(c, t) - BASE_ERR)
                # network latency faults also let deeper errors through
                if share > 0.001:
                    out[c] = out.get(c, 0) + share
                if f.type != "network_latency":
                    continue
            for origin, v in self._err_contributions(c, t, depth + 1).items():
                out[origin] = out.get(origin, 0) + scale * v
        return out

    def _lat_contributions(self, svc: str, t: int = -1, depth: int = 0) -> dict[str, float]:
        out: dict[str, float] = {}
        own = self.own_lat_added(svc, t)
        if own > 1:
            out[svc] = own
        if depth > 6:
            return out
        for c in self.topo.callees(svc):
            if not self._propagates(svc, c):
                continue
            scale = min(1.0, self.topo.call_fraction(svc, c) * 1.2)
            f = self._fault(c)
            if f and f.type == "network_latency" and _ramp(t, f.onset_min) > 0:
                out[c] = out.get(c, 0) + scale * f.latency_ms
            for origin, v in self._lat_contributions(c, t, depth + 1).items():
                out[origin] = out.get(origin, 0) + scale * v
        return out

    RECENT = (-5, -4, -3, -2, -1, 0)

    def _recent_avg(self, fn, svc: str) -> float:
        return sum(fn(svc, t) for t in self.RECENT) / len(self.RECENT)

    def _recent_contrib(self, fn, svc: str) -> dict[str, float]:
        acc: dict[str, float] = {}
        for t in self.RECENT:
            for k, v in fn(svc, t).items():
                acc[k] = acc.get(k, 0.0) + v / len(self.RECENT)
        return acc

    def _message_for(self, svc: str) -> str:
        f = self._fault(svc)
        if not f:
            return "internal error"
        return f.error_message or DEFAULT_MESSAGES.get(f.type, "internal error")

    def _span_chain(self, root: str, origin: str, error: bool, rng: random.Random) -> list[dict]:
        path = self.topo.path(root, origin) or [root, origin]
        f = self._fault(origin)
        spans = []
        unreachable = bool(f and f.type in ("dependency_unavailable", "crash_loop", "probe_failure", "network_latency"))
        for depth, svc in enumerate(path):
            is_last = depth == len(path) - 1
            base = self._base(svc)["p95"] * rng.uniform(0.5, 0.9)
            dur = base + self.caller_view_lat_added(svc, -1) * (0.9 if is_last else 1.0)
            span = {"service": svc, "operation": f"{svc}/handle", "duration_ms": round(dur, 1),
                    "error": error, "message": "", "depth": depth, "kind": "server"}
            if is_last and unreachable:
                # no server span from the origin: only the caller's client span, with peer.service set
                parent = path[depth - 1] if depth > 0 else root
                span.update(service=parent, operation=f"{origin}.Call", kind="client", peer_service=origin,
                            message=self._message_for(origin) if error else "")
                if f and f.type == "network_latency":
                    span["duration_ms"] = round(self._base(origin)["p95"] + f.latency_ms, 1)
            elif is_last and error:
                span["message"] = self._message_for(origin)
            elif error:
                span["message"] = f"rpc error calling {path[depth + 1]}: code = {'Unavailable' if unreachable else 'Internal'}"
            spans.append(span)
        return spans

    def get_traces(self, service: str, window: Window) -> list[Evidence]:
        rng = self._rng("traces", service)
        total = 200
        err_rate = self._recent_avg(self.effective_err, service)
        errors = int(round(total * err_rate))
        contrib = self._recent_contrib(self._err_contributions, service)
        csum = sum(contrib.values())
        origins: dict[str, int] = {}
        if errors and csum > 0:
            fault_errors = int(round(errors * min(1.0, csum / max(err_rate, 1e-9))))
            for o, v in sorted(contrib.items(), key=lambda kv: -kv[1]):
                origins[o] = int(round(fault_errors * v / csum))
            leftover = errors - sum(origins.values())
        else:
            leftover = errors
        # background errors scattered across downstream services
        downstream = [service] + self.topo.downstream(service)
        for _ in range(max(0, leftover)):
            o = rng.choice(downstream)
            origins[o] = origins.get(o, 0) + 1
        origins = {k: v for k, v in origins.items() if v > 0}

        lat = self._recent_contrib(self._lat_contributions, service)
        p95 = self._base(service)["p95"] + self._recent_avg(self.effective_lat_added, service)
        summary = Evidence(
            id=f"traces:{service}:summary",
            source="traces", service=service,
            title=f"{errors}/{total} sampled {service} traces failed in the last 5m"
                  + (f"; top error origin: {max(origins, key=origins.get)}" if origins else ""),
            severity="critical" if errors / total > 0.05 else ("warning" if errors / total > 0.02 else "info"),
            data={"kind": "trace_summary", "total": total, "errors": errors,
                  "error_origins": dict(sorted(origins.items(), key=lambda kv: -kv[1])),
                  "latency_contributors_ms": {k: round(v, 1) for k, v in sorted(lat.items(), key=lambda kv: -kv[1])},
                  "p95_ms": round(p95, 1)},
        )
        out = [summary]
        top = [o for o, n in sorted(origins.items(), key=lambda kv: -kv[1]) if n >= 2][:2]
        for o in top:
            tid = "%032x" % rng.getrandbits(128)
            spans = self._span_chain(service, o, True, rng)
            out.append(Evidence(
                id=f"traces:{service}:{tid[:12]}",
                source="traces", service=o,
                title=f"Failing trace {tid[:12]}: {' → '.join(s['service'] if s['kind']=='server' else s['operation'] for s in spans)}",
                detail=spans[-1]["message"], severity="critical",
                data={"kind": "trace_sample", "trace_id": tid, "spans": spans, "origin": o},
            ))
        slow = [o for o, ms in sorted(lat.items(), key=lambda kv: -kv[1]) if ms > max(50, 0.5 * self._base(service)["p95"])][:1]
        for o in slow:
            if o in top:
                continue
            tid = "%032x" % rng.getrandbits(128)
            spans = self._span_chain(service, o, False, rng)
            out.append(Evidence(
                id=f"traces:{service}:{tid[:12]}",
                source="traces", service=o,
                title=f"Slow trace {tid[:12]}: {sum(1 for _ in spans)} spans, {spans[-1]['duration_ms']:.0f}ms in {o}",
                severity="warning",
                data={"kind": "trace_sample", "trace_id": tid, "spans": spans, "origin": o, "slow": True},
            ))
        return out

    # --------------------------------------------------------------------- logs
    def get_logs(self, service: str, window: Window) -> list[Evidence]:
        out: list[Evidence] = []
        b = self._base(service)
        f = self._fault(service)
        n = 0

        def add(level: str, pattern: str, count: int, first_seen: float, sample: str | None = None):
            nonlocal n
            n += 1
            sev = "critical" if level in ("ERROR", "FATAL") and count > 50 else ("warning" if level in ("ERROR", "FATAL", "WARN") else "info")
            out.append(Evidence(
                id=f"logs:{service}:{n}", source="logs", service=service,
                title=f"{count:,}× {level} {pattern[:90]}", severity=sev,
                data={"kind": "log_pattern", "pattern": pattern, "level": level, "count": count,
                      "sample": sample or pattern, "first_seen_min": first_seen},
            ))

        if f:
            mins = max(1.0, -f.onset_min)
            msg = self._message_for(service)
            if f.type in ("bad_deploy", "config_change", "resource_exhaustion", "datastore_degradation", "cpu_saturation"):
                cnt = max(3, int(b["rps"] * 60 * mins * max(f.error_rate, 0.01)))
                add("ERROR", msg, cnt, f.onset_min, sample=f"{msg} (trace_id=…)")
                if f.type == "datastore_degradation":
                    add("WARN", f"slow query: duration {int(f.latency_ms + 800)} ms", cnt * 3, f.onset_min)
                if f.type == "cpu_saturation":
                    add("WARN", "event loop lag > 250ms", int(mins * 40), f.onset_min)
            elif f.type == "memory_leak":
                ooms = len(self._oom_times(f))
                add("WARN", "heap usage above 90% after full GC", int(mins * 12), f.onset_min + 2)
                if ooms:
                    add("ERROR", msg, ooms, f.onset_min + float(f.extra.get("period_min", 6)))
            elif f.type == "crash_loop":
                add("FATAL", msg, int(mins / 1.5) + 1, f.onset_min)
            elif f.type == "queue_backlog":
                add("WARN", msg, int(mins * 20), f.onset_min)
                add("WARN", "consumer group rebalancing: member left group", int(mins / 4) + 1, f.onset_min + 3)
            elif f.type == "probe_failure":
                add("WARN", f.extra.get("log", "readiness check failed: warming cache"), int(mins * 6), f.onset_min)
            # dependency_unavailable / network_latency: the origin itself logs nothing unusual

        # errors propagated from callees
        for c in self.topo.callees(service):
            if not self._propagates(service, c):
                continue
            excess = max(0.0, self._recent_avg(self.caller_view_err, c) - BASE_ERR)
            if excess > 0.01:
                contrib = self._recent_contrib(self._err_contributions, c) or {c: excess}
                origin = max(contrib, key=contrib.get) if contrib else c
                cf = self._fault(c)
                if cf and cf.type in CALLER_VIEW_FAULTS:
                    origin = c
                onset = min((ff.onset_min for ff in self.faults.values()), default=-10)
                cnt = max(3, int(b["rps"] * 60 * (-onset) * self.topo.call_fraction(service, c) * excess * PROPAGATION))
                add("ERROR", f"failed calling {c}: {self._message_for(origin)}", cnt, onset)
            lat = self._recent_avg(self.caller_view_lat_added, c)
            if lat > 200 and excess <= 0.01:
                add("WARN", f"request to {c} took {int(lat + self._base(c)['p95'])}ms", int(b["rps"] * 30), -10)

        for level, pattern, cnt in BENIGN_LOGS.get(service, []):
            add(level, pattern, cnt, -44)
        for nz in self.noise:
            if nz.service == service and nz.type in ("log_warn", "log_error"):
                add("WARN" if nz.type == "log_warn" else "ERROR", nz.message, nz.count, nz.at_min)
        return out

    # --------------------------------------------------------------- k8s events
    def get_k8s_events(self, window: Window) -> list[Evidence]:
        out: list[Evidence] = []

        def pod(svc: str) -> str:
            r = self._rng("pod", svc)
            return f"{svc}-{r.getrandbits(32):08x}-{r.getrandbits(20):05x}"

        def event(svc: str, reason: str, message: str, count: int, first_seen: float, sev: str = "warning"):
            out.append(Evidence(
                id=f"k8s:{svc}:{reason}:{len(out)}", source="k8s_events", service=svc,
                title=f"{reason} ×{count} on pod/{pod(svc)}", detail=message, severity=sev,
                data={"kind": "k8s_event", "reason": reason, "object": f"pod/{pod(svc)}",
                      "count": count, "message": message, "first_seen_min": first_seen},
            ))

        def status(svc: str, restarts: int, last_reason: str | None, waiting: str | None, phase: str = "Running"):
            out.append(Evidence(
                id=f"k8s:{svc}:pod_status", source="k8s_events", service=svc,
                title=f"pod/{pod(svc)} restarts={restarts}" + (f", last terminated: {last_reason}" if last_reason else "")
                      + (f", waiting: {waiting}" if waiting else ""),
                severity="critical" if restarts >= 3 or waiting else ("warning" if restarts else "info"),
                data={"kind": "pod_status", "pod": f"pod/{pod(svc)}", "phase": phase, "restarts": restarts,
                      "last_termination_reason": last_reason, "waiting_reason": waiting},
            ))

        for f in self.faults.values():
            s = f.service
            if f.type == "memory_leak":
                ooms = self._oom_times(f)
                if ooms:
                    status(s, len(ooms), "OOMKilled", None)
                    event(s, "BackOff", "Back-off restarting failed container", len(ooms), ooms[0])
            elif f.type == "crash_loop":
                restarts = int(-f.onset_min / 1.5) + 1
                status(s, restarts, "Error (exit code 1)", "CrashLoopBackOff")
                event(s, "BackOff", "Back-off restarting failed container", restarts, f.onset_min + 1, "critical")
            elif f.type == "probe_failure":
                event(s, "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503",
                      int(-f.onset_min * 6), f.onset_min, "critical")
            elif f.type == "dependency_unavailable" and f.extra.get("scaled_to_zero"):
                event(s, "ScalingReplicaSet", f"Scaled down replica set {s}-7d9c to 0", 1, f.onset_min, "warning")
            elif f.type == "bad_deploy":
                event(s, "ScalingReplicaSet", f"Scaled up replica set {s}-{f.version_to or 'new'} to 1", 1, f.onset_min, "info")
                event(s, "Pulled", f"Successfully pulled image \"ghcr.io/open-telemetry/demo:{f.version_to}-{s}\"",
                      1, f.onset_min, "info")
            elif f.type == "cpu_saturation" and f.extra.get("throttling", True):
                event(s, "CPUThrottlingHigh", f"{int(78 + 10 * _ramp(0, f.onset_min))}% throttling of CPU in namespace otel-demo for container {s}",
                      1, f.onset_min + 3)

        for nz in self.noise:
            if nz.type == "restart":
                status(nz.service, nz.count, "Error (exit code 137)" if "137" in nz.message else "Completed", None)
            elif nz.type == "event":
                reason, _, msg = nz.message.partition(":")
                event(nz.service, reason.strip() or "Warning", msg.strip(), nz.count, nz.at_min)

        # benign background events that exist in every real cluster
        out.append(Evidence(
            id="k8s:frontend:hpa", source="k8s_events", service="frontend",
            title="SuccessfulRescale on hpa/frontend", detail="New size: 2; reason: cpu resource utilization above target",
            severity="info", data={"kind": "k8s_event", "reason": "SuccessfulRescale", "object": "hpa/frontend",
                                   "count": 1, "message": "New size: 2", "first_seen_min": -41}))
        return out

    # ------------------------------------------------------------------ changes
    def get_changes(self, window: Window) -> list[Evidence]:
        out: list[Evidence] = []

        def deploy(svc: str, v_from: str | None, v_to: str | None, at: float):
            out.append(Evidence(
                id=f"changes:{svc}:deploy:{len(out)}", source="changes", service=svc,
                title=f"deployment/{svc} rolled out {v_from or '?'} → {v_to or '?'} ({abs(at):.0f}m before alert)",
                severity="warning" if at > -30 else "info",
                data={"kind": "deploy", "resource": f"deployment/{svc}", "from_version": v_from,
                      "to_version": v_to, "minutes_before_alert": round(-at, 1)},
            ))

        def config(svc: str, resource: str, key: str | None, old: str | None, new: str | None, at: float):
            out.append(Evidence(
                id=f"changes:{svc}:config:{len(out)}", source="changes", service=svc,
                title=f"{resource}: {key} changed {old!r} → {new!r} ({abs(at):.0f}m before alert)",
                severity="warning" if at > -30 else "info",
                data={"kind": "config_change", "resource": resource, "key": key, "old": old, "new": new,
                      "minutes_before_alert": round(-at, 1), "affects": svc},
            ))

        for f in self.faults.values():
            if f.version_to:
                deploy(f.service, f.version_from, f.version_to, f.onset_min)
            if f.config_key:
                config(f.service, f.config_resource or f"deployment/{f.service}", f.config_key,
                       f.config_old, f.config_new, f.onset_min - 0.5)
            if f.type == "dependency_unavailable" and f.extra.get("scaled_to_zero"):
                out.append(Evidence(
                    id=f"changes:{f.service}:scale", source="changes", service=f.service,
                    title=f"deployment/{f.service} scaled 1 → 0 replicas ({abs(f.onset_min):.0f}m before alert)",
                    severity="warning",
                    data={"kind": "scale", "resource": f"deployment/{f.service}", "from_replicas": 1,
                          "to_replicas": 0, "minutes_before_alert": round(-f.onset_min, 1)}))
        for nz in self.noise:
            if nz.type == "deploy":
                deploy(nz.service, nz.version_from, nz.version_to, nz.at_min)
            elif nz.type == "config_change":
                config(nz.service, nz.message or f"configmap/{nz.service}-config", nz.config_key,
                       nz.config_old, nz.config_new, nz.at_min)
            elif nz.type == "scale":
                out.append(Evidence(
                    id=f"changes:{nz.service}:scale:{len(out)}", source="changes", service=nz.service,
                    title=f"deployment/{nz.service} scaled {nz.message or '2 → 3'} replicas ({abs(nz.at_min):.0f}m before alert)",
                    severity="info",
                    data={"kind": "scale", "resource": f"deployment/{nz.service}", "minutes_before_alert": -nz.at_min}))
        out.sort(key=lambda e: e.data.get("minutes_before_alert", 0))
        return out

    def get_topology(self) -> Topology:
        return self.topo

