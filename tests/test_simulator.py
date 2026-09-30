from datetime import datetime, timezone

from copilot.providers.base import Window
from copilot.providers.simulated import SimulatedProvider

from .helpers import CATALOG

W = Window(datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))


def _metric(ev, name):
    return next(e for e in ev if e.data["metric"] == name)


def test_catalog_is_valid_and_large_enough():
    assert len(CATALOG) >= 24
    for sc in CATALOG.values():
        assert sc.faults, sc.id
        assert sc.ground_truth.service in {f.service for f in sc.faults}, sc.id


def test_simulation_is_deterministic():
    sc = CATALOG["payment-bad-deploy"]
    a = SimulatedProvider(sc).get_traces("checkout", W)
    b = SimulatedProvider(sc).get_traces("checkout", W)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]


def test_errors_propagate_upstream_and_traces_point_at_origin():
    p = SimulatedProvider(CATALOG["payment-bad-deploy"])
    checkout_err = _metric(p.get_metrics("checkout", W), "error_rate")
    assert checkout_err.data["current"] > 0.2 and checkout_err.severity == "critical"
    summary = p.get_traces("checkout", W)[0]
    assert next(iter(summary.data["error_origins"])) == "payment"


def test_unavailable_service_shows_traffic_drop_not_errors():
    p = SimulatedProvider(CATALOG["payment-scaled-to-zero"])
    ev = p.get_metrics("payment", W)
    assert _metric(ev, "request_rate").severity == "critical"
    assert _metric(ev, "error_rate").data["current"] < 0.01
    sample = [e for e in p.get_traces("checkout", W) if e.data["kind"] == "trace_sample"][0]
    assert sample.data["spans"][-1]["kind"] == "client"
    assert sample.data["spans"][-1]["peer_service"] == "payment"


def test_memory_leak_produces_sawtooth_and_oom_events():
    p = SimulatedProvider(CATALOG["recommendation-memory-leak"])
    mem = _metric(p.get_metrics("recommendation", W), "memory_utilization")
    assert mem.data["peak"] > 0.95
    statuses = [e for e in p.get_k8s_events(W) if e.data["kind"] == "pod_status" and e.service == "recommendation"]
    assert statuses and statuses[0].data["last_termination_reason"] == "OOMKilled"


def test_async_kafka_edge_does_not_propagate_errors_to_checkout():
    p = SimulatedProvider(CATALOG["fraud-detection-crashloop"])
    assert _metric(p.get_metrics("checkout", W), "error_rate").data["current"] < 0.02
    assert _metric(p.get_metrics("fraud-detection", W), "consumer_lag").severity == "critical"


def test_red_herring_changes_are_present():
    p = SimulatedProvider(CATALOG["checkout-memory-leak-payment-noise"])
    changed = {e.service for e in p.get_changes(W)}
    assert "payment" in changed
