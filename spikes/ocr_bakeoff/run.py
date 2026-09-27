"""OCR bake-off CLI (T0.5). ``make ocr-bakeoff`` runs this with defaults.

    uv run python -m spikes.ocr_bakeoff.run \
        --candidates a,b,c --pages all --out data/bakeoff

Every OCR call is cached by content hash in ``data/bakeoff/cache/`` (see
`cache.py`), so re-running is a no-op except for pages/candidates that changed.
Grid overlays are written to ``data/bakeoff/overlays/`` for every page where
candidate (c) ran, whether or not a grid was found.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spikes.ocr_bakeoff.cache import ContentCache, content_hash
from spikes.ocr_bakeoff.grid import Cell, GridResult, band_crops, crop_cell, detect_table_grid, draw_overlay
from spikes.ocr_bakeoff.hosted import (
    GroqPacer,
    downscale_long_side,
    router_available,
    run_cell_reread_band,
    run_mistral_table_read,
)
from spikes.ocr_bakeoff.normalize import tamil_numerals_to_ascii
from spikes.ocr_bakeoff.paddle_engine import ocr_array, ocr_image
from spikes.ocr_bakeoff.parse_fields import (
    header_from_text,
    rows_from_grid,
    rows_from_hosted_json,
    rows_from_text,
)
from spikes.ocr_bakeoff.score import CandidateScore, load_labels, numeric_cer, score_page
from spikes.ocr_bakeoff.tesseract_docker import TesseractDocker

GOLDEN_DIR = REPO_ROOT / "eval" / "golden"
PAGES_CSV = GOLDEN_DIR / "mini_pages.csv"
LABELS_PATH = GOLDEN_DIR / "mini.jsonl"
DEFAULT_OUT = REPO_ROOT / "data" / "bakeoff"

FULL_CORPUS_PAGES = 12_564

# language for PaddleOCR PP-OCRv5, chosen from doc_type_hint per direct inspection
# of the golden pages (see the bake-off report for the page-by-page evidence).
LANG_BY_HINT = {
    "GO": "ta", "AS": "en", "LPS": "ta",
    "AWARD_7_2": "ta", "AWARD_7_3": "ta",
    "FORM_F": "en", "FORM_E": "ta",
    "SEC32_NOTICE": "ta", "SEC32_ERRATA": "ta",
    "DISBURSEMENT": "ta", "POSSESSION_CERT": "en",
    "LDR": "en", "CHITTA": "ta", "DLPNC": "en",
}
LANG_OVERRIDE_BY_PAGE = {"g16": "en"}  # bank demand-draft scan, English/Hindi form

GROUP_CANDIDATES = {
    "a": ["tesseract_psm6", "tesseract_psm4"],
    "b": ["paddle_full"],
    "c": ["grid_cell"],
    "d": ["mistral_table_read"],
    "e": ["groq_cell_reread"],
    "f": ["hybrid_c_e"],
}
HOSTED_CANDIDATES = {"mistral_table_read", "groq_cell_reread", "hybrid_c_e"}
# hybrid_c_e (candidate f) is (c)'s header/prose extraction + (e)'s table rows --
# the intended production route -- so it needs both computed even if the user
# only asked for `--candidates f`.
CANDIDATE_DEPENDENCIES = {"hybrid_c_e": ["grid_cell", "groq_cell_reread"]}
ROWS_PER_BAND = 6


def lang_for(page_id: str, doc_type_hint: str) -> str:
    return LANG_OVERRIDE_BY_PAGE.get(page_id, LANG_BY_HINT.get(doc_type_hint, "ta"))


def load_pages(limit: int | None, selected_ids: set[str] | None) -> list[dict[str, str]]:
    rows = []
    with PAGES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if selected_ids and row["page_id"] not in selected_ids:
                continue
            rows.append(row)
    if limit is not None:
        rows = rows[:limit]
    return rows


# ---------------------------------------------------------------------------
# Candidate (a): Tesseract tam+eng full page, psm 6 and psm 4
# ---------------------------------------------------------------------------

def run_tesseract_candidate(tess: TesseractDocker, cache: ContentCache, page_id: str, img_path: Path,
                             img_bytes: bytes, psm: int) -> dict[str, Any]:
    cand_id = f"tesseract_psm{psm}"
    key = content_hash(img_bytes, cand_id, "lang=tam+eng")

    def compute():
        r = tess.ocr_file(img_path, lang="tam+eng", psm=psm)
        return {"text": r.text, "seconds": r.seconds, "returncode": r.returncode}

    result = cache.get_or_compute(key, compute)
    text = result["text"]
    header = header_from_text(text)
    rows = rows_from_text(text)
    return {
        "candidate": cand_id, "seconds": result["seconds"], "quota_units": 0,
        "from_cache": result["from_cache"], "text_sample": text[:400],
        "parsed": {"header": header, "rows": rows},
    }


# ---------------------------------------------------------------------------
# Candidate (b): PaddleOCR PP-OCRv5 full page
# ---------------------------------------------------------------------------

def run_paddle_full_candidate(cache: ContentCache, page_id: str, img_path: Path, img_bytes: bytes,
                               lang: str) -> dict[str, Any]:
    cand_id = "paddle_full"
    key = content_hash(img_bytes, cand_id, f"lang={lang}")

    def compute():
        r = ocr_image(img_path, lang=lang)
        return {"text": r.joined_text, "seconds": r.seconds, "n_texts": len(r.texts)}

    result = cache.get_or_compute(key, compute)
    text = result["text"]
    header = header_from_text(text)
    rows = rows_from_text(text)
    return {
        "candidate": cand_id, "seconds": result["seconds"], "quota_units": 0,
        "from_cache": result["from_cache"], "text_sample": text[:400], "lang": lang,
        "parsed": {"header": header, "rows": rows},
    }


# ---------------------------------------------------------------------------
# Candidate (c): grid detection + per-cell OCR
# ---------------------------------------------------------------------------

_NUMERIC_CELL_RE = __import__("re").compile(r"^[0-9./,\-\s]+$")


def _choose_cell_text(paddle_text: str, tess_text: str) -> str:
    tess_clean = tamil_numerals_to_ascii(tess_text).strip()
    if tess_clean and _NUMERIC_CELL_RE.match(tess_clean):
        return tess_clean
    if paddle_text.strip():
        return paddle_text.strip()
    return tess_clean


_GRID_CACHE: dict[str, tuple[Any, GridResult]] = {}  # in-process only; detection is cheap+deterministic


def get_grid(page_id: str, img_path: Path, overlay_dir: Path) -> tuple[Any, GridResult]:
    """Detect (and cache in-process, for this run only) the table grid for a page,
    shared between candidates (c) and (e)/(f) so it is computed at most once.
    Also (re)writes the overlay PNG."""
    if page_id in _GRID_CACHE:
        return _GRID_CACHE[page_id]
    img = cv2.imread(str(img_path))
    grid = detect_table_grid(img)
    overlay = draw_overlay(img, grid)
    overlay_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_dir / f"{page_id}_grid.png"), overlay)
    _GRID_CACHE[page_id] = (img, grid)
    return img, grid


def run_grid_candidate(tess: TesseractDocker, cache: ContentCache, page_id: str, img_path: Path,
                        img_bytes: bytes, lang: str, overlay_dir: Path,
                        fallback_text: str) -> dict[str, Any]:
    cand_id = "grid_cell"
    key = content_hash(img_bytes, cand_id, f"lang={lang}")

    def compute():
        t0 = time.perf_counter()
        img, grid = get_grid(page_id, img_path, overlay_dir)
        if not grid.found:
            return {
                "found": False, "reason": grid.reason, "cells": [],
                "seconds": time.perf_counter() - t0, "n_cells": 0,
            }
        cell_records = []
        for c in grid.cells:
            crop = crop_cell(img, c)
            if crop.size == 0:
                continue
            paddle_r = ocr_array(crop, lang=lang)
            paddle_text = " ".join(paddle_r.texts)
            tess_r = tess.ocr_cell_array(crop) if hasattr(tess, "ocr_cell_array") else None
            tess_text = tess_r.text.strip() if tess_r else ""
            cell_records.append({
                "row": c.row, "col": c.col, "x": c.x, "y": c.y, "w": c.w, "h": c.h,
                "paddle_text": paddle_text, "tess_text": tess_text,
            })
        return {
            "found": True, "reason": "", "cells": cell_records,
            "seconds": time.perf_counter() - t0, "n_cells": len(cell_records),
        }

    result = cache.get_or_compute(key, compute)
    if not result["found"]:
        header = header_from_text(fallback_text)
        rows = rows_from_text(fallback_text)
        return {
            "candidate": cand_id, "seconds": result["seconds"], "quota_units": 0,
            "from_cache": result["from_cache"], "grid_found": False, "reason": result["reason"],
            "parsed": {"header": header, "rows": rows},
        }

    cell_text: dict[tuple[int, int], str] = {}
    for cr in result["cells"]:
        if cr["row"] == 0:
            # Header row: always trust Paddle. The digit-whitelist Tesseract pass
            # often mis-reads Tamil/English header glyphs as stray digits (a real
            # bug found on g19: it read "புல எண்" as "66"), which would silently
            # break column classification if preferred here.
            cell_text[(cr["row"], cr["col"])] = cr["paddle_text"].strip()
        else:
            cell_text[(cr["row"], cr["col"])] = _choose_cell_text(cr["paddle_text"], cr["tess_text"])
    cells = [Cell(row=cr["row"], col=cr["col"], x=cr["x"], y=cr["y"], w=cr["w"], h=cr["h"]) for cr in result["cells"]]
    rows = rows_from_grid(cells, cell_text)
    header = header_from_text(fallback_text)
    return {
        "candidate": cand_id, "seconds": result["seconds"], "quota_units": 0,
        "from_cache": result["from_cache"], "grid_found": True, "n_cells": result["n_cells"],
        "parsed": {"header": header, "rows": rows},
    }


# ---------------------------------------------------------------------------
# Candidate (e): Groq Qwen vision cell/table re-read, in row bands
# ---------------------------------------------------------------------------

def _band_crop_bytes(page_id: str, img_path: Path, overlay_dir: Path) -> list[bytes]:
    """The image inputs for candidate (e): row-band crops of the detected table
    (header re-stacked on each band, per `grid.band_crops`), downscaled to <=1600px
    long side; or, when no grid was found, the whole page as a single crop."""
    img, grid = get_grid(page_id, img_path, overlay_dir)
    crops = band_crops(img, grid, rows_per_band=ROWS_PER_BAND) if grid.found else []
    if not crops:
        crops = [img]
    return [downscale_long_side(c, max_side=1600) for c in crops]


def run_groq_candidate(cache: ContentCache, page_id: str, img_path: Path, doc_type_hint: str,
                        overlay_dir: Path, fallback_text: str, pacer: GroqPacer) -> dict[str, Any]:
    cand_id = "groq_cell_reread"
    band_bytes_list = _band_crop_bytes(page_id, img_path, overlay_dir)

    all_rows: list[dict[str, Any]] = []
    total_seconds = 0.0
    n_calls_live = 0
    tokens_in_total = tokens_out_total = 0
    shadow_cost_total = 0.0
    errors: list[str] = []
    model_id = None

    for band_bytes in band_bytes_list:
        key = content_hash(band_bytes, cand_id, "schema=cell_reread_v2", f"doc_type={doc_type_hint}")

        def compute(_band_bytes=band_bytes):
            r = run_cell_reread_band(_band_bytes, doc_type_hint, pacer=pacer)
            return {
                "ok": r.ok, "rows": r.rows, "seconds": r.seconds, "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out, "shadow_cost_usd": r.shadow_cost_usd, "model_id": r.model_id,
                "error": r.error,
            }

        result = cache.get_or_compute(key, compute)
        if not result["from_cache"]:
            n_calls_live += 1
        total_seconds += result["seconds"]
        tokens_in_total += result["tokens_in"]
        tokens_out_total += result["tokens_out"]
        shadow_cost_total += result["shadow_cost_usd"]
        model_id = result["model_id"] or model_id
        if result["ok"]:
            all_rows.extend(result["rows"])
        elif result["error"]:
            errors.append(result["error"])

    rows = rows_from_hosted_json(all_rows)
    header = header_from_text(fallback_text)
    return {
        "candidate": cand_id, "seconds": total_seconds, "quota_units": tokens_out_total,
        "from_cache": n_calls_live == 0, "n_bands": len(band_bytes_list), "n_calls_live": n_calls_live,
        "tokens_in": tokens_in_total, "tokens_out": tokens_out_total, "shadow_cost_usd": shadow_cost_total,
        "model_id": model_id, "errors": errors,
        "parsed": {"header": header, "rows": rows},
    }


# ---------------------------------------------------------------------------
# Candidate (f): hybrid -- (c)'s header/prose extraction + (e)'s table rows.
# This is the intended production route: candidate (c) is free and fast for
# everything that isn't a table cell; candidate (e) is the one that can actually
# read a numeric/handwritten cell. No extra OCR calls -- purely a combination of
# already-computed (c) and (e) results.
# ---------------------------------------------------------------------------

def build_hybrid_candidate(grid_result: dict[str, Any], groq_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": "hybrid_c_e", "seconds": 0.0, "quota_units": 0, "from_cache": True,
        "header_source": "grid_cell", "rows_source": "groq_cell_reread",
        "parsed": {"header": grid_result["parsed"]["header"], "rows": groq_result["parsed"]["rows"]},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="NilaSaatchi OCR bake-off (T0.5)")
    p.add_argument("--candidates", default="a,b,c", help="comma list from a,b,c,d,e,f")
    p.add_argument("--pages", default="all", help="'all' or comma list of page_ids, e.g. g01,g02")
    p.add_argument("--limit", type=int, default=None, help="cap the number of pages (smoke run)")
    p.add_argument("--hosted", action="store_true", help="enable candidates d/e/f (needs app.router + keys)")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    p.add_argument("--workers", type=int, default=12, help="worker count used only for the full-corpus estimate")
    p.add_argument("--groq-otpm", type=int, default=1000, help="Groq output-tokens/min budget for pacing (e)/(f)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out_dir = Path(args.out)
    cache_dir = out_dir / "cache"
    overlay_dir = out_dir / "overlays"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = ContentCache(cache_dir)

    groups = [g.strip() for g in args.candidates.split(",") if g.strip()]
    requested_ids: list[str] = []
    for g in groups:
        if g not in GROUP_CANDIDATES:
            print(f"unknown candidate group: {g!r} (expected one of {sorted(GROUP_CANDIDATES)})", file=sys.stderr)
            return 2
        requested_ids.extend(GROUP_CANDIDATES[g])
    if any(c in HOSTED_CANDIDATES for c in requested_ids) and not args.hosted:
        print("candidates d/e/f require --hosted; ignoring them", file=sys.stderr)
        requested_ids = [c for c in requested_ids if c not in HOSTED_CANDIDATES]

    # compute_ids: requested candidates plus anything they depend on (e.g. (f)
    # needs (c) and (e) computed even if the user only asked for `--candidates f`).
    # Only requested_ids are scored/reported; dependency-only results are used
    # internally (to build the hybrid) and not written to the summary table.
    compute_ids = list(requested_ids)
    for cid in requested_ids:
        for dep in CANDIDATE_DEPENDENCIES.get(cid, []):
            if dep not in compute_ids:
                compute_ids.append(dep)

    selected_ids = None if args.pages == "all" else {p.strip() for p in args.pages.split(",") if p.strip()}
    pages = load_pages(args.limit, selected_ids)
    if not pages:
        print("no pages selected", file=sys.stderr)
        return 1

    labels = load_labels(LABELS_PATH)
    if labels is None:
        print(f"NOTE: {LABELS_PATH} does not exist yet -- reporting raw OCR + timings only, no accuracy scoring.")

    need_tesseract = any(c.startswith("tesseract_") for c in compute_ids) or "grid_cell" in compute_ids
    tess_ctx = TesseractDocker(REPO_ROOT) if need_tesseract else None
    pacer = GroqPacer(otpm=args.groq_otpm) if "groq_cell_reread" in compute_ids else None

    scores: dict[str, CandidateScore] = {cid: CandidateScore(candidate=cid) for cid in requested_ids}
    per_page: dict[str, Any] = {}

    def process_all():
        for row in pages:
            page_id = row["page_id"]
            doc_type_hint = row["doc_type_hint"]
            img_path = GOLDEN_DIR / "pages" / f"{page_id}.png"
            if not img_path.exists():
                print(f"WARNING: missing image for {page_id}: {img_path}", file=sys.stderr)
                continue
            img_bytes = img_path.read_bytes()
            lang = lang_for(page_id, doc_type_hint)
            gold = labels.get(page_id) if labels else None
            page_out: dict[str, Any] = {"doc_type_hint": doc_type_hint, "lang": lang, "candidates": {}}
            computed: dict[str, Any] = {}   # every candidate's raw result dict, incl. dependency-only ones

            paddle_text_for_fallback = ""

            if "tesseract_psm6" in compute_ids:
                r = run_tesseract_candidate(tess_ctx, cache, page_id, img_path, img_bytes, psm=6)
                computed[r["candidate"]] = r
                _store(page_out, r)
                if "tesseract_psm6" in requested_ids:
                    _score(scores, page_id, gold, r)
            if "tesseract_psm4" in compute_ids:
                r = run_tesseract_candidate(tess_ctx, cache, page_id, img_path, img_bytes, psm=4)
                computed[r["candidate"]] = r
                _store(page_out, r)
                if "tesseract_psm4" in requested_ids:
                    _score(scores, page_id, gold, r)
            needs_paddle_text = {"paddle_full", "grid_cell", "groq_cell_reread"} & set(compute_ids)
            if needs_paddle_text:
                r = run_paddle_full_candidate(cache, page_id, img_path, img_bytes, lang)
                # text_sample is truncated to 400 chars; pull the full cached text for header/fallback use
                full = cache.get(content_hash(img_bytes, "paddle_full", f"lang={lang}"))
                paddle_text_for_fallback = full["text"] if full else r["text_sample"]
                computed[r["candidate"]] = r
                _store(page_out, r)
                if "paddle_full" in requested_ids:
                    _score(scores, page_id, gold, r)
            if "grid_cell" in compute_ids:
                r = run_grid_candidate(tess_ctx, cache, page_id, img_path, img_bytes, lang, overlay_dir,
                                        fallback_text=paddle_text_for_fallback)
                computed[r["candidate"]] = r
                _store(page_out, r)
                if "grid_cell" in requested_ids:
                    _score(scores, page_id, gold, r)
            if "mistral_table_read" in compute_ids:
                hr = run_mistral_table_read(img_bytes, doc_type_hint)
                page_out["candidates"]["mistral_table_read"] = {"ok": hr.ok, "error": hr.error, "model_id": hr.model_id}
            if "groq_cell_reread" in compute_ids:
                r = run_groq_candidate(cache, page_id, img_path, doc_type_hint, overlay_dir,
                                        fallback_text=paddle_text_for_fallback, pacer=pacer)
                computed[r["candidate"]] = r
                _store(page_out, r)
                if "groq_cell_reread" in requested_ids:
                    _score(scores, page_id, gold, r)
            if "hybrid_c_e" in compute_ids:
                r = build_hybrid_candidate(computed["grid_cell"], computed["groq_cell_reread"])
                _store(page_out, r)
                if "hybrid_c_e" in requested_ids:
                    _score(scores, page_id, gold, r)

            per_page[page_id] = page_out
            print(f"  {page_id} ({doc_type_hint}, lang={lang}) done")

    if tess_ctx is not None:
        with tess_ctx:
            process_all()
    else:
        process_all()

    write_outputs(out_dir, args, requested_ids, pages, labels is not None, scores, per_page)
    return 0


def _store(page_out, r):
    """Always record a candidate's output into this page's output dict, whether
    or not it was user-requested (dependency-only candidates like grid_cell/
    groq_cell_reread computed only to build the (f) hybrid still need their raw
    output -- e.g. token usage -- available for reporting)."""
    cand = r["candidate"]
    page_out["candidates"][cand] = {
        "seconds": r["seconds"], "from_cache": r.get("from_cache"),
        "parsed": r["parsed"], "extra": {k: v for k, v in r.items()
                                          if k not in ("candidate", "seconds", "parsed", "quota_units")},
    }


def _score(scores, page_id, gold, r):
    """Update accuracy/timing aggregates for a user-requested candidate."""
    cand = r["candidate"]
    scores[cand].seconds_per_page.append(r["seconds"])
    scores[cand].quota_units_per_page.append(r.get("quota_units", 0))
    ps = score_page(page_id, gold, r["parsed"])
    scores[cand].pages.append(ps)
    c = numeric_cer(gold, r["parsed"])
    if c is not None:
        scores[cand].cer_values.append(c)


GROQ_QWEN_VL_LIMITS = {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200_000, "otpm": 1000}


def _groq_usage(per_page: dict[str, Any], cand_id: str) -> dict[str, Any] | None:
    """Sum tokens/calls/cost for a hosted candidate ((e) groq_cell_reread, or (f)'s
    underlying groq_cell_reread dependency) across all pages, from the `extra`
    fields `_record` stored -- and project a daily page-capacity under Groq's
    published `groq-qwen-vl` limits (config/models.yaml)."""
    tin = tout = 0
    shadow = 0.0
    calls_live = 0
    bands = 0
    n_pages = 0
    for pdata in per_page.values():
        c = pdata["candidates"].get(cand_id)
        if not c:
            continue
        extra = c.get("extra", {})
        n_pages += 1
        tin += extra.get("tokens_in") or 0
        tout += extra.get("tokens_out") or 0
        shadow += extra.get("shadow_cost_usd") or 0.0
        calls_live += extra.get("n_calls_live") or 0
        bands += extra.get("n_bands") or 0
    if n_pages == 0:
        return None
    mean_tokens_in = tin / n_pages
    mean_tokens_out = tout / n_pages
    mean_tokens_total = mean_tokens_in + mean_tokens_out
    mean_calls_per_page = bands / n_pages
    lim = GROQ_QWEN_VL_LIMITS
    # Three independent daily ceilings; the binding one is whichever gives the
    # smallest page count. tpd (total tokens/day) is normally the tightest --
    # otpm's per-minute cap allows far more tokens/day than tpd permits at all.
    cap_by_tpd = lim["tpd"] / mean_tokens_total if mean_tokens_total else None
    cap_by_rpd = lim["rpd"] / mean_calls_per_page if mean_calls_per_page else None
    cap_by_otpm_continuous = (lim["otpm"] * 60 * 24) / mean_tokens_out if mean_tokens_out else None
    candidates = [c for c in (cap_by_tpd, cap_by_rpd, cap_by_otpm_continuous) if c is not None]
    projected_daily_pages = min(candidates) if candidates else None
    return {
        "n_pages_measured": n_pages, "total_tokens_in": tin, "total_tokens_out": tout,
        "total_shadow_cost_usd": shadow, "total_calls_live": calls_live, "total_bands": bands,
        "mean_tokens_in_per_page": mean_tokens_in, "mean_tokens_out_per_page": mean_tokens_out,
        "mean_calls_per_page": mean_calls_per_page,
        "projected_daily_pages_by_tpd_200k": cap_by_tpd,
        "projected_daily_pages_by_rpd_1000": cap_by_rpd,
        "projected_daily_pages_by_otpm_1000_per_min": cap_by_otpm_continuous,
        "projected_daily_pages": projected_daily_pages,
    }


def write_outputs(out_dir: Path, args, candidate_ids, pages, has_labels, scores, per_page):
    aggregate = {}
    for cid, cs in scores.items():
        mean_s = cs.mean_seconds_per_page
        aggregate[cid] = {
            "n_pages": len(cs.seconds_per_page),
            "mean_seconds_per_page": mean_s,
            "total_seconds": sum(cs.seconds_per_page),
            "mean_cer": cs.mean_cer,
            "overall_exact_rate": cs.overall_exact_rate() if has_labels else None,
            "field_exact_rate": {
                f: cs.field_exact_rate(f) for f in
                ("village", "unit_no", "block_no", "doc_date", "doc_no",
                 "survey_no", "sub_div", "extent_ha", "owner", "amount_rs", "classification")
            } if has_labels else None,
            "estimated_full_corpus_seconds": (mean_s * FULL_CORPUS_PAGES / args.workers) if mean_s else None,
            "estimated_full_corpus_hours": (mean_s * FULL_CORPUS_PAGES / args.workers / 3600) if mean_s else None,
        }
        if cid == "groq_cell_reread":
            aggregate[cid]["groq_usage"] = _groq_usage(per_page, "groq_cell_reread")
        if cid == "hybrid_c_e":
            aggregate[cid]["groq_usage"] = _groq_usage(per_page, "groq_cell_reread")

    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidates_run": candidate_ids,
        "pages": [p["page_id"] for p in pages],
        "has_labels": has_labels,
        "workers_for_estimate": args.workers,
        "full_corpus_pages": FULL_CORPUS_PAGES,
        "per_page": per_page,
        "aggregate": aggregate,
        "router_available": router_available(),
    }
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    header_line = (
        "| candidate | n | mean s/page | mean CER | exact rate | est. full corpus (12,564 pages, "
        f"{args.workers} workers) |"
    )
    lines = ["# OCR bake-off summary (T0.5)", "",
              f"Generated: {results['generated_at']}",
              f"Pages: {len(pages)}  Candidates: {', '.join(candidate_ids)}  Labels present: {has_labels}",
              "",
              header_line,
              "|---|---|---|---|---|---|"]
    for cid, agg in aggregate.items():
        mean_s = f"{agg['mean_seconds_per_page']:.2f}" if agg["mean_seconds_per_page"] is not None else "n/a"
        mean_cer = f"{agg['mean_cer']:.3f}" if agg["mean_cer"] is not None else "n/a"
        exact = f"{agg['overall_exact_rate']:.2%}" if agg["overall_exact_rate"] is not None else "n/a"
        hours = f"{agg['estimated_full_corpus_hours']:.1f}h" if agg["estimated_full_corpus_hours"] is not None else "n/a"
        lines.append(f"| {cid} | {agg['n_pages']} | {mean_s} | {mean_cer} | {exact} | {hours} |")

    groq_usages = {cid: agg["groq_usage"] for cid, agg in aggregate.items() if agg.get("groq_usage")}
    if groq_usages:
        usage_header = (
            "| candidate | pages | calls/page | tokens in/page | tokens out/page | shadow $/page | "
            "daily pages (tpd 200k) | daily pages (rpd 1000) | daily pages (otpm 1000/min) | projected |"
        )
        lines += ["", "## Groq usage and projected daily capacity (candidate e / f's table-row step)",
                  "", usage_header, "|---|---|---|---|---|---|---|---|---|---|"]
        for cid, u in groq_usages.items():
            shadow_per_page = u["total_shadow_cost_usd"] / u["n_pages_measured"] if u["n_pages_measured"] else 0
            lines.append(
                f"| {cid} | {u['n_pages_measured']} | {u['mean_calls_per_page']:.1f} | "
                f"{u['mean_tokens_in_per_page']:.0f} | {u['mean_tokens_out_per_page']:.0f} | "
                f"${shadow_per_page:.5f} | {u['projected_daily_pages_by_tpd_200k']:.0f} | "
                f"{u['projected_daily_pages_by_rpd_1000']:.0f} | "
                f"{u['projected_daily_pages_by_otpm_1000_per_min']:.0f} | "
                f"**{u['projected_daily_pages']:.0f} pages/day** |"
            )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out_dir / 'results.json'} and {out_dir / 'summary.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
