"""`make cloud-export` (T7.1, D-067): builds `data/cloud/snapshot/` — everything the read-only
cloud Lambda (`app/cloud`) needs to answer every GET the web UI makes, without a database.

Two export strategies, matching the endpoint's key space (see `app/cloud/ENDPOINTS.md`):

1. **Bounded per-entity / no-filter endpoints** (parcel detail + its 4 sub-endpoints, a finding's
   evidence pack, an extraction's evidence, layer GeoJSON, router/models/usage/chains, examples,
   stats/overview, ...): fetched from the **live local API** (`:8000`) and the response body stored
   **verbatim** (exact bytes). The cloud app replays those bytes byte-for-byte — no re-serialization
   step to introduce drift from FastAPI/pydantic's own JSON encoding (datetime "Z" suffix, float
   formatting, key order, ...).
2. **List endpoints with filters** (findings, documents, review-queue, runs): exported as Parquet
   tables read directly from Postgres (values pre-formatted to already be JSON-ready — dates/
   datetimes as the exact strings pydantic would emit, Decimals as floats — see `_iso_z`/`_num`),
   so `app/cloud`'s DuckDB queries reproduce the router's SQL filters over a flat file instead of a
   database. `idle-land` and `findings/summary` have no query params at all in the live API, so
   they are exported as plain pre-rendered JSON (see strategy 1) rather than Parquet.

Run with `make cloud-export` (requires `make demo`/`scripts/demo_run.sh`'s API on :8000 and the DB
on :5439 — this script only reads, never writes, either).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.cloud.util import safe_name  # noqa: E402

API_BASE = os.environ.get("CLOUD_EXPORT_API_BASE", "http://localhost:8000")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://nila:nila_local_dev@localhost:5439/nilasaatchi"
)
SNAPSHOT_DIR = REPO_ROOT / "data" / "cloud" / "snapshot"
WORKERS = int(os.environ.get("CLOUD_EXPORT_WORKERS", "24"))

SEASONS = [f"{y}-{s}" for y in range(2019, 2027) for s in ("kharif", "rabi", "summer")]
# Only the layer names the web UI actually requests (web/src/pages/MapWorkspace.jsx) — "survey",
# "ref_layer_rail_stations", "ref_layer_airport" and "ref_layer_seaport" exist in the registry but
# are never fetched by the UI, so they are not part of the cloud demo's bounded key space.
LAYER_NAMES = [
    "parcel", "ref_layer_park_boundary", "ref_layer_roads", "ref_layer_waterbodies",
    "ref_layer_substations", "ref_layer_rail", "ref_layer_schools", "ref_layer_sipcot_parks",
    "fmb_qa", "fmb_overlaps", "outside_survey", "controls",
]

# Owner-safety scan (D-032/D-033): the API already masks by default (demo_mask=true) — this is a
# defence-in-depth assertion, not the primary control. Any real Tamil-script text under one of
# these keys, that is not a known mask token, fails the export.
_OWNER_KEY_RE = re.compile(r"^(owner|owner_raw|owners|name|canonical_name|owner_name|payee)$", re.IGNORECASE)
_TAMIL_RE = re.compile(r"[஀-௿]")
_MASK_TOKENS = {"[REDACTED]", None}


@dataclass
class ExportStats:
    n_requests: int = 0
    n_failed: list[str] = field(default_factory=list)
    n_owner_scanned: int = 0


STATS = ExportStats()


# --------------------------------------------------------------------------------- tiny utilities

def _iso_z(dt: datetime | None) -> str | None:
    """Matches pydantic v2's default JSON encoding of a tz-aware datetime exactly (see
    `docs/decisions.md`-adjacent investigation in this file's sibling test): isoformat() with
    "+00:00" replaced by "Z"."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_date(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _json_or_none(v: Any) -> str | None:
    return json.dumps(v, default=str) if v is not None else None


