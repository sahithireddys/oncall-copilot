# Evaluation results (simulated incidents, 26 scenarios)

| Reasoner | Run (UTC) | Correct (service + type) | Right service | Right type | Hard (n=5) | Median time |
|---|---|---|---|---|---|---|
| Claude (claude-sonnet-4-5) run 1 | 2026-09-30 02:23 | 92% | 96% | 92% | 100% | 29.8s |
| Claude (claude-sonnet-4-5) run 2 | 2026-09-30 02:35 | 92% | 92% | 96% | 100% | 31.1s |
| Claude (claude-sonnet-4-5) run 3 | 2026-09-30 02:43 | 92% | 100% | 92% | 60% | 30.5s |
| Claude (claude-sonnet-4-5) run 4 - final, frozen prompt | 2026-09-30 02:49 | 96% | 96% | 96% | 100% | 29.9s |
| Rule-based baseline | 2026-09-30 02:43 | 96% | 100% | 96% | 80% | 0.0s |

Prompt changes between Claude runs: evidence-coverage drill-down (after run 1), service-naming and
category-precedence rules (after runs 2-3). The prompt was frozen before the final run.

## Final Claude run: misses

- `quote-network-latency`: predicted `shipping/resource_exhaustion`, expected `quote/network_latency`

Caveats: incidents are simulated, the simulator and baseline were written by the same author, and the prompt
was tuned on these 26 scenarios. Held-out and live-cluster results should be reported separately.

Raw data: `eval-runs/` (scorecards) and `claude-investigations/` (full evidence + Claude reasoning for the final run).
