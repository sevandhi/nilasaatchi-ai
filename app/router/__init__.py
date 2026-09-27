"""NilaSaatchi free-model router.

Public contract (other agents code against this):

    from app.router import call, RouterResult
    call(task: str, payload: dict, *, schema: dict | None = None, privacy_tier: str = "PUBLIC",
         images: list[bytes] | None = None, producer_vendor: str | None = None) -> RouterResult

RouterResult: ok, model_id, text, data (schema-validated JSON), latency_ms, tokens_in, tokens_out,
shadow_cost_usd, route_log (candidates, filtered with reasons, scores, choice, attempts), error.

Payload keys: prompt, system, messages, max_tokens, temperature, hard_replan, step_id, run_id;
any other keys are sent to the model as JSON context. Images: PNG/JPEG/WEBP/TIFF/PDF bytes.
Pinning (official): call(..., only_model="<registry id>") restricts the task's chain to that one model
(any registry id; privacy, modality, quota, breaker, spend guard and D-017 still apply; no fallback).
Exclusion: call(..., exclude_models=[ids]) drops models from the chain (logged in route_log.filtered).
Tasks with `strict_order: true` try models in chain order (scores are only logged).
Deferred Bedrock batches (D-042, infra/bedrock_batch/SPEC.md): ROUTER_BEDROCK_MODE=live|collect|cache_only
+ ROUTER_BATCH_ID. In collect (and on a cache_only miss) a Bedrock call is written to
data/bedrock_batch/<id>/requests.jsonl and returns ok=False, error_kind="deferred", with NO fallback unless
call(..., allow_fallback_on_defer=True). Import responses with `python -m app.router.batch_import <id>`.
Embeddings raise app.router.embeddings.EmbeddingDeferred for uncached inputs in those modes.
RouterResult.error_kind: "deferred" | "no_eligible" | "all_failed" | None.
Critic calls (tasks with exclude_producer_vendor) MUST pass producer_vendor (D-017), e.g.
vendor_of(producer_result.model_id); otherwise RouterConfigError is raised.
Privacy: PII only reaches models with trains_on_free_tier resolved False; unknown tier => PII.
Env: ROUTER_MODE=live|record|replay, ROUTER_CHAOS, ROUTER_DATA_DIR, ROUTER_FIXTURE_DIR,
ROUTER_REGISTRY, ROUTER_USE_DEMO_RESERVE=1, ROUTER_USE_DOCTOR=0.
"""
from .core import Router, call, get_router, probe, reset_default_router, set_router, vendor_of
from .types import PrivacyViolation, ProviderError, RouterConfigError, RouterResult

__all__ = [
           "PrivacyViolation",
           "ProviderError",
           "Router",
           "RouterConfigError",
           "RouterResult",
           "call",
           "get_router",
           "probe",
           "reset_default_router",
           "set_router",
           "vendor_of",
]
