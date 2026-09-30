"""MCP server: exposes the copilot's telemetry tools and investigations to any MCP client
(Claude Desktop, Claude Code, ...).

    python -m copilot.mcp_server            # stdio transport

Example Claude Desktop config:
    {"mcpServers": {"oncall-copilot": {"command": "python", "args": ["-m", "copilot.mcp_server"],
                                        "cwd": "/path/to/oncall-copilot"}}}
"""
from __future__ import annotations

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from .models import Alert
from .providers import make_provider
from .providers.base import Window
from .service import CopilotService

mcp = FastMCP("oncall-copilot")
_service: CopilotService | None = None


def service() -> CopilotService:
    global _service
    if _service is None:
        _service = CopilotService()
    return _service


def _provider(scenario_id: str | None):
    s = service()
    sc = s.scenarios.get(scenario_id) if scenario_id else None
    return make_provider(s.settings, sc, mode="simulated" if sc else None)


def _window(minutes: int) -> Window:
    return Window(datetime.now(timezone.utc), lookback_minutes=minutes)


def _render(items) -> list[dict]:
    return [{"id": e.id, "service": e.service, "severity": e.severity, "title": e.title, "detail": e.detail,
             "data": {k: v for k, v in e.data.items() if k != "series"}} for e in items]


@mcp.tool()
def get_service_metrics(service_name: str, scenario_id: str | None = None, minutes: int = 45) -> list[dict]:
    """RED metrics (rate, errors, p95) plus CPU/memory for one service. Pass scenario_id to query a simulated incident."""
    return _render(_provider(scenario_id).get_metrics(service_name, _window(minutes)))


@mcp.tool()
def get_service_traces(service_name: str, scenario_id: str | None = None) -> list[dict]:
    """Trace summary for a service: failing-trace share, where errors originate, slow spans."""
    return _render(_provider(scenario_id).get_traces(service_name, _window(45)))


@mcp.tool()
def get_service_logs(service_name: str, scenario_id: str | None = None) -> list[dict]:
    """Error and warning log patterns for a service, clustered and counted."""
    return _render(_provider(scenario_id).get_logs(service_name, _window(45)))


@mcp.tool()
def get_cluster_events(scenario_id: str | None = None) -> list[dict]:
    """Kubernetes warning events and unhealthy pods (restarts, OOMKilled, CrashLoopBackOff) in the namespace."""
    return _render(_provider(scenario_id).get_k8s_events(_window(45)))


@mcp.tool()
def get_recent_changes(scenario_id: str | None = None) -> list[dict]:
    """Deploys, config/env/limit changes and scaling in the last 45 minutes."""
    return _render(_provider(scenario_id).get_changes(_window(45)))


@mcp.tool()
def search_runbooks(query: str, k: int = 5) -> list[dict]:
    """Search postmortems, runbooks and confirmed past incidents."""
    return [{**d.as_dict(), "score": s} for d, s in service().kb.search(query, k=k)]


@mcp.tool()
def investigate(service_name: str, alert_name: str, summary: str = "") -> dict:
    """Run a full investigation for an alert and return the root-cause hypothesis."""
    inv = service().create_investigation(Alert(name=alert_name, service=service_name, summary=summary), background=False)
    return {"id": inv.id, "status": inv.status.value,
            "hypothesis": inv.hypothesis.model_dump(mode="json") if inv.hypothesis else None, "error": inv.error}


@mcp.tool()
def list_scenarios() -> list[dict]:
    """Incident scenarios available for simulated investigations."""
    return [s.public_view() for s in service().scenarios.values()]


if __name__ == "__main__":
    mcp.run()
