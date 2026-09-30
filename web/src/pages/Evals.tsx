import { useMemo, useState } from "react";
import { api, type EvalRun, type Reasoner, type RuntimeConfig } from "../api";
import { GroupedBars } from "../components/charts";
import { ErrorBox, Icon, Tile } from "../components/ui";
import { duration, linkProps, pct, timeAgo, useAsync } from "../lib";

const CLAUDE = { name: "Claude", color: "var(--series-1)" };
const RULES = { name: "Rule-based", color: "var(--series-2)" };

export default function Evals({ config }: { config: RuntimeConfig | null }) {
  const runs = useAsync(() => api.evals(), [], 4000);
  const list = runs.data ?? [];
  const latest = (r: Reasoner) => list.find((x) => x.reasoner === r && x.status === "completed");
  const claudeId = latest("claude")?.id;
  const rulesId = latest("heuristic")?.id;
  const claude = useAsync(() => (claudeId ? api.evalRun(claudeId) : Promise.resolve(null)), [claudeId]);
  const rules = useAsync(() => (rulesId ? api.evalRun(rulesId) : Promise.resolve(null)), [rulesId]);
  const running = list.find((r) => r.status === "running");
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const start = async (r: Reasoner) => {
    if (r === "claude" && !window.confirm(`Run all ${config?.scenarios ?? 26} scenarios with Claude? This uses the Anthropic API (about $1).`)) return;
    setStarting(true);
    setErr(null);
    try {
      await api.startEval(r);
      runs.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const c = claude.data?.summary, h = rules.data?.summary;
  const diffs = ["easy", "medium", "hard"];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Evaluation</h1>
          <p>Every scenario has a known root cause, so accuracy is measured, not guessed. Latest Claude run vs the rule-based baseline.</p>
        </div>
        <div className="row">
          <button className="btn" disabled={!!running || starting} onClick={() => start("heuristic")}>{Icon.play(12)}Run baseline · free</button>
          <button className="btn btn-primary" disabled={!!running || starting || !config?.llm_enabled} onClick={() => start("claude")}
            title={config?.llm_enabled ? "" : "Claude is not enabled on the backend"}>{Icon.play(12)}Run with Claude · ~$1</button>
        </div>
      </div>

      {err && <div className="callout callout-bad" style={{ marginBottom: 12 }}>{err}</div>}
      {runs.error && !runs.data && <ErrorBox message={runs.error} onRetry={runs.reload} />}
      {running && (
        <div className="callout callout-info" style={{ marginBottom: 16 }}>
          <span className="row">{Icon.loader()} {running.reasoner === "claude" ? "Claude" : "Baseline"} run in progress —{" "}
            {running.summary?.completed ?? 0} of {running.scenario_ids.length} scenarios done</span>
        </div>
      )}

      {!claude.data && !rules.data && !runs.loading && (
        <div className="card empty">No completed evaluation runs yet. Start with the free baseline run.</div>
      )}

      {(c || h) && (
        <>
          <div className="tiles">
            <Tile label="Root cause correct (service + type)" value={<Pair a={c?.accuracy} b={h?.accuracy} />} sub={c && h ? "Claude vs rule-based" : c ? "Claude" : "rule-based (no Claude run yet)"} />
            <Tile label="Right service" value={<Pair a={c?.service_accuracy} b={h?.service_accuracy} />} sub={c && h ? "Claude vs rule-based" : c ? "Claude" : "rule-based (no Claude run yet)"} />
            <Tile label="Right service in top 3" value={<Pair a={c?.service_recall_at_3} b={h?.service_recall_at_3} />} sub={c && h ? "Claude vs rule-based" : c ? "Claude" : "rule-based (no Claude run yet)"} />
            <Tile label="Median time to hypothesis" value={duration(c?.median_time_to_hypothesis_ms ?? h?.median_time_to_hypothesis_ms)}
              sub={c ? "Claude (one LLM call)" : "rule-based"} />
          </div>

          <div className="grid-2">
            <div className="card card-pad">
              <h2 style={{ marginBottom: 12 }}>Accuracy by difficulty</h2>
              <GroupedBars
                categories={diffs}
                series={[
                  ...(c ? [{ ...CLAUDE, values: diffs.map((d) => c.by_difficulty?.[d]?.accuracy ?? null) }] : []),
                  ...(h ? [{ ...RULES, values: diffs.map((d) => h.by_difficulty?.[d]?.accuracy ?? null) }] : []),
                ]}
              />
              <div className="tiny muted" style={{ marginTop: 8 }}>
                n = {diffs.map((d) => `${d} ${(c ?? h)?.by_difficulty?.[d]?.n ?? 0}`).join(" · ")}. Hard scenarios include red-herring deploys and faults with no OOM yet.
              </div>
            </div>
            <div className="card card-pad">
              <h2 style={{ marginBottom: 6 }}>How to read this</h2>
              <ul className="small secondary" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.7 }}>
                <li><strong>Correct</strong> = right service <em>and</em> right mechanism (e.g. payment · bad deploy).</li>
                <li>The rule-based baseline was written alongside the simulator, so it scores high on simulated incidents. It is a floor, not a competitor.</li>
                <li>Claude's value shows on <strong>hard</strong> cases: red-herring deploys, slow leaks before any OOM, consumer lag with a healthy database.</li>
                <li>Simulated numbers prove the pipeline. Live-cluster runs (<span className="mono">chaos/inject.py</span>) are the real benchmark.</li>
              </ul>
            </div>
          </div>

          <ResultsTable claude={claude.data} rules={rules.data} />
        </>
      )}

      {list.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head"><h2>Run history</h2></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Run</th><th>Reasoner</th><th>Accuracy</th><th>Service</th><th>Scenarios</th><th>Median time</th><th style={{ textAlign: "right" }}>When</th></tr></thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td className="mono small">{r.id}</td>
                    <td>{r.reasoner === "claude" ? `Claude${r.model ? ` · ${r.model}` : ""}` : "Rule-based"}</td>
                    <td>{r.status === "running" ? <span className="row">{Icon.loader()}running</span> : pct(r.summary?.accuracy)}</td>
                    <td>{pct(r.summary?.service_accuracy)}</td>
                    <td>{r.summary?.completed ?? 0}/{r.scenario_ids.length}</td>
                    <td>{duration(r.summary?.median_time_to_hypothesis_ms)}</td>
                    <td style={{ textAlign: "right" }} className="small muted">{timeAgo(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function Pair({ a, b }: { a?: number; b?: number }) {
  if (a === undefined) return <span>{pct(b)}</span>;
  return (
    <span className="row" style={{ gap: 10, alignItems: "baseline" }}>
      <span>{pct(a)}</span>
      {b !== undefined && <span style={{ fontSize: 16, color: "var(--text-muted)", fontWeight: 550 }}>vs {pct(b)}</span>}
    </span>
  );
}

function ResultsTable({ claude, rules }: { claude: EvalRun | null; rules: EvalRun | null }) {
  const rows = useMemo(() => {
    const ids = new Set<string>([...(claude?.results ?? []), ...(rules?.results ?? [])].map((r) => r.scenario_id));
    return [...ids].sort().map((id) => ({
      id,
      c: claude?.results?.find((r) => r.scenario_id === id),
      h: rules?.results?.find((r) => r.scenario_id === id),
    }));
  }, [claude, rules]);
  const cell = (r?: NonNullable<EvalRun["results"]>[number]) => {
    if (!r) return <span className="muted">–</span>;
    const g = r.grading;
    const cls = g?.correct ? "chip-good" : g?.service_correct ? "chip-warn" : "chip-bad";
    return (
      <a {...linkProps(`/investigations/${r.investigation_id}`)} className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
        <span className={`chip ${cls}`}>{g?.correct ? Icon.check(12) : Icon.x(12)}{g?.correct ? "pass" : g?.service_correct ? "type" : "miss"}</span>
        <span className="tiny secondary truncate" style={{ maxWidth: 220 }}>{r.predicted}</span>
      </a>
    );
  };
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head"><h2>Per scenario</h2><span className="tiny muted">click a result to open that investigation</span></div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Scenario</th><th>Expected</th>{claude && <th>Claude</th>}{rules && <th>Rule-based</th>}</tr></thead>
          <tbody>
            {rows.map(({ id, c, h }) => (
              <tr key={id}>
                <td>
                  <div className="mono small">{id}</div>
                  <div className="tiny muted" style={{ textTransform: "capitalize" }}>{(c ?? h)?.difficulty}</div>
                </td>
                <td className="tiny secondary">{(c ?? h)?.expected}</td>
                {claude && <td>{cell(c)}</td>}
                {rules && <td>{cell(h)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
