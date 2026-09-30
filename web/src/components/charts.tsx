import { useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { metricValue } from "../lib";

/**
 * Time-series line for one metric. x = minutes relative to the alert (<= 0).
 * Dashed line = baseline (median of the first 10 minutes). Hover shows a crosshair + value.
 */
export function Sparkline({ series, unit, baseline, height = 64, label }: {
  series: [number, number][]; unit: string; baseline?: number; height?: number; label: string;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  if (!series.length) return null;
  const W = 300, H = height, padT = 6, padB = 16;
  const xs = series.map((p) => p[0]);
  const ys = series.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const yMax = Math.max(...ys, baseline ?? 0) * 1.08 || 1;
  const yMin = unit === "ratio" || unit === "messages" || unit === "req/s" ? 0 : Math.min(0, ...ys);
  const sx = (x: number) => ((x - x0) / (x1 - x0 || 1)) * W;
  const sy = (y: number) => padT + (1 - (y - yMin) / (yMax - yMin || 1)) * (H - padT - padB);
  const d = series.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  const area = `${d}L${sx(x1)},${sy(yMin)}L${sx(x0)},${sy(yMin)}Z`;

  const onMove = (e: RPointerEvent<SVGSVGElement>) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const x = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    series.forEach((p, i) => { if (Math.abs(sx(p[0]) - x) < Math.abs(sx(series[best][0]) - x)) best = i; });
    setHover(best);
  };
  const hp = hover !== null ? series[hover] : null;

  return (
    <div className="chart-wrap">
      <svg ref={ref} className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height: H }}
        role="img" aria-label={`${label} over the last ${Math.abs(x0)} minutes`}
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <line className="grid-line" x1={0} x2={W} y1={sy(yMin)} y2={sy(yMin)} />
        {baseline !== undefined && <line className="baseline" x1={0} x2={W} y1={sy(baseline)} y2={sy(baseline)} vectorEffect="non-scaling-stroke" />}
        <path d={area} fill="var(--series-1)" opacity={0.1} />
        <path d={d} fill="none" stroke="var(--series-1)" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {hp && (
          <>
            <line x1={sx(hp[0])} x2={sx(hp[0])} y1={padT} y2={H - padB} stroke="var(--text-muted)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <circle cx={sx(hp[0])} cy={sy(hp[1])} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      <div className="spread tiny muted" style={{ marginTop: -14, pointerEvents: "none" }}>
        <span>{x0}m</span><span>alert</span>
      </div>
      {hp && (
        <div className="tooltip" style={{ left: `${(sx(hp[0]) / W) * 100}%`, top: sy(hp[1]) }}>
          {hp[0] === 0 ? "at alert" : `${hp[0]}m`} · {metricValue(unit, hp[1])}
        </div>
      )}
    </div>
  );
}

/** Horizontal bars for one measure across categories (e.g. where failing traces originate). */
export function HBars({ items, format = (v) => String(v), color = "var(--series-1)" }: {
  items: { label: string; value: number }[]; format?: (v: number) => string; color?: string;
}) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="stack" style={{ gap: 6 }}>
      {items.map((it) => (
        <div key={it.label} className="wf-row" title={`${it.label}: ${format(it.value)}`}>
          <span className="mono truncate">{it.label}</span>
          <span className="row" style={{ flexWrap: "nowrap", gap: 8 }}>
            <span className="wf-bar" style={{ width: `${(it.value / max) * 80}%`, background: color }} />
            <span className="tiny secondary" style={{ fontVariantNumeric: "tabular-nums" }}>{format(it.value)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

/** Grouped vertical bars: categories on x, up to two series (e.g. Claude vs rule-based accuracy). */
export function GroupedBars({ categories, series, height = 190, format = (v) => `${Math.round(v * 100)}%`, max = 1 }: {
  categories: string[];
  series: { name: string; color: string; values: (number | null)[] }[];
  height?: number; format?: (v: number) => string; max?: number;
}) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const W = 520, H = height, padL = 36, padB = 26, padT = 18;
  const groupW = (W - padL) / categories.length;
  const barW = Math.min(34, (groupW - 24) / series.length);
  const sy = (v: number) => padT + (1 - v / max) * (H - padT - padB);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  return (
    <div className="chart-wrap">
      <div className="legend" style={{ marginBottom: 8 }}>
        {series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Accuracy by group" onPointerLeave={() => setTip(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={padL} x2={W} y1={sy(t)} y2={sy(t)} />
            <text x={padL - 6} y={sy(t) + 4} textAnchor="end">{format(t)}</text>
          </g>
        ))}
        {categories.map((c, ci) => {
          const gx = padL + ci * groupW + (groupW - barW * series.length - 2 * (series.length - 1)) / 2;
          return (
            <g key={c}>
              {series.map((s, si) => {
                const v = s.values[ci];
                if (v === null || v === undefined) return null;
                const x = gx + si * (barW + 2);
                const y = sy(v);
                const h = Math.max(0, sy(0) - y);
                const r = Math.min(4, h / 2);
                return (
                  <g key={s.name}>
                    <path
                      d={`M${x},${sy(0)}V${y + r}Q${x},${y} ${x + r},${y}H${x + barW - r}Q${x + barW},${y} ${x + barW},${y + r}V${sy(0)}Z`}
                      fill={s.color}
                      onPointerMove={() => setTip({ x: ((x + barW / 2) / W) * 100, y: (y / H) * 100, text: `${s.name} · ${c}: ${format(v)}` })}
                    />
                    <text x={x + barW / 2} y={y - 5} textAnchor="middle" style={{ fill: "var(--text-secondary)", fontSize: 10.5 }}>{format(v)}</text>
                  </g>
                );
              })}
              <text x={padL + ci * groupW + groupW / 2} y={H - 8} textAnchor="middle" style={{ textTransform: "capitalize" }}>{c}</text>
            </g>
          );
        })}
      </svg>
      {tip && <div className="tooltip" style={{ left: `${tip.x}%`, top: `${tip.y}%` }}>{tip.text}</div>}
    </div>
  );
}
