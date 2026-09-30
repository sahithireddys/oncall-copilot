"""Prometheus metrics for the OpenTelemetry Demo.

RED metrics come from the collector's spanmetrics connector (always present in the
demo). CPU / memory need cAdvisor + kube-state-metrics (e.g. kube-prometheus-stack);
if those series are missing the evidence is simply skipped.
"""
from __future__ import annotations

import logging

import httpx

from ..models import Evidence
from .base import Window, metric_evidence

log = logging.getLogger(__name__)

SERVER = 'span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"'

DEFAULT_QUERIES: dict[str, str] = {
    "request_rate": 'sum(rate(traces_span_metrics_calls_total{service_name="$svc",' + SERVER + '}[2m]))',
    "error_rate": (
        'sum(rate(traces_span_metrics_calls_total{service_name="$svc",' + SERVER + ',status_code="STATUS_CODE_ERROR"}[2m]))'
        ' / clamp_min(sum(rate(traces_span_metrics_calls_total{service_name="$svc",' + SERVER + '}[2m])), 1e-9)'
    ),
    "p95_latency_ms": (
        'histogram_quantile(0.95, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket'
        '{service_name="$svc",' + SERVER + '}[2m])))'
    ),
    "cpu_utilization": (
        'sum(rate(container_cpu_usage_seconds_total{namespace="$ns",container="$svc"}[2m]))'
        ' / sum(kube_pod_container_resource_limits{namespace="$ns",container="$svc",resource="cpu"})'
    ),
    "memory_utilization": (
        'sum(container_memory_working_set_bytes{namespace="$ns",container="$svc"})'
        ' / sum(kube_pod_container_resource_limits{namespace="$ns",container="$svc",resource="memory"})'
    ),
    "consumer_lag": 'sum(kafka_consumergroup_lag{consumergroup="$svc"})',
}


def parse_range_result(payload: dict, window: Window) -> list[list[float]]:
    """Convert a Prometheus query_range response to [[t_min_rel_to_alert, value], ...]."""
    if payload.get("status") != "success":
        return []
    results = payload.get("data", {}).get("result", [])
    if not results:
        return []
    alert_ts = window.alert_time.timestamp()
    out = []
    for ts, val in results[0].get("values", []):
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if v != v or v in (float("inf"), float("-inf")):  # NaN / inf from empty histograms
            continue
        out.append([round((float(ts) - alert_ts) / 60.0, 1), round(v, 5)])
    return out


class PrometheusMetrics:
    def __init__(self, base_url: str, namespace: str, queries: dict[str, str] | None = None,
                 grafana_url: str | None = None, client: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        self.ns = namespace
        self.queries = {**DEFAULT_QUERIES, **(queries or {})}
        self.grafana_url = grafana_url
        self.http = client or httpx.Client(timeout=15)

    def _query_range(self, promql: str, window: Window) -> dict:
        r = self.http.get(f"{self.base}/api/v1/query_range", params={
            "query": promql, "start": window.start.timestamp(), "end": window.end.timestamp(), "step": 60})
        r.raise_for_status()
        return r.json()

    def _link(self, promql: str) -> str | None:
        if not self.grafana_url:
            return None
        from urllib.parse import quote
        return (f"{self.grafana_url.rstrip('/')}/explore?left="
                + quote('{"datasource":"webstore-metrics","queries":[{"expr":' + _json_str(promql) + '}],"range":{"from":"now-1h","to":"now"}}'))

    def get_metrics(self, service: str, window: Window) -> list[Evidence]:
        out = []
        for metric, tmpl in self.queries.items():
            if metric == "consumer_lag" and service not in ("accounting", "fraud-detection"):
                continue
            q = tmpl.replace("$svc", service).replace("$ns", self.ns)
            try:
                series = parse_range_result(self._query_range(q, window), window)
            except Exception as exc:
                log.debug("prometheus query failed for %s/%s: %s", service, metric, exc)
                continue
            if series:
                out.append(metric_evidence(service, metric, series, link=self._link(q)))
        return out


def _json_str(s: str) -> str:
    import json
    return json.dumps(s)
