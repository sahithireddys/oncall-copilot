import { api } from "../api";
import { Confidence, ErrorBox, GradingChip, Icon, ReasonerChip, SeverityBadge, Skeleton, StatusBadge, Tile, VerdictChip } from "../components/ui";
import { duration, humanize, linkProps, navigate, pct, timeAgo, useAsync, categoryLabel } from "../lib";

export default function Incidents() {
  const list = useAsync(() => api.investigations(100), [], 3000);
  const stats = useAsync(() => api.stats(), [], 5000);
  const items = list.data?.items ?? [];
  const s = stats.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p>Every alert the copilot investigated, with its root-cause call and what the on-call engineer said about it.</p>
        </div>
        <a className="btn btn-primary" {...linkProps("/scenarios")}>{Icon.play()}Trigger an incident</a>
      </div>

      <div className="tiles">
        <Tile label="Investigations" value={s ? s.total : "–"} sub={s && s.running ? `${s.running} running now` : `${s?.failed ?? 0} failed`} />
        <Tile label="Median time to root cause" value={duration(s?.median_time_to_hypothesis_ms)} sub="alert → hypothesis" />
        <Tile label="Matched known root cause" value={pct(s?.scenario_accuracy)} sub={s ? `across ${s.scenario_runs} scenario runs` : ""} />
        <Tile label="Engineer-confirmed" value={pct(s?.feedback.accuracy)}
          sub={s ? `${s.feedback.count} rated · ${s.learned_incidents} learned into knowledge base` : ""} />
      </div>

      {list.error && !list.data ? (
        <ErrorBox message={list.error} onRetry={list.reload} />
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Alert</th>
                  <th>Likely root cause</th>
                  <th>Confidence</th>
                  <th>Outcome</th>
                  <th>Reasoner</th>
                  <th style={{ textAlign: "right" }}>When</th>
                </tr>
              </thead>
              <tbody>
                {list.loading && !list.data && [0, 1, 2, 3].map((i) => (
                  <tr key={i}><td colSpan={6}><Skeleton h={18} /></td></tr>
                ))}
                {items.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => navigate(`/investigations/${i.id}`)}>
                    <td style={{ minWidth: 240 }}>
                      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                        <SeverityBadge severity={i.alert.severity} />
                        <a {...linkProps(`/investigations/${i.id}`)} style={{ color: "var(--text-primary)", fontWeight: 580 }}>
                          {i.alert.name}
                        </a>
                      </div>
                      <div className="tiny muted" style={{ marginTop: 2 }}>
                        on <span className="mono">{i.alert.service}</span>{i.scenario_id && <> · scenario <span className="mono">{i.scenario_id}</span></>}
                      </div>
                    </td>
                    <td>
                      {i.hypothesis ? (
                        <>
                          <span className="svc">{i.hypothesis.root_cause_service}</span>
                          <div className="tiny muted">{categoryLabel(i.hypothesis.category)}</div>
                        </>
                      ) : (
                        <StatusBadge status={i.status} />
                      )}
                    </td>
                    <td>{i.hypothesis ? <Confidence value={i.hypothesis.confidence} width={60} /> : <span className="muted">–</span>}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <VerdictChip verdict={i.feedback} />
                        <GradingChip grading={i.grading} />
                        {!i.feedback && !i.grading && i.status === "completed" && <span className="tiny muted">awaiting review</span>}
                        {i.status === "failed" && <StatusBadge status="failed" />}
                      </div>
                    </td>
                    <td><ReasonerChip reasoner={i.reasoner} /></td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <div className="small">{timeAgo(i.created_at)}</div>
                      <div className="tiny muted">{duration(i.duration_ms)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!list.loading && items.length === 0 && (
              <div className="empty">
                <div style={{ marginBottom: 10 }}>No investigations yet.</div>
                <a className="btn btn-primary" {...linkProps("/scenarios")}>{Icon.play()}Trigger your first incident</a>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