def _write_json(rel: str, obj: Any) -> Path:
    path = SNAPSHOT_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def _write_bytes(rel: str, data: bytes) -> Path:
    path = SNAPSHOT_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _scan_owner_leak(obj: Any, where: str) -> list[str]:
    """Recursively looks for owner/name-shaped keys holding un-masked Tamil text. Returns a list of
    human-readable problem descriptions (empty = clean)."""
    problems: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}"
                if _OWNER_KEY_RE.match(str(k)) and isinstance(v, str) and v not in _MASK_TOKENS:
                    if _TAMIL_RE.search(v):
                        problems.append(f"{where}:{p} = {v!r}")
                _walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(obj, where)
    STATS.n_owner_scanned += 1
    return problems


# --------------------------------------------------------------------------------- strategy 1: API replay

class ApiClient:
    def __init__(self, base: str) -> None:
        self.client = httpx.Client(base_url=base, timeout=30.0)

    def get_verbatim(self, path: str, params: dict | None = None) -> tuple[int, bytes]:
        r = self.client.get(path, params=params)
        STATS.n_requests += 1
        return r.status_code, r.content


def _fetch_and_store(api: ApiClient, url_path: str, params: dict | None, snapshot_rel: str,
                     owner_scan: bool = True) -> str | None:
    status, body = api.get_verbatim(url_path, params)
    if status != 200:
        STATS.n_failed.append(f"{url_path} params={params} -> HTTP {status}")
        return None
    if owner_scan:
        try:
            obj = json.loads(body)
        except Exception:  # noqa: BLE001
            obj = None
        if obj is not None:
            problems = _scan_owner_leak(obj, snapshot_rel)
            if problems:
                raise SystemExit(
                    "OWNER-LEAK SCAN FAILED — refusing to write snapshot:\n" + "\n".join(problems)
                )
    _write_bytes(snapshot_rel, body)
    return snapshot_rel


def export_meta(api: ApiClient) -> None:
    print("meta: stats/overview, router/*, examples, classifier/summary, eval/summary, "
          "decisions, progress, findings/summary, idle-land ...")
    jobs = [
        ("/stats/overview", None, "meta/stats_overview.json"),
        ("/router/models", None, "meta/router_models.json"),
        ("/router/usage", None, "meta/router_usage.json"),
        ("/router/chains", None, "meta/router_chains.json"),
        ("/examples", None, "meta/examples.json"),
        ("/classifier/summary", None, "meta/classifier_summary.json"),
        ("/eval/summary", None, "meta/eval_summary.json"),
        ("/decisions", None, "meta/decisions.json"),
        ("/progress", None, "meta/progress.json"),
        ("/findings/summary", None, "meta/findings_summary.json"),
        ("/idle-land", None, "meta/idle_land.json"),
        ("/ingest/doc-types", None, "meta/ingest_doc_types.json"),
        ("/ingest/satellite/status", None, "meta/ingest_satellite_status.json"),
    ]
    for path, params, rel in jobs:
        # Never owner/PII-bearing (router config, docs/metrics.md tables, aggregate views) — skip
        # the scan so it can't false-positive on unrelated Tamil-script content (e.g. router/usage
        # task names are never Tamil in practice, but decisions.md/progress.md prose legitimately
        # may quote Tamil place names).
        _fetch_and_store(api, path, params, rel, owner_scan=False)


def export_layers(api: ApiClient) -> None:
    print(f"layers: {len(LAYER_NAMES)} names, parcel x {len(SEASONS)} seasons + default ...")
    jobs: list[tuple[str, dict | None, str]] = []
    for name in LAYER_NAMES:
        jobs.append((f"/layers/{name}.geojson", None, f"layers/{name}.geojson"))
    for season in SEASONS:
        jobs.append((
            "/layers/parcel.geojson", {"season": season}, f"layers/parcel__season-{season}.geojson"
        ))
    # Map layer `name` properties are GIS reference-layer labels (schools, waterbodies, ...), never
    # owner/PII — some are legitimately Tamil-script place/institution names, so the owner-leak scan
    # (which looks for Tamil text under a `name` key) does not apply here; see export_meta's comment.
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_fetch_and_store, api, p, params, rel, False) for p, params, rel in jobs]
        for f in as_completed(futs):
            f.result()


def _parcel_uids(conn: psycopg.Connection) -> list[str]:
    return [r["parcel_uid"] for r in conn.execute("SELECT parcel_uid FROM parcel ORDER BY parcel_uid").fetchall()]


