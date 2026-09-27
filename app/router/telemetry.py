"""Per-call telemetry (router_log in data/router_log.sqlite) and measured p50 latency."""
from __future__ import annotations

import json
import sqlite3
import statistics
import threading
from pathlib import Path

from .clock import Clock
from .secrets import redact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  step_id TEXT, run_id TEXT, task TEXT, model_id TEXT, attempt INTEGER,
  outcome TEXT, error_kind TEXT, latency_ms INTEGER,
  tokens_in INTEGER, tokens_out INTEGER,
  shadow_cost_usd REAL, actual_cost_usd REAL,
  privacy_tier TEXT, mode TEXT,
  record_json TEXT
);
CREATE INDEX IF NOT EXISTS router_log_model ON router_log(model_id, outcome, ts);
"""


class Telemetry:
    def __init__(self, path: str | Path, clock: Clock | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or Clock()
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def log(self, rec: dict) -> None:
        """`rec` follows the skill's telemetry shape; stored redacted."""
        body = redact(json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False))
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO router_log(ts, step_id, run_id, task, model_id, attempt, outcome, error_kind,"
                " latency_ms, tokens_in, tokens_out, shadow_cost_usd, actual_cost_usd, privacy_tier, mode,"
                " record_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.clock.now(), rec.get("step_id"), rec.get("run_id"), rec.get("task"),
                 rec.get("choice"), rec.get("attempt"), rec.get("outcome"), rec.get("error_kind"),
                 rec.get("latency_ms"), rec.get("tokens_in"), rec.get("tokens_out"),
                 rec.get("shadow_cost_usd"), rec.get("actual_cost_usd"), rec.get("privacy_tier"),
                 rec.get("mode"), body))

    def p50(self, model_id: str, min_samples: int = 3, last_n: int = 50) -> float | None:
        with self._conn() as c:
            rows = c.execute("SELECT latency_ms FROM router_log WHERE model_id=? AND outcome='ok'"
                             " ORDER BY id DESC LIMIT ?", (model_id, last_n)).fetchall()
        vals = [r[0] for r in rows if r[0] is not None]
        if len(vals) < min_samples:
            return None
        return float(statistics.median(vals))

    def rows(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT record_json FROM router_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]
