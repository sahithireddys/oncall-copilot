import { useEffect, useState } from "react";
import { api, type KnowledgeDoc } from "../api";
import { ErrorBox, Icon, Skeleton } from "../components/ui";
import { humanize, linkProps, navigate, useAsync, useLocation, categoryLabel } from "../lib";

const TYPE_CHIP: Record<string, string> = { postmortem: "chip-accent", runbook: "", incident: "chip-good" };

export default function Knowledge() {
  const { search } = useLocation();
  const docId = search.get("doc");
  const all = useAsync(() => api.knowledge(), []);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<KnowledgeDoc[] | null>(null);

  useEffect(() => {
    const needle = q.trim();
    if (!needle) { setResults(null); return; }
    const t = window.setTimeout(() => { api.searchKnowledge(needle).then(setResults).catch(() => setResults([])); }, 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const docs = results ?? all.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Knowledge base</h1>
          <p>Postmortems, runbooks and incidents confirmed by engineers. The agent searches these (BM25) on every investigation.</p>
        </div>
      </div>
      <div className="grid-detail">
        <div className="stack" style={{ gap: 10 }}>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <span className="muted">{Icon.search()}</span>
            <input type="search" style={{ flex: 1 }} placeholder='Try "redis pool timeout" or "OOMKilled"' value={q}
              onChange={(e) => setQ(e.target.value)} aria-label="Search knowledge" />
          </div>
          {all.error && !all.data && <ErrorBox message={all.error} onRetry={all.reload} />}
          {all.loading && !all.data && <Skeleton h={200} />}
          <div className="card">
            {docs.map((d) => (
              <a key={d.id} {...linkProps(`/knowledge?doc=${encodeURIComponent(d.id)}`)}
                style={{ display: "block", padding: "12px 16px", borderBottom: "1px solid var(--border)", color: "inherit", textDecoration: "none",
                  background: d.id === docId ? "var(--accent-soft)" : undefined }}>
                <div className="spread">
                  <span className="mono tiny muted">{d.id}</span>
                  <span className="row" style={{ gap: 4 }}>
                    {d.score !== undefined && <span className="tiny muted">score {d.score.toFixed(1)}</span>}
                    <span className={`chip ${TYPE_CHIP[d.type] ?? ""}`}>{d.type}</span>
                  </span>
                </div>
                <div style={{ fontWeight: 560, marginTop: 2 }}>{d.title}</div>
              </a>
            ))}
            {docs.length === 0 && !all.loading && <div className="empty">No matches.</div>}
          </div>
        </div>
        <DocView id={docId ?? docs[0]?.id ?? null} />
      </div>
    </>
  );
}

function DocView({ id }: { id: string | null }) {
  const doc = useAsync(() => (id ? api.document(id) : Promise.resolve(null)), [id]);
  if (!id) return <div className="card empty">Select a document.</div>;
  if (doc.loading && !doc.data) return <div className="card card-pad"><Skeleton h={24} w="60%" /><div style={{ height: 12 }} /><Skeleton h={160} /></div>;
  if (!doc.data) return <div className="card empty">Document not found. <button className="btn btn-sm" onClick={() => navigate("/knowledge")}>Back</button></div>;
  const d = doc.data;
  return (
    <div className="card card-pad stack">
      <div>
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          <span className={`chip ${TYPE_CHIP[d.type] ?? ""}`}>{d.type}</span>
          {d.category && <span className="chip chip-outline" >{categoryLabel(d.category)}</span>}
          {d.date && <span className="tiny muted">{d.date}</span>}
        </div>
        <h2 style={{ fontSize: 19 }}>{d.title}</h2>
        <div className="row" style={{ gap: 4, marginTop: 8 }}>
          {d.services.map((s) => <span key={s} className="chip mono">{s}</span>)}
        </div>
      </div>
      <div className="doc-body">
        {(d.body ?? "").split("\n").map((line, i) =>
          line.startsWith("## ") ? <h3 key={i} style={{ margin: "14px 0 4px", color: "var(--text-primary)" }}>{line.slice(3)}</h3>
          : <div key={i}>{line.trim() ? renderInline(line) : "\u00a0"}</div>)}
      </div>
    </div>
  );
}

/** Minimal inline markdown: `code` spans only. */
function renderInline(line: string) {
  const parts = line.split(/(`[^`]+`)/g);
  return parts.map((p, i) => p.startsWith("`") && p.endsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : p);
}