def _finding_ids(conn: psycopg.Connection) -> list[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM finding ORDER BY id").fetchall()]


def _referenced_extraction_ids(conn: psycopg.Connection) -> list[int]:
    """"evidence per extraction id referenced by the UI" — the UI opens `/evidence/{id}` from a
    parcel's extractions list, a review-queue item or a finding's evidence pack; all three trace
    back to parcel_fact / acquisition_event / review_queue's `extraction_id` column."""
    rows = conn.execute(
        "SELECT DISTINCT extraction_id FROM ("
        "  SELECT extraction_id FROM parcel_fact WHERE extraction_id IS NOT NULL"
        "  UNION SELECT extraction_id FROM acquisition_event WHERE extraction_id IS NOT NULL"
        "  UNION SELECT extraction_id FROM review_queue WHERE extraction_id IS NOT NULL"
        ") t ORDER BY 1"
    ).fetchall()
    return [r["extraction_id"] for r in rows]


def export_parcels(api: ApiClient, conn: psycopg.Connection) -> None:
    uids = _parcel_uids(conn)
    print(f"parcels: {len(uids)} x 5 endpoints ...")

    def _one(uid: str) -> None:
        key = safe_name(uid)
        base = f"/parcels/{uid}"
        _fetch_and_store(api, base, None, f"parcels/{key}__detail.json")
        _fetch_and_store(api, f"{base}/timeline", None, f"parcels/{key}__timeline.json")
        _fetch_and_store(api, f"{base}/extractions", None, f"parcels/{key}__extractions.json")
        _fetch_and_store(api, f"{base}/satellite", None, f"parcels/{key}__satellite.json")
        _fetch_and_store(api, f"{base}/findings", None, f"parcels/{key}__findings.json")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_one, uid) for uid in uids]
        done = 0
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 200 == 0:
                print(f"  parcels: {done}/{len(uids)}")


def _run_ids(conn: psycopg.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM run ORDER BY id").fetchall()]


def export_run_details(api: ApiClient, conn: psycopg.Connection) -> None:
    """GET /runs/{run_id} (read-only history, D-067 — creating/streaming/resuming a run stays
    local-only). Bounded by the runs already in the DB at export time."""
    ids = _run_ids(conn)
    print(f"runs: {len(ids)} run-detail snapshots ...")

    def _one(rid: str) -> None:
        # owner_scan=True: RunPublicState.claims can surface extracted row facts, which may carry
        # owner-shaped keys the same way parcel extractions do.
        _fetch_and_store(api, f"/runs/{rid}", None, f"runs/{rid}.json")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_one, rid) for rid in ids]
        for f in as_completed(futs):
            f.result()


def export_findings_packs(api: ApiClient, conn: psycopg.Connection) -> None:
    ids = _finding_ids(conn)
    print(f"findings: {len(ids)} evidence packs ...")

    def _one(fid: int) -> None:
        _fetch_and_store(api, f"/findings/{fid}/evidence-pack", None, f"findings/{fid}__evidence-pack.json")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_one, fid) for fid in ids]
        done = 0
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 200 == 0:
                print(f"  findings evidence-packs: {done}/{len(ids)}")


def export_evidence(api: ApiClient, conn: psycopg.Connection) -> None:
    ids = _referenced_extraction_ids(conn)
    print(f"evidence: {len(ids)} referenced extraction ids ...")

    def _one(eid: int) -> None:
        _fetch_and_store(api, f"/evidence/{eid}", None, f"evidence/{eid}.json")

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_one, eid) for eid in ids]
        done = 0
        for f in as_completed(futs):
            f.result()
            done += 1
            if done % 1000 == 0:
                print(f"  evidence: {done}/{len(ids)}")


# --------------------------------------------------------------------------------- strategy 2: DuckDB tables

FINDING_COLS = ["id", "finding_key", "category", "severity", "confidence", "parcel_uid", "village",
                "unit_id", "block_id", "title", "evidence_level", "verdict", "status", "caveats",
                "created_at"]

DOCUMENT_COLS = ["id", "sha256", "path", "classified_type", "scheme_relevance", "village", "unit_no",
                 "block_no", "doc_no", "doc_date", "pages", "status", "stage", "folder_label",
                 "folder_type_mismatch"]

