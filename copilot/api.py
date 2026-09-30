"""HTTP API (FastAPI). Run with:  uvicorn copilot.api:app --reload --port 8000"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .evals.runner import run_eval
from .models import Alert, FeedbackIn, Investigation, InvestigationStatus, utcnow
from .service import CopilotService, NotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()

app = FastAPI(
    title="On-call Copilot",
    version="0.1.0",
    description="AI on-call copilot for Kubernetes: investigates alerts across metrics, traces, logs, "
                "Kubernetes events and recent changes, and proposes a root cause.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"], allow_headers=["*"],
)

_service: CopilotService | None = None


def svc() -> CopilotService:
    global _service
    if _service is None:
        _service = CopilotService(settings)
    return _service


# ----------------------------------------------------------------- schemas
class CreateInvestigationIn(BaseModel):
    alert: Alert | None = None
    scenario_id: str | None = None
    reasoner: Literal["claude", "heuristic"] | None = None


class EvalIn(BaseModel):
    reasoner: Literal["claude", "heuristic"] = "heuristic"
    scenario_ids: list[str] | None = None
    concurrency: int = Field(default=4, ge=1, le=8)


def summary_view(inv: Investigation) -> dict[str, Any]:
    h = inv.hypothesis
    return {
        "id": inv.id, "status": inv.status.value, "created_at": inv.created_at, "completed_at": inv.completed_at,
        "duration_ms": inv.duration_ms, "alert": inv.alert.model_dump(mode="json"), "scenario_id": inv.scenario_id,
        "reasoner": inv.reasoner, "telemetry_mode": inv.telemetry_mode,
        "hypothesis": None if h is None else {
            "root_cause_service": h.root_cause_service, "category": h.category.value, "title": h.title,
            "confidence": h.confidence},
        "feedback": inv.feedback.verdict.value if inv.feedback else None,
        "grading": inv.grading, "error": inv.error,
    }


# ------------------------------------------------------------------ routes
@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
def config() -> dict:
    return svc().runtime_info()


@app.get("/api/topology")
def topology() -> dict:
    from .topology import DEFAULT_TOPOLOGY
    return DEFAULT_TOPOLOGY.as_dict()


@app.post("/api/investigations", status_code=202)
def create_investigation(body: CreateInvestigationIn) -> dict:
    try:
        if body.scenario_id:
            inv = svc().investigate_scenario(body.scenario_id, reasoner=body.reasoner)
        elif body.alert:
            inv = svc().create_investigation(body.alert, reasoner=body.reasoner)
        else:
            raise HTTPException(422, "provide either `alert` or `scenario_id`")
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return summary_view(inv)


@app.get("/api/investigations")
def list_investigations(limit: int = Query(50, le=500), offset: int = 0, status: str | None = None,
                        service: str | None = None) -> dict:
    items = svc().store.list_investigations(limit=limit, offset=offset, status=status, service=service)
    return {"items": [summary_view(i) for i in items], "total": svc().store.count_investigations()}


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str) -> Investigation:
    try:
        return svc().get_investigation(inv_id)
    except NotFound as exc:
        raise HTTPException(404, "investigation not found") from exc


@app.get("/api/investigations/{inv_id}/events")
def list_events(inv_id: str, after: int = 0) -> list[dict]:
    return [e.model_dump(mode="json") for e in svc().store.list_events(inv_id, after)]


@app.get("/api/investigations/{inv_id}/stream")
async def stream_events(inv_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: one `step` event per agent step, then a final `done` event."""
    try:
        svc().get_investigation(inv_id)
    except NotFound as exc:
        raise HTTPException(404, "investigation not found") from exc
    last_id = int(request.headers.get("last-event-id") or 0)

    async def gen():
        nonlocal last_id
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            events = await asyncio.to_thread(svc().store.list_events, inv_id, last_id)
            for ev in events:
                last_id = ev.id or last_id
                yield f"id: {ev.id}\nevent: step\ndata: {ev.model_dump_json()}\n\n"
            inv = await asyncio.to_thread(svc().store.get_investigation, inv_id)
            if inv and inv.status in (InvestigationStatus.completed, InvestigationStatus.failed) and not events:
                yield f"event: done\ndata: {json.dumps({'status': inv.status.value})}\n\n"
                return
            idle = 0 if events else idle + 1
            if idle and idle % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/investigations/{inv_id}/feedback")
