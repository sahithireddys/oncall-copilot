import { useEffect, useMemo, useState } from "react";
import { api, CATEGORIES, streamSteps, type Category, type Evidence, type Investigation, type StepEvent, type Verdict } from "../api";
import { EvidenceCard } from "../components/EvidenceCard";
import {
  Confidence, CopyButton, ErrorBox, GradingChip, Icon, ReasonerChip, SeverityBadge, Skeleton, StatusBadge, VerdictChip,
} from "../components/ui";
import { SOURCE_LABELS, clock, duration, humanize, linkProps, timeAgo, useAsync, useLocation, categoryLabel } from "../lib";

const NODE_LABEL: Record<string, string> = {
  queued: "Alert received",
  triage: "Triage",
  gather: "Collect telemetry",
  analyze: "Analyze signals",
  retrieve: "Search past incidents",
  hypothesize: "Form hypothesis",
  report: "Report",
  notify: "Post to Slack",
  feedback: "Engineer feedback",
};

export default function InvestigationPage({ id }: { id: string }) {
  const [running, setRunning] = useState(true);
  const inv = useAsync(() => api.investigation(id), [id], running ? 2000 : null);
  const [events, setEvents] = useState<StepEvent[]>([]);

  const status = inv.data?.status;
  useEffect(() => {
    if (status === "completed" || status === "failed") setRunning(false);
  }, [status]);

  // Live agent steps over SSE; the full event list is fetched once more at the end.
  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    const add = (e: StepEvent) =>
      setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e].sort((a, b) => a.id - b.id)));
    const stop = streamSteps(id, add, async () => {
      if (cancelled) return;
      const all = await api.events(id).catch(() => [] as StepEvent[]);
      if (!cancelled) setEvents(all);
      inv.reload();
    });
    return () => { cancelled = true; stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (inv.error && !inv.data) return <ErrorBox message={inv.error} onRetry={inv.reload} />;
  const data = inv.data;

  return (
    <>
      <a className="small row" style={{ gap: 4, marginBottom: 12 }} {...linkProps("/")}>{Icon.back()}All incidents</a>
      {!data ? (
        <div className="stack"><Skeleton h={28} w={320} /><Skeleton h={180} /></div>
      ) : (
        <>
          <Header inv={data} />
          <div className="grid-detail" style={{ marginTop: 16 }}>
            <Timeline events={events} running={data.status === "running" || data.status === "queued"} />
            <div className="stack">
              {data.hypothesis ? (
                <>
                  <Result inv={data} />
                  <FeedbackPanel inv={data} onSaved={inv.reload} />
                </>
              ) : data.status === "failed" ? (
                <div className="card card-pad"><div className="callout callout-bad"><strong>Investigation failed.</strong> {data.error}</div></div>
              ) : (
                <div className="card hero">
                  <div className="row secondary">{Icon.loader(16)}<span className="pulse">The agent is investigating…</span></div>
                  <Skeleton h={22} w="50%" /><Skeleton h={14} /><Skeleton h={14} w="80%" />
                </div>
              )}
            </div>
          </div>
          {data.evidence.length > 0 && <EvidenceSection inv={data} />}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------- header
function Header({ inv }: { inv: Investigation }) {
  const a = inv.alert;
  return (
    <div className="page-head" style={{ marginBottom: 0 }}>
      <div>
        <div className="row" style={{ gap: 8, marginBottom: 6 }}>
          <SeverityBadge severity={a.severity} />
          <StatusBadge status={inv.status} />
          <ReasonerChip reasoner={inv.hypothesis?.reasoner ?? inv.reasoner} />
          <span className="chip chip-outline">{inv.telemetry_mode} telemetry</span>
        </div>
        <h1>{a.name}</h1>
        <p>
          <span className="mono">{a.service}</span> · {a.summary || "no summary"} · fired {timeAgo(inv.created_at)}
          {inv.duration_ms !== null && <> · root cause in <strong>{duration(inv.duration_ms)}</strong></>}
        </p>
      </div>
      {inv.scenario_id && (
        <div className="tiny muted" style={{ textAlign: "right" }}>
          Scenario<br /><span className="mono secondary">{inv.scenario_id}</span>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------- timeline
function Timeline({ events, running }: { events: StepEvent[]; running: boolean }) {
  // group consecutive events from the same node into one step
  const groups: { node: string; events: StepEvent[] }[] = [];
  for (const e of events) {
    const last = groups[groups.length - 1];
    if (last && last.node === e.node) last.events.push(e);
    else groups.push({ node: e.node, events: [e] });
  }
  return (
    <div className="card card-pad" style={{ position: "sticky", top: 16 }}>
      <h2 style={{ marginBottom: 14 }}>Agent steps</h2>
      {groups.length === 0 && <div className="muted small row">{Icon.loader()}Connecting…</div>}
      <ol className="timeline">
        {groups.map((g, i) => {
          const failed = g.events.some((e) => e.status === "failed");
          const done = g.events.some((e) => e.status === "completed");
          const isLast = i === groups.length - 1;
          const live = running && isLast && !failed;
          const main = [...g.events].reverse().find((e) => e.status === "completed" || e.status === "failed") ?? g.events[0];
          const allProgress = g.events.filter((e) => e.status === "progress");
          // for telemetry collection, only list sources where something looked abnormal
          const progress = g.node === "gather" ? allProgress.filter((e) => (e.data?.notable?.length ?? 0) > 0) : allProgress;
          const quiet = allProgress.length - progress.length;
          const cls = failed ? "fail" : live ? "live" : done ? "done" : "";
          return (
            <li key={`${g.node}-${i}`} className={`tl-item ${cls}`}>
              <span className="tl-icon">{live ? Icon.loader(12) : failed ? Icon.x(12) : Icon.node(g.node)}</span>
              <div className="spread">
                <span className="tl-title">{NODE_LABEL[g.node] ?? g.node}</span>
                <span className="tiny muted">{clock(g.events[0].ts)}</span>
              </div>
              <div className="tl-msg">{main.message}</div>
              {progress.length > 0 && (
                <ul className="tl-sub">
                  {progress.slice(0, 12).map((p) => <li key={p.id}>{p.message}</li>)}
                  {progress.length > 12 && <li>+{progress.length - 12} more</li>}
                </ul>
              )}
              {quiet > 0 && <div className="tiny muted" style={{ marginTop: 4 }}>{quiet} other source{quiet > 1 ? "s" : ""} looked normal</div>}
              {g.node === "retrieve" && Array.isArray(main.data?.documents) && main.data.documents.length > 0 && (
                <ul className="tl-sub">
                  {(main.data.documents as { id: string; title: string }[]).slice(0, 3).map((d) => (
                    <li key={d.id}><a {...linkProps(`/knowledge?doc=${encodeURIComponent(d.id)}`)}>{d.id}</a> {d.title}</li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------- result
function Result({ inv }: { inv: Investigation }) {
  const h = inv.hypothesis!;
  const g = inv.grading;
  return (
    <div className="card hero">
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="section-title" style={{ marginBottom: 6 }}>Likely root cause</div>
          <div className="hero-cause">
            <span className="svc">{h.root_cause_service}</span>
            <span className="hero-cat" style={{ textTransform: "none" }}>{categoryLabel(h.category)}</span>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="tiny muted" style={{ marginBottom: 4 }}>Confidence</div>
          <Confidence value={h.confidence} width={100} />
        </div>
      </div>
      <div>
        <h3 style={{ marginBottom: 6 }}>{h.title}</h3>
        <div className="secondary">{h.summary}</div>
      </div>

      {g && (
        <div className={`callout ${g.correct ? "callout-good" : "callout-bad"}`}>
          <div className="row" style={{ gap: 8 }}>
            <GradingChip grading={g} />
            <span>Known root cause: <strong className="mono">{g.expected_service}</strong> · {categoryLabel(g.expected_category).toLowerCase()}</span>
          </div>
        </div>
      )}

      {h.reasoning && (
        <details>
          <summary>How the agent reasoned</summary>
          <div className="secondary small" style={{ lineHeight: 1.65 }}>{h.reasoning}</div>
        </details>
      )}

      {h.suggested_actions.length > 0 && (
        <div>
          <div className="section-title">Suggested next steps</div>
          <div className="stack" style={{ gap: 10 }}>
            {h.suggested_actions.map((a, i) => (
              <div key={i}>
                <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                  <strong>{i + 1}. {a.title}</strong>
                  <span className={`chip ${a.risk === "high" ? "chip-bad" : a.risk === "medium" ? "chip-warn" : "chip-good"}`}>{a.risk} risk</span>
                </div>
                {a.rationale && <div className="small secondary" style={{ marginBottom: 4 }}>{a.rationale}</div>}
                {a.command && <div className="cmd"><code>{a.command}</code><CopyButton text={a.command} /></div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {h.alternatives.length > 0 && (
        <div>
          <div className="section-title">Also considered</div>
          <div className="stack" style={{ gap: 6 }}>
            {h.alternatives.map((a, i) => (
              <div key={i} className="small">
                <span className="mono" style={{ fontWeight: 600 }}>{a.root_cause_service}</span>{" "}
                <span className="muted">({categoryLabel(a.category).toLowerCase()})</span> — <span className="secondary">{a.why_less_likely}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {(h.related_documents.length > 0 || inv.services_examined.length > 0) && (
        <div className="row small" style={{ gap: 16 }}>
          {h.related_documents.length > 0 && (
            <span className="row" style={{ gap: 6 }}>
              <span className="muted">Similar incidents:</span>
              {h.related_documents.map((d) => <a key={d} className="mono" {...linkProps(`/knowledge?doc=${encodeURIComponent(d)}`)}>{d}</a>)}
            </span>
          )}
          {inv.services_examined.length > 0 && (
            <span className="row" style={{ gap: 6 }}>
              <span className="muted">Examined:</span>
              {inv.services_examined.map((s) => <span key={s} className="chip mono">{s}</span>)}
            </span>
          )}
        </div>
      )}
      {inv.notes.length > 0 && <div className="callout callout-info tiny">{inv.notes.join(" · ")}</div>}
    </div>
  );
}

// -------------------------------------------------------------------- feedback
function FeedbackPanel({ inv, onSaved }: { inv: Investigation; onSaved: () => void }) {
  const { search } = useLocation();
  const initial = (search.get("feedback") as Verdict | null) ?? null;
  const [verdict, setVerdict] = useState<Verdict | null>(inv.feedback?.verdict ?? initial);
  const [service, setService] = useState(inv.feedback?.actual_root_cause_service ?? "");
  const [category, setCategory] = useState<Category | "">(inv.feedback?.actual_category ?? "");
  const [notes, setNotes] = useState(inv.feedback?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const topo = useAsync(() => api.topology(), []);
  const services = topo.data?.nodes.map((n) => n.id) ?? [];

  const save = async (v: Verdict) => {
    setSaving(true);
    setMsg(null);
    try {
      await api.feedback(inv.id, {
        verdict: v,
        actual_root_cause_service: v !== "correct" && service ? service : undefined,
        actual_category: v !== "correct" && category ? category : undefined,
        notes: notes || undefined,
      });
      setMsg(v === "correct" || service ? "Saved — added to the knowledge base for future incidents." : "Saved.");
      onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not save feedback");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card card-pad">
      <div className="spread" style={{ marginBottom: 10 }}>
        <h2>Was this right?</h2>
        <VerdictChip verdict={inv.feedback?.verdict} />
      </div>
      <div className="row">
        <button className={`btn btn-good ${verdict === "correct" ? "selected" : ""}`} disabled={saving}
          onClick={() => { setVerdict("correct"); save("correct"); }}>{Icon.check()}Correct</button>
        <button className={`btn btn-warn ${verdict === "partially_correct" ? "selected" : ""}`} disabled={saving}
          onClick={() => setVerdict("partially_correct")}>Partly right</button>
        <button className={`btn btn-bad ${verdict === "incorrect" ? "selected" : ""}`} disabled={saving}
          onClick={() => setVerdict("incorrect")}>{Icon.x()}Wrong</button>
      </div>
      {verdict && verdict !== "correct" && (
        <div className="stack" style={{ gap: 10, marginTop: 14 }}>
          <div className="small secondary">What was the actual root cause? Corrections are stored as a past incident so the agent retrieves them next time.</div>
          <div className="row">
            <select value={service} onChange={(e) => setService(e.target.value)} aria-label="Actual root-cause service">
              <option value="">Actual service…</option>
              {services.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={category} onChange={(e) => setCategory(e.target.value as Category)} aria-label="Actual category">
              <option value="">Actual type…</option>
              {CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}
            </select>
          </div>
          <textarea placeholder="Notes (optional): what actually happened?" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <div><button className="btn btn-primary" disabled={saving} onClick={() => save(verdict)}>{saving ? "Saving…" : "Submit feedback"}</button></div>
        </div>
      )}
      {msg && <div className="small secondary" style={{ marginTop: 10 }}>{msg}</div>}
    </div>
  );
}

// -------------------------------------------------------------------- evidence
function EvidenceSection({ inv }: { inv: Investigation }) {
  const cited = useMemo(() => {
    const m = new Map<string, string>();
    inv.hypothesis?.supporting_evidence.forEach((r) => m.set(r.evidence_id, r.why_it_matters));
    return m;
  }, [inv.hypothesis]);
  const [tab, setTab] = useState<string>(cited.size ? "cited" : "all");
  const sources = ["traces", "metrics", "logs", "k8s_events", "changes"].filter((s) => inv.evidence.some((e) => e.source === s));
  const sevRank = { critical: 0, warning: 1, info: 2 } as const;
  const sorted = [...inv.evidence].sort((a, b) =>
    (cited.has(a.id) ? 0 : 1) - (cited.has(b.id) ? 0 : 1) || sevRank[a.severity] - sevRank[b.severity]);
  const shown: Evidence[] =
    tab === "cited" ? sorted.filter((e) => cited.has(e.id))
    : tab === "all" ? sorted
    : sorted.filter((e) => e.source === tab);

  return (
    <div style={{ marginTop: 28 }}>
      <div className="spread" style={{ marginBottom: 6 }}>
        <h2>Evidence</h2>
        <span className="small muted">{inv.evidence.length} items collected · {inv.findings.length} signals extracted</span>
      </div>
      <div className="tabs" role="tablist">
        {cited.size > 0 && (
          <button className={`tab ${tab === "cited" ? "on" : ""}`} onClick={() => setTab("cited")}>Cited by agent<span className="count">{cited.size}</span></button>
        )}
        <button className={`tab ${tab === "all" ? "on" : ""}`} onClick={() => setTab("all")}>All<span className="count">{inv.evidence.length}</span></button>
        {sources.map((s) => (
          <button key={s} className={`tab ${tab === s ? "on" : ""}`} onClick={() => setTab(s)}>
            {SOURCE_LABELS[s]}<span className="count">{inv.evidence.filter((e) => e.source === s).length}</span>
          </button>
        ))}
        <button className={`tab ${tab === "signals" ? "on" : ""}`} onClick={() => setTab("signals")}>Signals<span className="count">{inv.findings.length}</span></button>
      </div>
      {tab === "signals" ? (
        <div className="card">
          <table className="table">
            <thead><tr><th>Score</th><th>Service</th><th>Signal</th><th>Kind</th></tr></thead>
            <tbody>
              {inv.findings.map((f, i) => (
                <tr key={i}>
                  <td style={{ fontVariantNumeric: "tabular-nums", width: 70 }}>{f.score.toFixed(1)}</td>
                  <td><span className="mono">{f.service}</span></td>
                  <td className="secondary">{f.summary}</td>
                  <td><span className="chip">{humanize(f.kind)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ev-grid">
          {shown.map((e) => <EvidenceCard key={e.id} ev={e} why={cited.get(e.id)} />)}
        </div>
      )}
    </div>
  );
}
