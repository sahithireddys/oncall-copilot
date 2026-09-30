import { useMemo, useState } from "react";
import { api, type Reasoner, type RuntimeConfig } from "../api";
import { ErrorBox, Icon, SeverityBadge, Skeleton } from "../components/ui";
import { navigate, useAsync } from "../lib";

const DIFFS = ["all", "easy", "medium", "hard"] as const;

export default function Scenarios({ config }: { config: RuntimeConfig | null }) {
  const scenarios = useAsync(() => api.scenarios(), []);
  const [diff, setDiff] = useState<(typeof DIFFS)[number]>("all");
  const [q, setQ] = useState("");
  const [reasoner, setReasoner] = useState<Reasoner>("heuristic");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const llm = !!config?.llm_enabled;

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (scenarios.data ?? []).filter((s) =>
      (diff === "all" || s.difficulty === diff) &&
      (!needle || [s.id, s.title, s.alert.service, s.alert.name, ...s.tags].join(" ").toLowerCase().includes(needle)));
  }, [scenarios.data, diff, q]);

  const run = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      const inv = await api.startScenario(id, reasoner);
      navigate(`/investigations/${inv.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Scenarios</h1>
          <p>Injected incidents with a known root cause. Trigger one and watch the agent investigate it live.
            The agent only sees telemetry, never the scenario title or its answer.</p>
        </div>
      </div>

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="spread" style={{ flexWrap: "wrap" }}>
          <div className="row" style={{ gap: 10 }}>
            <input type="search" placeholder="Search service, tag…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search scenarios" />
            <div className="seg" role="group" aria-label="Difficulty">
              {DIFFS.map((d) => (
                <button key={d} className={diff === d ? "on" : ""} onClick={() => setDiff(d)} style={{ textTransform: "capitalize" }}>{d}</button>
              ))}
            </div>
          </div>
          <div className="row" style={{ gap: 10 }}>
            <span className="small secondary">Reasoner</span>
            <div className="seg" role="group" aria-label="Reasoner">
              <button className={reasoner === "heuristic" ? "on" : ""} onClick={() => setReasoner("heuristic")}>Rule-based · free</button>
              <button className={reasoner === "claude" ? "on" : ""} disabled={!llm} onClick={() => setReasoner("claude")}
                title={llm ? "Uses the Anthropic API (~$0.04 per investigation)" : "Set ANTHROPIC_API_KEY and REASONER=claude to enable"}>
                Claude · ~4¢
              </button>
            </div>
          </div>
        </div>
        {!llm && <div className="tiny muted" style={{ marginTop: 8 }}>Claude is off: the backend has no API key or REASONER=heuristic. Rule-based runs cost nothing.</div>}
      </div>

      {error && <div className="callout callout-bad" style={{ marginBottom: 12 }}>{error}</div>}
      {scenarios.error && !scenarios.data && <ErrorBox message={scenarios.error} onRetry={scenarios.reload} />}

      <div className="scn-grid">
        {scenarios.loading && !scenarios.data && [0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="card scn"><Skeleton h={18} w="70%" /><Skeleton h={50} /></div>)}
        {shown.map((s) => (
          <div key={s.id} className="card scn">
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <h3>{s.title}</h3>
              <span className={`chip ${s.difficulty === "hard" ? "chip-bad" : s.difficulty === "medium" ? "chip-warn" : "chip-good"}`}
                style={{ textTransform: "capitalize" }}>{s.difficulty}</span>
            </div>
            <div className="row" style={{ gap: 6 }}>
              <SeverityBadge severity={s.alert.severity} />
              <span className="small"><strong>{s.alert.name}</strong> on <span className="mono">{s.alert.service}</span></span>
            </div>
            <p>{s.alert.summary}</p>
            <div className="row" style={{ gap: 4 }}>
              {s.tags.map((t) => <span key={t} className="chip chip-outline tiny">{t}</span>)}
            </div>
            <div className="spread" style={{ marginTop: "auto" }}>
              <span className="tiny muted" title="How to reproduce this on a real cluster">live: {s.reproduce_method}</span>
              <button className="btn btn-primary btn-sm" disabled={busy !== null} onClick={() => run(s.id)}>
                {busy === s.id ? Icon.loader() : Icon.play(12)}Investigate
              </button>
            </div>
          </div>
        ))}
      </div>
      {!scenarios.loading && shown.length === 0 && scenarios.data && <div className="empty">No scenarios match.</div>}
    </>
  );
}
