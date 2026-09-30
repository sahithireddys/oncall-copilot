"""Parsers for the live backends, tested against recorded response shapes."""
from datetime import datetime, timezone

from copilot.agent.analysis import extract_findings
from copilot.providers.base import Window
from copilot.providers.jaeger import analyse_trace, summarise_traces
from copilot.providers.opensearch import cluster_logs, to_pattern
from copilot.providers.prometheus import parse_range_result
from copilot.topology import DEFAULT_TOPOLOGY

ALERT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
W = Window(ALERT)


def _span(sid, parent, pid, op, dur_ms, kind="server", error=False, tags=None):
    t = [{"key": "span.kind", "value": kind}]
    if error:
        t += [{"key": "error", "value": True}, {"key": "otel.status_description", "value": "boom"}]
    t += [{"key": k, "value": v} for k, v in (tags or {}).items()]
    return {"spanID": sid, "operationName": op, "duration": dur_ms * 1000, "processID": pid, "tags": t,
            "references": [{"refType": "CHILD_OF", "spanID": parent}] if parent else []}


def _trace(tid, spans):
    return {"traceID": tid, "spans": spans,
            "processes": {"p1": {"serviceName": "checkout"}, "p2": {"serviceName": "payment"}}}


def test_jaeger_error_origin_is_deepest_error_span():
    t = _trace("t1", [
        _span("a", None, "p1", "PlaceOrder", 300, error=True),
        _span("b", "a", "p1", "payment.Charge", 250, kind="client", error=True),
        _span("c", "b", "p2", "Charge", 240, error=True),
    ])
    a = analyse_trace(t)
    assert a["origin"] == "payment" and a["error"]
    assert [s["service"] for s in a["chain"]] == ["checkout", "checkout", "payment"]


def test_jaeger_client_span_without_child_blames_peer():
    t = _trace("t2", [
        _span("a", None, "p1", "PlaceOrder", 30, error=True),
        _span("b", "a", "p1", "payment.Charge", 5, kind="client", error=True, tags={"rpc.service": "payment"}),
    ])
    assert analyse_trace(t)["origin"] == "payment"


def test_jaeger_summary_feeds_analysis():
    traces = [_trace(f"t{i}", [_span("a", None, "p1", "PlaceOrder", 300, error=i < 5),
                               _span("c", "a", "p2", "Charge", 240, error=i < 5)]) for i in range(20)]
    ev = summarise_traces("checkout", traces, "http://jaeger")
    assert ev[0].data["error_origins"] == {"payment": 5}
    findings = extract_findings(ev, "checkout", DEFAULT_TOPOLOGY)
    assert any(f.kind == "trace_error_origin" and f.service == "payment" for f in findings)


def test_prometheus_range_parsing_skips_nan_and_converts_time():
    ts = ALERT.timestamp()
    payload = {"status": "success", "data": {"result": [{"values": [[ts - 600, "0.01"], [ts - 60, "NaN"], [ts, "0.4"]]}]}}
    assert parse_range_result(payload, W) == [[-10.0, 0.01], [0.0, 0.4]]


def test_log_patterns_normalise_ids_and_numbers():
    assert to_pattern("timeout after 5000ms for order 3fa85f64-5717-4562-b3fc-2c963f66afa6") == \
        "timeout after <n> for order <uuid>"
    hits = [{"_source": {"body": f"charge failed for card {i}", "severity": {"text": "ERROR"},
                         "@timestamp": "2026-09-01T11:55:00Z"}} for i in range(60)]
    ev = cluster_logs("payment", hits, W, "body", "severity.text", "@timestamp")
    assert len(ev) == 1 and ev[0].data["count"] == 60 and ev[0].data["first_seen_min"] == -5.0
