"""AWS spend guard (D-028): refuse capped_fallback (Bedrock) calls once estimated cumulative AWS spend
reaches `threshold` (80%) of AWS_SPEND_CAP_USD ($15).

Estimate (conservative):
    spend = ce_total (cached Cost Explorer, refreshed at most hourly)
          + local router spend recorded in quota.sqlite since (ce_fetched_at - 24h)   # CE lags ~24h
If Cost Explorer has never been read (or refresh is disabled), spend = all local router spend on
AWS-billed ids. Cost Explorer costs $0.01/request, so it is read lazily, only when a capped call is
about to be made and the cache is older than `refresh_s`. Disable with ROUTER_AWS_CE_REFRESH=0.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .clock import Clock
from .quota import QuotaDB
from .secrets import redact

CE_LAG_S = 86400.0


@dataclass
class SpendStatus:
    ok: bool
    spend_usd: float
    cap_usd: float
    threshold: float
    source: str
    reason: str | None = None


def default_ce_fetch() -> float:
    """Project-to-date UnblendedCost from Cost Explorer (same logic as scripts/aws_cost.py)."""
    import datetime as dt

    import boto3

    start = os.environ.get("AWS_PROJECT_START", "2026-09-01")
    ce = boto3.Session(profile_name=os.environ.get("AWS_COST_PROFILE", "fai-cost")).client(
        "ce", region_name="us-east-1")
    end = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=2)).isoformat()
    r = ce.get_cost_and_usage(TimePeriod={"Start": start, "End": end}, Granularity="MONTHLY",
                              Metrics=["UnblendedCost"])
    return sum(float(p["Total"]["UnblendedCost"]["Amount"]) for p in r["ResultsByTime"])


class SpendGuard:
    def __init__(self, quota: QuotaDB, clock: Clock | None = None, cache_path: str | Path | None = None,
                 aws_ids: Callable[[], set[str]] | set[str] | None = None,
                 ce_fetch: Callable[[], float] | None = None, cap_usd: float | None = None,
                 threshold: float = 0.80, refresh_s: float = 3600.0):
        self.quota = quota
        self.clock = clock or Clock()
        self.cache_path = Path(cache_path) if cache_path else quota.path.parent / "aws_spend.json"
        self._aws_ids = aws_ids or set()
        self.ce_fetch = ce_fetch
        self._cap = cap_usd
        self.threshold = threshold
        self.refresh_s = refresh_s
        self.last_error: str | None = None

    @property
    def cap_usd(self) -> float:
        if self._cap is not None:
            return float(self._cap)
        try:
            return float(os.environ.get("AWS_SPEND_CAP_USD", "15"))
        except ValueError:
            return 15.0

    def aws_ids(self) -> set[str]:
        return set(self._aws_ids() if callable(self._aws_ids) else self._aws_ids)

    # -- Cost Explorer cache ---------------------------------------------------------------------
    def _read_cache(self) -> dict | None:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return None

    def refresh_if_stale(self, force: bool = False) -> None:
        if self.ce_fetch is None:
            return
        c = self._read_cache()
        if not force and c and self.clock.now() - float(c.get("fetched_at", 0)) < self.refresh_s:
            return
        try:
            total = float(self.ce_fetch())
        except Exception as e:  # noqa: BLE001  expired SSO etc. -> keep previous cache / local estimate
            self.last_error = redact(f"{type(e).__name__}: {e}")[:200]
            if c is None or force:
                # remember the failed attempt so we don't retry (and pay) on every call
                self._write({"fetched_at": self.clock.now(), "total_usd": (c or {}).get("total_usd"),
                             "error": self.last_error})
            return
        self._write({"fetched_at": self.clock.now(), "total_usd": total})

    def _write(self, d: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(d))

    # -- estimate --------------------------------------------------------------------------------
    def estimate(self) -> tuple[float, str]:
        ids = self.aws_ids()
        c = self._read_cache()
        if c and c.get("total_usd") is not None:
            since = float(c["fetched_at"]) - CE_LAG_S
            local = self.quota.cost_since(ids, since)
            return round(float(c["total_usd"]) + local, 6), "cost_explorer+local"
        return round(self.quota.cost_since(ids, 0.0), 6), "local"

    def status(self, refresh: bool = True) -> SpendStatus:
        if refresh:
            self.refresh_if_stale()
        spend, src = self.estimate()
        limit = self.threshold * self.cap_usd
        ok = spend < limit
        reason = None if ok else (f"quota:aws_spend ${spend:.2f} >= {int(self.threshold * 100)}% of "
                                  f"${self.cap_usd:g} cap (D-028: ask the user)")
        return SpendStatus(ok, spend, self.cap_usd, self.threshold, src, reason)
