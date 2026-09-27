"""Import Bedrock batch responses into the router cache (D-042, infra/bedrock_batch/SPEC.md step 4).

    python -m app.router.batch_import <batch_id>

Reads data/bedrock_batch/<id>/responses/*.jsonl (one line per request:
`key, ok, output_text, usage{inputTokens, outputTokens}, latency_ms, error`; embedding ops carry
`embedding` instead of output_text), matches each line to requests.jsonl by key, and:
  * converse: runs the same JSON extraction + schema validation as live calls, stores the response in
    data/bedrock_cache.sqlite (served by ROUTER_BEDROCK_MODE=cache_only);
  * embed_text / embed_image: stores the vector in the embedding cache under the same key;
  * records usage x list price in quota.sqlite (so the AWS spend guard sees it) and a router_log row
    (mode="batch", actual_cost_usd). Idempotent: keys already imported with ok=1 are skipped.
Failed lines (ok=false) are stored as not-served (so a cache_only re-run collects them again).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .jsonutil import parse_and_validate
from .scorer import estimate_shadow_cost


def _responses(batch_dir: Path):
    for f in sorted((batch_dir / "responses").glob("*.jsonl")):
        for n, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ln.strip():
                try:
                    yield f.name, json.loads(ln)
                except ValueError:
                    yield f.name, {"_bad_line": f"{f.name}:{n}"}


def import_batch(batch_id: str, router=None, embed_cache=None) -> dict:
    from .core import data_dir, get_router
    from .embeddings import EmbeddingCache

    router = router or get_router()
    store = router.batch
    bdir = store.dir(batch_id)
    if not bdir.exists():
        raise SystemExit(f"no batch dir {bdir}")
    reqs = store.requests(batch_id)
    ecache = embed_cache or EmbeddingCache(data_dir() / "embed_cache.sqlite")
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    s = {"batch_id": batch_id, "requests": len(reqs), "lines": 0, "imported": 0, "skipped_existing": 0,
         "schema_invalid": 0, "errors": 0, "unknown_key": 0, "bad_lines": 0, "cost_usd": 0.0}
    for _fname, r in _responses(bdir):
        s["lines"] += 1
        if "_bad_line" in r:
            s["bad_lines"] += 1
            continue
        key = r.get("key")
        req = reqs.get(key)
        if req is None:
            s["unknown_key"] += 1
            continue
        prev = store.imported_row(key)
        if prev is not None and prev[0] == 1:
            s["skipped_existing"] += 1
            continue
        rid = req.get("router_model_id")
        op = req.get("op", "converse")
        usage = r.get("usage") or {}
        tin, tout = int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0)
        ok = bool(r.get("ok"))
        cost = 0.0
        valid, errs = False, []
        if ok and op == "converse":
            spec = router.registry.model(rid)
            cost = estimate_shadow_cost(spec, tin, tout)
            if req.get("schema") is not None:
                data, errs = parse_and_validate(r.get("output_text"), req["schema"])
                valid = data is not None
            else:
                valid = True
        elif ok and op in ("embed_text", "embed_image"):
            espec = router.registry.embeddings[rid]
            cost = (round(tin * espec.price_per_mtok_in / 1e6, 10) if op == "embed_text"
                    else espec.price_per_image_usd)
            vec = r.get("embedding")
            valid = isinstance(vec, list) and len(vec) == espec.dims
            if valid:
                ecache.put(key, rid, vec)
            else:
                ok, errs = False, [f"embedding missing or not {espec.dims}-d"]
        store.store({"key": key, "batch_id": batch_id, "op": op, "router_model_id": rid, "model": req.get("model_id"),
                     "ok": int(ok), "output_text": r.get("output_text"), "tokens_in": tin, "tokens_out": tout,
                     "latency_ms": int(r.get("latency_ms") or 0), "valid": int(valid),
                     "errors": "; ".join(errs)[:500] or None, "error": (r.get("error") or None),
                     "cost_usd": cost, "imported_at": now})
        if tin or tout or cost:
            router.quota.record(rid, tin + tout, cost)
        outcome = ("ok" if valid else "schema_invalid") if ok else "error"
        s["imported" if ok else "errors"] += 1
        s["schema_invalid"] += int(ok and not valid)
        s["cost_usd"] = round(s["cost_usd"] + cost, 8)
        router.telemetry.log({"step_id": f"batch:{batch_id}", "task": req.get("task"), "choice": rid,
                              "attempt": 1, "outcome": outcome, "latency_ms": int(r.get("latency_ms") or 0),
                              "tokens_in": tin, "tokens_out": tout, "shadow_cost_usd": cost,
                              "actual_cost_usd": cost, "privacy_tier": req.get("privacy_tier"), "mode": "batch",
                              "batch_id": batch_id, "key": key,
                              "error": (r.get("error") or "; ".join(errs) or None)})
    s["missing_responses"] = len(reqs) - len({k for k in reqs if store.imported_row(k)})
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("batch_id")
    args = ap.parse_args(argv)
    summary = import_batch(args.batch_id)
    print(json.dumps(summary, indent=1))
    return 0 if summary["bad_lines"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
