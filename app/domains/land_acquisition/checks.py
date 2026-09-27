"""Deterministic checks specific to land acquisition (plan §5.7): extent vs GIS, compensation arithmetic with
either acre factor, stage-order legality, lifecycle recount, DiD consistency, satellite count recount."""
from __future__ import annotations

from app.agent.state import CheckResult, Claim
from app.tools.db import query

AC_FACTORS = (2.47, 2.47105)


def extent_vs_gis(c: Claim, dsn: str) -> CheckResult:
    uid = c.context.get("parcel_uid") or c.subject
    r = query(dsn, "SELECT round(geo_area_ha(geom)::numeric, 4) AS ha FROM parcel WHERE parcel_uid = %s", (uid,))
    if not r:
        return CheckResult(name="extent_vs_gis", passed=False, detail="parcel not found")
    gis = float(r[0]["ha"])
    doc = float(c.context.get("doc_ha") or 0)
    rel = abs(doc - gis) / gis if gis else 0
    ok = rel > 0.05 and abs(gis - float(c.context.get("gis_ha") or gis)) < 0.002
    return CheckResult(name="extent_vs_gis", passed=ok, detail=f"recomputed geodesic {gis} ha vs document {doc} ha ({rel:.1%})")


def compensation_arith(c: Claim, dsn: str) -> CheckResult:
    amt = float(c.context.get("amount_rs") or 0)
    ac = float(c.context.get("extent_ac") or 0)
    rate = float(c.context.get("rate_per_acre") or 0)
    exp = ac * rate
    mismatch = abs(amt - exp) > max(100, 0.005 * exp)
    return CheckResult(name="compensation_arith", passed=mismatch,
                       detail=f"{ac} ac x Rs {rate:,.0f} = Rs {exp:,.0f}; printed Rs {amt:,.0f}")


def ha_ac_consistent(c: Claim, dsn: str) -> CheckResult:
    ha, ac = c.context.get("doc_ha"), c.context.get("extent_ac")
    if ha is None or ac is None:
        return CheckResult(name="ha_ac_consistent", passed=True, detail="n/a")
    tol = 0.02 if ha <= 10 else ha * 0.001 * 2.47
    ok = any(abs(ac - ha * f) <= tol for f in AC_FACTORS)
    return CheckResult(name="ha_ac_consistent", passed=ok, detail=f"{ha} ha vs {ac} ac")


def _events(dsn: str, uid: str) -> list[dict]:
    return query(dsn, "SELECT stage, event_date FROM acquisition_event WHERE parcel_uid = %s ORDER BY event_date", (uid,))


def stage_order_legal(c: Claim, dsn: str) -> CheckResult:
    from app.domains.land_acquisition.tools import MANDATORY, STAGE_ORDER
    ev = _events(dsn, c.subject)
    stages = [e["stage"] for e in ev]
    if not stages:
        return CheckResult(name="stage_order_legal", passed=False, detail="no events on re-query")
    cur = max(stages, key=STAGE_ORDER.index)
    missing = [m for m in MANDATORY if STAGE_ORDER.index(m) < STAGE_ORDER.index(cur) and m not in stages]
    return CheckResult(name="stage_order_legal", passed=bool(missing),
                       detail=f"current {cur}; missing mandatory {missing or 'none'}")


def lifecycle_recount(c: Claim, dsn: str) -> CheckResult:
    from app.domains.land_acquisition.tools import LifecycleIn, lifecycle_status

    class _Ctx:
        kg = dsn
    scope = c.context.get("scope") or {}
    res = lifecycle_status(LifecycleIn(**{k: v for k, v in scope.items() if k in ("villages", "blocks", "parcel_uids")},
                                       as_of=c.context.get("as_of"),
                                       stalled_days=c.context.get("stalled_days", 90)), _Ctx())
    flag = c.context.get("flag", "STALLED")
    if c.kind == "kpi":
        n = sum(1 for p in res.data["parcels"] if flag in p["flags"])
        return CheckResult(name="lifecycle_recount", passed=n == c.value, detail=f"recount {flag}={n}")
    has = any(flag in p["flags"] for p in res.data["parcels"] if p["parcel_uid"] == c.subject)
    return CheckResult(name="lifecycle_recount", passed=has, detail=f"{flag} {'confirmed' if has else 'not reproduced'}")


def did_ci_consistent(c: Claim, dsn: str) -> CheckResult:
    did, ci, p = c.context.get("did"), c.context.get("ci") or [None, None], c.context.get("farmland_like_p")
    ok = None not in (did, ci[0], ci[1], p) and ci[0] <= did <= ci[1] and p >= 0.7
    return CheckResult(name="did_ci_consistent", passed=bool(ok), detail=f"did={did} ci={ci} p_farmland={p}")


def satellite_counts_recount(c: Claim, dsn: str) -> CheckResult:
    r = query(dsn, "SELECT count(*) FILTER (WHERE state='irrigated_multi') AS n_irrig, count(*) FILTER (WHERE state='cropped') AS n_crop "
                   "FROM parcel_season WHERE parcel_uid = %s AND ag_year BETWEEN 2019 AND 2021 AND season <> 'annual'",
              (c.subject,))
    ok = r and r[0]["n_irrig"] == c.context.get("irrigated_multi_seasons") and r[0]["n_crop"] == c.context.get("cropped_seasons")
    return CheckResult(name="satellite_counts_recount", passed=bool(ok), detail=f"recount {r[0] if r else None}")


def did_ci_contains(c: Claim, dsn: str) -> CheckResult:
    """DiD point estimate inside its CI; for a signed signal the CI must exclude 0 on the claimed side."""
    did, ci = c.context.get("did"), c.context.get("ci") or [None, None]
    if None in (did, ci[0], ci[1]):
        return CheckResult(name="did_ci_contains", passed=False, detail=f"missing did/ci: {did} {ci}")
    ok = float(ci[0]) <= float(did) <= float(ci[1])
    sig = c.context.get("signal")
    if sig == "vigour_drop":
        ok = ok and float(ci[1]) < 0
    return CheckResult(name="did_ci_contains", passed=ok, detail=f"did={did} ci={ci} signal={sig}")


def finding_reread(c: Claim, dsn: str) -> CheckResult:
    """Re-read the finding row (agent_ro) and compare every numeric metric the claim states."""
    fid = c.context.get("finding_id")
    r = query(dsn, "SELECT parcel_uid, category, metrics FROM finding WHERE id = %s", (fid,))
    if not r:
        return CheckResult(name="finding_reread", passed=False, detail=f"finding {fid} not found")
    m = r[0]["metrics"] or {}
    bad = [k for k, v in (c.value or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)
           and not (isinstance(m.get(k), (int, float)) and abs(float(m[k]) - float(v)) <= 1e-6 * max(1, abs(float(v))))]
    ok = not bad and r[0]["category"] == c.field
    return CheckResult(name="finding_reread", passed=ok, detail="metrics re-read match" if ok else f"differs: {bad[:4]}")


LAND_CHECKS = {f.__name__: f for f in (extent_vs_gis, compensation_arith, ha_ac_consistent, stage_order_legal,
                                       lifecycle_recount, did_ci_consistent, satellite_counts_recount,
                                       did_ci_contains, finding_reread)}
