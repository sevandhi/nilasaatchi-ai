"""Generic deterministic checks run by the verify node on tool claims.

A check is ``fn(claim: Claim, kg_dsn: str) -> CheckResult``. Tools name the checks for each claim seed; domain
packs add their own checks (DomainPack.checks). Unknown check names fail closed (passed=False).
"""
from __future__ import annotations

import math
from typing import Any

from app.agent.state import CheckResult, Claim
from app.tools.db import query

_QCACHE: dict[tuple, list[dict]] = {}


def clear_cache() -> None:
    _QCACHE.clear()


def cached_query(dsn: str, sql: str, params=None, trusted: bool = False) -> list[dict]:
    """trusted=True only for tool-authored template SQL (never model-written SQL): runs read-only as the app role."""
    import json as _json
    key = (dsn, sql, _json.dumps(params, default=str), trusted)
    if key not in _QCACHE:
        _QCACHE[key] = query(dsn, sql, params, readonly_role=not trusted)
    return _QCACHE[key]


def _num_eq(a: Any, b: Any, rel: float = 1e-6, abs_: float = 1e-6) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    return math.isclose(fa, fb, rel_tol=rel, abs_tol=abs_)


def count_matches_rows(c: Claim, dsn: str) -> CheckResult:
    ok = c.value == c.context.get("rows")
    return CheckResult(name="count_matches_rows", passed=bool(ok), detail=f"value={c.value} rows={c.context.get('rows')}")


def rerun_sql(c: Claim, dsn: str) -> CheckResult:
    sql, row, col = c.context.get("sql"), c.context.get("row"), c.context.get("column")
    if not sql:
        return CheckResult(name="rerun_sql", passed=False, detail="no SQL in claim context")
    params = c.context.get("params")
    try:
        rows = cached_query(dsn, sql, params or None, trusted=bool(c.context.get("trusted")))
    except Exception as e:  # noqa: BLE001
        return CheckResult(name="rerun_sql", passed=False, detail=f"re-run failed: {str(e)[:160]}")
    if row is None or row >= len(rows) or col not in rows[row]:
        return CheckResult(name="rerun_sql", passed=False, detail=f"row {row} / column {col} missing on re-run")
    v = rows[row][col]
    return CheckResult(name="rerun_sql", passed=_num_eq(v, c.value), detail=f"re-run value={v}")


def union_le_sum(c: Claim, dsn: str) -> CheckResult:
    sql, row = c.context.get("sql"), c.context.get("row")
    rows = cached_query(dsn, sql, c.context.get("params")) if sql else []
    if not rows or row is None or row >= len(rows):
        return CheckResult(name="union_le_sum", passed=False, detail="cannot recompute")
    r = rows[row]
    ok = float(r.get("area_union_ha") or 0) <= float(r.get("area_sum_ha") or 0) + 1e-4
    return CheckResult(name="union_le_sum", passed=ok, detail=f"union={r.get('area_union_ha')} sum={r.get('area_sum_ha')}")


def bbox_contains_value(c: Claim, dsn: str) -> CheckResult:
    """Every page_bbox evidence item must be a well-formed page-fraction bbox whose extraction row holds the value."""
    items = [e for e in c.evidence if e.kind == "page_bbox" and e.ref.startswith("extraction:")]
    if not items:
        return CheckResult(name="bbox_contains_value", passed=True, detail="no page_bbox evidence")
    for e in items:
        b = e.bbox or []
        if len(b) != 4 or not (0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1):
            return CheckResult(name="bbox_contains_value", passed=False, detail=f"bad bbox {b} on {e.ref}")
        xid = int(e.ref.split(":")[1])
        row = query(dsn, "SELECT row_json FROM extraction WHERE id = %s", (xid,))
        if not row:
            return CheckResult(name="bbox_contains_value", passed=False, detail=f"{e.ref} missing")
        rj = row[0]["row_json"] or {}
        vals = {k: v for k, v in (c.value or {}).items()} if isinstance(c.value, dict) else {c.field: c.value}
        keymap = {"doc_ha": "extent_ha", "extent_ac": "extent_ac", "amount_rs": "amount_rs",
                  "rate_per_acre": "rate_per_acre", "classification": "classification"}
        for k, v in vals.items():
            rk = keymap.get(k)
            if rk and rk in rj and not _num_eq(rj[rk], v, rel=1e-6):
                return CheckResult(name="bbox_contains_value", passed=False,
                                   detail=f"{k}={v} not in the evidence row ({rk}={rj[rk]})")
    return CheckResult(name="bbox_contains_value", passed=True, detail=f"{len(items)} bbox(es) hold the value")


def satellite_min_obs(c: Claim, dsn: str) -> CheckResult:
    f = c.context.get("features") or {}
    n, gap = f.get("n_obs"), f.get("max_gap_days")
    ok = (n or 0) >= 2 and f.get("data_sufficient") is not False
    det = f"n_obs={n} max_gap_days={gap}" + (" (gap > 45 d: confidence capped)" if gap and gap > 45 else "")
    return CheckResult(name="satellite_min_obs", passed=ok, detail=det)


def satellite_indices_agree(c: Claim, dsn: str) -> CheckResult:
    f = c.context.get("features") or {}
    amp, bsi = f.get("amplitude"), f.get("bsi_dry_mean")
    if c.value in ("cropped", "irrigated_multi"):
        ok = amp is not None and amp >= 0.15 and (bsi is None or bsi >= -0.05)
        return CheckResult(name="satellite_indices_agree", passed=ok, detail=f"NDVI amplitude={amp} dry BSI={bsi}")
    if c.value == "bare_fallow":
        ok = amp is None or amp < 0.3
        return CheckResult(name="satellite_indices_agree", passed=ok, detail=f"NDVI amplitude={amp}")
    return CheckResult(name="satellite_indices_agree", passed=True, detail=f"state {c.value}: no two-index rule")


def satellite_route_consistent(c: Claim, dsn: str) -> CheckResult:
    route, needs = c.context.get("route"), c.context.get("needs_field")
    ok = route in ("student", "vlm_second_opinion", "rule") and not needs
    return CheckResult(name="satellite_route_consistent", passed=ok, detail=f"route={route} needs_field={needs}")


def crop_share_consistent(c: Claim, dsn: str) -> CheckResult:
    s, cov = c.context.get("crop_share"), c.context.get("coverage") or 0
    exp = ("insufficient_coverage" if cov < 0.5 else None)
    if c.value in ("crop", "no_crop") and s is not None:
        exp = "crop" if s >= 0.5 else "no_crop"
    ok = exp is None or exp == c.value or c.value in ("insufficient_data",)
    return CheckResult(name="crop_share_consistent", passed=ok, detail=f"crop_share={s} coverage={cov}")


def fallow_streak_recount(c: Claim, dsn: str) -> CheckResult:
    seq = [r for r in c.context.get("seasons_table", []) if r.get("ag_year") is not None]
    best = cur = 0
    runs = []
    for r in seq:
        cur = cur + 1 if r["status"] == "no_crop" else 0
        if r["status"] != "no_crop" and cur == 0:
            pass
        runs.append(cur)
        best = max(best, cur)
    ok = c.value in runs and c.value >= c.context.get("min_seasons", 1)
    return CheckResult(name="fallow_streak_recount", passed=ok, detail=f"longest run={best}")


GENERIC_CHECKS = {f.__name__: f for f in (count_matches_rows, rerun_sql, union_le_sum, bbox_contains_value,
                                          satellite_min_obs, satellite_indices_agree, satellite_route_consistent,
                                          crop_share_consistent, fallow_streak_recount)}
