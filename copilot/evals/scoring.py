"""Scoring a hypothesis against a scenario's ground truth."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from ..models import Hypothesis
from ..providers.scenarios import Scenario


def grade(h: Hypothesis | None, sc: Scenario) -> dict[str, Any]:
    gt = sc.ground_truth
    if h is None:
        return {"service_correct": False, "category_correct": False, "correct": False, "score": 0.0,
                "expected_service": gt.service, "expected_category": gt.category.value}
    service_ok = h.root_cause_service == gt.service
    accepted = {gt.category, *gt.also_accept}
    category_ok = h.category in accepted
    # in top-3 (primary + alternatives) counts for "recall@3"
    candidates = [h.root_cause_service] + [a.root_cause_service for a in h.alternatives]
    return {
        "service_correct": service_ok,
        "category_correct": category_ok,
        "correct": service_ok and category_ok,
        "service_in_top3": gt.service in candidates[:3],
        # partial credit: right service, wrong mechanism = 0.5
        "score": 1.0 if service_ok and category_ok else (0.5 if service_ok else 0.0),
        "expected_service": gt.service,
        "expected_category": gt.category.value,
        "predicted_service": h.root_cause_service,
        "predicted_category": h.category.value,
        "confidence": h.confidence,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    if not n:
        return {"n": 0}
    done = [r for r in results if r.get("grading")]

    def rate(key: str, rows: list[dict]) -> float:
        return round(sum(1 for r in rows if r["grading"].get(key)) / len(rows), 3) if rows else 0.0

    by_diff: dict[str, list] = defaultdict(list)
    by_cat: dict[str, list] = defaultdict(list)
    for r in done:
        by_diff[r["difficulty"]].append(r)
        by_cat[r["grading"]["expected_category"]].append(r)
    durations = [r["duration_ms"] for r in done if r.get("duration_ms") is not None]

    # calibration: accuracy of confident (>=0.7) vs unsure answers
    confident = [r for r in done if r["grading"].get("confidence", 0) >= 0.7]
    unsure = [r for r in done if r["grading"].get("confidence", 0) < 0.7]
    return {
        "n": n,
        "completed": len(done),
        "accuracy": rate("correct", done),
        "service_accuracy": rate("service_correct", done),
        "category_accuracy": rate("category_correct", done),
        "service_recall_at_3": rate("service_in_top3", done),
        "mean_score": round(sum(r["grading"]["score"] for r in done) / len(done), 3) if done else 0.0,
        "median_time_to_hypothesis_ms": int(median(durations)) if durations else None,
        "p95_time_to_hypothesis_ms": int(sorted(durations)[int(0.95 * (len(durations) - 1))]) if durations else None,
        "by_difficulty": {k: {"n": len(v), "accuracy": rate("correct", v)} for k, v in sorted(by_diff.items())},
        "by_category": {k: {"n": len(v), "accuracy": rate("correct", v)} for k, v in sorted(by_cat.items())},
        "calibration": {
            "confident_n": len(confident), "confident_accuracy": rate("correct", confident),
            "unsure_n": len(unsure), "unsure_accuracy": rate("correct", unsure),
        },
    }
