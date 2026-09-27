"""Deterministic scorer (plan.md §5.6):
    score = 0.35·capability + 0.25·confidence − 0.15·latency − 0.10·shadow_cost − 0.15·quota_pressure
All terms are normalised to [0,1]; totals are rounded to 6 d.p. and ties broken by chain order,
so the same registry + telemetry + quota snapshot always yields the same ranking.
"""
from __future__ import annotations

from .eligibility import Candidate
from .registry import ModelSpec, RouterSettings


def estimate_shadow_cost(spec: ModelSpec, tokens_in: int, tokens_out: int, pages: int = 0) -> float:
    if spec.list_price_per_page_usd:
        return round(pages * spec.list_price_per_page_usd, 8)
    return round(tokens_in * spec.price_in() / 1e6 + tokens_out * spec.price_out() / 1e6, 8)


def actual_cost(spec: ModelSpec, shadow: float, pages: int) -> float:
    if spec.actual_cost_per_page_usd:
        return round(pages * spec.actual_cost_per_page_usd, 8)
    if spec.actual_cost_is_shadow:
        return shadow
    return 0.0


def score(c: Candidate, settings: RouterSettings, p50_ms: float | None, est_in: int, est_out: int,
          n_images: int = 0) -> dict:
    w = settings.weights
    spec = c.spec
    lat_ms = p50_ms if p50_ms is not None else spec.p50_ms_default
    comp = {
        "capability": round(max(0.0, min(1.0, c.entry.capability)), 6),
        "confidence": round(max(0.0, min(1.0, spec.expected_confidence)), 6),
        "latency": round(min(1.0, lat_ms / settings.latency_ref_ms), 6),
        "shadow_cost": round(min(1.0, estimate_shadow_cost(spec, est_in, est_out, max(1, n_images))
                                 / settings.cost_ref_usd), 6),
        "quota_pressure": round(c.quota_pressure, 6),
    }
    total = sum(w.get(k, 0.0) * v for k, v in comp.items())
    return {"total": round(total, 6), "components": comp,
            "p50_ms": lat_ms, "p50_source": "measured" if p50_ms is not None else "registry"}


def rank(cands: list[Candidate], scores: dict[str, dict], strict_order: bool = False) -> list[Candidate]:
    if strict_order:
        return sorted(cands, key=lambda c: c.index)
    return sorted(cands, key=lambda c: (-scores[c.spec.id]["total"], c.index))
