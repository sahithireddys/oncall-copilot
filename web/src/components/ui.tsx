import { useState, type ReactNode } from "react";
import type { Grading, Severity, Status, Verdict } from "../api";
import { copyText, humanize, pct, categoryLabel } from "../lib";

// ------------------------------------------------------------------ icons (inline SVG, no dependency)
const P = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
export const Icon = {
  pulse: (s = 16) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M3 12h4l3-8 4 16 3-8h4" /></svg>,
  list: (s = 16) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>,
  flask: (s = 16) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M9 3h6M10 3v6l-6 10a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-10V3" /></svg>,
  chart: (s = 16) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>,
  book: (s = 16) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14zM20 17v4H6.5" /></svg>,
  play: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M6 4l14 8-14 8z" /></svg>,
  check: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M5 12l5 5L20 7" /></svg>,
  x: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M6 6l12 12M18 6L6 18" /></svg>,
  alert: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 3l10 18H2zM12 10v4M12 17h.01" /></svg>,
  info: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>,
  loader: (s = 14) => <svg className="spin" width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 3a9 9 0 1 0 9 9" /></svg>,
  copy: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>,
  back: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M15 18l-6-6 6-6" /></svg>,
  ext: (s = 12) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M14 4h6v6M20 4L10 14M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>,
  moon: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z" /></svg>,
  sun: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  search: (s = 14) => <svg width={s} height={s} viewBox="0 0 24 24" {...P}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>,
  node: (name: string, s = 12) => {
    const paths: Record<string, string> = {
      queued: "M12 7v5l3 3M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
      triage: "M12 3l10 18H2zM12 10v4",
      gather: "M4 7h16M4 12h16M4 17h10",
      analyze: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14M21 21l-5-5",
      retrieve: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z",
      hypothesize: "M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2",
      report: "M5 12l5 5L20 7",
      notify: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
      feedback: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
    };
    return <svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d={paths[name] ?? "M12 12h.01"} /></svg>;
  },
};

// ------------------------------------------------------------------ badges
const SEV: Record<Severity, { cls: string; icon: ReactNode; label: string }> = {
  critical: { cls: "chip-bad", icon: Icon.alert(12), label: "Critical" },
  warning: { cls: "chip-warn", icon: Icon.alert(12), label: "Warning" },
  info: { cls: "", icon: Icon.info(12), label: "Info" },
};
export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEV[severity] ?? SEV.info;
  return <span className={`chip ${s.cls}`}>{s.icon}{s.label}</span>;
}

export function StatusBadge({ status }: { status: Status }) {
  if (status === "running" || status === "queued")
    return <span className="chip chip-accent">{Icon.loader(12)}{status === "queued" ? "Queued" : "Investigating"}</span>;
  if (status === "failed") return <span className="chip chip-bad">{Icon.x(12)}Failed</span>;
  return <span className="chip">{Icon.check(12)}Done</span>;
}

export function VerdictChip({ verdict }: { verdict: Verdict | null | undefined }) {
  if (!verdict) return null;
  if (verdict === "correct") return <span className="chip chip-good">{Icon.check(12)}Confirmed</span>;
  if (verdict === "partially_correct") return <span className="chip chip-warn">{Icon.alert(12)}Partly right</span>;
  return <span className="chip chip-bad">{Icon.x(12)}Marked wrong</span>;
}

export function GradingChip({ grading }: { grading: Grading | null | undefined }) {
  if (!grading) return null;
  if (grading.correct) return <span className="chip chip-good" title="Matches the scenario's known root cause">{Icon.check(12)}Matches ground truth</span>;
  if (grading.service_correct) return <span className="chip chip-warn" title={`Expected ${categoryLabel(grading.expected_category)}`}>{Icon.alert(12)}Right service, wrong type</span>;
  return <span className="chip chip-bad" title={`Expected ${grading.expected_service}`}>{Icon.x(12)}Missed</span>;
}

export function ReasonerChip({ reasoner }: { reasoner: string }) {
  const claude = reasoner.startsWith("claude");
  return <span className={`chip ${claude ? "chip-accent" : ""}`}>{claude ? "Claude" : "Rule-based"}</span>;
}

export function Confidence({ value, width = 90 }: { value: number; width?: number }) {
  return (
    <span className="row" style={{ gap: 8, flexWrap: "nowrap" }} title={`Confidence ${pct(value)}`}>
      <span className="meter" style={{ width }}><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
      <span className="small secondary" style={{ fontVariantNumeric: "tabular-nums" }}>{pct(value)}</span>
    </span>
  );
}

export function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn btn-sm btn-ghost" aria-label="Copy command" title="Copy"
      onClick={async () => { if (await copyText(text)) { setDone(true); setTimeout(() => setDone(false), 1400); } }}>
      {done ? Icon.check(13) : Icon.copy(13)}
    </button>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card card-pad">
      <div className="callout callout-bad">
        <strong>Couldn't reach the backend.</strong> {message}
        <div className="small" style={{ marginTop: 6 }}>
          Is it running? <code>uvicorn copilot.api:app --reload --port 8000</code>
        </div>
      </div>
      {onRetry && <button className="btn btn-sm" style={{ marginTop: 10 }} onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}
