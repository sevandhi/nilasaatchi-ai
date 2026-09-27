"""Demo upload test files, and cleanup of what the demo uploaded.

    uv run python scripts/demo_uploads.py build     # -> data/demo_uploads/ (git-ignored: real documents)
    uv run python scripts/demo_uploads.py cleanup   # remove every document created by an upload
    uv run python scripts/demo_uploads.py working   # -> data/demo_uploads/working/ (20 known-good rescans)
    uv run python scripts/demo_uploads.py verify    # upload each working file via the API (needs `make api`
                                                    # + AWS login), save its report, write working/RESULTS.md

`build` makes one file per case the upload path handles. The "rescan" files re-render real dataset
pages (slight rotation, brightness, JPEG), the way a new scanned copy of a document would arrive: new
bytes and new pixels, so dedup, the extraction cache and the loader's page-hash key all treat them as
new and the table reader does a fresh read. Dataset/ is only read.

`cleanup` deletes documents whose path is under data/inbox/ (i.e. created by an upload; dataset
documents never live there), cascading to their pages/extractions/facts/events/links/review items,
then recomputes findings and clears the upload history, so the demo leaves the app as it was.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import psycopg
import pypdfium2 as pdfium
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageEnhance

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
OUT = REPO_ROOT / "data" / "demo_uploads"
INBOX = REPO_ROOT / "data" / "inbox"
LAND = "Dataset/Land_documents/LA_Patta_land_Documents"

RESCANS = [  # (output name, source pdf, rotation degrees)
    ("02_award_7_2_rescan_umarikottai.pdf", f"{LAND}/7(2) Award/b8award.pdf", 0.6),
    ("03_chitta_rescan_peroorani.pdf", f"{LAND}/Patta Transferred/Peroorani_Sipcot_S (2).pdf", -0.5),
    ("04_disbursement_rescan_allikulam.pdf", f"{LAND}/Amount Disbursed/AmountDisbursedblock8.pdf", 0.4),
    ("05_ldr_rescan_melathattaparai.pdf", f"{LAND}/LDR Issued/LDR_07 (10).pdf", -0.4),
    ("06_sec32_notice_rescan_ramasamypuram.pdf", f"{LAND}/3(2) Notice/32NoticePap4.pdf", 0.5),
    ("07_other_scheme_gazette_rescan.pdf", f"{LAND}/3(1) Publish/31Publishblock4.pdf", -0.3),
]
DUPLICATE_SRC = f"{LAND}/7(2) Award/b8award.pdf"
WORKING = OUT / "working"
# dataset documents whose original read passed its checks and linked parcels (picked by SQL on
# extraction.self_consistency + parcel_fact.match_status); (document id, declared type, village)
WORKING_SOURCES = [
    (434, "AWARD_7_2", "umarikottai"), (625, "AWARD_7_2", "allikulam"), (517, "AWARD_7_2", "keelathattaparai"),
    (492, "AWARD_7_2", "peroorani"), (494, "AWARD_7_2", "peroorani"), (359, "AWARD_7_2", "ramasamypuram"),
    (495, "AWARD_7_2", "peroorani"), (2245, "LDR", "allikulam"), (2243, "LDR", "allikulam"),
    (2299, "LDR", "allikulam"), (2240, "LDR", "allikulam"), (2234, "LDR", "keelathattaparai"),
    (2316, "LDR", "keelathattaparai"), (2225, "LDR", "keelathattaparai"), (2258, "LDR", "peroorani"),
    (2249, "LDR", "melathattaparai"), (2208, "LDR", "melathattaparai"), (211, "SEC32_NOTICE", "peroorani"),
    (1955, "FORM_F", "umarikottai"), (1504, "FORM_F", "allikulam"),
]
API = os.environ.get("API_BASE", "http://localhost:8000")


def _conn() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL") or dotenv_values(REPO_ROOT / ".env").get("DATABASE_URL")
    return psycopg.connect(url)


def rescan(src: Path, dst: Path, angle: float, dpi: int = 170) -> int:
    pdf = pdfium.PdfDocument(str(src))
    pages = []
    for page in pdf:
        img = page.render(scale=dpi / 72).to_pil().convert("RGB")
        img = img.rotate(angle, expand=True, fillcolor="white", resample=Image.BICUBIC)
        img = ImageEnhance.Brightness(img).enhance(0.97)
        pages.append(img)
    pdf.close()
    pages[0].save(dst, "PDF", resolution=dpi, save_all=True, append_images=pages[1:], quality=85)
    return len(pages)


def unrelated_letter(dst: Path) -> None:
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    lines = ["Quarterly Newsletter - Community Library", "", "Dear members,",
             "The reading room will be closed on Sunday for maintenance.",
             "New arrivals this month include books on astronomy and cooking.",
             "Thank you for your continued support.", "", "The Librarian"]
    for i, line in enumerate(lines):
        d.text((120, 150 + i * 60), line, fill="black")
    img.save(dst, "PDF", resolution=150)


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / DUPLICATE_SRC, OUT / "01_duplicate_award_b8.pdf")
    for name, src, angle in RESCANS:
        n = rescan(REPO_ROOT / src, OUT / name, angle)
        print(f"{name}: {n} page(s) from {src}")
    unrelated_letter(OUT / "08_unrelated_newsletter.pdf")
    (OUT / "09_fake_pdf_text.pdf").write_text("This is plain text saved with a .pdf name.\n")
    (OUT / "10_notes_not_pdf.txt").write_text("A text file: uploads accept PDF only.\n")
    award = (REPO_ROOT / DUPLICATE_SRC).read_bytes()
    (OUT / "11_corrupt_truncated.pdf").write_bytes(award[: len(award) // 3])
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT.relative_to(REPO_ROOT)}")


def working() -> None:
    WORKING.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        paths = dict(conn.execute("SELECT id, path FROM document WHERE id = ANY(%s)",
                                  ([d for d, _, _ in WORKING_SOURCES],)).fetchall())
    for i, (doc_id, t, village) in enumerate(WORKING_SOURCES, 1):
        name = f"{i:02d}_{t}_{village}.pdf"
        n = rescan(REPO_ROOT / paths[doc_id], WORKING / name, 0.3 + (i % 5) * 0.1 * (-1) ** i)
        print(f"{name}: {n} page(s) from document {doc_id}")


def verify() -> None:
    import json
    import time
    import urllib.request

    import httpx

    reports = WORKING / "reports"
    reports.mkdir(exist_ok=True)
    rows = []
    for f in sorted(WORKING.glob("[0-9]*.pdf")):
        t = f.stem.split("_", 1)[1].rsplit("_", 1)[0]
        with f.open("rb") as fh:
            acc = httpx.post(f"{API}/ingest/documents", files={"file": (f.name, fh, "application/pdf")},
                             data={"doc_type": t}, timeout=120).json()
        job_id = acc["job_id"]
        if acc["status"] == "duplicate":  # uploaded by an earlier (interrupted) verify run: reuse that job
            jobs = httpx.get(f"{API}/ingest/jobs", params={"kind": "document", "limit": 200}, timeout=60).json()["jobs"]
            job_id = next((j["id"] for j in jobs if j["filename"] == f.name and j["status"] != "duplicate"), job_id)
        t0 = time.time()
        while True:
            job = json.load(urllib.request.urlopen(f"{API}/ingest/jobs/{job_id}"))
            if job["status"] not in ("queued", "running") or time.time() - t0 > 600:
                break
            time.sleep(4)
        r = job.get("result") or {}
        for fmt, ext in (("pdf", "pdf"), ("csv", "csv")):
            rep = httpx.get(f"{API}/ingest/jobs/{job_id}/report", params={"fmt": fmt}, timeout=120)
            if rep.status_code == 200:
                (reports / f"{f.stem}.{ext}").write_bytes(rep.content)
        classify = next((s["detail"] for s in job["stages"] if s["name"] == "classify"), "")
        rows.append({"file": f.name, "status": job["status"], "rows": r.get("extraction_rows"),
                     "facts": r.get("facts"), "linked": len(r.get("linked_parcels") or []),
                     "example": ", ".join((r.get("linked_parcels") or [])[:3]), "review": r.get("review_items"),
                     "ai": "agrees" if "agrees" in classify else (classify.split("AI suggested ")[-1].split(";")[0]
                                                                 if "AI suggested" in classify else "-"),
                     "secs": round(time.time() - t0)})
        print(rows[-1])
    lines = ["| File | Status | Rows read | Facts | Parcels linked | Example parcels | Review items | AI type guess | Seconds |",
             "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {x['file']} | {x['status']} | {x['rows']} | {x['facts']} | {x['linked']} | {x['example']} | "
              f"{x['review']} | {x['ai']} | {x['secs']} |" for x in rows]
    (WORKING / "RESULTS.md").write_text("# Measured results (uploaded via the API with the declared type)\n\n"
                                        + "\n".join(lines) + "\n")
    print(f"wrote {WORKING.relative_to(REPO_ROOT)}/RESULTS.md and reports/")


def cleanup() -> None:
    import pipeline.findings.__main__ as findings_main

    with _conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM document WHERE path LIKE 'data/inbox/%'")]
        if ids:
            conn.execute("UPDATE ingest_job SET document_id = NULL WHERE document_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM document WHERE id = ANY(%s)", (ids,))
            conn.commit()
            fs = findings_main.run(conn)
            findings_main.upsert(conn, fs)
            conn.commit()
        conn.execute("DELETE FROM ingest_job WHERE kind = 'document'")  # clear the "Recent uploads" history
        conn.commit()
    for f in INBOX.glob("*.pdf"):
        f.unlink()
    print(f"removed {len(ids)} uploaded document(s) {ids}; findings recomputed; upload history and inbox emptied")


if __name__ == "__main__":
    {"build": build, "cleanup": cleanup, "working": working, "verify": verify}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