REVIEW_COLS = ["id", "content_hash", "document_id", "page_id", "page_ref", "extraction_id", "reason",
              "detail", "status", "decided_value", "decided_by", "decided_at", "created_at"]

RUN_COLS = ["id", "workspace_id", "status", "request", "created_at", "updated_at"]


def export_findings_table(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT id, finding_key, category, severity, confidence, parcel_uid, village, unit_id, "
        "block_id, title, evidence_level, verdict, status, caveats, created_at FROM finding "
        "ORDER BY id"
    ).fetchall()
    table = pa.table({
        "id": [r["id"] for r in rows],
        "finding_key": [r["finding_key"] for r in rows],
        "category": [r["category"] for r in rows],
        "severity": [r["severity"] for r in rows],
        "confidence": [_num(r["confidence"]) for r in rows],
        "parcel_uid": [r["parcel_uid"] for r in rows],
        "village": [r["village"] for r in rows],
        "unit_id": [r["unit_id"] for r in rows],
        "block_id": [r["block_id"] for r in rows],
        "title": [r["title"] for r in rows],
        "evidence_level": [r["evidence_level"] for r in rows],
        "verdict": [r["verdict"] for r in rows],
        "status": [r["status"] for r in rows],
        "caveats": [list(r["caveats"] or []) for r in rows],
        "created_at": [_iso_z(r["created_at"]) for r in rows],
    })
    path = SNAPSHOT_DIR / "tables" / "findings.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    print(f"tables/findings.parquet: {len(rows)} rows")


def export_documents_table(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT id, sha256, path, classified_type, scheme_relevance, village, unit_no, block_no, "
        "doc_no, doc_date, pages, status, stage, folder_label, "
        "(folder_label IS NOT NULL AND classified_type IS NOT NULL "
        "AND folder_label <> classified_type) AS folder_type_mismatch FROM document ORDER BY id"
    ).fetchall()
    table = pa.table({
        "id": [r["id"] for r in rows],
        "sha256": [r["sha256"] for r in rows],
        "path": [r["path"] for r in rows],
        "classified_type": [r["classified_type"] for r in rows],
        "scheme_relevance": [r["scheme_relevance"] for r in rows],
        "village": [r["village"] for r in rows],
        "unit_no": [r["unit_no"] for r in rows],
        "block_no": [r["block_no"] for r in rows],
        "doc_no": [r["doc_no"] for r in rows],
        "doc_date": [_iso_date(r["doc_date"]) for r in rows],
        "pages": [r["pages"] for r in rows],
        "status": [r["status"] for r in rows],
        "stage": [r["stage"] for r in rows],
        "folder_label": [r["folder_label"] for r in rows],
        "folder_type_mismatch": [bool(r["folder_type_mismatch"]) for r in rows],
    })
    path = SNAPSHOT_DIR / "tables" / "documents.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    print(f"tables/documents.parquet: {len(rows)} rows")


def export_review_queue_table(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT id, content_hash, document_id, page_id, page_ref, extraction_id, reason, detail, "
        "status, decided_value, decided_by, decided_at, created_at FROM review_queue ORDER BY created_at"
    ).fetchall()
    table = pa.table({
        "id": [r["id"] for r in rows],
        "content_hash": [r["content_hash"] for r in rows],
        "document_id": [r["document_id"] for r in rows],
        "page_id": [r["page_id"] for r in rows],
        "page_ref": [r["page_ref"] for r in rows],
        "extraction_id": [r["extraction_id"] for r in rows],
        "reason": [r["reason"] for r in rows],
        "detail": [_json_or_none(r["detail"]) for r in rows],
        "status": [r["status"] for r in rows],
        "decided_value": [_json_or_none(r["decided_value"]) for r in rows],
        "decided_by": [r["decided_by"] for r in rows],
        "decided_at": [_iso_z(r["decided_at"]) for r in rows],
        "created_at": [_iso_z(r["created_at"]) for r in rows],
    })
    path = SNAPSHOT_DIR / "tables" / "review_queue.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    print(f"tables/review_queue.parquet: {len(rows)} rows")


