"""Run the scenario catalog through the agent and score it against ground truth."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from .scoring import summarize

if TYPE_CHECKING:
    from ..service import CopilotService


def run_eval(service: "CopilotService", reasoner: str = "heuristic", scenario_ids: list[str] | None = None,
             concurrency: int = 4, run_id: str | None = None,
             on_progress: Callable[[dict], None] | None = None) -> dict:
    ids = scenario_ids or list(service.scenarios)
    effective = service.resolve_reasoner(reasoner)
    run = {
        "id": run_id or uuid.uuid4().hex[:10],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "reasoner": effective,
        "requested_reasoner": reasoner,
        "model": service.settings.anthropic_model if effective == "claude" else None,
        "scenario_ids": ids,
        "results": [],
        "summary": {},
    }
    service.store.save_eval_run(run)

    def one(sid: str) -> dict:
        sc = service.scenarios[sid]
        inv = service.investigate_scenario(sid, reasoner=effective, background=False)
        h = inv.hypothesis
        return {
            "scenario_id": sid, "title": sc.title, "difficulty": sc.difficulty, "investigation_id": inv.id,
            "status": inv.status.value, "duration_ms": inv.duration_ms, "grading": inv.grading,
            "predicted": f"{h.root_cause_service}/{h.category.value}" if h else None,
            "expected": f"{sc.ground_truth.service}/{sc.ground_truth.category.value}",
            "reasoner_used": inv.reasoner, "error": inv.error,
        }

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for res in pool.map(one, ids):
            run["results"].append(res)
            run["summary"] = summarize(run["results"])
            service.store.save_eval_run(run)
            if on_progress:
                on_progress(res)
    run["status"] = "completed"
    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    run["summary"] = summarize(run["results"])
    service.store.save_eval_run(run)
    return run
