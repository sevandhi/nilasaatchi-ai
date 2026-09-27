"""Stage-B batch runner (D-029/D-032/D-033): extract priority-type pages through app.router.

    uv run python -m pipeline.extract.batch --types AWARD_7_2,AWARD_7_3,FORM_F,LDR,CHITTA,GO,AS,LPS,DISBURSEMENT \
        --workers 4 --budget-usd 7            # dry run (default): page counts + cost estimate, no calls
    ... --run                                 # actually extract (needs the user's go-ahead, D-029)

- selection: every page of a scheme-relevant document (scheme_relevance ≠ other_scheme); its type is
  the typed document_segment covering it, else document.classified_type (classification v2 may
  still be running: untyped pages fall back to the document type, pages of unknown type are skipped).
  The corpus is already de-duplicated (one document row per sha256).
- order: priority GO/AS/LPS → AWARD_7_2/7_3 → FORM_F → DISBURSEMENT/COURT_DEPOSIT → LDR/POSSESSION →
  SEC32 → FORM_E → CHITTA → rest; whole documents go to one worker (header carry-over needs page order).
- resumable: a page whose output JSON exists (data/extract/pages/<doc_id>/<page_no>.json, status ≠
  failed) is skipped; VLM reads are content-hash cached, so a restart re-spends nothing.
- stops cleanly: before the next page when spent + expected page cost > --budget-usd; immediately when
  the router refuses Bedrock with its AWS spend guard (`quota:aws_spend`, D-028) or after
  --max-consecutive-failures VLM failures (e.g. expired SSO session).
- progress log: data/extract/batch_progress.jsonl (one line per page) + batch_state.json (totals).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "extract" / "pages"
RENDER_DIR = REPO_ROOT / "data" / "pages300"
LOG_PATH = REPO_ROOT / "data" / "extract" / "batch_progress.jsonl"
STATE_PATH = REPO_ROOT / "data" / "extract" / "batch_state.json"
RENDER_DPI = 300
from .vlm import PREFERRED_MODEL  # noqa: E402

PRIORITY = ["GO", "AS", "AS_PROPOSAL", "LPS", "AWARD_7_2", "AWARD_7_3", "FORM_F", "DISBURSEMENT", "COURT_DEPOSIT",
            "BANK_INSTRUMENT", "LDR", "POSSESSION_CERT", "SEC32_NOTICE", "SEC32_ERRATA", "FORM_E", "CHITTA"]
# measured on the golden set (router ledger, Bedrock Ministral 3 8B): primary whole page, second read crop
PRIMARY_USD = 0.00066
SECOND_USD = 0.00107
SECOND_READ_RATE = 0.40          # share of golden pages that got a second read (8/20)

SELECT_SQL = """
SELECT d.id, d.sha256, d.path, d.classified_type, d.folder_label, p.page_no, p.id AS page_id,
       p.text_layer_ok, (SELECT s.doc_type FROM document_segment s
                          WHERE s.document_id = d.id AND p.page_no BETWEEN s.page_start AND s.page_end
                            AND s.doc_type IS NOT NULL
                          ORDER BY s.seg_no LIMIT 1) AS seg_type
