# On-call Copilot

An AI on-call copilot for Kubernetes. When an alert fires, a LangGraph agent investigates the way an SRE would. It
checks traces, metrics, logs, Kubernetes events and recent changes. It follows the failure downstream to where it
starts, searches past postmortems and runbooks, and returns a root-cause hypothesis with linked evidence and suggested
fixes, in Slack and over an API. Engineers mark each answer right or wrong, and confirmed incidents go back into the
knowledge base.

It is built for the [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) (a ~20-service shop
running on Kubernetes). It ships with a catalog of **26 incidents with known root causes**, including red-herring deploys,
so accuracy can be measured rather than guessed.

```
 Alertmanager ──webhook──►  FastAPI  ──►  LangGraph agent  ──►  Slack / UI (SSE stream)
                                            │
      triage ─► gather ─► analyze ──(new suspect?)──► gather      drill-down loop (≤2 rounds)
                              │
                              └─► retrieve ─► hypothesize ─► report
                                    │             │            │
                              BM25 over      Claude (structured   guardrails: only cite
                              postmortems,   output) or rule-     evidence that was
                              runbooks,      based baseline       actually collected
                              past incidents
```

| Source | Simulated mode | Live mode |
|---|---|---|
| Metrics (rate, errors, p95, CPU, memory, consumer lag) | scenario simulator | Prometheus (spanmetrics + cAdvisor/KSM) |
| Traces (error origin, latency attribution) | scenario simulator | Jaeger query API |
| Logs (clustered into patterns) | scenario simulator | OpenSearch |
| K8s events + pod health (OOMKilled, CrashLoopBackOff, probes) | scenario simulator | Kubernetes API |
| Changes (rollouts, env/limit diffs, ConfigMaps, scaling) | scenario simulator | ReplicaSet revision diffs |

## Quick start (no cluster needed)

```bash
cd oncall-copilot
uv venv --python 3.12 && source .venv/bin/activate      # or: python3.12 -m venv .venv
uv pip install -r requirements.txt                        # or: pip install -r requirements.txt
cp .env.example .env                                      # add ANTHROPIC_API_KEY
pytest -q                                                 # 31 tests
uvicorn copilot.api:app --reload --port 8000              # API docs at http://localhost:8000/docs
```

Trigger an incident and watch the agent work:

```bash
curl -X POST localhost:8000/api/investigations -H 'content-type: application/json' \
     -d '{"scenario_id": "checkout-memory-leak-payment-noise"}'
curl -N localhost:8000/api/investigations/<id>/stream       # live agent steps (SSE)
curl localhost:8000/api/investigations/<id>                  # hypothesis + evidence + grading
curl -X POST localhost:8000/api/investigations/<id>/feedback -H 'content-type: application/json' \
     -d '{"verdict": "correct"}'
```

## Web UI

```bash
cd web && npm install && npm run dev      # http://localhost:5173, with the API running on :8000
```

Incidents list, a live view of the agent's steps (Server-Sent Events), the root cause with copyable kubectl
commands, evidence cards with charts, one-click feedback, a scenario launcher and the eval scorecard.
See [web/README.md](web/README.md).

## Evaluation

```bash
python scripts/run_eval.py --reasoner heuristic   # offline rule-based baseline
python scripts/run_eval.py --reasoner both        # baseline vs Claude, side by side
```

The scorecard reports accuracy (service and mechanism both right), service accuracy, category accuracy,
recall@3, time to hypothesis, accuracy by difficulty and category, and calibration (are confident answers more
often right?). Every run is saved in `reports/` and `/api/evals`.

**Read the simulated numbers with care.** The simulator and the rule-based baseline were written together, so the
baseline scores very high on simulated incidents (96% at the time of writing). That number shows the pipeline works.
It does not show the agent is good. The numbers worth putting on a resume come from **live** runs:
`chaos/inject.py` breaks the real cluster, the agent investigates real telemetry, and the result is graded against the
same ground truth. See [deploy/CLUSTER_SETUP.md](deploy/CLUSTER_SETUP.md).

