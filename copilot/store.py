"""SQLite persistence for investigations, step events, feedback and eval runs."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Feedback, Investigation, StepEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    alert_service TEXT,
    scenario_id TEXT,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inv_created ON investigations(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    node TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_inv ON events(investigation_id, id);
CREATE TABLE IF NOT EXISTS feedback (
    investigation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------ investigations
    def save_investigation(self, inv: Investigation) -> None:
        body = inv.model_dump_json(exclude={"feedback"})
        with self._tx() as c:
            c.execute(
                "INSERT INTO investigations(id, created_at, status, alert_service, scenario_id, body) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                (inv.id, inv.created_at.isoformat(), inv.status.value, inv.alert.service, inv.scenario_id, body),
            )

    def get_investigation(self, inv_id: str) -> Investigation | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM investigations WHERE id=?", (inv_id,)).fetchone()
        if not row:
            return None
        inv = Investigation.model_validate_json(row["body"])
        inv.feedback = self.get_feedback(inv_id)
        return inv

    def list_investigations(self, limit: int = 50, offset: int = 0, status: str | None = None,
                            service: str | None = None) -> list[Investigation]:
        q, args = "SELECT id, body FROM investigations", []
        where = []
        if status:
            where.append("status=?")
            args.append(status)
        if service:
            where.append("alert_service=?")
            args.append(service)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
            fb_rows = self._conn.execute("SELECT investigation_id, body FROM feedback").fetchall()
        feedback = {r["investigation_id"]: Feedback.model_validate_json(r["body"]) for r in fb_rows}
        out = []
        for r in rows:
            inv = Investigation.model_validate_json(r["body"])
            inv.feedback = feedback.get(inv.id)
            out.append(inv)
        return out

    def count_investigations(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM investigations").fetchone()[0]

    # -------------------------------------------------------------------- events
    def add_event(self, ev: StepEvent) -> StepEvent:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO events(investigation_id, ts, node, status, message, data) VALUES (?,?,?,?,?,?)",
                (ev.investigation_id, ev.ts.isoformat(), ev.node, ev.status, ev.message, json.dumps(ev.data, default=str)),
            )
            ev.id = cur.lastrowid
        return ev

    def list_events(self, inv_id: str, after_id: int = 0) -> list[StepEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE investigation_id=? AND id>? ORDER BY id", (inv_id, after_id)).fetchall()
        return [StepEvent(id=r["id"], investigation_id=r["investigation_id"], ts=datetime.fromisoformat(r["ts"]),
                          node=r["node"], status=r["status"], message=r["message"], data=json.loads(r["data"]))
                for r in rows]

    # ------------------------------------------------------------------ feedback
    def save_feedback(self, fb: Feedback) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO feedback(investigation_id, created_at, body) VALUES (?,?,?) "
                      "ON CONFLICT(investigation_id) DO UPDATE SET created_at=excluded.created_at, body=excluded.body",
                      (fb.investigation_id, fb.created_at.isoformat(), fb.model_dump_json()))

    def get_feedback(self, inv_id: str) -> Feedback | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM feedback WHERE investigation_id=?", (inv_id,)).fetchone()
        return Feedback.model_validate_json(row["body"]) if row else None

    def list_feedback(self) -> list[Feedback]:
        with self._lock:
            rows = self._conn.execute("SELECT body FROM feedback ORDER BY created_at").fetchall()
        return [Feedback.model_validate_json(r["body"]) for r in rows]

    # ------------------------------------------------------------------ eval runs
    def save_eval_run(self, run: dict[str, Any]) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO eval_runs(id, created_at, status, body) VALUES (?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                      (run["id"], run.get("created_at") or datetime.now(timezone.utc).isoformat(), run["status"],
                       json.dumps(run, default=str)))

    def get_eval_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM eval_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def list_eval_runs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT body FROM eval_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r["body"]) for r in rows]
