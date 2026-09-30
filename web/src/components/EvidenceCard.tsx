import type { Evidence } from "../api";
import { METRIC_LABELS, SOURCE_LABELS, metricValue } from "../lib";
import { HBars, Sparkline } from "./charts";
import { Icon, SeverityBadge } from "./ui";

export function EvidenceCard({ ev, why }: { ev: Evidence; why?: string }) {
  const d = ev.data ?? {};
  return (
    <div className={`card ev ${why ? "cited" : ""}`} id={`ev-${ev.id}`}>
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div className="row tiny muted" style={{ gap: 6 }}>
          <span>{SOURCE_LABELS[ev.source] ?? ev.source}</span>
          {ev.service && <><span>·</span><span className="mono">{ev.service}</span></>}
        </div>
        {ev.severity !== "info" && <SeverityBadge severity={ev.severity} />}
      </div>
      {body()}
      {why && <div className="ev-why"><strong>Why it matters:</strong> {why}</div>}
      {ev.link && (
        <a className="tiny row" style={{ gap: 4 }} href={ev.link} target="_blank" rel="noreferrer">
          Open in {ev.source === "traces" ? "Jaeger" : "Grafana"} {Icon.ext()}
        </a>
      )}
    </div>
  );

  function body() {
    switch (d.kind) {
      case "metric_series":
        return (
          <>
            <div className="spread">
              <span className="ev-title">{METRIC_LABELS[d.metric] ?? d.metric}</span>
              <span className="small secondary" style={{ fontVariantNumeric: "tabular-nums" }}>
                {metricValue(d.unit, d.baseline)} → <strong style={{ color: "var(--text-primary)" }}>{metricValue(d.unit, d.current)}</strong>
              </span>
            </div>
            <Sparkline series={d.series} unit={d.unit} baseline={d.baseline} label={METRIC_LABELS[d.metric] ?? d.metric} />
          </>
        );
      case "trace_summary": {
        const origins = Object.entries(d.error_origins ?? {}) as [string, number][];
        const lat = Object.entries(d.latency_contributors_ms ?? {}) as [string, number][];
        return (
          <>
            <div className="ev-title">{ev.title}</div>
            {origins.length > 0 && (
              <div>
                <div className="tiny muted" style={{ marginBottom: 4 }}>Where failing traces end ({d.errors}/{d.total})</div>
                <HBars items={origins.slice(0, 5).map(([label, value]) => ({ label, value }))} format={(v) => `${v}`} color="var(--critical)" />
              </div>
            )}
            {lat.length > 0 && (
              <div>
                <div className="tiny muted" style={{ marginBottom: 4 }}>Added latency by service</div>
                <HBars items={lat.slice(0, 5).map(([label, value]) => ({ label, value }))} format={(v) => `+${Math.round(v)}ms`} />
              </div>
            )}
          </>
        );
      }
      case "trace_sample": {
        const spans = (d.spans ?? []) as { service: string; operation: string; duration_ms: number; error: boolean; message: string; kind: string; peer_service?: string }[];
        const max = Math.max(...spans.map((s) => s.duration_ms), 1);
        const msg = [...spans].reverse().find((s) => s.message)?.message;
        return (
          <>
            <div className="ev-title">{d.slow ? "Slow" : "Failing"} trace <span className="mono">{String(d.trace_id).slice(0, 12)}</span></div>
            <div className="waterfall">
              {spans.map((s, i) => (
                <div className="wf-row" key={i} title={`${s.operation} · ${s.duration_ms}ms`}>
                  <span className="mono truncate" style={{ paddingLeft: i * 10 }}>
                    {s.kind === "client" ? `→ ${s.peer_service ?? s.operation}` : s.service}
                  </span>
                  <span className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                    <span className={`wf-bar ${s.error ? "err" : ""}`} style={{ width: `${(s.duration_ms / max) * 75}%` }} />
                    <span className="tiny muted">{Math.round(s.duration_ms)}ms</span>
                  </span>
                </div>
              ))}
            </div>
            {msg && <div className="logline">{msg}</div>}
          </>
        );
      }
      case "log_pattern":
        return (
          <>
            <div className="row" style={{ gap: 8 }}>
              <span className={`chip ${d.level === "ERROR" || d.level === "FATAL" ? "chip-bad" : d.level === "WARN" ? "chip-warn" : ""}`}>{d.level}</span>
              <span className="ev-title" style={{ fontVariantNumeric: "tabular-nums" }}>{Number(d.count).toLocaleString()}×</span>
              {d.first_seen_min !== null && d.first_seen_min !== undefined && <span className="tiny muted">since {d.first_seen_min}m</span>}
            </div>
            <div className="logline">{d.sample || d.pattern}</div>
          </>
        );
      case "pod_status":
      case "k8s_event":
        return (
          <>
            <div className="ev-title">{ev.title}</div>
            {ev.detail && <div className="small secondary">{ev.detail}</div>}
          </>
        );
      case "deploy":
      case "config_change":
      case "scale":
        return (
          <>
            <div className="ev-title">{ev.title}</div>
            {d.kind === "config_change" && d.key && (
              <div className="logline">{d.key}: {JSON.stringify(d.old)} → {JSON.stringify(d.new)}</div>
            )}
          </>
        );
      default:
        return <div className="ev-title">{ev.title}</div>;
    }
  }
}
