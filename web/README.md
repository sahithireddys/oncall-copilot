# On-call Copilot: web UI

React + TypeScript + Vite. No UI or chart libraries: charts are hand-written SVG.

```bash
# terminal 1: backend (from the repo root)
uvicorn copilot.api:app --reload --port 8000

# terminal 2: frontend
cd web
npm install
npm run dev            # http://localhost:5173 (proxies /api to :8000)
```

`npm run build` writes `web/dist`; the FastAPI app then serves the UI itself at http://localhost:8000.
`npm run typecheck` runs the TypeScript compiler.

Pages: **Incidents** (all investigations + KPIs) · **Investigation** (live agent steps over SSE, root cause,
suggested kubectl commands, evidence with charts, feedback) · **Scenarios** (trigger an injected incident) ·
**Evaluation** (Claude vs rule-based scorecard) · **Knowledge** (postmortems, runbooks, learned incidents).

Tip: keep `REASONER=heuristic` in `.env` while you click around. It costs nothing. Pick "Claude" per run when you want it.
