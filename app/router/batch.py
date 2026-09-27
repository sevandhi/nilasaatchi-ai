"""Deferred Bedrock batches (D-042, infra/bedrock_batch/SPEC.md) — router side.

ROUTER_BEDROCK_MODE:
  live        (default) call Bedrock directly
  collect     every would-be Bedrock call is appended to
              data/bedrock_batch/<ROUTER_BATCH_ID>/requests.jsonl (dedup by key; images -> img/<sha256>.jpg)
              and the call returns ok=False, error_kind="deferred" (no fallback unless allow_fallback_on_defer)
  cache_only  Bedrock calls are served from the imported response cache (data/bedrock_cache.sqlite);
              a miss is collected as above and returned as deferred

Request line (SPEC) + router extensions: "op" (converse | embed_text | embed_image), "router_model_id",
"schema", "est_cost_usd". Converse key = sha256(canonical {model_id, system, messages with image sha256
refs, inferenceConfig, schema}). Embedding key = the embedder's content-hash cache key.
Collect-time spend guard: est cost of all pending (not yet imported) requests + this one must fit under
80% x AWS_SPEND_CAP_USD minus spend to date.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .types import ProviderError, ProviderRequest, RouterConfigError

MODES = ("live", "collect", "cache_only")
JPEG_QUALITY = 92


def bedrock_mode() -> str:
    m = os.environ.get("ROUTER_BEDROCK_MODE", "live").strip().lower()
    if m not in MODES:
        raise RouterConfigError(f"ROUTER_BEDROCK_MODE={m!r}; expected one of {MODES}")
    return m


def batch_id() -> str:
    b = os.environ.get("ROUTER_BATCH_ID", "").strip()
    if not b:
        raise RouterConfigError("ROUTER_BATCH_ID must be set when ROUTER_BEDROCK_MODE is collect/cache_only")
    if "/" in b or b.startswith("."):
        raise RouterConfigError(f"invalid ROUTER_BATCH_ID {b!r}")
    return b


class Deferred(Exception):
    def __init__(self, batch: str, key: str):
        super().__init__(f"deferred to batch {batch}")
        self.batch, self.key = batch, key


def to_jpeg(b: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(b))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=False)
    return buf.getvalue()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def converse_line(req: ProviderRequest, bedrock_model: str, router_model_id: str, task: str,
                  privacy_tier: str) -> tuple[dict, dict[str, bytes]]:
    """(request line, {sha256: jpeg bytes}) for a Converse call."""
    from .providers.bedrock import to_converse

    system, msgs = to_converse(req.messages)
    images: dict[str, bytes] = {}
    out_msgs = []
    for m in msgs:
        blocks = []
        for b in m["content"]:
            if "image" in b:
                jpg = to_jpeg(b["image"]["source"]["bytes"])
                sha = hashlib.sha256(jpg).hexdigest()
                images[sha] = jpg
                blocks.append({"image": {"format": "jpeg", "s3_key": f"img/{sha}.jpg"}})
            elif "document" in b:
                raise ProviderError("unsupported", "PDF/document blocks are not supported in Bedrock batches")
            else:
                blocks.append({"text": b["text"]})
        out_msgs.append({"role": m["role"], "content": blocks})
    system_text = "\n\n".join(s["text"] for s in system)
    inference = {"maxTokens": int(req.max_tokens), "temperature": float(req.temperature)}
    key = hashlib.sha256(canonical({"model_id": bedrock_model, "system": system_text, "messages": out_msgs,
                                    "inferenceConfig": inference, "schema": req.schema}).encode()).hexdigest()
    line = {"key": key, "op": "converse", "task": task, "model_id": bedrock_model,
            "router_model_id": router_model_id, "system": system_text, "messages": out_msgs,
            "inferenceConfig": inference, "schema": req.schema, "privacy_tier": privacy_tier}
    return line, images


@dataclass
class CacheHit:
    output_text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    model: str
    valid: bool


class BatchStore:
    """Per-process view of batch dirs + the imported response cache."""

    def __init__(self, root: Path, cache_path: Path):
        self.root = Path(root)
        self.cache_path = Path(cache_path)
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, float]] = {}      # batch -> {key: est_cost}
        self._imported: set[str] | None = None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS responses (
                key TEXT PRIMARY KEY, batch_id TEXT, op TEXT, router_model_id TEXT, model TEXT, ok INTEGER,
                output_text TEXT, tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, valid INTEGER,
                errors TEXT, error TEXT, cost_usd REAL, imported_at TEXT)""")

    def _conn(self):
        return sqlite3.connect(self.cache_path, timeout=10)

    def dir(self, batch: str) -> Path:
        return self.root / batch

    # -- cache -----------------------------------------------------------------------------------
    def lookup(self, key: str) -> CacheHit | None:
        with self._conn() as c:
            r = c.execute("SELECT output_text, tokens_in, tokens_out, latency_ms, model, valid FROM responses "
                          "WHERE key=? AND ok=1", (key,)).fetchone()
        return CacheHit(r[0], r[1], r[2], r[3] or 0, r[4], bool(r[5])) if r else None

    def imported_row(self, key: str) -> tuple | None:
        with self._conn() as c:
            return c.execute("SELECT ok FROM responses WHERE key=?", (key,)).fetchone()

    def store(self, row: dict) -> None:
        cols = ["key", "batch_id", "op", "router_model_id", "model", "ok", "output_text", "tokens_in",
                "tokens_out", "latency_ms", "valid", "errors", "error", "cost_usd", "imported_at"]
        with self._lock, self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO responses ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      [row.get(k) for k in cols])
        if self._imported is not None and row.get("ok"):
            self._imported.add(row["key"])

    def _imported_keys(self) -> set[str]:
        if self._imported is None:
            with self._conn() as c:
                self._imported = {r[0] for r in c.execute("SELECT key FROM responses WHERE ok=1")}
        return self._imported

    # -- collect ---------------------------------------------------------------------------------
    def _load(self, batch: str) -> dict[str, float]:
        if batch not in self._index:
            idx: dict[str, float] = {}
            f = self.dir(batch) / "requests.jsonl"
            if f.exists():
                for ln in f.read_text(encoding="utf-8").splitlines():
                    if ln.strip():
                        j = json.loads(ln)
                        idx[j["key"]] = float(j.get("est_cost_usd", 0.0))
            self._index[batch] = idx
        return self._index[batch]

    def pending_cost(self) -> float:
        """Estimated cost of every collected request (all batches) not yet imported."""
        if self.root.exists():
            for d in self.root.iterdir():
                if d.is_dir():
                    self._load(d.name)
        done = self._imported_keys()
        return sum(c for idx in self._index.values() for k, c in idx.items() if k not in done)

    def collect(self, batch: str, line: dict, images: dict[str, bytes], est_cost: float,
                budget_left: float) -> tuple[bool, str]:
        """Append `line` unless its key is already collected. Returns (collected_or_dup, reason)."""
        with self._lock:
            idx = self._load(batch)
            if line["key"] in idx:
                return True, "duplicate"
            pending = self.pending_cost()
            if pending + est_cost > budget_left:
                return False, (f"quota:batch_budget pending ${pending:.4f} + ${est_cost:.4f} > "
                               f"${max(budget_left, 0):.4f} left under 80% of the AWS cap")
            d = self.dir(batch)
            (d / "img").mkdir(parents=True, exist_ok=True)
            for sha, jpg in images.items():
                p = d / "img" / f"{sha}.jpg"
                if not p.exists():
                    p.write_bytes(jpg)
            full = {**line, "batch_id": batch, "est_cost_usd": round(est_cost, 8),
                    "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
            with open(d / "requests.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(full, ensure_ascii=False, sort_keys=True) + "\n")
            idx[line["key"]] = est_cost
            return True, "collected"

    def requests(self, batch: str) -> dict[str, dict]:
        f = self.dir(batch) / "requests.jsonl"
        out = {}
        if f.exists():
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    j = json.loads(ln)
                    out[j["key"]] = j
        return out