FROM document d JOIN page p ON p.document_id = d.id
WHERE coalesce(d.scheme_relevance, 'unknown') <> 'other_scheme'
ORDER BY d.id, p.page_no
"""


def priority_of(t: str) -> int:
    return PRIORITY.index(t) if t in PRIORITY else len(PRIORITY)


def select_pages(conn, types: set[str], max_pages: dict[str, int] | None = None) -> list[dict]:
    """max_pages: per-type cap on page_no (e.g. {"FORM_F": 2} keeps only pages 1-2 of each Form F; D-047)."""
    rows = conn.execute(SELECT_SQL).fetchall()
    pages = []
    max_pages = max_pages or {}
    for did, sha, path, ctype, folder, page_no, page_id, tl_ok, seg_type in rows:
        t = seg_type or ctype
        if t not in types:
            continue
        if t in max_pages and page_no > max_pages[t]:
            continue
        pages.append({"doc_id": did, "sha256": sha, "path": path, "type": t, "type_source":
                      "segment" if seg_type else "document", "folder": folder, "page_no": page_no,
                      "page_id": page_id, "text_layer_ok": bool(tl_ok)})
    return pages


def out_path(doc_id: int, page_no: int) -> Path:
    return OUT_DIR / str(doc_id) / f"{page_no}.json"


def is_done(doc_id: int, page_no: int) -> bool:
    p = out_path(doc_id, page_no)
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text()).get("status") != "failed"
    except (OSError, ValueError):
        return False


def estimate(pages: list[dict]) -> dict:
    todo = [p for p in pages if not is_done(p["doc_id"], p["page_no"])]
    per_page = PRIMARY_USD + SECOND_READ_RATE * SECOND_USD
    by_type: dict[str, dict] = {}
    for p in pages:
        d = by_type.setdefault(p["type"], {"pages": 0, "docs": set(), "done": 0, "from_segment": 0})
        d["pages"] += 1
        d["docs"].add(p["doc_id"])
        d["done"] += int(is_done(p["doc_id"], p["page_no"]))
        d["from_segment"] += int(p["type_source"] == "segment")
    table = {t: {"pages": d["pages"], "docs": len(d["docs"]), "already_done": d["done"],
                 "typed_by_segment": d["from_segment"], "est_usd": round((d["pages"] - d["done"]) * per_page, 2)}
             for t, d in sorted(by_type.items(), key=lambda kv: priority_of(kv[0]))}
    return {"by_type": table, "pages_total": len(pages), "pages_todo": len(todo),
            "est_usd_primary": round(len(todo) * PRIMARY_USD, 2),
            "est_usd_second": round(len(todo) * SECOND_READ_RATE * SECOND_USD, 2),
            "est_usd_total": round(len(todo) * per_page, 2),
            "assumptions": {"primary_usd_per_page": PRIMARY_USD, "second_usd_per_read": SECOND_USD,
                            "second_read_rate": SECOND_READ_RATE}}


def render_page(pdf_path: Path, sha: str, page_no: int) -> Path:
    import pypdfium2 as pdfium
    out = RENDER_DIR / sha[:2] / sha / f"{page_no}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_no - 1]
        try:
            page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB").save(out, "PNG")
        finally:
            page.close()
    finally:
        doc.close()
    return out


class Runner:
    def __init__(self, budget_usd: float, max_fail: int):
        self.budget = budget_usd
        self.max_fail = max_fail
        self.spent = 0.0
        self.consecutive_fail = 0
        self.stop_reason: str | None = None
        self.counts = {"done": 0, "skipped": 0, "failed": 0}
        self.lock = threading.Lock()
        self.expected = PRIMARY_USD + SECOND_READ_RATE * SECOND_USD

    def may_start(self) -> bool:
        with self.lock:
            if self.stop_reason:
                return False
            if self.spent + self.expected > self.budget:
                self.stop_reason = f"budget: spent ${self.spent:.4f} of ${self.budget}"
                return False
            return True

    def record(self, page: dict, res: dict | None, err: str | None) -> None:
        cost = sum((r.get("cost_usd") or 0.0) for r in (res or {}).get("reads") or [] if not r.get("from_cache"))
        errors = [r.get("error") or "" for r in (res or {}).get("reads") or [] if not r.get("ok")]
        with self.lock:
            self.spent += cost
            if err or (res and res.get("status") == "failed") or (res and "vlm_failed" in res.get("review_reasons", [])):
                self.consecutive_fail += 1
                self.counts["failed"] += 1
            else:
                self.consecutive_fail = 0
                self.counts["done"] += 1
            fresh_models = {r.get("model_id") for r in (res or {}).get("reads") or []
                            if r.get("ok") and not r.get("from_cache") and r.get("model_id")}
            if any("quota:aws_spend" in e for e in errors):
                self.stop_reason = "router AWS spend guard tripped (D-028: ask the user)"
            elif any("SSO session expired" in e or "credentials:" in e for e in errors):
                self.stop_reason = "AWS SSO session expired: run make aws-login, then re-run (resumes from cache)"
            elif fresh_models and not fresh_models <= {PREFERRED_MODEL}:
                # D-048: one fallback read (e.g. a schema-invalid second read) must not stop the batch.
                # A page whose PRIMARY read came from a fallback model is marked for redo, not accepted;
                # only 5 consecutive fallback pages mean Bedrock itself is unavailable.
                primary = next((r for r in (res or {}).get("reads") or [] if r.get("route") == "primary"), {})
                if primary.get("model_id") and primary.get("model_id") != PREFERRED_MODEL and res is not None:
                    res["status"] = "failed"
                    out_path(page["doc_id"], page["page_no"]).write_text(json.dumps(res, ensure_ascii=False, default=str))
                self.consecutive_fallback = getattr(self, "consecutive_fallback", 0) + 1
                if self.consecutive_fallback >= 5:
                    self.stop_reason = ("5 consecutive pages served by a fallback model "
                                        f"{sorted(m for m in fresh_models if m)}: Bedrock unavailable? run make aws-login")
            else:
                self.consecutive_fallback = 0
            if self.stop_reason:
                pass
            elif self.consecutive_fail >= self.max_fail:
                self.stop_reason = f"{self.consecutive_fail} consecutive VLM failures (SSO expired? run make aws-login)"
            line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "doc_id": page["doc_id"], "page_no": page["page_no"],
                    "type": page["type"], "status": (res or {}).get("status"), "error": err,
                    "cost_usd": round(cost, 6), "spent_usd": round(self.spent, 5),
                    "review_queue": (res or {}).get("review_queue")}
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            STATE_PATH.write_text(json.dumps({"spent_usd": round(self.spent, 5), "budget_usd": self.budget,
                                              "stop_reason": self.stop_reason, **self.counts}, indent=1))


def run_document(runner: Runner, pages: list[dict], ocr, text_by_page: dict) -> None:
    from .page import extract_page
    carry = None
    prev_no = None
    for p in pages:
        if is_done(p["doc_id"], p["page_no"]):
            with runner.lock:
                runner.counts["skipped"] += 1
            prev_no = None
            continue
        if not runner.may_start():
            return
        res, err = None, None
        try:
            img = render_page(REPO_ROOT / p["path"], p["sha256"], p["page_no"])
            res = extract_page(img, doc_id=p["doc_id"], page_no=p["page_no"],
                               page_ref=f"{p['path']}#p{p['page_no']}",
                               text_layer=text_by_page.get(p["page_id"]) if p["text_layer_ok"] else None,
                               ocr=ocr, classified_type=p["type"], folder_prior=None,
                               carry=carry if prev_no == p["page_no"] - 1 else None)
            res["selection"] = {"type": p["type"], "type_source": p["type_source"]}
            op = out_path(p["doc_id"], p["page_no"])
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
            carry, prev_no = res.get("header"), p["page_no"]
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the batch
            err = f"{type(exc).__name__}: {exc}"[:300]
        runner.record(p, res, err)


def main() -> None:
    import psycopg
    from dotenv import load_dotenv

    ap = argparse.ArgumentParser()
    ap.add_argument("--types", required=True, help="comma-separated doc types")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--run", action="store_true", help="actually extract (default: dry run, no calls)")
    ap.add_argument("--limit", type=int, default=0, help="max pages this run (0 = all)")
    ap.add_argument("--max-consecutive-failures", type=int, default=5)
    ap.add_argument("--max-pages", default="", help="per-type page cap, e.g. FORM_F=2 (D-047)")
    a = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    types = {t.strip() for t in a.types.split(",") if t.strip()}
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        caps = {k.strip(): int(v) for k, v in (x.split("=") for x in a.max_pages.split(",") if "=" in x)}
        pages = select_pages(conn, types, caps)
        est = estimate(pages)
        print(json.dumps({"mode": "run" if a.run else "dry-run", "budget_usd": a.budget_usd, **est}, indent=1))
        if est["est_usd_total"] > a.budget_usd:
            print(f"NOTE: estimate ${est['est_usd_total']} exceeds budget ${a.budget_usd}: the run will stop at the "
                  f"budget, lowest-priority types last.")
        if not a.run:
            return
        text_by_page = dict(conn.execute("SELECT id, text FROM page WHERE text_layer_ok").fetchall())
    pages.sort(key=lambda p: (priority_of(p["type"]), p["doc_id"], p["page_no"]))
    if a.limit:
        pages = [p for p in pages if not is_done(p["doc_id"], p["page_no"])][: a.limit]
    docs: dict[int, list[dict]] = {}
    for p in pages:
        docs.setdefault(p["doc_id"], []).append(p)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    runner = Runner(a.budget_usd, a.max_consecutive_failures)
    from .tesseract import Tesseract
    with Tesseract() as ocr, ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = [pool.submit(run_document, runner, ps, ocr, text_by_page) for ps in docs.values()]
        for f in as_completed(futs):
            f.result()
    print(json.dumps({"spent_usd": round(runner.spent, 4), "stop_reason": runner.stop_reason, **runner.counts}))


if __name__ == "__main__":
    main()
