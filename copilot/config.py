"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no overriding of real env vars."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # "simulated" replays scenario telemetry; "live" talks to a real cluster.
    telemetry_mode: str = field(default_factory=lambda: _env("TELEMETRY_MODE", "simulated"))
    # "claude" uses the Anthropic API; "heuristic" is the offline rule-based baseline.
    reasoner: str = field(default_factory=lambda: _env("REASONER", "claude"))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    llm_max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "2500")))

    database_path: str = field(default_factory=lambda: _env("DATABASE_PATH", str(ROOT / "data" / "copilot.db")))
    scenarios_path: str = field(default_factory=lambda: _env("SCENARIOS_PATH", str(ROOT / "data" / "scenarios" / "catalog.yaml")))
    knowledge_path: str = field(default_factory=lambda: _env("KNOWLEDGE_PATH", str(ROOT / "data" / "knowledge")))

    # Live-mode endpoints (defaults match the OpenTelemetry Demo Helm chart via port-forward).
    k8s_namespace: str = field(default_factory=lambda: _env("K8S_NAMESPACE", "otel-demo"))
    prometheus_url: str = field(default_factory=lambda: _env("PROMETHEUS_URL", "http://localhost:9090"))
    jaeger_url: str = field(default_factory=lambda: _env("JAEGER_URL", "http://localhost:16686/jaeger/ui"))
    opensearch_url: str = field(default_factory=lambda: _env("OPENSEARCH_URL", "http://localhost:9200"))
    opensearch_index: str = field(default_factory=lambda: _env("OPENSEARCH_INDEX", "otel-logs-*"))
    grafana_url: str = field(default_factory=lambda: _env("GRAFANA_URL", "http://localhost:3000/grafana"))

    slack_webhook_url: str = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    public_ui_url: str = field(default_factory=lambda: _env("PUBLIC_UI_URL", "http://localhost:5173"))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))

    max_drilldown_rounds: int = field(default_factory=lambda: int(_env("MAX_DRILLDOWN_ROUNDS", "2")))
    investigation_window_minutes: int = field(default_factory=lambda: int(_env("INVESTIGATION_WINDOW_MINUTES", "45")))

    @property
    def llm_enabled(self) -> bool:
        return self.reasoner == "claude" and bool(self.anthropic_api_key)

    @property
    def effective_reasoner(self) -> str:
        return "claude" if self.llm_enabled else "heuristic"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(**kwargs) -> Settings:
    """Used by tests and the eval CLI."""
    global _settings
    base = get_settings()
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base
