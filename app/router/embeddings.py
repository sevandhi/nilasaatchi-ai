"""Embeddings via Amazon Titan on Bedrock (D-027), with content-hash caching, the AWS spend guard and a
local BGE-M3 fallback.

    from app.router.embeddings import embed_text, embed_image
    vecs = embed_text(["survey 233/2 award", ...])      # list[list[float]], 1024-d, L2-normalised
    vec  = embed_image(png_bytes)                        # list[float], 1024-d (Titan Multimodal)

IMPORTANT: vectors from different models are NOT comparable. `embed_text_detailed` returns the model
id actually used; store it next to the vector and never mix models in one index. The BGE-M3 fallback is
a stub (FlagEmbedding is not installed): when Titan is unavailable (spend guard at 80%, SSO expired,
no credentials) the fallback raises NotImplementedError / EmbeddingUnavailable.
Titan Text v2 has no batch endpoint: `batch_size` texts are embedded concurrently per round.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import batch as batch_mod
from .types import ProviderError

MAX_TEXT_CHARS = 50_000          # Titan v2 accepts up to 8k tokens; longer inputs are truncated


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingDeferred(RuntimeError):
    """ROUTER_BEDROCK_MODE=collect|cache_only: uncached inputs were collected into a Bedrock batch (D-042)."""

    def __init__(self, batch: str, n_collected: int, n_refused: int = 0):
        super().__init__(f"deferred to batch {batch}: {n_collected} embedding request(s) collected"
                         + (f", {n_refused} refused by the batch budget" if n_refused else ""))
        self.batch, self.n_collected, self.n_refused = batch, n_collected, n_refused


@dataclass
class EmbedResult:
    model_id: str
    vectors: list[list[float]]
    cached: int
    tokens_in: int
    cost_usd: float


class EmbeddingCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, model_id TEXT, vec TEXT)")

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        if not keys:
            return {}
        out = {}
        with sqlite3.connect(self.path) as c:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = ",".join("?" * len(chunk))
                for k, v in c.execute(f"SELECT key, vec FROM emb WHERE key IN ({q})", chunk):
                    out[k] = json.loads(v)
        return out

    def put(self, key: str, model_id: str, vec: list[float]) -> None:
        with self._lock, sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO emb VALUES (?,?,?)", (key, model_id, json.dumps(vec)))


def _key(model_id: str, dims: int, normalize: bool, payload: bytes) -> str:
    h = hashlib.sha256()
    h.update(f"{model_id}|{dims}|{int(normalize)}|".encode())
    h.update(payload)
    return h.hexdigest()


class Embedder:
    def __init__(self, router=None, cache: EmbeddingCache | None = None,
                 text_invoke: Callable | None = None, image_invoke: Callable | None = None,
                 local_embed: Callable[[list[str]], list[list[float]]] | None = None,
                 batch_size: int = 8):
        from .core import data_dir, get_router

        self.router = router or get_router()
        self.cache = cache or EmbeddingCache(data_dir() / "embed_cache.sqlite")
        reg = self.router.registry
        self.text_spec = reg.embeddings.get("titan-embed-text-v2")
        self.image_spec = reg.embeddings.get("titan-embed-image-v1")
        if text_invoke is None or image_invoke is None:
            from .providers import bedrock
            text_invoke = text_invoke or bedrock.invoke_titan_text
            image_invoke = image_invoke or bedrock.invoke_titan_image
        self.text_invoke = text_invoke
        self.image_invoke = image_invoke
        self.local_embed = local_embed or bge_m3_stub
        self.batch_size = batch_size

    # -- guard -------------------------------------------------------------------------------------
    def _titan_blocked(self, spec) -> str | None:
        if spec is None:
            return "titan not configured"
        ok, why = self.router._credentials(spec.provider)
        if not ok:
            return why
        return self.router.spend.status().reason

    def _log(self, spec_id: str, n: int, tokens: int, cost: float, outcome: str, err: str | None = None):
        self.router.telemetry.log({"task": "embed", "choice": spec_id, "attempt": 1, "outcome": outcome,
                                   "tokens_in": tokens, "tokens_out": 0, "shadow_cost_usd": cost,
                                   "actual_cost_usd": cost, "n_inputs": n, "error": err})

    # -- batch (D-042) ---------------------------------------------------------------------------
    def _collect(self, bid: str, line: dict, images: dict, est_cost: float) -> bool:
        sp = self.router.spend
        left = sp.threshold * sp.cap_usd - sp.estimate()[0]
        ok, _ = self.router.batch.collect(bid, line, images, est_cost, left)
        return ok

    def _batch_text(self, spec, texts: list[str]) -> EmbedResult:
        """Serve cached vectors; collect misses (both collect and cache_only modes)."""
        keys = [_key(spec.id, spec.dims, spec.normalize, t.encode()) for t in texts]
        hits = self.cache.get_many(list(set(keys)))
        todo = sorted({k: t for k, t in zip(keys, texts, strict=True) if k not in hits}.items())
        if not todo:
            return EmbedResult(spec.id, [hits[k] for k in keys], len(texts), 0, 0.0)
        bid = batch_mod.batch_id()
        n_ok = n_refused = 0
        for k, t in todo:
            est = round(len(t) / 3.5 * spec.price_per_mtok_in / 1e6, 10)
            line = {"key": k, "op": "embed_text", "task": "embed_text", "model_id": spec.model,
                    "router_model_id": spec.id, "inputText": t, "dimensions": spec.dims,
                    "normalize": spec.normalize, "privacy_tier": "PII"}
            if self._collect(bid, line, {}, est):
                n_ok += 1
            else:
                n_refused += 1
        raise EmbeddingDeferred(bid, n_ok, n_refused)

    def _batch_image(self, spec, png: bytes) -> EmbedResult:
        k = _key(spec.id, spec.dims, True, png)
        hit = self.cache.get_many([k])
        if k in hit:
            return EmbedResult(spec.id, [hit[k]], 1, 0, 0.0)
        bid = batch_mod.batch_id()
        jpg = batch_mod.to_jpeg(png)
        sha = hashlib.sha256(jpg).hexdigest()
        line = {"key": k, "op": "embed_image", "task": "embed_image", "model_id": spec.model,
                "router_model_id": spec.id, "image": {"format": "jpeg", "s3_key": f"img/{sha}.jpg"},
                "dimensions": spec.dims, "privacy_tier": "PUBLIC"}
        ok = self._collect(bid, line, {sha: jpg}, spec.price_per_image_usd)
        raise EmbeddingDeferred(bid, int(ok), int(not ok))

    # -- text --------------------------------------------------------------------------------------
    def embed_text_detailed(self, texts: list[str]) -> EmbedResult:
        spec = self.text_spec
        texts = [t[:MAX_TEXT_CHARS] for t in texts]
        if spec is not None and batch_mod.bedrock_mode() != "live":
            return self._batch_text(spec, texts)
        blocked = self._titan_blocked(spec)
        if blocked:
            return self._local(texts, blocked)
        keys = [_key(spec.id, spec.dims, spec.normalize, t.encode()) for t in texts]
        hits = self.cache.get_many(list(set(keys)))
        todo = sorted({k: t for k, t in zip(keys, texts, strict=True) if k not in hits}.items())
        tokens = 0
        try:
            for i in range(0, len(todo), self.batch_size):
                chunk = todo[i:i + self.batch_size]
                with ThreadPoolExecutor(max_workers=min(4, len(chunk))) as ex:
                    res = list(ex.map(lambda kt: self.text_invoke(spec.model, kt[1], spec.dims, spec.normalize,
                                                                   spec.region), chunk))
                for (k, _), (vec, ntok) in zip(chunk, res, strict=True):
                    self.cache.put(k, spec.id, vec)
                    hits[k] = vec
                    tokens += ntok
        except ProviderError as e:
            self._record(spec, tokens, len(todo))
            if e.kind == "auth_expired":
                self.router._provider_down[spec.provider] = (self.router.clock.now() + 300, str(e.args[0]))
            self._log(spec.id, len(todo), tokens, 0.0, "error", str(e))
            return self._local(texts, str(e))
        cost = self._record(spec, tokens, len(todo))
        self._log(spec.id, len(todo), tokens, cost, "ok")
        return EmbedResult(spec.id, [hits[k] for k in keys], len(texts) - len(todo), tokens, cost)

    def _record(self, spec, tokens: int, n_calls: int) -> float:
        cost = round(tokens * spec.price_per_mtok_in / 1e6, 8)
        if n_calls:
            self.router.quota.record(spec.id, tokens, cost)
        return cost

    def _local(self, texts: list[str], why: str) -> EmbedResult:
        try:
            vecs = self.local_embed(texts)
        except NotImplementedError as e:
            raise EmbeddingUnavailable(f"Titan unavailable ({why}); local BGE-M3 fallback: {e}") from None
        return EmbedResult("bge-m3", vecs, 0, 0, 0.0)

    # -- image -------------------------------------------------------------------------------------
    def embed_image_detailed(self, png: bytes) -> EmbedResult:
        spec = self.image_spec
        if spec is not None and batch_mod.bedrock_mode() != "live":
            return self._batch_image(spec, png)
        blocked = self._titan_blocked(spec)
        if blocked:
            raise EmbeddingUnavailable(f"Titan image embeddings unavailable: {blocked} (no local image fallback)")
        k = _key(spec.id, spec.dims, True, png)
        hit = self.cache.get_many([k])
        if k in hit:
            return EmbedResult(spec.id, [hit[k]], 1, 0, 0.0)
        try:
            vec = self.image_invoke(spec.model, png, spec.dims, spec.region)
        except ProviderError as e:
            if e.kind == "auth_expired":
                self.router._provider_down[spec.provider] = (self.router.clock.now() + 300, str(e.args[0]))
            self._log(spec.id, 1, 0, 0.0, "error", str(e))
            raise EmbeddingUnavailable(str(e)) from None
        self.cache.put(k, spec.id, vec)
        cost = spec.price_per_image_usd
        self.router.quota.record(spec.id, 0, cost)
        self._log(spec.id, 1, 0, cost, "ok")
        return EmbedResult(spec.id, [vec], 0, 0, cost)


def bge_m3_stub(texts: list[str]) -> list[list[float]]:
    try:
        import FlagEmbedding  # noqa: F401
    except ImportError:
        raise NotImplementedError("FlagEmbedding (BGE-M3) is not installed") from None
    raise NotImplementedError("BGE-M3 local embedding not wired yet")


_default: Embedder | None = None


def _embedder() -> Embedder:
    global _default
    if _default is None:
        _default = Embedder()
    return _default


def embed_text(texts: list[str]) -> list[list[float]]:
    return _embedder().embed_text_detailed(list(texts)).vectors


def embed_image(png_bytes: bytes) -> list[float]:
    return _embedder().embed_image_detailed(png_bytes).vectors[0]


def embed_text_detailed(texts: list[str]) -> EmbedResult:
    return _embedder().embed_text_detailed(list(texts))


def reset_default_embedder() -> None:
    global _default
    _default = None
