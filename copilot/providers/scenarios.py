"""Incident scenario catalog: each scenario is a fault with a known root cause."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..models import Alert, RootCauseCategory

FaultType = Literal[
    "bad_deploy", "config_change", "memory_leak", "crash_loop", "cpu_saturation",
    "network_latency", "dependency_unavailable", "datastore_degradation",
    "resource_exhaustion", "queue_backlog", "probe_failure",
]
NoiseType = Literal["deploy", "config_change", "log_warn", "log_error", "restart", "event", "latency_blip", "scale"]


class Fault(BaseModel):
    service: str
    type: FaultType
    onset_min: float = -12
    error_rate: float = 0.0          # own server-side error rate after onset
    latency_ms: float = 0.0          # added p95 latency after onset
    error_message: str = ""
    version_from: str | None = None
    version_to: str | None = None
    config_resource: str | None = None
    config_key: str | None = None
    config_old: str | None = None
    config_new: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Noise(BaseModel):
    type: NoiseType
    service: str
    at_min: float = -30
    message: str = ""
    count: int = 1
    version_from: str | None = None
    version_to: str | None = None
    config_key: str | None = None
    config_old: str | None = None
    config_new: str | None = None
    latency_ms: float = 0.0


class GroundTruth(BaseModel):
    service: str
    category: RootCauseCategory
    # Categories that are also a fair description (e.g. a deploy that causes a crash loop).
    also_accept: list[RootCauseCategory] = Field(default_factory=list)
    fix: str = ""
    explanation: str = ""


class Reproduce(BaseModel):
    method: Literal["flagd", "kubectl", "chaos-mesh", "manual"] = "manual"
    flag: str | None = None
    variant: str | None = None
    commands: list[str] = Field(default_factory=list)
    revert: list[str] = Field(default_factory=list)
    manifest: str | None = None
    notes: str = ""


class Scenario(BaseModel):
    id: str
    title: str
    description: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    alert: Alert
    faults: list[Fault]
    noise: list[Noise] = Field(default_factory=list)
    ground_truth: GroundTruth
    reproduce: Reproduce = Field(default_factory=Reproduce)
    tags: list[str] = Field(default_factory=list)
    # add seeded background noise (random unrelated deploy / error logs / restart)
    extra_noise: bool = True

    def public_view(self) -> dict:
        """Scenario info safe to show before an investigation (no ground truth)."""
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "difficulty": self.difficulty, "alert": self.alert.model_dump(mode="json"),
            "tags": self.tags, "reproduce_method": self.reproduce.method,
        }


def load_scenarios(path: str | Path) -> dict[str, Scenario]:
    raw = yaml.safe_load(Path(path).read_text())
    items = raw["scenarios"] if isinstance(raw, dict) else raw
    out: dict[str, Scenario] = {}
    for item in items:
        sc = Scenario.model_validate(item)
        if sc.id in out:
            raise ValueError(f"duplicate scenario id {sc.id}")
        out[sc.id] = sc
    return out


@lru_cache(maxsize=4)
def _cached(path: str) -> dict[str, Scenario]:
    return load_scenarios(path)


def get_catalog(path: str | None = None) -> dict[str, Scenario]:
    from ..config import get_settings
    return _cached(path or get_settings().scenarios_path)
