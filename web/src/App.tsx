import { useState } from "react";
import { api } from "./api";
import { Icon } from "./components/ui";
import { linkProps, readTheme, useAsync, useLocation, writeTheme } from "./lib";
import Evals from "./pages/Evals";
import Incidents from "./pages/Incidents";
import InvestigationPage from "./pages/Investigation";
import Knowledge from "./pages/Knowledge";
import Scenarios from "./pages/Scenarios";

const NAV = [
  { to: "/", label: "Incidents", icon: Icon.list },
  { to: "/scenarios", label: "Scenarios", icon: Icon.flask },
  { to: "/evals", label: "Evaluation", icon: Icon.chart },
  { to: "/knowledge", label: "Knowledge", icon: Icon.book },
];

export default function App() {
  const { path } = useLocation();
  const config = useAsync(() => api.config(), []);
  const [theme, setTheme] = useState(readTheme());

  let page;
  const inv = path.match(/^\/investigations\/([\w-]+)/);
  if (inv) page = <InvestigationPage id={inv[1]} />;
  else if (path.startsWith("/scenarios")) page = <Scenarios config={config.data} />;
  else if (path.startsWith("/evals")) page = <Evals config={config.data} />;
  else if (path.startsWith("/knowledge")) page = <Knowledge />;
  else page = <Incidents />;

  const active = (to: string) => (to === "/" ? path === "/" || path.startsWith("/investigations") : path.startsWith(to));
  const isDark = theme === "dark" || (theme === null && window.matchMedia?.("(prefers-color-scheme: dark)").matches);

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <a className="brand" {...linkProps("/")} style={{ color: "inherit", textDecoration: "none" }}>
          <span className="brand-mark" style={{ color: "#fff" }}>{Icon.pulse(16)}</span>
          On-call Copilot
        </a>
        {NAV.map((n) => (
          <a key={n.to} className={`nav-link ${active(n.to) ? "active" : ""}`} {...linkProps(n.to)}>
            {n.icon(16)}{n.label}
          </a>
        ))}
        <div className="sidebar-foot">
          {config.data ? (
            <>
              <div className="row" style={{ gap: 6 }}>
                <span className="dot" style={{ background: "var(--good)" }} />Backend connected
              </div>
              <div>Telemetry: <strong className="secondary">{config.data.telemetry_mode}</strong></div>
              <div>Default reasoner: <strong className="secondary">{config.data.reasoner === "claude" ? "Claude" : "rule-based"}</strong></div>
              {config.data.model && <div className="mono truncate" title={config.data.model}>{config.data.model}</div>}
            </>
          ) : config.error ? (
            <div className="row" style={{ gap: 6 }}><span className="dot" style={{ background: "var(--critical)" }} />Backend offline</div>
          ) : null}
          <button className="btn btn-sm" style={{ alignSelf: "flex-start", marginTop: 6 }}
            onClick={() => { const next = isDark ? "light" : "dark"; writeTheme(next); setTheme(next); }}>
            {isDark ? Icon.sun() : Icon.moon()}{isDark ? "Light" : "Dark"} mode
          </button>
        </div>
      </nav>
      <main className="main">{page}</main>
    </div>
  );
}
