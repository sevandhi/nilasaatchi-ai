"""Generic knowledge-graph tools: catalog_search, doc_retrieve, sql_query, spatial_query, extract_document.

They are domain-agnostic: domain packs supply the SQL allow-list, the schema card notes and examples.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.state import Evidence, SqlOutput, llm_schema
from app.tools.db import query
from app.tools.registry import ClaimSeed, Tool, ToolError, ToolResult
from app.tools.sql_guard import MAX_ROWS, SqlGuardError, guard_sql

REPO = Path(__file__).resolve().parents[2]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------ catalog_search
class CatalogSearchIn(_In):
    doc_type: str | None = Field(None, description="classified document type, e.g. AWARD_7_2, FORM_F, LDR")
    stage: str | None = Field(None, description="lifecycle stage code")
    village: str | None = None
    block: str | None = None
    date_from: str | None = Field(None, description="ISO date")
    date_to: str | None = None
    limit: int = Field(50, ge=1, le=500)


def catalog_search(a: CatalogSearchIn, ctx) -> ToolResult:
    where, params = ["true"], []
    for col, val in (("classified_type", a.doc_type), ("stage", a.stage), ("block_no", a.block)):
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    if a.village:
        where.append("lower(village) = lower(%s)")
        params.append(a.village)
    if a.date_from:
        where.append("doc_date >= %s")
        params.append(a.date_from)
    if a.date_to:
        where.append("doc_date <= %s")
        params.append(a.date_to)
    sql = ("SELECT id AS document_id, classified_type AS doc_type, stage, village, unit_no, block_no, doc_no, "
           "doc_date, pages, folder_label, scheme_relevance FROM document WHERE " + " AND ".join(where) +
           " ORDER BY doc_date NULLS LAST, id LIMIT %s")
    rows = query(ctx.kg, sql, (*params, a.limit), readonly_role=False)
    ev = [Evidence(kind="document", ref=f"document:{r['document_id']}", document_id=r["document_id"],
                   excerpt=f"{r['doc_type']} {r['doc_date'] or ''}") for r in rows[:20]]
    return ToolResult(data={"rows": rows}, evidence=ev, n_rows=len(rows), confidence=0.9,
                      summary=f"{len(rows)} documents", caveats=["doc_type is the content classifier result"],
                      claims=[ClaimSeed(kind="kpi", subject="documents", field="n_documents", value=len(rows),
                                        checks=["count_matches_rows"], context={"rows": len(rows)})])


# ------------------------------------------------------------------------------------------ doc_retrieve
class DocRetrieveIn(_In):
    query: str = Field(description="search text (English or Tamil)")
    village: str | None = None
    doc_type: str | None = None
    k: int = Field(8, ge=1, le=50)


def doc_retrieve(a: DocRetrieveIn, ctx) -> ToolResult:
    """Full-text search (tsvector 'simple', ts_rank_cd). Embedding rank (Titan/BGE-M3) is fused by RRF when
    page.embedding is populated; until then this is FTS-only (reported in caveats)."""
    terms = [t for t in re.findall(r"[\w஀-௿]+", a.query) if len(t) > 1][:12]
    if not terms:
        raise ToolError("bad_args", "doc_retrieve: empty query")
    tsq = " | ".join(t.replace("'", "") for t in terms)
    where, params = ["p.tsv @@ to_tsquery('simple', %s)"], [tsq]
    if a.village:
        where.append("lower(d.village) = lower(%s)")
        params.append(a.village)
    if a.doc_type:
        where.append("d.classified_type = %s")
        params.append(a.doc_type)
    sql = ("SELECT p.id AS page_id, p.document_id, p.page_no, d.classified_type AS doc_type, d.village, "
           "ts_rank_cd(p.tsv, to_tsquery('simple', %s)) AS rank, "
           "ts_headline('simple', coalesce(p.text,''), to_tsquery('simple', %s), 'MaxWords=30,MinWords=10') AS excerpt "
           "FROM page p JOIN document d ON d.id = p.document_id WHERE " + " AND ".join(where) +
           " ORDER BY rank DESC, p.id LIMIT %s")
    rows = query(ctx.kg, sql, (tsq, tsq, *params, a.k), readonly_role=False)
    emb = query(ctx.kg, "SELECT count(*) AS n FROM page WHERE embedding IS NOT NULL", readonly_role=False)[0]["n"]
    ev = [Evidence(kind="page_bbox", ref=f"page:{r['page_id']}", page=r["page_no"], document_id=r["document_id"],
                   excerpt=(r["excerpt"] or "")[:300]) for r in rows]
    cav = [] if emb else ["FTS only: page embeddings not loaded yet (Titan/BGE-M3 fusion pending)"]
    return ToolResult(data={"hits": rows}, evidence=ev, n_rows=len(rows), confidence=0.7 if rows else 0.3,
                      caveats=cav, summary=f"{len(rows)} page hits")


# ------------------------------------------------------------------------------------------ sql_query
class SqlQueryIn(_In):
    question: str = Field(description="natural-language question to answer with one SELECT over the KG")
    max_rows: int = Field(500, ge=1, le=MAX_ROWS)


@lru_cache(maxsize=16)
def _columns(dsn: str, tables: tuple[str, ...]) -> dict[str, list[str]]:
    rows = query(dsn, "SELECT c.table_name, c.column_name, c.data_type FROM information_schema.columns c "
                      "WHERE c.table_schema = 'public' AND c.table_name = ANY(%s) "
                      "AND has_column_privilege('agent_ro', format('%%I.%%I', c.table_schema, c.table_name), "
                      "c.column_name, 'SELECT') ORDER BY c.table_name, c.ordinal_position",
                 (list(tables),), readonly_role=False)
    out: dict[str, list[str]] = {}
    for r in rows:
        if r["column_name"] in ("geom_utm",) or r["data_type"] == "USER-DEFINED" and r["column_name"] != "geom":
            continue
        out.setdefault(r["table_name"], []).append(f"{r['column_name']}:{_short(r['data_type'])}")
    return out


def _short(t: str) -> str:
    return {"character varying": "text", "timestamp with time zone": "timestamptz", "double precision": "float",
            "USER-DEFINED": "geometry", "ARRAY": "array"}.get(t, t)


def schema_card(pack, dsn: str) -> str:
    cols = _columns(dsn, tuple(sorted(pack.sql_allowlist)))
    lines = [f"{t}({', '.join(c)})" for t, c in sorted(cols.items())]
    return "Tables (PostgreSQL 16 + PostGIS; read-only):\n" + "\n".join(lines) + "\n\nNotes:\n" + pack.schema_notes


SQL_SYSTEM = (REPO / "prompts" / "sql.md")


def sql_query(a: SqlQueryIn, ctx) -> ToolResult:
    from app.domains import get_pack
    pack = get_pack(ctx.domain)
    card = schema_card(pack, ctx.kg)
    system = SQL_SYSTEM.read_text(encoding="utf-8")
    question = ctx.pseudo.text(a.question) if ctx.pseudo else a.question
    payload = {"system": system, "prompt": f"Question: {question}\n\n{card}\n\nExamples:\n" +
               "\n".join(f"-- {e['q']}\n{e['sql']}" for e in pack.sql_examples), "max_tokens": 700}
    errors: list[str] = []
    last_sql = None
    model_id = vendor = None
    for attempt in range(3):                              # first try + error-guided repair (max 2)
        if errors:
            payload = {**payload, "prompt": payload["prompt"] + f"\n\nYour previous SQL:\n{last_sql}\nfailed with: "
                       f"{errors[-1]}\nReturn a corrected single SELECT."}
        res = ctx.llm("sql", payload, schema=llm_schema(SqlOutput), privacy_tier="PII")
        if not res.ok or not res.data:
            raise ToolError("unavailable", f"sql model unavailable: {res.error}")
        model_id = res.model_id
        from app.router import vendor_of
        vendor = vendor_of(model_id)
        last_sql = str(res.data.get("sql", ""))
        try:
            g = guard_sql(last_sql, set(pack.sql_allowlist), min(a.max_rows, MAX_ROWS))
            rows = query(ctx.kg, g.sql)
        except SqlGuardError as e:
            errors.append(f"guard: {e}")
            _emit_repair(ctx, attempt, "sql_guard", model_id)
            continue
        except Exception as e:  # noqa: BLE001 - psycopg errors -> error-guided repair
            errors.append(f"db: {type(e).__name__}: {str(e).splitlines()[0][:240]}")
            _emit_repair(ctx, attempt, "sql_error", model_id)
            continue
        return _sql_result(g.sql, rows, model_id, vendor, attempt, errors, question=a.question)
    raise ToolError("sql_error", f"SQL failed after 2 repairs: {errors[-1] if errors else '?'}",
                    detail={"sql": last_sql, "errors": errors})


def _emit_repair(ctx, attempt: int, reason: str, model_id: str | None) -> None:
    if ctx.emit and attempt < 2:
        ctx.emit("fallback", {"step_id": ctx.step_id, "task": "sql", "from": model_id, "to": "repair",
                              "reason": reason})


def _sql_result(sql: str, rows: list[dict], model_id, vendor, attempt, errors, *, question: str,
                claim_rows: int = 25) -> ToolResult:
    sql_hash = hashlib.sha256(sql.encode()).hexdigest()[:16]
    ev = [Evidence(kind="sql", ref=f"sql:{sql_hash}", excerpt=sql[:1000])]
    claims = []
    if rows and len(rows) <= claim_rows:
        keys = list(rows[0].keys())
        label_keys = [k for k in keys if not isinstance(rows[0][k], (int, float)) or isinstance(rows[0][k], bool)]
        for i, r in enumerate(rows):
            subj = " / ".join(str(r[k]) for k in label_keys[:2]) or f"row {i + 1}"
            for k in keys:
                v = r[k]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    claims.append(ClaimSeed(kind="table_cell" if len(rows) > 1 else "kpi", subject=subj, field=k,
                                            value=v, evidence=ev, checks=["rerun_sql"],
                                            context={"sql": sql, "row": i, "column": k}))
    conf = 0.85 if not errors else 0.75
    return ToolResult(data={"rows": rows, "columns": list(rows[0].keys()) if rows else []}, sql=sql, evidence=ev,
                      n_rows=len(rows), confidence=conf, model_id=model_id, vendor=vendor,
                      summary=f"{len(rows)} rows" + (f" after {attempt} repair(s)" if attempt else ""),
                      caveats=[f"SQL repaired: {e}" for e in errors][:2], claims=claims[:40])


# ------------------------------------------------------------------------------------------ spatial_query
SPATIAL_LAYERS = {"roads": "ref_layer_roads", "major_roads": "ref_layer_roads", "rail": "ref_layer_rail",
                  "rail_stations": "ref_layer_rail_stations", "waterbodies": "ref_layer_waterbodies",
                  "substations": "ref_layer_substations", "airport": "ref_layer_airport",
                  "seaport": "ref_layer_seaport", "schools": "ref_layer_schools",
                  "sipcot_parks": "ref_layer_sipcot_parks", "park_boundary": "ref_layer_park_boundary"}


class SpatialQueryIn(_In):
    template: Literal["buffer_within", "intersects_layer", "distance_to_nearest", "area_by_group",
                      "filter_by_raster_stat"] | None = Field(
        None, description="parameterised PostGIS template; null = generated SQL (guarded) from `question`")
    layer: str | None = Field(None, description="reference layer: " + ", ".join(SPATIAL_LAYERS))
    distance_m: float | None = Field(None, ge=0, le=50000)
    group_by: Literal["village", "block", "unit", "village_block"] | None = None
    raster_stat: str | None = Field(None, description="key in parcel.raster_stats, e.g. slope_mean, dem_mean")
    op: Literal["<", "<=", ">", ">=", "="] | None = None
    value: float | None = None
    villages: list[str] = Field(default_factory=list)
    parcel_uids: list[str] = Field(default_factory=list)
    blocks: list[int] = Field(default_factory=list)
    question: str | None = None
    max_rows: int = Field(2000, ge=1, le=MAX_ROWS)

    @model_validator(mode="after")
    def _template_args(self) -> SpatialQueryIn:
        """Template-specific required args are checked at plan time (so the planner can repair them)."""
        t = self.template
        need: list[str] = []
        if t is None and not self.question:
            raise ValueError("spatial_query needs `template` (buffer_within, intersects_layer, distance_to_nearest, "
                             "area_by_group, filter_by_raster_stat) or a natural-language `question`")
        if t in ("buffer_within", "intersects_layer", "distance_to_nearest") and not self.layer:
            need.append("layer")
        if t == "buffer_within" and self.distance_m is None:
            need.append("distance_m")
        if t == "filter_by_raster_stat":
            need += [k for k in ("raster_stat", "op", "value") if getattr(self, k) is None]
        if need:
            raise ValueError(f"template {t} needs {need}")
        if self.layer is not None and self.layer not in SPATIAL_LAYERS:
            raise ValueError(f"unknown layer {self.layer!r}; one of {sorted(SPATIAL_LAYERS)}")
        return self


def _scope(a: SpatialQueryIn, alias: str = "p") -> tuple[str, list]:
    w, params = [], []
    if a.villages:
        w.append("v.name = ANY(%s)")
        params.append(a.villages)
    if a.parcel_uids:
        w.append(f"{alias}.parcel_uid = ANY(%s)")
        params.append(a.parcel_uids)
    if a.blocks:
        w.append(f"{alias}.block_id = ANY(%s)")
        params.append(a.blocks)
    return (" AND ".join(w) or "true"), params


def spatial_query(a: SpatialQueryIn, ctx) -> ToolResult:
    if a.template is None:
        if not a.question:
            raise ToolError("bad_args", "spatial_query needs a template or a question")
        res = sql_query(SqlQueryIn(question=a.question, max_rows=a.max_rows), ctx)
        res.caveats.append("generated PostGIS SQL (no template matched); guarded + agent_ro")
        return res
    scope, params = _scope(a)
    t = a.template
    if t in ("buffer_within", "intersects_layer", "distance_to_nearest"):
        if not a.layer or a.layer not in SPATIAL_LAYERS:
            raise ToolError("bad_args", f"{t} needs layer in {sorted(SPATIAL_LAYERS)}")
        lt = SPATIAL_LAYERS[a.layer]
        extra = " AND r.is_major" if a.layer == "major_roads" else (" AND r.is_vehicular" if a.layer == "roads" else "")
    if t == "buffer_within":
        if a.distance_m is None:
            raise ToolError("bad_args", "buffer_within needs distance_m")
        sql = (f"SELECT p.parcel_uid, v.name AS village, p.block_id, round(geo_area_ha(p.geom_utm)::numeric, 4) AS area_ha, "
               f"round(min(ST_Distance(p.geom_utm, r.geom_utm))::numeric, 1) AS distance_m "
               f"FROM parcel p JOIN village v ON v.id = p.village_id JOIN {lt} r "
               f"ON ST_DWithin(p.geom_utm, r.geom_utm, %s){extra} WHERE {scope} "
               f"GROUP BY 1, 2, 3, 4 ORDER BY distance_m, 1")
        params = [a.distance_m, *params]
    elif t == "intersects_layer":
        sql = (f"SELECT p.parcel_uid, v.name AS village, p.block_id, "
               f"round(geo_area_ha(ST_Intersection(p.geom_utm, ST_Union(r.geom_utm)))::numeric, 4) AS overlap_ha "
               f"FROM parcel p JOIN village v ON v.id = p.village_id JOIN {lt} r ON ST_Intersects(p.geom_utm, r.geom_utm){extra} "
               f"WHERE {scope} GROUP BY p.parcel_uid, v.name, p.block_id, p.geom_utm ORDER BY overlap_ha DESC")
    elif t == "distance_to_nearest":
        sql = (f"SELECT p.parcel_uid, v.name AS village, p.block_id, n.name AS nearest, round(n.d::numeric, 1) AS distance_m "
               f"FROM parcel p JOIN village v ON v.id = p.village_id CROSS JOIN LATERAL ("
               f"SELECT r.name, ST_Distance(p.geom_utm, r.geom_utm) AS d FROM {lt} r WHERE true{extra} "
               f"ORDER BY p.geom_utm <-> r.geom_utm LIMIT 1) n WHERE {scope} ORDER BY distance_m, 1")
    elif t == "area_by_group":
        g = {"village": "v.name", "block": "p.block_id", "unit": "p.unit_id",
             "village_block": "v.name || ' / block ' || coalesce(p.block_id::text, '?')"}[a.group_by or "village"]
        sql = (f"SELECT {g} AS grp, count(*) AS n_parcels, round(sum(p.area_ha_gis), 4) AS area_sum_ha, "
               f"round(geo_area_ha(ST_Union(p.geom))::numeric, 4) AS area_union_ha "
               f"FROM parcel p JOIN village v ON v.id = p.village_id WHERE {scope} GROUP BY 1 ORDER BY 1")
    else:  # filter_by_raster_stat
        if not (a.raster_stat and a.op and a.value is not None) or not re.fullmatch(r"[a-z0-9_]+", a.raster_stat):
            raise ToolError("bad_args", "filter_by_raster_stat needs raster_stat, op, value")
        sql = (f"SELECT p.parcel_uid, v.name AS village, p.block_id, (p.raster_stats->>%s)::float AS {a.raster_stat} "
               f"FROM parcel p JOIN village v ON v.id = p.village_id WHERE {scope} "
               f"AND (p.raster_stats->>%s)::float {a.op} %s ORDER BY 4 DESC")
        params = [a.raster_stat, *params, a.raster_stat, a.value]
    sql_full = sql + f" LIMIT {int(a.max_rows)}"
    try:
        rows = query(ctx.kg, sql_full, params)
    except Exception as e:  # noqa: BLE001
        raise ToolError("sql_error", f"spatial template {t} failed: {str(e).splitlines()[0][:200]}") from None
    shown = _inline(sql_full, params)
    res = _sql_result(shown, rows, None, None, 0, [], question=a.question or t)
    for c in res.claims:
        c.context = {"sql": sql_full, "params": _jsonable_params(params), "row": c.context["row"],
                     "column": c.context["column"]}
    if t == "area_by_group":
        res.caveats.append("areas are geodesic ha; union dissolves overlapping FMB polygons (D-023)")
        for c in res.claims:
            if c.field == "area_union_ha":
                c.checks.append("union_le_sum")
    res.summary = f"{t}: {len(rows)} rows"
    res.confidence = 0.9
    return res


def _jsonable_params(params: list) -> list:
    return [list(p) if isinstance(p, (list, tuple)) else p for p in params]


def _inline(sql: str, params: list) -> str:
    out = sql
    for p in params:
        out = out.replace("%s", repr(p) if not isinstance(p, list) else "ARRAY" + json.dumps(p, ensure_ascii=False), 1)
    return out


# ------------------------------------------------------------------------------------------ extract_document
class ExtractDocumentIn(_In):
    attachment_id: str | None = Field(None, description="id of an uploaded attachment (pdf/image)")
    path: str | None = None
    max_pages: int = Field(20, ge=1, le=200)


SURVEY_RE = re.compile(r"(?<![\d/])(\d{1,4})\s*/\s*(\d{1,3}[A-Za-z]?\d?)(?![\d/])")
EXTENT_RE = re.compile(r"\b(\d{1,3})\s*[.:]\s*(\d{2})\s*[.:]\s*(\d{1,2})\b")


def extract_document(a: ExtractDocumentIn, ctx) -> ToolResult:
    """INTERIM (P2 pending): text-layer pass only. TODO(P2): delegate to pipeline.extract (OCR -> Bedrock
    table read -> normalisers -> self-consistency) with privacy tiers and progress events, then match_parcels."""
    path = a.path
    if a.attachment_id:
        att = next((x for x in ctx.attachments if x.id == a.attachment_id), None)
        if att is None:
            raise ToolError("not_found", f"attachment {a.attachment_id} not found")
        path = att.path
    if not path or not Path(path).exists():
        raise ToolError("not_found", f"file not found: {path}")
    try:
        txt = subprocess.run(["pdftotext", "-layout", "-l", str(a.max_pages), path, "-"], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError("failed", f"pdftotext failed: {e}") from None
    pages = txt.split("\f")
    rows, ev = [], []
    for pno, ptxt in enumerate(pages, start=1):
        for line in ptxt.splitlines():
            m = SURVEY_RE.search(line)
            if not m:
                continue
            e = EXTENT_RE.search(line[m.end():])
            ext = round(int(e.group(1)) + int(e.group(2)) / 100 + int(e.group(3)) / 10000, 4) if e else None
            rows.append({"page": pno, "survey_no": m.group(1), "sub_div": m.group(2).upper(), "extent_ha": ext})
            ev.append(Evidence(kind="page_bbox", ref=f"upload:{Path(path).name}#p{pno}", page=pno,
                               excerpt=(ctx.pseudo.text(line.strip()) if ctx.pseudo else line.strip())[:200]))
    good = sum(1 for p in pages if len(re.sub(r"\s", "", p)) >= 50)
    conf = 0.4 if rows else 0.1
    cav = ["INTERIM extractor: text layer only; scanned pages need the P2 pipeline (OCR/VLM)"]
    if good < max(1, len(pages) - 1):
        cav.append(f"low text coverage: {good}/{len(pages)} pages have a usable text layer (low OCR confidence)")
    seeds = [ClaimSeed(kind="kpi", subject=Path(path).name, field="n_rows_extracted", value=len(rows),
                       checks=["count_matches_rows"], context={"rows": len(rows)}, confidence=conf)]
    return ToolResult(data={"rows": rows[:500], "pages": len(pages), "text_pages": good}, evidence=ev[:50],
                      n_rows=len(rows), confidence=conf, caveats=cav, degraded=True, claims=seeds,
                      summary=f"{len(rows)} survey rows from {good}/{len(pages)} text pages")


def spatial_query_tool() -> Tool:
    return next(t for t in generic_tools() if t.name == "spatial_query")


def generic_tools() -> list[Tool]:
    return [
        Tool("catalog_search", "Find documents by classified type, stage, village, block or date range.",
             CatalogSearchIn, catalog_search, privacy_tier="PUBLIC", timeout_s=15),
        Tool("doc_retrieve", "Full-text (and later semantic) search over document pages; returns page excerpts.",
             DocRetrieveIn, doc_retrieve, privacy_tier="PII", timeout_s=15),
        Tool("sql_query", "Answer a question with ONE guarded read-only SQL SELECT over the knowledge graph "
             "(model writes SQL -> AST guard -> agent_ro, 5 s timeout, error-guided repair). Returns rows + SQL.",
             SqlQueryIn, sql_query, privacy_tier="PII", timeout_s=90, uses_llm=True),
        Tool("spatial_query", "PostGIS templates over parcels: buffer_within, intersects_layer, distance_to_nearest, "
             "area_by_group (geodesic ha, sum and dissolved union), filter_by_raster_stat. "
             "template=null uses guarded generated SQL.", SpatialQueryIn, spatial_query, timeout_s=60),
        Tool("extract_document", "Extract rows (survey, extent) from an uploaded document with page evidence.",
             ExtractDocumentIn, extract_document, privacy_tier="PII", timeout_s=120),
    ]
