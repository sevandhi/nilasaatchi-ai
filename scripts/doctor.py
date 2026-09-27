"""`make doctor` — probe tools, the database and every model in config/models.yaml.

* Tools: python, uv, docker, node, paddle import, tesseract (host or nilasaatchi-ocr-tools image),
  gdal via rasterio. Missing tools are WARN, never FAIL.
* DB: connectivity + postgis / vector extensions.
* Models: ONE minimal live call each through app.router (tiny JSON-schema prompt; vision models
  also get a 16x16 PNG; mistral-ocr gets a generated "TEST 123" PNG). Records status
  OK/SKIPPED/FAIL, resolved model id, latency and rate-limit headers. On a 404 the provider's
  model list is fetched and the closest IDs are REPORTED (config is never edited).
* Writes data/doctor.json and prints tables. Exit 1 only if a task chain has zero eligible OK models.

Usage: uv run python scripts/doctor.py [--model ID ...] [--no-live]   (or env MODEL=id1,id2)
Keys are never printed; every error string is redacted.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
os.environ["ROUTER_USE_DOCTOR"] = "0"           # never let a stale doctor.json filter the probes
if os.environ.get("ROUTER_MODE", "").lower() not in ("record",):
    os.environ["ROUTER_MODE"] = "live"

from rich.console import Console
from rich.table import Table

from app.router import core
from app.router.eligibility import privacy_ok, required_modalities
from app.router.providers import credentials_available
from app.router.providers.listing import closest, list_models
from app.router.registry import load_registry
from app.router.secrets import redact, redact_url

console = Console()

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "answer": {"type": "integer"}},
    "required": ["ok", "answer"],
    "additionalProperties": False,
}
VISION_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "answer": {"type": "integer"}, "color": {"type": "string"}},
    "required": ["ok", "answer", "color"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------------------- tools
def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or p.stderr).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def check_tools() -> list[dict]:
    out = [{"name": "python", "status": "OK" if sys.version_info[:2] == (3, 12) else "WARN",
            "version": platform.python_version(), "detail": sys.executable}]
    for name, cmd in (("uv", ["uv", "--version"]), ("docker", ["docker", "--version"]),
                      ("node", ["node", "--version"])):
        rc, txt = _run(cmd)
        out.append({"name": name, "status": "OK" if rc == 0 else "WARN",
                    "version": txt.splitlines()[0] if rc == 0 and txt else None,
                    "detail": None if rc == 0 else txt[:200]})
    rc, txt = _run([sys.executable, "-c",
                    "import paddle, paddleocr; print(paddle.__version__, paddleocr.__version__)"], 180)
    out.append({"name": "paddle", "status": "OK" if rc == 0 else "WARN",
                "version": "paddle {}, paddleocr {}".format(*txt.split()[-2:]) if rc == 0 else None,
                "detail": None if rc == 0 else txt[-200:]})
    tess = {"name": "tesseract", "status": "WARN", "version": None, "detail": None}
    if shutil.which("tesseract"):
        rc, txt = _run(["tesseract", "--version"])
        _, langs = _run(["tesseract", "--list-langs"])
        tess.update(status="OK" if rc == 0 else "WARN", version=txt.splitlines()[0] if txt else None,
                    detail="host; tam=%s" % ("tam" in langs.split()))
    else:
        rc, _ = _run(["docker", "image", "inspect", "nilasaatchi-ocr-tools:latest"], 20)
        if rc == 0:
            rc, txt = _run(["docker", "run", "--rm", "nilasaatchi-ocr-tools:latest", "tesseract", "--version"], 120)
            _, langs = _run(["docker", "run", "--rm", "nilasaatchi-ocr-tools:latest", "tesseract",
                             "--list-langs"], 120)
            tess.update(status="OK" if rc == 0 else "WARN", version=txt.splitlines()[0] if txt else None,
                        detail="docker image nilasaatchi-ocr-tools; tam=%s" % ("tam" in langs.split()))
        else:
            tess["detail"] = ("not on host and image nilasaatchi-ocr-tools not built "
                              "(docker compose --profile tools build ocr-tools)")
    out.append(tess)
    try:
        import rasterio
        out.append({"name": "gdal", "status": "OK", "version": f"GDAL {rasterio.__gdal_version__}",
                    "detail": f"via rasterio {rasterio.__version__}"})
    except Exception as e:  # noqa: BLE001
        out.append({"name": "gdal", "status": "WARN", "version": None, "detail": redact(e)[:200]})
    return out


# ------------------------------------------------------------------------------------------- db
def check_db() -> dict:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"status": "FAIL", "detail": "DATABASE_URL not set"}
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=5) as conn:
            ver = conn.execute("SELECT current_setting('server_version')").fetchone()[0]
            exts = dict(conn.execute("SELECT extname, extversion FROM pg_extension").fetchall())
            avail = {r[0] for r in conn.execute(
                "SELECT name FROM pg_available_extensions WHERE name IN ('postgis','vector')").fetchall()}
        missing = [e for e in ("postgis", "vector") if e not in exts]
        status = "OK" if not missing else "WARN"
        detail = ", ".join(f"{k} {v}" for k, v in sorted(exts.items()) if k in ("postgis", "vector"))
        if missing:
            detail += f"; not created: {missing} (available: {sorted(avail & set(missing))})"
        return {"status": status, "server_version": ver, "extensions": exts, "detail": detail,
                "url": redact_url(url)}
    except Exception as e:  # noqa: BLE001
        return {"status": "FAIL", "detail": redact(f"{type(e).__name__}: {e}")[:300], "url": redact_url(url)}


# --------------------------------------------------------------------------------------- images
def tiny_png(color=(220, 20, 20), size: int = 16) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def ocr_png(text: str = "TEST 123") -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (420, 120), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:
        font = ImageFont.load_default()
    d.text((20, 25), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


VISION_PROMPT = ("Health check. Reply with JSON where ok=true, answer = 3 + 4, and color = the dominant colour "
                 "of the attached image as one lowercase English word (or \"none\" if there is no image).")
COLORS = [("red", (220, 20, 20)), ("green", (30, 170, 40)), ("blue", (20, 60, 220)), ("yellow", (235, 210, 20))]


def probe_color(model_id: str) -> tuple[str, tuple[int, int, int]]:
    """A per-model solid colour, so a model that never sees the image is unlikely to guess right."""
    import hashlib
    return COLORS[int(hashlib.sha256(model_id.encode()).hexdigest(), 16) % len(COLORS)]


def vision_token_proof(spec, tokens_with_image: int, schema) -> dict:
    """Text-only control call with the same prompt: input tokens must rise when the image is attached.
    Skipped for models with a tiny daily quota (rpd <= 50)."""
    if (spec.limits or {}).get("rpd", 1e9) <= 50:
        return {"vision_tokens_ok": None, "vision_tokens_note": "control skipped (rpd <= 50)"}
    ctrl = core.probe(spec.id, {"prompt": VISION_PROMPT, "max_tokens": 512, "step_id": f"doctor-ctrl-{spec.id}"},
                      schema=schema)
    first = next((a for a in ctrl.route_log.get("attempts", []) if "tokens_in" in a), None)
    if first is None:
        return {"vision_tokens_ok": None, "vision_tokens_note": f"control failed: {(ctrl.error or '')[:120]}"}
    delta = tokens_with_image - int(first["tokens_in"])
    return {"tokens_in_with_image": tokens_with_image, "tokens_in_control": int(first["tokens_in"]),
            "vision_tokens_ok": delta >= 8}


# --------------------------------------------------------------------------------------- models
def probe_model(spec) -> dict:
    rec = {"id": spec.id, "provider": spec.provider, "vendor": spec.vendor, "registry_model": spec.model,
           "fallback_model": spec.fallback_model, "trains_on_free_tier_resolved": spec.trains_on_free_tier(),
           "status": None, "latency_ms": None, "resolved_model": None, "resolved_model_name": None,
           "rate_limit_headers": {}, "error": None, "error_kind": None, "checks": {}, "suggestions": []}
    has, why = credentials_available(spec.provider)
    if not has:
        rec.update(status="SKIPPED", error=why)
        return rec
    images, schema = None, PROBE_SCHEMA
    payload = {"prompt": "Health check. Reply with JSON where ok=true and answer = 3 + 4.", "max_tokens": 512,
               "step_id": f"doctor-{spec.id}"}
    if spec.provider == "mistral_ocr":
        images, schema = [ocr_png()], None
        payload = {"prompt": "ocr", "step_id": f"doctor-{spec.id}"}
    elif "image" in spec.modalities:
        color_name, rgb = probe_color(spec.id)
        images, schema = [tiny_png(rgb, 256)], VISION_SCHEMA
        payload["prompt"] = VISION_PROMPT
    t0 = time.monotonic()
    res = core.probe(spec.id, payload, schema=schema, images=images)
    rl = res.route_log
    atts = rl.get("attempts", [])
    last_ok = next((a for a in reversed(atts) if a["outcome"].endswith("ok")), None)
    rec["attempts"] = [{k: a.get(k) for k in ("outcome", "model_name", "error_kind", "status", "latency_ms")}
                       for a in atts]
    err_headers = {}
    for a in atts:
        err_headers.update(a.get("rate_limit_headers") or {})
    if res.ok:
        rec.update(status="OK", latency_ms=res.latency_ms, resolved_model=rl.get("resolved_model"),
                   resolved_model_name=last_ok["model_name"] if last_ok else spec.model,
                   rate_limit_headers=rl.get("rate_limit_headers") or {}, tokens_in=res.tokens_in,
                   tokens_out=res.tokens_out, json_mode_used=rl.get("json_mode_used"),
                   shadow_cost_usd=res.shadow_cost_usd)
        if spec.provider == "mistral_ocr":
            txt = (res.text or "").upper()
            rec["checks"]["ocr_text_ok"] = "TEST" in txt and "123" in txt
        else:
            rec["checks"]["answer_ok"] = (res.data or {}).get("answer") == 7
            if images:
                got = str((res.data or {}).get("color", "")).lower()
                rec["checks"]["vision_expected"] = color_name
                rec["checks"]["vision_color"] = got
                rec["checks"]["vision_ok"] = color_name in got
                if rl.get("image_tokens"):
                    rec["checks"]["image_tokens"] = rl["image_tokens"]
                rec["checks"].update(vision_token_proof(spec, res.tokens_in, schema))
                if not (rec["checks"]["vision_ok"] and rec["checks"].get("vision_tokens_ok", True)):
                    rec["status"] = "FAIL"
                    rec["error_kind"] = "vision_not_proven"
                    rec["error"] = "image not demonstrably seen (colour or token check failed)"
        if last_ok and last_ok["model_name"] != spec.model:
            rec["checks"]["note"] = f"primary {spec.model} failed; registry fallback_model used"
    else:
        kinds = [a.get("error_kind") for a in atts if a.get("error_kind")]
        filtered = rl.get("filtered") or []
        rec.update(status="FAIL", error=redact(res.error or "")[:500],
                   error_kind=kinds[-1] if kinds else ("filtered" if filtered else "unknown"),
                   latency_ms=int((time.monotonic() - t0) * 1000))
        rec["rate_limit_headers"] = err_headers
        if atts and all(k in ("rate_limit",) for k in kinds):
            zero = [k for k, v in err_headers.items() if "limit" in k and "remaining" not in k and v == "0"]
            rec["checks"]["note"] = (f"account limit is 0 ({', '.join(zero)}): plan/workspace not activated"
                                     if zero else "rate limited during probe (model exists)")
    if "not_found" in [a.get("error_kind") for a in atts]:
        ids, err = list_models(spec.provider)
        rec["available_models_error"] = err
        rec["suggestions"] = closest(spec.model, ids, 6) if ids else []
        if spec.fallback_model:
            rec["suggestions_for_fallback"] = closest(spec.fallback_model, ids, 3) if ids else []
    return rec


# ----------------------------------------------------------------------------------- embeddings
def probe_embeddings(reg) -> list[dict]:
    import tempfile

    from app.router.embeddings import Embedder, EmbeddingCache, EmbeddingUnavailable

    out = []
    for spec in reg.embeddings.values():
        rec = {"id": spec.id, "model": spec.model, "kind": spec.kind, "status": None, "dims": None,
               "latency_ms": None, "tokens_in": None, "cost_usd": None, "error": None}
        if spec.provider != "bedrock":
            rec.update(status="SKIPPED", error="local model (stub; FlagEmbedding not installed)")
        elif spec.kind != "text":
            rec.update(status="SKIPPED", error="not probed (text embedding probe only)")
        else:
            with tempfile.TemporaryDirectory() as td:     # fresh cache so the probe really calls Titan
                emb = Embedder(router=core.get_router(), cache=EmbeddingCache(Path(td) / "c.sqlite"))
                t0 = time.monotonic()
                try:
                    r = emb.embed_text_detailed(["NilaSaatchi doctor probe: survey 233/2"])
                    ok = r.model_id == spec.id
                    rec.update(status="OK" if ok else "FAIL", dims=len(r.vectors[0]), tokens_in=r.tokens_in,
                               cost_usd=r.cost_usd, latency_ms=int((time.monotonic() - t0) * 1000),
                               error=None if ok else f"fell back to {r.model_id}")
                except EmbeddingUnavailable as e:
                    rec.update(status="FAIL", error=redact(e)[:300])
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------- chains
def check_chains(reg, models: dict[str, dict]) -> list[dict]:
    rows = []
    for name, t in reg.tasks.items():
        tier = t.typical_tier
        ok_models, notes = [], []
        for e in t.chain:
            spec = reg.model(e.model)
            m = models.get(spec.id, {})
            pv, why = privacy_ok(spec, tier)
            mods = required_modalities(t, set())
            if not pv:
                notes.append(f"{spec.id}: {why}")
                continue
            if mods - set(spec.modalities):
                notes.append(f"{spec.id}: modality")
                continue
            if m.get("status") != "OK":
                notes.append(f"{spec.id}: {m.get('status')}")
                continue
            ok_models.append(spec.id + (f" (when {e.when})" if e.when else ""))
        rows.append({"task": name, "typical_tier": tier, "ok_models": ok_models,
                     "status": "OK" if ok_models else "FAIL", "notes": notes, "terminal": t.terminal})
    return rows


# ----------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--no-live", action="store_true", help="skip model probes (reuse previous doctor.json)")
    args = ap.parse_args()
    only = set(args.model) | {m for m in os.environ.get("MODEL", "").split(",") if m}

    reg = load_registry()
    out_path = core.data_dir() / "doctor.json"
    prev = {}
    if out_path.exists():
        try:
            prev = {m["id"]: m for m in json.loads(out_path.read_text()).get("models", [])}
        except (ValueError, KeyError):
            prev = {}

    console.rule("[bold]NilaSaatchi doctor")
    tools = check_tools()
    db = check_db()

    models: dict[str, dict] = {}
    for spec in reg.models.values():
        if args.no_live or (only and spec.id not in only):
            if spec.id in prev:
                models[spec.id] = prev[spec.id]
            continue
        console.print(f"probing {spec.id} ({spec.provider}:{spec.model}) ...")
        models[spec.id] = probe_model(spec)

    embeddings = [] if args.no_live else probe_embeddings(reg)
    sp = core.get_router().spend
    try:
        sp.refresh_if_stale()
    except Exception:  # noqa: BLE001, S110
        pass
    st = sp.status(refresh=False)
    spend = {"estimate_usd": st.spend_usd, "cap_usd": st.cap_usd, "threshold": st.threshold,
             "source": st.source, "ok": st.ok, "ce_error": sp.last_error}
    chains = check_chains(reg, models)
    mistral = reg.models.get("mistral-ocr")
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "router_mode": os.environ.get("ROUTER_MODE"),
        "tools": tools, "db": db, "models": list(models.values()), "embeddings": embeddings,
        "aws_spend": spend, "chains": chains,
        "mistral_training_optout": {
            "api_exposes_optout": False,
            "env_MISTRAL_TRAINING_OPTOUT_CONFIRMED": os.environ.get("MISTRAL_TRAINING_OPTOUT_CONFIRMED", "unset"),
            "resolved_trains_on_free_tier": mistral.trains_on_free_tier() if mistral else None,
            "note": "Mistral API does not expose the console training opt-out; treated as training-tier "
                    "(PII-ineligible) until the user confirms and sets MISTRAL_TRAINING_OPTOUT_CONFIRMED=true.",
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(redact(json.dumps(report, indent=2, ensure_ascii=False, default=str)), encoding="utf-8")

    # --- tables
    t = Table(title="Tools & services")
    for c in ("check", "status", "version / detail"):
        t.add_column(c)
    for r in tools:
        t.add_row(r["name"], _st(r["status"]), " ".join(x for x in (r.get("version"), r.get("detail")) if x))
    t.add_row("database", _st(db["status"]), db.get("detail", ""))
    console.print(t)

    t = Table(title="Models (one live probe each)")
    for c in ("id", "status", "latency", "resolved model", "PII-ok", "checks", "rate limits / error"):
        t.add_column(c, overflow="fold")
    for m in models.values():
        rl = m.get("rate_limit_headers") or {}
        rl_s = ", ".join(f"{k.replace('x-ratelimit-', '')}={v}" for k, v in rl.items()) or "-"
        extra = rl_s if m["status"] == "OK" else (m.get("error") or "")[:220] + (f" | {rl_s}" if rl else "")
        if m.get("suggestions"):
            extra += f" | closest available: {', '.join(m['suggestions'])}"
        checks = ", ".join(f"{k}={v}" for k, v in (m.get("checks") or {}).items())
        t.add_row(m["id"], _st(m["status"]), f"{m['latency_ms']} ms" if m.get("latency_ms") else "-",
                  str(m.get("resolved_model") or "-"), "no" if m["trains_on_free_tier_resolved"] else "yes",
                  checks, extra)
    console.print(t)

    if embeddings:
        t = Table(title="Embeddings")
        for c in ("id", "status", "dims", "latency", "tokens", "detail"):
            t.add_column(c, overflow="fold")
        for e in embeddings:
            t.add_row(e["id"], _st(e["status"]), str(e["dims"] or "-"),
                      f"{e['latency_ms']} ms" if e["latency_ms"] else "-", str(e["tokens_in"] or "-"),
                      e["error"] or "")
        console.print(t)
    console.print(f"AWS spend guard: ${spend['estimate_usd']:.4f} of ${spend['cap_usd']:g} "
                  f"(block at {int(spend['threshold'] * 100)}%), source={spend['source']}"
                  + (f", CE error: {spend['ce_error']}" if spend["ce_error"] else ""))

    t = Table(title="Task chains (typical privacy tier, eligible OK models)")
    for c in ("task", "tier", "status", "eligible OK", "excluded"):
        t.add_column(c, overflow="fold")
    for r in chains:
        t.add_row(r["task"], r["typical_tier"], _st(r["status"]), ", ".join(r["ok_models"]) or "-",
                  "; ".join(r["notes"]))
    console.print(t)
    console.print(f"wrote {out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path}")
    bad = [r["task"] for r in chains if r["status"] != "OK"]
    if bad:
        console.print(f"[red]FAIL: no eligible OK model for task chain(s): {', '.join(bad)}")
        return 1
    return 0


def _st(s: str) -> str:
    return {"OK": "[green]OK", "WARN": "[yellow]WARN", "SKIPPED": "[yellow]SKIPPED", "FAIL": "[red]FAIL"}.get(s, s)


if __name__ == "__main__":
    sys.exit(main())