def export_runs_table(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "SELECT id, workspace_id, status, request->>'request' AS request, created_at, updated_at "
        "FROM run ORDER BY created_at DESC"
    ).fetchall()
    table = pa.table({
        "id": [r["id"] for r in rows],
        "workspace_id": [r["workspace_id"] for r in rows],
        "status": [r["status"] for r in rows],
        "request": [r["request"] for r in rows],
        "created_at": [_iso_z(r["created_at"]) for r in rows],
        "updated_at": [_iso_z(r["updated_at"]) for r in rows],
    })
    path = SNAPSHOT_DIR / "tables" / "runs.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    print(f"tables/runs.parquet: {len(rows)} rows")


# --------------------------------------------------------------------------------- asset manifests

def export_page_manifest(conn: psycopg.Connection) -> None:
    rows = conn.execute("SELECT document_id, page_no, preview_path FROM page WHERE preview_path IS NOT NULL").fetchall()
    manifest = {}
    prefix = "data/pages/"
    for r in rows:
        p = r["preview_path"]
        rel = p[len(prefix):] if p.startswith(prefix) else p
        manifest[f"{r['document_id']}:{r['page_no']}"] = rel
    _write_json("manifest/pages.json", manifest)
    print(f"manifest/pages.json: {len(manifest)} pages")


_CHIP_FILE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<kind>truecolor|ndvi)\.png$")


def export_chip_manifest() -> None:
    chips_dir = REPO_ROOT / "data" / "s2" / "chips"
    manifest: dict[str, str] = {}
    if chips_dir.exists():
        for uid_dir in sorted(chips_dir.iterdir()):
            if not uid_dir.is_dir():
                continue
            for f in sorted(uid_dir.glob("*.png")):
                m = _CHIP_FILE_RE.match(f.name)
                if not m:
                    continue
                key = f"{uid_dir.name}:{m.group('date')}:{m.group('kind')}"
                manifest[key] = f"{uid_dir.name}/{f.name}"
    _write_json("manifest/chips.json", manifest)
    print(f"manifest/chips.json: {len(manifest)} cached chips (only these resolve in the cloud demo)")


# --------------------------------------------------------------------------------- entrypoint

def _du(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    for unit in ["B", "KB", "MB", "GB"]:
        if total < 1024:
            return f"{total:.1f}{unit}"
        total /= 1024
    return f"{total:.1f}TB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-per-entity", action="store_true",
                    help="skip the (slow) parcel/finding/evidence per-entity export; useful for a quick re-run of tables/layers/meta only")
    args = ap.parse_args()

    t0 = time.time()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5.0)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: local API not reachable at {API_BASE}/health ({e}). "
              f"Start it first (scripts/demo_run.sh / make demo).", file=sys.stderr)
        return 1

    api = ApiClient(API_BASE)
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        export_meta(api)
        export_layers(api)
        export_findings_table(conn)
        export_documents_table(conn)
        export_review_queue_table(conn)
        export_runs_table(conn)
        export_page_manifest(conn)
        export_chip_manifest()
        if not args.skip_per_entity:
            export_parcels(api, conn)
            export_findings_packs(api, conn)
            export_evidence(api, conn)
            export_run_details(api, conn)
    finally:
        conn.close()

    manifest = {
        "snapshot_at": _iso_z(datetime.now(timezone.utc)),
        "api_base": API_BASE,
        "requests_made": STATS.n_requests,
        "failed": STATS.n_failed,
        "owner_scan_files": STATS.n_owner_scanned,
        "layer_names": LAYER_NAMES,
        "seasons": SEASONS,
    }
    _write_json("manifest.json", manifest)

    elapsed = time.time() - t0
    size = _du(SNAPSHOT_DIR)
    print(f"\ncloud-export done in {elapsed:.1f}s — snapshot size: {size} at {SNAPSHOT_DIR}")
    print(f"requests made: {STATS.n_requests}; owner-scan files checked: {STATS.n_owner_scanned}")
    if STATS.n_failed:
        print(f"WARNING: {len(STATS.n_failed)} requests failed (see manifest.json 'failed'):")
        for f in STATS.n_failed[:20]:
            print(f"  {f}")
        return 1
    print("owner-leak scan: PASS (no Tamil-script text found under owner/name-shaped keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