## The incident catalog

`data/scenarios/catalog.yaml`: 26 scenarios across 11 root-cause categories (bad deploy, config/flag change, memory
leak, crash loop, CPU saturation, network latency, dependency unavailable, datastore degradation, pool exhaustion,
queue backlog, probe failure). Each one has:

- `faults`: what breaks, which the simulator turns into telemetry that propagates upstream through the call graph
- `noise`: red herrings (an unrelated deploy 6 minutes before the alert, benign error logs, old restarts), plus seeded
  random background noise
- `ground_truth`: the service, the category (and acceptable alternates) and the fix
- `reproduce`: how to cause the same incident on a real cluster (flagd flag, kubectl commands or a Chaos Mesh manifest)

## Project layout

```
copilot/
  api.py              FastAPI routes: investigations, SSE stream, feedback, Alertmanager webhook, evals, stats
  service.py          orchestration, persistence, feedback -> knowledge base, stats
  agent/graph.py      LangGraph state machine (triage → gather ⇄ analyze → retrieve → hypothesize → report)
  agent/analysis.py   deterministic signal extraction (trace origins, OOMs, recent changes, pool exhaustion…)
  agent/llm.py        Claude reasoner via langchain-anthropic structured output
  agent/prompts.py    prompt construction (unit-tested)
  agent/heuristic.py  rule-based baseline + suggested kubectl actions
  providers/          simulated + live (Prometheus, Jaeger, OpenSearch, Kubernetes) telemetry
  knowledge/          dependency-free BM25 retrieval over postmortems / runbooks / confirmed incidents
  evals/              grading + eval runner
  notify/slack.py     Block Kit message with evidence and feedback buttons
  mcp_server.py       the same tools exposed over MCP (Claude Desktop / Claude Code)
data/scenarios/       incident catalog
data/knowledge/       postmortems + runbooks
chaos/                fault injection CLI, Chaos Mesh manifests, Prometheus/Alertmanager config
deploy/               in-cluster deployment with read-only RBAC, cluster setup guide
tests/                pytest suite
```

## API

| Method | Path | |
|---|---|---|
| POST | `/api/investigations` | `{"scenario_id": ...}` or `{"alert": {...}}`, optional `"reasoner"` |
| GET | `/api/investigations` | list (summaries) |
| GET | `/api/investigations/{id}` | full result: hypothesis, evidence, findings, documents, grading |
| GET | `/api/investigations/{id}/stream` | Server-Sent Events of agent steps |
| POST | `/api/investigations/{id}/feedback` | `{"verdict": "correct" \| "partially_correct" \| "incorrect", "actual_root_cause_service"?, "actual_category"?, "notes"?}` |
| POST | `/api/webhooks/alertmanager` | Alertmanager webhook receiver |
| GET | `/api/scenarios`, `/api/scenarios/{id}?reveal=true` | incident catalog |
| GET | `/api/knowledge`, `/api/knowledge/search?q=` | knowledge base |
| POST/GET | `/api/evals`, `/api/evals/{id}` | run / read eval scorecards |
| GET | `/api/stats`, `/api/config`, `/api/topology` | dashboard data |

## Design notes

- **Deterministic evidence gathering, LLM for judgment.** Tool calls are planned by the graph, not by the model, so an
  investigation always costs one LLM call (about 10 s, a few cents) and can be replayed. The model gets pre-computed
  findings as *hints to verify*, the raw evidence and matching postmortems.
- **Drill-down loop.** Round one looks at the alerting service and cluster-wide events and changes. The analyze step
  ranks suspects (for example, "94% of failing traces end in payment") and loops back to collect metrics and logs for them.
- **Guardrails.** The report node drops evidence ids the model cites but the agent never collected, and caps confidence
  when the named service isn't in the call graph. If Claude fails or times out, the heuristic answer is used and noted.
- **Learning from feedback.** A confirmed or corrected investigation becomes an `incident` document in the retrieval index
  (persisted, reloaded on start-up), so the next similar alert retrieves it.
