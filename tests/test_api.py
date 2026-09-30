import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("langgraph")


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from copilot import api
    from copilot.service import CopilotService

    from .helpers import build_settings
    monkeypatch.setattr(api, "_service", CopilotService(build_settings(tmp_path)))
    return TestClient(api.app)


def _wait(c, inv_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/api/investigations/{inv_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("investigation did not finish")


def test_scenario_flow_with_feedback(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/health").json() == {"ok": True}
    assert len(c.get("/api/scenarios").json()) >= 24
    r = c.post("/api/investigations", json={"scenario_id": "payment-bad-deploy"})
    assert r.status_code == 202
    inv = _wait(c, r.json()["id"])
    assert inv["hypothesis"]["root_cause_service"] == "payment"
    events = c.get(f"/api/investigations/{inv['id']}/events").json()
    assert events[0]["node"] == "queued"
    with c.stream("GET", f"/api/investigations/{inv['id']}/stream") as s:
        body = "".join(s.iter_text())
    assert "event: step" in body and "event: done" in body
    fb = c.post(f"/api/investigations/{inv['id']}/feedback", json={"verdict": "correct"})
    assert fb.status_code == 200
    assert c.get("/api/stats").json()["feedback"]["count"] == 1


def test_alertmanager_webhook(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    payload = {"alerts": [{"status": "firing", "labels": {"alertname": "CheckoutHighErrorRate", "service_name": "checkout",
                                                          "severity": "critical"},
                           "annotations": {"summary": "checkout errors"}, "startsAt": "2026-09-01T12:00:00Z"}]}
    r = c.post("/api/webhooks/alertmanager", json=payload)
    assert r.status_code == 202 and len(r.json()["investigations"]) == 1
    inv = _wait(c, r.json()["investigations"][0])
    assert inv["status"] == "completed"  # healthy simulated cluster -> low-confidence answer, but no crash


def test_unknown_scenario_is_404(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/investigations", json={"scenario_id": "nope"}).status_code == 404
