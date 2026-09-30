"""Telemetry provider interface.

Every provider (simulated or live) returns `Evidence` objects whose `data`
payload follows one of the shapes below, keyed by `data["kind"]`. The analysis
step only depends on these shapes, so swapping simulated -> live telemetry does
not touch the agent.

metrics     kind=metric_series   {metric, unit, series: [[t_min, value], ...], baseline, current, peak}
traces      kind=trace_summary   {total, errors, error_origins: {svc: n}, slow_origins: {svc: n}, p95_ms}
traces      kind=trace_sample    {trace_id, spans: [{service, operation, duration_ms, error, message, depth}], origin}
logs        kind=log_pattern     {pattern, level, count, sample, first_seen_min}
k8s_events  kind=k8s_event       {reason, object, count, message, first_seen_min}
k8s_events  kind=pod_status      {pod, phase, restarts, last_termination_reason, waiting_reason}
changes     kind=deploy          {resource, from_version, to_version, minutes_before_alert}
changes     kind=config_change   {resource, key, old, new, minutes_before_alert}
changes     kind=scale           {resource, from_replicas, to_replicas, minutes_before_alert}

Time is expressed as minutes relative to the alert (t <= 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ..models import Evidence
from ..topology import Topology

METRICS = ["request_rate", "error_rate", "p95_latency_ms", "cpu_utilization", "memory_utilization"]


@dataclass
class Window:
    alert_time: datetime
    lookback_minutes: int = 45

    @property
    def start(self) -> datetime:
        return self.alert_time - timedelta(minutes=self.lookback_minutes)

    @property
    def end(self) -> datetime:
        return self.alert_time

    def rel_minutes(self, ts: datetime) -> float:
        return round((ts - self.alert_time).total_seconds() / 60.0, 1)


class TelemetryProvider(Protocol):
    name: str

    def get_metrics(self, service: str, window: Window) -> list[Evidence]: ...

    def get_traces(self, service: str, window: Window) -> list[Evidence]: ...

    def get_logs(self, service: str, window: Window) -> list[Evidence]: ...

    def get_k8s_events(self, window: Window) -> list[Evidence]: ...

    def get_changes(self, window: Window) -> list[Evidence]: ...

    def get_topology(self) -> Topology: ...


def summarize_series(series: list[list[float]]) -> tuple[float, float, float]:
    """Return (baseline, current, peak) for a [[t, v], ...] series.

    baseline = median of the first 10 minutes of the window, current = mean of the last 5.
    """
    if not series:
        return 0.0, 0.0, 0.0
    t0 = series[0][0]
    before = [v for t, v in series if t <= t0 + 10] or [series[0][1]]
    recent = [v for t, v in series if t >= -5] or [series[-1][1]]
    baseline = sorted(before)[len(before) // 2]
    current = sum(recent) / len(recent)
    peak = max(v for _, v in series)
    return round(baseline, 4), round(current, 4), round(peak, 4)


# ---------------------------------------------------------------------------
# Evidence builders shared by simulated and live providers
# ---------------------------------------------------------------------------

METRIC_UNITS = {
    "request_rate": "req/s",
    "error_rate": "ratio",
    "p95_latency_ms": "ms",
    "cpu_utilization": "ratio",
    "memory_utilization": "ratio",
    "consumer_lag": "messages",
}


def _fmt(metric: str, v: float) -> str:
    unit = METRIC_UNITS.get(metric, "")
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit == "ms":
        return f"{v:.0f}ms"
    if unit == "req/s":
        return f"{v:.1f} req/s"
    return f"{v:,.0f}"


def metric_evidence(service: str, metric: str, series: list[list[float]], link: str | None = None) -> Evidence:
    baseline, current, peak = summarize_series(series)
    unit = METRIC_UNITS.get(metric, "")
    severity = "info"
    if metric == "error_rate":
        if current >= 0.05 and current > baseline * 3:
            severity = "critical"
        elif current >= 0.02 and current > baseline * 2:
            severity = "warning"
    elif metric in ("p95_latency_ms", "consumer_lag"):
        if baseline > 0 and current > baseline * 3:
            severity = "critical"
        elif baseline > 0 and current > baseline * 1.6:
            severity = "warning"
    elif metric in ("cpu_utilization", "memory_utilization"):
        if peak >= 0.95:
            severity = "critical"
        elif current >= 0.85:
            severity = "warning"
    elif metric == "request_rate":
        if baseline > 0 and current < baseline * 0.2:
            severity = "critical"
        elif baseline > 0 and current < baseline * 0.6:
            severity = "warning"
    label = metric.replace("_", " ")
    title = f"{service} {label}: {_fmt(metric, baseline)} → {_fmt(metric, current)}"
    if metric in ("memory_utilization", "cpu_utilization"):
        title += f" (peak {_fmt(metric, peak)})"
    return Evidence(
        id=f"metrics:{service}:{metric}",
        source="metrics",
        service=service,
        title=title,
        severity=severity,
        link=link,
        data={"kind": "metric_series", "metric": metric, "unit": unit, "series": series,
              "baseline": baseline, "current": current, "peak": peak},
    )