def feedback(inv_id: str, body: FeedbackIn) -> dict:
    try:
        fb = svc().submit_feedback(inv_id, body)
    except NotFound as exc:
        raise HTTPException(404, "investigation not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return fb.model_dump(mode="json")


@app.post("/api/webhooks/alertmanager", status_code=202)
def alertmanager_webhook(payload: dict) -> dict:
    """Accepts Alertmanager's webhook format; starts one investigation per firing alert."""
    started = []
    for a in payload.get("alerts", []):
        if a.get("status", "firing") != "firing":
            continue
        labels = {k: str(v) for k, v in (a.get("labels") or {}).items()}
        ann = a.get("annotations") or {}
        service_name = labels.get("service_name") or labels.get("service") or labels.get("job") or "unknown"
        starts = a.get("startsAt")
        alert = Alert(
            name=labels.get("alertname", "Alert"), service=service_name,
            severity=labels.get("severity", "critical") if labels.get("severity") in ("critical", "warning", "info") else "critical",
            summary=ann.get("summary") or ann.get("description") or "", labels=labels,
            starts_at=datetime.fromisoformat(starts.replace("Z", "+00:00")) if starts and not starts.startswith("0001") else utcnow(),
            fingerprint=a.get("fingerprint"),
        )
        started.append(svc().create_investigation(alert).id)
    return {"investigations": started}


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [s.public_view() for s in svc().scenarios.values()]


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, reveal: bool = False) -> dict:
    sc = svc().scenarios.get(scenario_id)
    if not sc:
        raise HTTPException(404, "scenario not found")
    out = sc.public_view()
    out["reproduce"] = sc.reproduce.model_dump()
    if reveal:
        out["ground_truth"] = sc.ground_truth.model_dump(mode="json")
    return out


@app.get("/api/knowledge")
def list_knowledge() -> list[dict]:
    return [d.as_dict() for d in svc().kb.list()]


@app.get("/api/knowledge/search")
def search_knowledge(q: str, k: int = 5) -> list[dict]:
    return [{**d.as_dict(), "score": s} for d, s in svc().kb.search(q, k=k)]


@app.get("/api/knowledge/{doc_id}")
def get_document(doc_id: str) -> dict:
    d = svc().kb.get(doc_id)
    if not d:
        raise HTTPException(404, "document not found")
    return d.as_dict(with_body=True)


@app.post("/api/evals", status_code=202)
def start_eval(body: EvalIn) -> dict:
    run_id = uuid.uuid4().hex[:10]
    threading.Thread(target=run_eval, kwargs=dict(service=svc(), reasoner=body.reasoner,
                     scenario_ids=body.scenario_ids, concurrency=body.concurrency, run_id=run_id), daemon=True).start()
    return {"id": run_id, "status": "running"}


@app.get("/api/evals")
def list_evals(limit: int = 20) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "results"} for r in svc().store.list_eval_runs(limit)]


@app.get("/api/evals/{run_id}")
def get_eval(run_id: str) -> dict:
    run = svc().store.get_eval_run(run_id)
    if not run:
        raise HTTPException(404, "eval run not found")
    return run


@app.get("/api/stats")
def stats() -> dict:
    return svc().stats()


# ---------------------------------------------------- serve the built frontend (optional)
# `cd web && npm run build` produces web/dist; the API then also serves the UI at http://localhost:8000
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402

_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

if _DIST.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = (_DIST / full_path).resolve()
        if full_path and f.is_file() and _DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
