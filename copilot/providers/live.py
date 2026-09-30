"""Live provider: Prometheus + Jaeger + OpenSearch + Kubernetes API behind one interface."""
from __future__ import annotations

import logging
import time

from ..config import Settings
from ..models import Evidence
from ..topology import DEFAULT_TOPOLOGY, Topology
from .base import Window
from .jaeger import JaegerTraces
from .opensearch import OpenSearchLogs
from .prometheus import PrometheusMetrics

log = logging.getLogger(__name__)


class LiveProvider:
    name = "live"

    def __init__(self, settings: Settings):
        self.metrics = PrometheusMetrics(settings.prometheus_url, settings.k8s_namespace, grafana_url=settings.grafana_url)
        self.traces = JaegerTraces(settings.jaeger_url)
        self.logs = OpenSearchLogs(settings.opensearch_url, settings.opensearch_index)
        self._k8s = None
        self._ns = settings.k8s_namespace
        self._topology: Topology | None = None
        self._topology_at = 0.0

    @property
    def k8s(self):
        if self._k8s is None:
            from .kubernetes import KubernetesCluster
            self._k8s = KubernetesCluster(self._ns)
        return self._k8s

    def get_metrics(self, service: str, window: Window) -> list[Evidence]:
        return self.metrics.get_metrics(service, window)

    def get_traces(self, service: str, window: Window) -> list[Evidence]:
        return self.traces.get_traces(service, window)

    def get_logs(self, service: str, window: Window) -> list[Evidence]:
        return self.logs.get_logs(service, window)

    def get_k8s_events(self, window: Window) -> list[Evidence]:
        return self.k8s.get_k8s_events(window)

    def get_changes(self, window: Window) -> list[Evidence]:
        return self.k8s.get_changes(window)

    def get_topology(self) -> Topology:
        # refresh the call graph from Jaeger at most every 10 minutes; fall back to the known demo graph
        if self._topology is None or time.time() - self._topology_at > 600:
            try:
                from datetime import datetime, timezone
                topo = self.traces.get_topology(Window(datetime.now(timezone.utc)))
                if topo:
                    merged = {s: sorted(set(DEFAULT_TOPOLOGY.callees(s)) | set(topo.callees(s)))
                              for s in set(DEFAULT_TOPOLOGY.services) | set(topo.services)}
                    self._topology = Topology(merged)
            except Exception as exc:
                log.info("could not load dependencies from jaeger: %s", exc)
            self._topology = self._topology or DEFAULT_TOPOLOGY
            self._topology_at = time.time()
        return self._topology
