"""Quota DB (data/quota.sqlite): rolling RPM/RPD/TPM/TPD/monthly windows, reserve/reconcile.

Before each call the router reserves the estimated tokens (one request row); afterwards it
reconciles the row with the actual tokens and cost. Daily windows keep a reserve fraction
(default 10%) for the demo unless `ROUTER_USE_DEMO_RESERVE=1`.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .clock import Clock

MINUTE, DAY, MONTH = 60.0, 86400.0, 30 * 86400.0

# limit key -> (window seconds, measure, is_daily_or_longer)
WINDOWS: dict[str, tuple[float, str, bool]] = {
    "rpm": (MINUTE, "requests", False),
    "tpm": (MINUTE, "tokens", False),
    "otpm": (MINUTE, "tokens_out", False),     # Groq output-tokens-per-minute (counts max_tokens)
    "rpd": (DAY, "requests", True),
    "tpd": (DAY, "tokens", True),
    "monthly_calls": (MONTH, "requests", True),
    "monthly_credit_usd": (MONTH, "cost", True),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id TEXT NOT NULL,
  ts REAL NOT NULL,
  est_tokens INTEGER NOT NULL DEFAULT 0,
  tokens INTEGER,
  est_out INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER,
  cost_usd REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'reserved'
);
CREATE INDEX IF NOT EXISTS usage_model_ts ON usage(model_id, ts);
"""


@dataclass
class QuotaCheck:
    ok: bool
    reason: str | None
    pressure: float          # max used/limit over all windows, 0..1


class QuotaDB:
    def __init__(self, path: str | Path, clock: Clock | None = None, reserve_fraction: float = 0.10):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or Clock()
        self.reserve_fraction = reserve_fraction
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(usage)")}
            for col, ddl in (("est_out", "INTEGER NOT NULL DEFAULT 0"), ("tokens_out", "INTEGER")):
                if col not in cols:
                    c.execute(f"ALTER TABLE usage ADD COLUMN {col} {ddl}")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # -- measurements ---------------------------------------------------------------------
    def used(self, model_id: str, key: str) -> float:
        window, measure, _ = WINDOWS[key]
        since = self.clock.now() - window
        col = {"requests": "COUNT(*)", "tokens": "COALESCE(SUM(COALESCE(tokens, est_tokens)),0)",
               "tokens_out": "COALESCE(SUM(COALESCE(tokens_out, est_out)),0)",
               "cost": "COALESCE(SUM(cost_usd),0)"}[measure]
        with self._conn() as c:
            row = c.execute(f"SELECT {col} FROM usage WHERE model_id=? AND ts>? AND status!='rejected'",
                            (model_id, since)).fetchone()
        return float(row[0] or 0)

    def total_cost(self, model_id: str) -> float:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage WHERE model_id=?",
                            (model_id,)).fetchone()
        return float(row[0] or 0)

    def cost_since(self, model_ids: set[str], since: float) -> float:
        if not model_ids:
            return 0.0
        q = ",".join("?" * len(model_ids))
        with self._conn() as c:
            row = c.execute(f"SELECT COALESCE(SUM(cost_usd),0) FROM usage WHERE model_id IN ({q}) AND ts>?",
                            (*sorted(model_ids), since)).fetchone()
        return float(row[0] or 0)

    def record(self, model_id: str, tokens: int, cost_usd: float, status: str = "ok") -> None:
        """One-shot usage row (e.g. embeddings)."""
        self.reconcile(self.reserve(model_id, tokens), tokens, cost_usd, status, tokens_out=0)

    def _use_reserve(self) -> bool:
        return os.environ.get("ROUTER_USE_DEMO_RESERVE", "0") == "1"

    def check(self, model_id: str, limits: dict, est_tokens: int, cap_usd: float | None = None,
              est_out: int = 0) -> QuotaCheck:
        pressure = 0.0
        reason = None
        for key, limit in (limits or {}).items():
            if key not in WINDOWS or not limit:
                continue
            _, measure, daily = WINDOWS[key]
            used = self.used(model_id, key)
            pressure = max(pressure, min(1.0, used / float(limit)))
            add = {"requests": 1, "tokens": est_tokens, "tokens_out": est_out, "cost": 0}[measure]
            allowed = float(limit)
            if daily and not self._use_reserve():
                allowed = float(limit) * (1.0 - self.reserve_fraction)
            if reason is None and used + add > allowed:
                reason = f"quota:{key} {used:g}+{add:g}>{allowed:g}"
        if cap_usd is not None:
            spent = self.total_cost(model_id)
            pressure = max(pressure, min(1.0, spent / cap_usd if cap_usd else 1.0))
            if reason is None and spent >= cap_usd:
                reason = f"quota:cap_usd {spent:.4f}>={cap_usd:g}"
        return QuotaCheck(ok=reason is None, reason=reason, pressure=round(pressure, 6))

    def pressure(self, model_id: str, limits: dict, cap_usd: float | None = None) -> float:
        return self.check(model_id, limits, 0, cap_usd).pressure

    # -- reserve / reconcile --------------------------------------------------------------
    def reserve(self, model_id: str, est_tokens: int, est_out: int = 0) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute("INSERT INTO usage(model_id, ts, est_tokens, est_out, status)"
                            " VALUES (?,?,?,?,'reserved')",
                            (model_id, self.clock.now(), int(est_tokens), int(est_out)))
            return int(cur.lastrowid)

    def reconcile(self, reservation_id: int, tokens: int, cost_usd: float = 0.0, status: str = "ok",
                  tokens_out: int | None = None) -> None:
        """status: ok | failed (request counted) | rejected (429: request not counted).
        tokens_out defaults to the reserved est_out (Groq counts requested max_tokens)."""
        with self._lock, self._conn() as c:
            c.execute("UPDATE usage SET tokens=?, cost_usd=?, status=?, tokens_out=COALESCE(?, est_out)"
                      " WHERE id=?", (int(tokens), float(cost_usd), status, tokens_out, reservation_id))
