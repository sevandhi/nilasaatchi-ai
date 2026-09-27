"""GET /router/models, /router/usage, /router/chains (docs/ui-spec.md "Backend additions needed").
Read-only: this module never calls a model, it only reads config/models.yaml, data/doctor.json and
data/router_log.sqlite (owned by free-model-router / app/router, not edited here)."""
from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi import APIRouter

from app.api.config import REPO_ROOT
from app.api.schemas import RouterChainsResponse, RouterModelsResponse, RouterUsageResponse

router = APIRouter(tags=["router"])

ROUTER_LOG_PATH = REPO_ROOT / "data" / "router_log.sqlite"
DOCTOR_PATH = REPO_ROOT / "data" / "doctor.json"


def _load_registry():
    from app.router.registry import load_registry

    return load_registry()


@router.get("/router/models", response_model=RouterModelsResponse)
async def router_models() -> RouterModelsResponse:
    def _run():
        reg = _load_registry()
        doctor = json.loads(DOCTOR_PATH.read_text()) if DOCTOR_PATH.exists() else None
        doctor_by_id = {p.get("id"): p for p in (doctor or {}).get("models", [])} if doctor else {}
        models = []
        for mid, spec in sorted(reg.models.items()):
            d = spec.model_dump(mode="json")
            d["trains_on_free_tier"] = spec.trains_on_free_tier()
            d["doctor"] = doctor_by_id.get(mid)
            models.append(d)
        return models, doctor

    models, doctor = await asyncio.to_thread(_run)
    return RouterModelsResponse(models=models, doctor=doctor)


@router.get("/router/chains", response_model=RouterChainsResponse)
async def router_chains() -> RouterChainsResponse:
    def _run():
        reg = _load_registry()
        return [{"task": name, **spec.model_dump(mode="json")} for name, spec in sorted(reg.tasks.items())]

    tasks = await asyncio.to_thread(_run)
    return RouterChainsResponse(tasks=tasks)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(pct / 100 * (len(values) - 1))))
    return values[k]


@router.get("/router/usage", response_model=RouterUsageResponse)
async def router_usage(last_n: int = 20000) -> RouterUsageResponse:
    def _run():
        if not ROUTER_LOG_PATH.exists():
            return [], {}
        conn = sqlite3.connect(ROUTER_LOG_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT model_id, task, outcome, latency_ms, shadow_cost_usd, actual_cost_usd, "
                "privacy_tier FROM router_log ORDER BY id DESC LIMIT ?", (last_n,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    rows = await asyncio.to_thread(_run)
    if not rows:
        return RouterUsageResponse(by_model_task=[], latency=[],
                                   pii_audit={"pii_calls_ok": 0, "pii_to_training_tier": 0, "note": "no router_log.sqlite"})

    reg = await asyncio.to_thread(_load_registry)

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["model_id"], r["task"]), []).append(r)

    by_model_task = []
    latency = []
    for (model_id, task), grp in sorted(groups.items(), key=lambda kv: (-len(kv[1]))):
        n = len(grp)
        n_ok = sum(1 for r in grp if r["outcome"] == "ok")
        n_fallback = sum(1 for r in grp if r["outcome"] not in ("ok", None))
        shadow = sum(r["shadow_cost_usd"] or 0 for r in grp)
        actual = sum(r["actual_cost_usd"] or 0 for r in grp)
        by_model_task.append({
            "model_id": model_id, "task": task, "calls": n, "success_rate": round(n_ok / n, 3) if n else None,
            "fallback_rate": round(n_fallback / n, 3) if n else None,
            "shadow_cost_usd": round(shadow, 6), "actual_cost_usd": round(actual, 6),
        })
        lat = [r["latency_ms"] for r in grp if r["latency_ms"] is not None]
        latency.append({"model_id": model_id, "task": task, "n": len(lat),
                        "p50_ms": _percentile(lat, 50), "p95_ms": _percentile(lat, 95)})

    pii_ok = [r for r in rows if r["privacy_tier"] == "PII" and r["outcome"] == "ok"]
    training_tier_hits, unknown_model_ids = [], []
    for r in pii_ok:
        try:
            spec = reg.model(r["model_id"])
        except Exception:  # noqa: BLE001 - stale/retired model id no longer in config/models.yaml
            unknown_model_ids.append({"model_id": r["model_id"], "task": r["task"]})
            continue
        if spec.trains_on_free_tier():
            training_tier_hits.append({"model_id": r["model_id"], "task": r["task"]})
    pii_audit = {"pii_calls_ok": len(pii_ok), "pii_to_training_tier": len(training_tier_hits),
                "offenders": training_tier_hits[:20], "unknown_model_ids": unknown_model_ids[:20]}
    return RouterUsageResponse(by_model_task=by_model_task, latency=latency, pii_audit=pii_audit)
