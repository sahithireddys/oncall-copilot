"""Run the incident catalog through the agent and print / save a scorecard.

    python scripts/run_eval.py                      # heuristic baseline, all scenarios
    python scripts/run_eval.py --reasoner claude    # needs ANTHROPIC_API_KEY
    python scripts/run_eval.py --reasoner both      # side-by-side comparison
    python scripts/run_eval.py --only payment-bad-deploy cart-pool-exhaustion
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from copilot.config import override_settings  # noqa: E402
from copilot.evals.runner import run_eval  # noqa: E402
from copilot.service import CopilotService  # noqa: E402


def print_run(run: dict) -> None:
    s = run["summary"]
    print(f"\n=== reasoner: {run['reasoner']}{' (' + run['model'] + ')' if run.get('model') else ''} ===")
    for r in sorted(run["results"], key=lambda r: r["scenario_id"]):
        g = r.get("grading") or {}
        mark = "PASS" if g.get("correct") else ("SVC " if g.get("service_correct") else "FAIL")
        print(f"  {mark}  {r['scenario_id']:<38} predicted {r['predicted'] or '-':<40} expected {r['expected']:<40} "
              f"{(r['duration_ms'] or 0) / 1000:5.1f}s")
    print(f"\n  accuracy {s['accuracy']:.0%}   service {s['service_accuracy']:.0%}   category {s['category_accuracy']:.0%}"
          f"   recall@3 {s['service_recall_at_3']:.0%}   median time {s['median_time_to_hypothesis_ms'] / 1000:.1f}s")
    print("  by difficulty: " + ", ".join(f"{k} {v['accuracy']:.0%} (n={v['n']})" for k, v in s["by_difficulty"].items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reasoner", choices=["heuristic", "claude", "both"], default="heuristic")
    ap.add_argument("--only", nargs="*", help="scenario ids")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--db", default=None, help="sqlite path (default: data/copilot.db)")
    args = ap.parse_args()
    if args.db:
        override_settings(database_path=args.db)
    service = CopilotService()
    reasoners = ["heuristic", "claude"] if args.reasoner == "both" else [args.reasoner]
    runs = []
    for r in reasoners:
        if r == "claude" and service.resolve_reasoner("claude") != "claude":
            print("ANTHROPIC_API_KEY not set - skipping the claude run", file=sys.stderr)
            continue
        run = run_eval(service, reasoner=r, scenario_ids=args.only, concurrency=args.concurrency,
                       on_progress=lambda res: print(".", end="", flush=True))
        print_run(run)
        runs.append(run)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(runs, indent=2, default=str))
    print(f"\nsaved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
