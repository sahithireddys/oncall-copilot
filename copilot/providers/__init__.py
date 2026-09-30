from __future__ import annotations

from ..config import Settings
from .base import TelemetryProvider, Window
from .scenarios import Scenario
from .simulated import SimulatedProvider

_live_singleton = None


def make_provider(settings: Settings, scenario: Scenario | None = None, mode: str | None = None) -> TelemetryProvider:
    """`mode` defaults to TELEMETRY_MODE. In live mode the scenario is only used for grading."""
    global _live_singleton
    if (mode or settings.telemetry_mode) == "simulated":
        return SimulatedProvider(scenario, lookback=settings.investigation_window_minutes)
    if _live_singleton is None:
        from .live import LiveProvider
        _live_singleton = LiveProvider(settings)
    return _live_singleton


__all__ = ["make_provider", "TelemetryProvider", "Window", "SimulatedProvider", "Scenario"]
