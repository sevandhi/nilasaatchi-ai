"""Router core: eligibility -> deterministic ranking -> attempts with retry, breaker, quota,
schema validation (repair once, then alternate model), telemetry and fixtures."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from . import batch as batch_mod
from . import chaos, fixtures, gateway
from .breaker import CircuitBreaker
from .clock import Clock
from .eligibility import Candidate, FilterContext, filter_candidates, required_modalities
from .jsonutil import parse_and_validate
from .messages import build_messages, estimate_tokens, input_modalities
from .quota import QuotaDB
from .registry import REPO_ROOT, ModelSpec, Registry, load_registry
from .scorer import actual_cost, estimate_shadow_cost, rank, score
from .secrets import redact
from .types import ProviderError, ProviderRequest, ProviderResponse, RouterConfigError, RouterResult


def data_dir() -> Path:
    return Path(os.environ.get("ROUTER_DATA_DIR") or (REPO_ROOT / "data"))


class _Fail(Exception):
    pass


class Router:
    def __init__(self, registry: Registry | None = None, providers: dict | None = None,
                 clock: Clock | None = None, quota: QuotaDB | None = None, telemetry=None,
                 breaker: CircuitBreaker | None = None, credentials=None, doctor: dict | None = None,
                 spend_guard=None,
                 data_path: str | Path | None = None):
        from .telemetry import Telemetry

        self.registry = registry or load_registry()
        self.clock = clock or Clock()
        s = self.registry.router
        d = Path(data_path) if data_path else data_dir()
        self.quota = quota or QuotaDB(d / "quota.sqlite", self.clock, s.daily_reserve_fraction)
        self.telemetry = telemetry or Telemetry(d / "router_log.sqlite", self.clock)
        self.breaker = breaker or CircuitBreaker(self.clock, int(s.breaker.get("failures", 3)),
                                                 float(s.breaker.get("open_seconds", 60)))
        self._providers = providers
        if credentials is None:
            from .providers import credentials_available
            credentials = credentials_available
        self.credentials = credentials
        self.doctor = doctor if doctor is not None else _load_doctor(d)
        from .spend import SpendGuard, default_ce_fetch
        ce = default_ce_fetch if os.environ.get("ROUTER_AWS_CE_REFRESH", "1") != "0" else None
        self.spend = spend_guard or SpendGuard(self.quota, self.clock, d / "aws_spend.json",
                                               aws_ids=self.registry.aws_billed_ids, ce_fetch=ce)
        self._provider_down: dict[str, tuple[float, str]] = {}
        from .batch import BatchStore
        self.batch = BatchStore(d / "bedrock_batch", d / "bedrock_cache.sqlite")
        self._dead_names: set[tuple[str, str]] = set()

    def _pinned_task(self, tspec, model_id: str, in_mods: set[str]):
        """Restrict a task (or an ad-hoc probe) to one registry model; all filters still apply."""
        from .registry import ChainEntry, TaskSpec
        spec = self.registry.model(model_id)             # RouterConfigError if unknown
        if tspec is None:
            req = [] if (in_mods or "text" not in spec.modalities) else ["text"]
            return TaskSpec(name=f"probe:{model_id}", requires=req, typical_tier="PUBLIC",
                            chain=[ChainEntry(model=model_id, capability=1.0)])
        entry = next((e for e in tspec.chain if e.model == model_id), None)
        entry = ChainEntry(model=model_id, capability=entry.capability if entry else 1.0)
        return tspec.model_copy(update={"chain": [entry]})

    def _credentials(self, provider: str) -> tuple[bool, str]:
        if provider == "bedrock" and batch_mod.bedrock_mode() != "live":
            return True, "bedrock batch mode (no AWS call)"
        down = self._provider_down.get(provider)
        if down and self.clock.now() < down[0]:
            return False, down[1]
        return self.credentials(provider)

    def _spend_check(self, spec: ModelSpec) -> str | None:
        if spec.cost_class != "capped_fallback":
            return None
        if spec.provider == "bedrock" and batch_mod.bedrock_mode() != "live":
            return None                      # collect-time batch budget applies instead (D-042)
        return self.spend.status().reason

    # -- providers ----------------------------------------------------------------------------
    @property
    def providers(self) -> dict:
        if self._providers is None:
            from .providers import default_providers
            self._providers = default_providers()
        return self._providers

    # -- public ---------------------------------------------------------------------------------
    def call(self, task: str, payload: dict, *, schema: dict | None = None, privacy_tier: str = "PUBLIC",
             images: list[bytes] | None = None, producer_vendor: str | None = None,
             only_model: str | None = None, exclude_models: list[str] | None = None,
             allow_fallback_on_defer: bool = False) -> RouterResult:
        tspec = self.registry.task(task) if task else None
        if tspec is not None and tspec.exclude_producer_vendor and not producer_vendor:
            raise RouterConfigError(
                f"task {task!r} requires producer_vendor (critic independence, D-017); pass "
                "app.router.vendor_of(<producer RouterResult>.model_id)")
        payload = payload or {}
        images = list(images or [])
        mode = fixtures.current_mode()
        tier, tier_notes = gateway.effective_tier(payload, privacy_tier)
        settings = self.registry.router
        max_tokens = int(payload.get("max_tokens") or settings.default_max_tokens)
        msgs = build_messages(payload, schema, images)
        est_in = estimate_tokens(msgs, images)
        est_out = min(max_tokens, 512)
        in_mods = input_modalities(images)
        step_id = str(payload.get("step_id") or uuid.uuid4().hex[:12])

        if only_model is not None:
            tspec = self._pinned_task(tspec, only_model, in_mods)
        elif tspec is None:
            raise RouterConfigError("call() needs a task (or only_model for a single-model probe)")
        ctx = FilterContext(tier=tier, required_modalities=required_modalities(tspec, in_mods),
                            est_tokens_in=est_in, max_tokens=max_tokens, producer_vendor=producer_vendor,
                            flags={k: bool(payload.get(k)) for k in ("hard_replan",)},
                            check_credentials=(mode != "replay"), doctor=self.doctor)
        eligible, filtered = filter_candidates(self.registry, tspec, ctx, self.breaker, self.quota,
                                               self._credentials, spend=self._spend_check)
        env_ex = {m.strip() for m in os.environ.get("ROUTER_EXCLUDE_MODELS", "").split(",") if m.strip()}
        if env_ex:                               # deployment-level exclusion (e.g. a server without AWS credentials)
            filtered = list(filtered) + [{"id": c.spec.id, "reason": "excluded by deployment (ROUTER_EXCLUDE_MODELS)"}
                                         for c in eligible if c.spec.id in env_ex]
            eligible = [c for c in eligible if c.spec.id not in env_ex]
        if exclude_models:                       # caller-side exclusion (agent reroute after a refuted claim)
            ex = set(exclude_models)
            filtered = list(filtered) + [{"id": c.spec.id, "reason": "excluded by caller (reroute)"}
                                         for c in eligible if c.spec.id in ex]
            eligible = [c for c in eligible if c.spec.id not in ex]
        scores = {c.spec.id: score(c, settings, self.telemetry.p50(c.spec.id, settings.p50_min_samples),
                                   est_in, est_out, len(images)) for c in eligible}
        order = rank(eligible, scores, tspec.strict_order)
        route_log: dict = {
            "step_id": step_id, "run_id": payload.get("run_id"), "task": tspec.name, "mode": mode,
            "privacy_tier": privacy_tier, "effective_tier": tier, "tier_notes": tier_notes,
            "producer_vendor": producer_vendor,
            "required_modalities": sorted(ctx.required_modalities),
            "candidates": [e.model for e in tspec.chain], "filtered": filtered, "scores": scores,
            "order": [c.spec.id for c in order], "choice": None, "attempts": [], "terminal": None,
            "strict_order": tspec.strict_order, "pinned": only_model,
            "excluded": sorted(exclude_models or []),
        }
        deferred: batch_mod.Deferred | None = None

        for cand in order:
            try:
                resp, data, latency = self._try(cand, msgs, schema, images, max_tokens, payload,
                                                tier, mode, route_log)
            except _Fail:
                continue
            except batch_mod.Deferred as dfr:
                deferred = deferred or dfr
                route_log.setdefault("deferred", []).append({"model_id": cand.spec.id, "batch_id": dfr.batch,
                                                             "key": dfr.key})
                if allow_fallback_on_defer:
                    continue
                return RouterResult(ok=False, model_id=cand.spec.id, route_log=route_log, error_kind="deferred",
                                    error=f"deferred to batch {dfr.batch}")
            spec = cand.spec
            shadow = estimate_shadow_cost(spec, resp.tokens_in, resp.tokens_out, resp.pages)
            route_log.update(choice=spec.id, resolved_model=resp.resolved_model,
                             rate_limit_headers=resp.headers, json_mode_used=resp.json_mode_used,
                             image_tokens=resp.image_tokens)
            return RouterResult(ok=True, model_id=spec.id, text=resp.text, data=data,
                                latency_ms=int(latency), tokens_in=resp.tokens_in,
                                tokens_out=resp.tokens_out, shadow_cost_usd=shadow, route_log=route_log)

        if deferred is not None:             # fallback allowed but nothing else answered
            return RouterResult(ok=False, model_id=route_log["deferred"][0]["model_id"], route_log=route_log,
                                error_kind="deferred", error=f"deferred to batch {deferred.batch}")
        route_log["terminal"] = tspec.terminal
        errs = [f"{a['model_id']}: {a['outcome']}{' ' + a['error'] if a.get('error') else ''}"
                for a in route_log["attempts"]]
        if not order:
            msg = "no eligible model"
        else:
            msg = "all candidates failed"
        detail = "; ".join(errs) if errs else "; ".join(f"{f['id']}: {f['reason']}" for f in filtered)
        return RouterResult(ok=False, route_log=route_log, error_kind="no_eligible" if not order else "all_failed",
                            error=redact(f"{msg} (terminal={tspec.terminal}): {detail}"))

    # -- one model: breaker, quota, retry once, fallback_model on 404, repair once --------------
    def _try(self, cand: Candidate, msgs, schema, images, max_tokens, payload, tier, mode, route_log):
        spec = cand.spec
        s = self.registry.router
        kind = "ocr" if spec.provider == "mistral_ocr" else "chat"
        if images and spec.image_timeout_s:
            timeout = spec.image_timeout_s
        else:
            timeout = spec.timeout_s or (s.timeouts_s.get("ocr") if kind == "ocr" else
                                         s.timeouts_s.get("image") if images else s.timeouts_s.get("text"))
        names = self._model_names(spec)
        req = ProviderRequest(model_id=spec.id, provider=spec.provider, model_name=names[0],
                              messages=msgs, schema=schema, json_mode=spec.json_mode,
                              max_tokens=_clamp_max_tokens(spec, max_tokens),
                              temperature=float(payload.get("temperature", 0.0)),
                              timeout_s=float(timeout), images=images, kind=kind,
                              extra={"region": spec.region, "params": getattr(spec, "params", None),
                                     "image_detail": spec.image_detail,
                                     "min_image_px": getattr(spec, "min_image_px", None)})
        resp, latency = self._attempt_with_retry(spec, req, names, tier, mode, route_log)
        if schema is None:
            return resp, resp.data, latency
        data, errs = parse_and_validate(resp.text, schema)
        if data is not None:
            return resp, data, latency
        self._log_attempt(route_log, spec, "schema_invalid", error="; ".join(errs)[:300], resp=resp,
                          latency=latency, tier=tier, mode=mode)
        if kind == "ocr":
            raise _Fail
        # repair once on the same model
        repair_msgs = list(msgs) + [
            {"role": "assistant", "content": resp.text or ""},
            {"role": "user", "content": "Your previous reply was not valid for the required JSON Schema. "
                                        f"Errors: {'; '.join(errs)[:800]}. Return ONLY the corrected JSON "
                                        "object, no prose."}]
        rreq = ProviderRequest(**{**req.__dict__, "messages": repair_msgs, "model_name": req.model_name})
        resp2, lat2 = self._attempt_with_retry(spec, rreq, [req.model_name], tier, mode, route_log,
                                               retries=0, label="repair")
        data, errs = parse_and_validate(resp2.text, schema)
        if data is None:
            self._log_attempt(route_log, spec, "schema_invalid_after_repair", error="; ".join(errs)[:300],
                              resp=resp2, latency=lat2, tier=tier, mode=mode)
            raise _Fail
        resp2.tokens_in += resp.tokens_in
        resp2.tokens_out += resp.tokens_out
        return resp2, data, latency + lat2

    def _model_names(self, spec: ModelSpec) -> list[str]:
        names = [spec.model] + ([spec.fallback_model] if spec.fallback_model else [])
        d = self.doctor.get(spec.id) or {}
        if d.get("resolved_model_name") in names:        # doctor found which declared name works
            names.remove(d["resolved_model_name"])
            names.insert(0, d["resolved_model_name"])
        return [n for n in names if (spec.id, n) not in self._dead_names] or names

    def _attempt_with_retry(self, spec, req, names, tier, mode, route_log, retries=None, label="call"):
        if spec.provider == "bedrock" and batch_mod.bedrock_mode() != "live":
            return self._batch_attempt(spec, req, tier, route_log, label)
        retries = self.registry.router.retries_per_model if retries is None else retries
        names = list(names)
        attempt = 0
        while True:
            if not self.breaker.acquire(spec.id):
                self._log_attempt(route_log, spec, "skipped", error="breaker open", tier=tier, mode=mode)
                raise _Fail
            est = estimate_tokens(req.messages, req.images) + min(req.max_tokens, 512)
            qc = self.quota.check(spec.id, spec.limits, est, spec.cap_usd, est_out=req.max_tokens)
            if qc.ok and spec.cost_class == "capped_fallback":
                sp = self.spend.status()
                if not sp.ok:
                    qc.ok, qc.reason = False, sp.reason
            if not qc.ok:
                self.breaker.release(spec.id)
                self._log_attempt(route_log, spec, "skipped", error=qc.reason, tier=tier, mode=mode)
                raise _Fail
            gateway.guard(spec, tier)       # defence in depth: raises PrivacyViolation
            rid = self.quota.reserve(spec.id, est, est_out=req.max_tokens)
            t_start = self.clock.monotonic_ms()
            try:
                resp, latency = self._invoke(spec, req, mode)
            except ProviderError as e:
                self.quota.reconcile(rid, 0, 0.0, "rejected" if e.kind == "rate_limit" else "failed")
                if e.transient:
                    self.breaker.record_failure(spec.id, transient=True)
                else:
                    self.breaker.release(spec.id)
                self._log_attempt(route_log, spec, label + "_error", error=str(e), error_kind=e.kind,
                                  status=e.status, tier=tier, mode=mode, model_name=req.model_name,
                                  latency=self.clock.monotonic_ms() - t_start, headers=e.headers)
                if e.kind == "auth_expired":           # e.g. AWS SSO lapsed: park the provider, go on
                    self._provider_down[spec.provider] = (self.clock.now() + 300, str(e.args[0]))
                if e.kind == "not_found" and len(names) > 1:
                    self._dead_names.add((spec.id, names.pop(0)))
                    req = ProviderRequest(**{**req.__dict__, "model_name": names[0]})
                    continue
                if e.transient and attempt < retries and _retry_after(e) <= 5:
                    attempt += 1
                    self.clock.sleep(_jitter(spec.id, attempt))
                    continue
                raise _Fail from None
            self.breaker.record_success(spec.id)
            shadow = estimate_shadow_cost(spec, resp.tokens_in, resp.tokens_out, resp.pages)
            cost = actual_cost(spec, shadow, resp.pages)
            self.quota.reconcile(rid, resp.tokens_in + resp.tokens_out, cost, "ok")
            self._log_attempt(route_log, spec, "ok" if label == "call" else f"{label}_ok", resp=resp,
                              latency=latency, tier=tier, mode=mode, shadow=shadow, cost=cost,
                              model_name=req.model_name)
            return resp, latency

    def _batch_attempt(self, spec, req, tier, route_log, label):
        """collect / cache_only (D-042): serve from the imported cache or defer to the batch."""
        gateway.guard(spec, tier)
        line, images = batch_mod.converse_line(req, req.model_name, spec.id, route_log["task"], tier)
        key = line["key"]
        if batch_mod.bedrock_mode() == "cache_only":
            hit = self.batch.lookup(key)
            if hit is not None:
                resp = ProviderResponse(text=hit.output_text, tokens_in=hit.tokens_in, tokens_out=hit.tokens_out,
                                        resolved_model=hit.model, json_mode_used="batch", latency_ms=hit.latency_ms)
                self._log_attempt(route_log, spec, "batch_cache_ok" if label == "call" else f"{label}_batch_cache_ok",
                                  resp=resp, latency=hit.latency_ms, tier=tier, mode="batch_cache",
                                  model_name=req.model_name)
                return resp, hit.latency_ms
        bid = batch_mod.batch_id()
        est_cost = estimate_shadow_cost(spec, estimate_tokens(req.messages, req.images), int(req.max_tokens))
        budget_left = self.spend.threshold * self.spend.cap_usd - self.spend.estimate()[0]
        ok, why = self.batch.collect(bid, line, images, est_cost, budget_left)
        if not ok:
            self._log_attempt(route_log, spec, "skipped", error=why, tier=tier, mode="batch_collect",
                              model_name=req.model_name)
            raise _Fail
        self._log_attempt(route_log, spec, "deferred", error=f"{why} in batch {bid}", tier=tier,
                          mode="batch_collect", model_name=req.model_name)
        raise batch_mod.Deferred(bid, key)

    def _invoke(self, spec: ModelSpec, req: ProviderRequest, mode: str) -> tuple[ProviderResponse, int]:
        effects = chaos.effects_for(spec.id, spec.provider)
        chaos.pre_call(effects)
        key = fixtures.request_key(req)
        t0 = self.clock.monotonic_ms()
        if mode == "replay":
            resp = fixtures.load(req, key)
            latency = int(resp.latency_ms or 0)
        else:
            provider = self.providers.get(spec.provider)
            if provider is None:
                raise ProviderError("unsupported", f"no provider adapter for {spec.provider}")
            try:
                resp = provider.complete(req)
            except ProviderError as e:
                if mode == "record":
                    fixtures.save(req, key, None, e, int(self.clock.monotonic_ms() - t0))
                raise
            except Exception as e:  # noqa: BLE001  adapter bug / SDK surprise
                raise ProviderError("server", redact(f"{type(e).__name__}: {e}")[:300]) from None
            latency = int(resp.latency_ms if resp.latency_ms is not None else self.clock.monotonic_ms() - t0)
            if mode == "record":
                fixtures.save(req, key, resp, None, latency)
        if "slow" in effects:
            if req.timeout_s <= chaos.SLOW_EXTRA_S:
                self.clock.sleep(req.timeout_s)
                raise ProviderError("timeout", "chaos: slow beyond timeout")
            self.clock.sleep(chaos.SLOW_EXTRA_S)
            latency += int(chaos.SLOW_EXTRA_S * 1000)
        if "badjson" in effects:
            resp = ProviderResponse(**{**resp.__dict__, "text": '{"chaos": "truncated'})
        return resp, latency

    def _log_attempt(self, route_log, spec, outcome, *, error=None, error_kind=None, status=None, resp=None,
                     latency=0, tier=None, mode=None, shadow=0.0, cost=0.0, model_name=None, headers=None):
        a = {"model_id": spec.id, "attempt": len(route_log["attempts"]) + 1, "outcome": outcome,
             "model_name": model_name or spec.model, "latency_ms": int(latency or 0)}
        if error:
            a["error"] = redact(error)[:300]
        if error_kind:
            a["error_kind"] = error_kind
        if status:
            a["status"] = status
        if headers:
            a["rate_limit_headers"] = headers
        if resp is not None:
            a.update(tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, resolved_model=resp.resolved_model)
            if resp.image_tokens:
                a["image_tokens"] = resp.image_tokens
        route_log["attempts"].append(a)
        self.telemetry.log({
            "step_id": route_log["step_id"], "run_id": route_log.get("run_id"), "task": route_log["task"],
            "candidates": route_log["candidates"], "filtered": route_log["filtered"],
            "scores": {k: v["total"] for k, v in route_log["scores"].items()}, "choice": spec.id,
            "attempt": a["attempt"], "outcome": outcome, "error_kind": error_kind,
            "latency_ms": a["latency_ms"], "tokens_in": a.get("tokens_in", 0),
            "tokens_out": a.get("tokens_out", 0), "shadow_cost_usd": shadow, "actual_cost_usd": cost,
            "privacy_tier": tier, "mode": mode, "error": a.get("error")})


def _clamp_max_tokens(spec: ModelSpec, max_tokens: int) -> int:
    """A single request may not ask for more output tokens than the per-minute output budget."""
    otpm = spec.limits.get("otpm")
    return min(max_tokens, int(otpm * 0.5)) if otpm else max_tokens


def _retry_after(e: ProviderError) -> float:
    try:
        return float(e.headers.get("retry-after", 0))
    except (TypeError, ValueError):
        return 0.0


def _jitter(model_id: str, attempt: int) -> float:
    """Deterministic 'jitter' (0.5-1.0 s) so retries stay reproducible."""
    h = int(hashlib.sha256(f"{model_id}:{attempt}".encode()).hexdigest()[:8], 16)
    return 0.5 + (h % 500) / 1000.0


def _load_doctor(d: Path) -> dict:
    if os.environ.get("ROUTER_USE_DOCTOR", "1") == "0":
        return {}
    p = d / "doctor.json"
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        return {m["id"]: m for m in j.get("models", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


# -- module-level default router -------------------------------------------------------------------
_default: Router | None = None
_lock = threading.Lock()


def get_router() -> Router:
    global _default
    with _lock:
        if _default is None:
            if os.environ.get("ROUTER_NO_DOTENV") != "1":
                try:
                    from dotenv import load_dotenv
                    load_dotenv(REPO_ROOT / ".env", override=False)
                except ImportError:
                    pass
            _default = Router()
        return _default


def set_router(router: Router | None) -> None:
    global _default
    with _lock:
        _default = router


def reset_default_router() -> None:
    set_router(None)


def call(task: str, payload: dict, *, schema: dict | None = None, privacy_tier: str = "PUBLIC",
         images: list[bytes] | None = None, producer_vendor: str | None = None,
         only_model: str | None = None, exclude_models: list[str] | None = None,
         allow_fallback_on_defer: bool = False) -> RouterResult:
    """Route one model call. See app/router/__init__.py for the contract.
    `only_model` pins the call to one registry model (task settings and every filter still apply).
    `exclude_models` drops registry ids from the chain (logged as filtered; used by agent reroutes).
    `allow_fallback_on_defer`: in Bedrock collect/cache_only mode, try the next model instead of returning
    the deferred result (error_kind="deferred")."""
    return get_router().call(task, payload, schema=schema, privacy_tier=privacy_tier, images=images,
                             producer_vendor=producer_vendor, only_model=only_model,
                             exclude_models=exclude_models, allow_fallback_on_defer=allow_fallback_on_defer)


def vendor_of(model_id: str) -> str:
    """Vendor of a registry model id (use for `producer_vendor` on critic calls)."""
    return get_router().registry.model(model_id).vendor


def probe(model_id: str, payload: dict, *, schema: dict | None = None, images: list[bytes] | None = None,
          privacy_tier: str = "PUBLIC") -> RouterResult:
    """Single-model call (used by `make doctor`); same filters, telemetry, quota and fixtures."""
    return get_router().call("", payload, schema=schema, privacy_tier=privacy_tier, images=images,
                             only_model=model_id)
