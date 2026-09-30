from __future__ import annotations

from pathlib import Path

from copilot.config import ROOT, Settings
from copilot.knowledge.store import KnowledgeBase
from copilot.providers.scenarios import load_scenarios

CATALOG = load_scenarios(ROOT / "data" / "scenarios" / "catalog.yaml")
KB = KnowledgeBase(ROOT / "data" / "knowledge")


def build_settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings()
    s.database_path = str(tmp_path / "test.db")
    s.reasoner = "heuristic"
    s.anthropic_api_key = ""
    s.slack_webhook_url = ""
    s.telemetry_mode = "simulated"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def make_service(tmp_path: Path, **overrides):
    from copilot.service import CopilotService
    return CopilotService(build_settings(tmp_path, **overrides))
