import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

// ---------------------------------------------------------------- tiny router
// History-API router (no dependency). Paths: /, /investigations/:id, /scenarios, /evals, /knowledge

function subscribe(cb: () => void) {
  window.addEventListener("popstate", cb);
  return () => window.removeEventListener("popstate", cb);
}

export function useLocation(): { path: string; search: URLSearchParams } {
  const href = useSyncExternalStore(subscribe, () => window.location.pathname + window.location.search);
  const url = new URL(href, window.location.origin);
  return { path: url.pathname, search: url.searchParams };
}

export function navigate(to: string) {
  if (to === window.location.pathname + window.location.search) return;
  window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo({ top: 0 });
}

/** Props for <a> that navigate client-side but still work with cmd-click / middle-click. */
export function linkProps(to: string) {
  return {
    href: to,
    onClick: (e: ReactMouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(to);
    },
  };
}

// ---------------------------------------------------------------- data hooks

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      const v = await fnRef.current();
      setData(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!pollMs) return;
    const t = window.setInterval(reload, pollMs);
    return () => window.clearInterval(t);
  }, [pollMs, reload]);

  return { data, error, loading, reload, setData };
}

// ---------------------------------------------------------------- formatting

export const pct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(digits)}%`;

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const humanize = (s: string) => s.replace(/_/g, " ");

const CATEGORY_LABELS: Record<string, string> = {
  bad_deploy: "Bad deploy", config_change: "Config change", memory_leak: "Memory leak", crash_loop: "Crash loop",
  cpu_saturation: "CPU saturation", network_latency: "Network latency", dependency_unavailable: "Dependency unavailable",
  datastore_degradation: "Datastore degradation", resource_exhaustion: "Resource exhaustion", queue_backlog: "Queue backlog",
  probe_failure: "Probe failure", unknown: "Unknown",
};
export const categoryLabel = (c: string) => CATEGORY_LABELS[c] ?? humanize(c);

export function metricValue(unit: string, v: number): string {
  if (unit === "ratio") return `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`;
  if (unit === "ms") return `${Math.round(v)}ms`;
  if (unit === "req/s") return `${v.toFixed(1)}/s`;
  return Math.round(v).toLocaleString();
}

export const METRIC_LABELS: Record<string, string> = {
  request_rate: "Request rate",
  error_rate: "Error rate",
  p95_latency_ms: "p95 latency",
  cpu_utilization: "CPU (of limit)",
  memory_utilization: "Memory (of limit)",
  consumer_lag: "Consumer lag",
};

export const SOURCE_LABELS: Record<string, string> = {
  traces: "Traces",
  metrics: "Metrics",
  logs: "Logs",
  k8s_events: "Kubernetes",
  changes: "Changes",
  knowledge: "Knowledge",
};

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function readTheme(): "light" | "dark" | null {
  try {
    const v = localStorage.getItem("theme");
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function writeTheme(v: "light" | "dark" | null) {
  try {
    if (v) localStorage.setItem("theme", v);
    else localStorage.removeItem("theme");
  } catch {
    /* storage unavailable */
  }
  if (v) document.documentElement.setAttribute("data-theme", v);
  else document.documentElement.removeAttribute("data-theme");
}
