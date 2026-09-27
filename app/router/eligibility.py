"""Hard eligibility filters (plan.md §5.6): condition, modality, privacy (fail closed), critic
vendor independence, context length, credentials, doctor status, circuit breaker, quota reserve.

Every rejected candidate is reported with ALL its reasons so the route log explains the choice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .breaker import CircuitBreaker
from .quota import QuotaDB
from .registry import ChainEntry, ModelSpec, Registry, TaskSpec


@dataclass
class FilterContext:
    tier: str                                  # effective tier: PII | PSEUDO | PUBLIC
    required_modalities: set[str]
    est_tokens_in: int
    max_tokens: int
    producer_vendor: str | None = None
    flags: dict = field(default_factory=dict)  # payload flags, e.g. hard_replan
    check_credentials: bool = True             # False in replay mode (CI must not depend on keys)
    doctor: dict = field(default_factory=dict) # model_id -> doctor record


@dataclass
class Candidate:
    entry: ChainEntry
    spec: ModelSpec
    index: int
    quota_pressure: float = 0.0


def required_modalities(task: TaskSpec, input_mods: set[str]) -> set[str]:
    req = set(task.requires)
    if input_mods:
        req = (req - {"image", "pdf"}) | input_mods
    return req


def privacy_ok(spec: ModelSpec, tier: str) -> tuple[bool, str | None]:
    if tier == "PII" and spec.trains_on_free_tier():
        raw = spec.trains_on_free_tier_raw
        return False, f"privacy:PII->training-tier (trains_on_free_tier={raw})"
    return True, None


def filter_candidates(reg: Registry, task: TaskSpec, ctx: FilterContext, breaker: CircuitBreaker,
                      quota: QuotaDB, credentials=None, spend=None) -> tuple[list[Candidate], list[dict]]:
    eligible: list[Candidate] = []
    filtered: list[dict] = []
    for i, entry in enumerate(task.chain):
        spec = reg.model(entry.model)
        reasons: list[str] = []
        if entry.when and not ctx.flags.get(entry.when):
            reasons.append(f"condition:{entry.when} not set")
        missing = ctx.required_modalities - set(spec.modalities)
        if missing:
            reasons.append(f"modality:missing {sorted(missing)}")
        ok, why = privacy_ok(spec, ctx.tier)
        if not ok:
            reasons.append(why)
        if task.exclude_producer_vendor and ctx.producer_vendor and spec.vendor == ctx.producer_vendor:
            reasons.append(f"vendor:same as producer ({spec.vendor})")
        if spec.context_tokens and ctx.est_tokens_in + ctx.max_tokens > spec.context_tokens:
            reasons.append(f"context:{ctx.est_tokens_in + ctx.max_tokens}>{spec.context_tokens}")
        if ctx.check_credentials and credentials is not None:
            has, why = credentials(spec.provider)
            if not has:
                reasons.append(f"credentials:{why}")
        d = ctx.doctor.get(spec.id)
        if d and d.get("status") == "FAIL" and d.get("error_kind") in ("not_found", "auth"):
            reasons.append(f"doctor:{d.get('error_kind')}")
        allowed, state = breaker.allow(spec.id)
        if not allowed:
            reasons.append(state)
        est = ctx.est_tokens_in + min(ctx.max_tokens, 512)
        otpm = spec.limits.get("otpm")
        est_out = min(ctx.max_tokens, int(otpm * 0.5)) if otpm else ctx.max_tokens
        qc = quota.check(spec.id, spec.limits, est, spec.cap_usd, est_out=est_out)
        if not qc.ok:
            reasons.append(qc.reason)
        if spend is not None and not reasons:
            why = spend(spec)          # AWS spend guard (capped_fallback only); checked last (may hit CE)
            if why:
                reasons.append(why)
        if reasons:
            filtered.append({"id": spec.id, "reason": "; ".join(reasons)})
        else:
            eligible.append(Candidate(entry=entry, spec=spec, index=i, quota_pressure=qc.pressure))
    return eligible, filtered
