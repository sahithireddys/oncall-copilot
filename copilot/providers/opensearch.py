"""Logs from OpenSearch (the OpenTelemetry Demo's log backend), clustered into patterns."""
from __future__ import annotations

import re
from collections import defaultdict

import httpx

from ..models import Evidence
from .base import Window

_NORMALISE = [
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<id>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d+(\.\d+)?(ms|s|m|%|KiB|MiB|GiB)?\b"), "<n>"),
    (re.compile(r"'[^']{1,80}'|\"[^\"]{1,80}\""), "<str>"),
]
LEVELS = {"FATAL": 5, "CRITICAL": 5, "ERROR": 4, "WARN": 3, "WARNING": 3, "INFO": 2, "DEBUG": 1}


def to_pattern(message: str) -> str:
    msg = message.strip().splitlines()[0] if message else ""
    for rx, repl in _NORMALISE:
        msg = rx.sub(repl, msg)
    return msg[:200]


def _get(doc: dict, dotted: str):
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, dict) and dotted in cur:  # flattened keys
            return cur[dotted]
        else:
            return None
    return cur


def cluster_logs(service: str, hits: list[dict], window: Window, body_field: str, level_field: str,
                 time_field: str) -> list[Evidence]:
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"count": 0, "sample": "", "first": None})
    from datetime import datetime
    for h in hits:
        src = h.get("_source", h)
        body = str(_get(src, body_field) or "")
        level = str(_get(src, level_field) or "INFO").upper()
        level = "WARN" if level == "WARNING" else level
        key = (level, to_pattern(body))
        g = groups[key]
        g["count"] += 1
        g["sample"] = g["sample"] or body[:400]
        ts = _get(src, time_field)
        if ts:
            try:
                t = window.rel_minutes(datetime.fromisoformat(str(ts).replace("Z", "+00:00")))
                g["first"] = t if g["first"] is None else min(g["first"], t)
            except ValueError:
                pass
    ranked = sorted(groups.items(), key=lambda kv: (-LEVELS.get(kv[0][0], 0), -kv[1]["count"]))[:12]
    out = []
    for i, ((level, pattern), g) in enumerate(ranked):
        sev = "critical" if level in ("ERROR", "FATAL") and g["count"] > 50 else (
            "warning" if level in ("ERROR", "FATAL", "WARN") else "info")
        out.append(Evidence(
            id=f"logs:{service}:{i + 1}", source="logs", service=service,
            title=f"{g['count']:,}× {level} {pattern[:90]}", severity=sev,
            data={"kind": "log_pattern", "pattern": pattern, "level": level, "count": g["count"],
                  "sample": g["sample"], "first_seen_min": g["first"]},
        ))
    return out


class OpenSearchLogs:
    def __init__(self, base_url: str, index: str, service_field: str = "resource.service.name",
                 body_field: str = "body", level_field: str = "severity.text", time_field: str = "@timestamp",
                 client: httpx.Client | None = None, auth: tuple[str, str] | None = None):
        self.base, self.index = base_url.rstrip("/"), index
        self.service_field, self.body_field = service_field, body_field
        self.level_field, self.time_field = level_field, time_field
        self.http = client or httpx.Client(timeout=15, verify=False, auth=auth)

    def get_logs(self, service: str, window: Window) -> list[Evidence]:
        query = {
            "size": 1000,
            "sort": [{self.time_field: "desc"}],
            "query": {"bool": {"filter": [
                {"term": {self.service_field: service}},
                {"range": {self.time_field: {"gte": window.start.isoformat(), "lte": window.end.isoformat()}}},
                {"terms": {self.level_field: ["ERROR", "Error", "error", "WARN", "Warning", "warn", "FATAL", "Fatal"]}},
            ]}},
        }
        r = self.http.post(f"{self.base}/{self.index}/_search", json=query)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        return cluster_logs(service, hits, window, self.body_field, self.level_field, self.time_field)
