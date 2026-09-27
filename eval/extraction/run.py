"""`make eval-extract`: `uv run python -m eval.extraction.run [--set golden|heldout] [--live|--no-live]`.

Runs pipeline.extract.page over a labelled page set and scores it:
- `--set golden` (default): eval/golden/mini_pages.csv + mini.jsonl, images eval/golden/pages/;
- `--set heldout`: eval/heldout/pages.csv + labels.jsonl, images eval/heldout/pages/ (or an
  `image` column in pages.csv). Held-out labels are only read by the scorer — never tune on them.
Cache-only by default (`--no-live`): only cached VLM reads are used and nothing is spent;
`--live` permits router calls for uncached pages (Bedrock ~$0.0007/page — `make aws-cost` first).
Doc-type prior = mini_pages.csv doc_type_hint (the lead's instruction: document.classified_type is
being rebuilt under D-030). Outputs go to data/extract/golden/ (git-ignored: contains PII).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from eval.extraction.score import score_page, summarise
from pipeline.extract.page import extract_page
from pipeline.extract.tesseract import Tesseract

ROOT = Path(__file__).resolve().parents[2]
SETS = {
    "golden": {"pages": ROOT / "eval/golden/mini_pages.csv", "labels": ROOT / "eval/golden/mini.jsonl",
               "images": ROOT / "eval/golden/pages", "out": ROOT / "data/extract/golden"},
    "dev": {"pages": ROOT / "eval/dev/pages.csv", "labels": ROOT / "eval/dev/labels.jsonl",
            "images": ROOT / "eval/dev/pages", "out": ROOT / "data/extract/dev"},
    "dev2": {"pages": ROOT / "eval/dev2/pages.csv", "labels": ROOT / "eval/dev2/labels.jsonl",
             "images": ROOT / "eval/dev2/pages", "out": ROOT / "data/extract/dev2"},
    "heldout": {"pages": ROOT / "eval/heldout/pages.csv", "labels": ROOT / "eval/heldout/labels.jsonl",
                "images": ROOT / "eval/heldout/pages", "out": ROOT / "data/extract/heldout"},
    # D-054: fresh, never-seen held-out set for the final extraction score (replaces the partially seen heldout)
    "heldout2": {"pages": ROOT / "eval/heldout2/pages.csv", "labels": ROOT / "eval/heldout2/labels.jsonl",
                 "images": ROOT / "eval/heldout2/pages", "out": ROOT / "data/extract/heldout2"},
}
OUT = SETS["golden"]["out"]
TARGETS = {"survey": 0.92, "extent_ha": 0.90, "owner": 0.85, "header_acc": 0.95}


def image_for(row: dict, cfg: dict) -> Path:
    if row.get("image"):
        p = Path(row["image"])
        return p if p.is_absolute() else ROOT / p
    return cfg["images"] / f"{row['page_id']}.png"


def run_extraction(live: bool, second_read: bool, only: set[str] | None = None, cfg: dict | None = None
                   ) -> dict[str, dict]:
    cfg = cfg or SETS["golden"]
    OUT = cfg["out"]
    OUT.mkdir(parents=True, exist_ok=True)
    preds: dict[str, dict] = {}
    prev = None
    with Tesseract() as tess:
        for p in csv.DictReader(cfg["pages"].open()):
            pid = p["page_id"]
            if only and pid not in only:
                f = OUT / f"{pid}.json"
                if f.exists():
                    preds[pid] = json.loads(f.read_text())
                continue
            # continuation page of the previous golden page's PDF → carry its village/unit/block
            carry = None
            if prev and p.get("source_pdf") and prev[0] == p["source_pdf"] and \
                    int(p.get("page_no") or 0) == prev[1] + 1:
                carry = prev[2]
            res = extract_page(image_for(p, cfg), doc_id=pid, page_no=int(p.get("page_no") or 0),
                               page_ref=f"{p.get('source_pdf')}#p{p.get('page_no')}", ocr=tess,
                               classified_type=p.get("doc_type_hint") or None, allow_calls=live, allow_second_read=second_read,
                               carry=carry)
            prev = (p.get("source_pdf"), int(p.get("page_no") or 0), res.get("header"))
            (OUT / f"{pid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
            preds[pid] = res
    return preds


def usage(preds: dict[str, dict]) -> dict:
    u = {"fresh_calls": 0, "cached_reads": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "by_model": {}}
    for res in preds.values():
        for r in res.get("reads") or []:
            if r.get("from_cache"):
                u["cached_reads"] += 1
                continue
            if r.get("ok") is None or r.get("error") == "not cached and calls disabled":
                continue
            u["fresh_calls"] += 1
            u["tokens_in"] += r.get("tokens_in") or 0
            u["tokens_out"] += r.get("tokens_out") or 0
            u["cost_usd"] += r.get("cost_usd") or 0.0
            m = r.get("model_id") or "none"
            u["by_model"][m] = u["by_model"].get(m, 0) + 1
    u["cost_usd"] = round(u["cost_usd"], 5)
    return u


def cached_token_totals(preds: dict[str, dict]) -> dict:
    """Tokens of every read behind the current outputs (cached or not) = the cost of one fresh pass."""
    from pipeline.extract.cache import ContentCache
    from pipeline.extract.vlm import CACHE_DIR
    c = ContentCache(CACHE_DIR)
    tot = {"reads": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    for res in preds.values():
        for r in res.get("reads") or []:
            hit = c.get(r.get("cache_key") or "") if r.get("cache_key") else None
            if hit:
                tot["reads"] += 1
                tot["tokens_in"] += hit.get("tokens_in") or 0
                tot["tokens_out"] += hit.get("tokens_out") or 0
                tot["cost_usd"] += hit.get("cost_usd") or 0.0
    tot["cost_usd"] = round(tot["cost_usd"], 5)
    return tot


def fmt(v) -> str:
    return "  -  " if v is None else f"{v * 100:5.1f}"


def report(golds: list[dict], preds: dict[str, dict]) -> dict:
    groups = {"legible": [g for g in golds if not g.get("needs_human")],
              "needs_human": [g for g in golds if g.get("needs_human")], "all": golds}
    result: dict = {}
    for name, gs in groups.items():
        details = [score_page(g, preds.get(g["page_id"])) for g in gs]
        details_fl = [score_page(g, preds.get(g["page_id"]), skip_uncertain=True) for g in gs]
        result[name] = {"page_level": summarise(details), "certain_fields_only": summarise(details_fl),
                        "pages": {d["page_id"]: {"doc_type": d["doc_type"], "pred_doc_type": d["pred_doc_type"],
                                                 "status": d["status"], "fails": d["fails"]} for d in details}}
    # per doc type (recall on gold values)
    per_type: dict[str, dict] = {}
    for g in golds:
        d = score_page(g, preds.get(g["page_id"]))
        m = summarise([d])
        k = f"{g['doc_type']}{' (NH)' if g.get('needs_human') else ''}"
        per_type.setdefault(k, []).append(d)
    result["per_doc_type"] = {k: summarise(v) for k, v in per_type.items()}
    return result


def print_report(res: dict, u: dict, tot: dict) -> None:
    cols = ["survey", "extent_ha", "extent_ac", "owner", "amount_rs", "classification", "headers", "header_acc",
            "doc_type"]
    print("\n== Field recall (= accuracy on gold values) / precision, %")
    print(f"{'group':28s}" + "".join(f"{c[:10]:>12s}" for c in cols) + "   rowEM")
    for name in ("legible", "needs_human", "all"):
        for view in ("page_level", "certain_fields_only"):
            m = res[name][view]
            line = f"{name + ('' if view == 'page_level' else ' (certain)'):28s}"
            for c in cols:
                x = m.get(c) or {}
                line += f"  {fmt(x.get('recall'))}/{fmt(x.get('precision')).strip():>5s}"[:12].rjust(12)
            print(line + f"   {fmt(m.get('_row_exact_match'))}  ({m['_pages']}p, {m['_rows']} rows)")
    print("\n== Per doc type (recall %, n gold values)")
    print(f"{'doc_type':22s}" + "".join(f"{c[:9]:>14s}" for c in cols[:7]))
    for k, m in sorted(res["per_doc_type"].items()):
        line = f"{k:22s}"
        for c in cols[:7]:
            x = m.get(c) or {}
            line += (f"{fmt(x.get('recall'))} (n={x.get('n_gold', 0)})" if x.get("n_gold") else "      -").rjust(14)
        print(line)
    print("\n== Targets on LEGIBLE pages (page-level view)")
    for f, tgt in TARGETS.items():
        v = (res["legible"]["page_level"].get(f) or {}).get("recall")
        print(f"  {f:12s} {fmt(v)}%  target {tgt * 100:.0f}%  {'MET' if v is not None and v >= tgt else 'MISSED'}")
    print(f"\n== Router usage this run: {u}")
    print(f"== Tokens behind current outputs (one fresh pass): {tot}")


def routing_summary(golds: list[dict], preds: dict[str, dict]) -> dict:
    out = {"status": {}, "gold_vs_pred": {}, "review_queue": 0, "owner_sample": 0, "owner_reads": {}}
    for g in golds:
        r = preds.get(g["page_id"]) or {}
        st = r.get("status")
        out["status"][st] = out["status"].get(st, 0) + 1
        k = f"{'needs_human' if g.get('needs_human') else 'legible'}->{'review' if r.get('review_queue') else 'auto'}"
        out["gold_vs_pred"][k] = out["gold_vs_pred"].get(k, 0) + 1
        out["review_queue"] += int(bool(r.get("review_queue")))
        out["owner_sample"] += int("owner_sample" in (r.get("queue_reasons") or []))
        for s, n in (r.get("owner_reads") or {}).items():
            out["owner_reads"][s] = out["owner_reads"].get(s, 0) + n
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="golden", dest="page_set")
    live = ap.add_mutually_exclusive_group()
    live.add_argument("--live", action="store_true", help="allow router calls for uncached pages")
    live.add_argument("--no-live", action="store_true", help="cache-only (default): no router calls")
    ap.add_argument("--no-second-read", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated page ids to (re)extract")
    ap.add_argument("--score-only", action="store_true", help="score existing outputs without re-extracting")
    a = ap.parse_args()
    cfg = SETS[a.page_set]
    for k in ("pages", "labels"):
        if not cfg[k].exists():
            raise SystemExit(f"{a.page_set}: missing {cfg[k]}")
    golds = [json.loads(line) for line in cfg["labels"].open() if line.strip()]
    if a.score_only:
        preds = {p.stem: json.loads(p.read_text()) for p in cfg["out"].glob("*.json") if p.stem != "metrics"}
    else:
        preds = run_extraction(a.live and not a.no_live, not a.no_second_read,
                               set(a.only.split(",")) if a.only else None, cfg)
    res = report(golds, preds)
    u, tot = usage(preds), cached_token_totals(preds)
    routing = routing_summary(golds, preds)
    (cfg["out"] / "metrics.json").write_text(json.dumps({"set": a.page_set, "live": bool(a.live), "metrics": res,
                                                        "routing": routing, "usage": u, "tokens": tot},
                                                       ensure_ascii=False, indent=1, default=str))
    print(f"== set: {a.page_set}  ({'live' if a.live else 'cache-only'})")
    print_report(res, u, tot)
    fallback = {m: n for m, n in u["by_model"].items() if m not in ("bedrock-ministral-8b", "none")}
    if fallback:
        print(f"!! WARNING: {sum(fallback.values())} fresh reads served by fallback models {fallback} "
              f"(first-choice Bedrock unavailable: AWS SSO expired? run make aws-login). Scores are not comparable.")
    print(f"== Routing / review queue: {routing}")


if __name__ == "__main__":
    main()
