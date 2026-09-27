"""python -m pipeline.match [--all]  — resolve parcel_fact / acquisition_event rows to parcel_uid.

Idempotent and incremental: by default only rows with match_status IS NULL are processed (the
extraction loader deletes+reinserts reloaded pages, so new/reloaded rows are picked up). --all
re-matches everything. Single-parcel matches set parcel_uid; survey-level and list matches go to
parcel_fact_link / acquisition_event_link (migration 0013). Top-3 candidates -> match_candidates.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter

import psycopg
from dotenv import load_dotenv

from pipeline.match.core import (SCHEME_NAMES, Context, MatchOutcome, VillageIndex, load_thresholds,
                                 make_parcel, match_record)
from pipeline.normalize.village import normalise_village

AC_TO_HA = 0.40468564224
_CFG = load_thresholds()
BLOCK_SCORE = float(_CFG.get("match", {}).get("block_level_score", 0.5))


def sync_thresholds(conn) -> None:
    """Flatten numeric `match` / `lifecycle` / `discrepancy` settings into the threshold table."""
    rows = []

    def walk(prefix, node):
        for k, v in node.items():
            if isinstance(v, dict):
                walk(f"{prefix}{k}.", v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                rows.append((f"{prefix}{k}", v))
    walk("", {k: _CFG.get(k, {}) for k in ("match", "lifecycle", "discrepancy")})
    for k, v in rows:
        conn.execute("""INSERT INTO threshold(key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (k, v))


def _int(x) -> int | None:
    m = re.search(r"\d+", str(x)) if x is not None else None
    return int(m.group()) if m else None


def load_index(conn) -> dict[str, VillageIndex]:
    rows = conn.execute("""SELECT v.name, p.parcel_uid, p.kide, p.block_id, p.unit_id, p.area_ha_gis
                           FROM parcel p JOIN village v ON v.id = p.village_id""").fetchall()
    by_v: dict[str, list] = {}
    for vname, uid, kide, blk, unit, area in rows:
        by_v.setdefault(vname, []).append(make_parcel(uid, kide, blk, unit, area))
    return {v: VillageIndex(ps) for v, ps in by_v.items()}


def resolve_village(row_village: str | None, header_village: str | None) -> tuple[str | None, Context]:
    rv = normalise_village(row_village) if row_village else None
    hv = normalise_village(header_village) if header_village else None
    ctx = Context(header_is_scheme=hv in SCHEME_NAMES)
    if rv:
        ctx.header_conflict = bool(hv and hv != rv)
        return rv, ctx
    if hv:
        ctx.village_from_header = True
        return hv, ctx
    return None, ctx


FACT_SQL = """
WITH ext AS (
  SELECT extraction_id,
         COALESCE(max(value_num) FILTER (WHERE fact_type='extent_ha'),
                  max(value_num) FILTER (WHERE fact_type='extent_ac') * %(ac)s,
                  max(value_num) FILTER (WHERE fact_type='cents') * %(ac)s / 100) AS extent_ha
  FROM parcel_fact GROUP BY extraction_id)
SELECT f.id, f.village, f.survey_no, f.sub_div, d.village, d.unit_no, d.block_no, ext.extent_ha
FROM parcel_fact f
JOIN extraction e ON e.id = f.extraction_id
LEFT JOIN document d ON d.id = e.document_id
LEFT JOIN ext ON ext.extraction_id = f.extraction_id
{where}"""

EVENT_SQL = """
WITH ext AS (
  SELECT extraction_id,
         COALESCE(max(value_num) FILTER (WHERE fact_type='extent_ha'),
                  max(value_num) FILTER (WHERE fact_type='extent_ac') * %(ac)s) AS extent_ha
  FROM parcel_fact GROUP BY extraction_id)
SELECT a.id, a.village, a.survey_no, a.sub_div, d.village, COALESCE(a.unit_no, d.unit_no::text),
       COALESCE(a.block_no, d.block_no::text), ext.extent_ha
FROM acquisition_event a
LEFT JOIN document d ON d.id = a.document_id
LEFT JOIN ext ON ext.extraction_id = a.extraction_id
{where}"""


def block_level(index: dict[str, VillageIndex], village: str | None, ctx: Context, score: float) -> MatchOutcome | None:
    """D-052: a document-level event (no survey ref) is evidence for every FMB parcel in its
    village + unit + block. A scheme-labelled/missing village is replaced only when the unit+block
    exists in exactly one village."""
    if ctx.block_no is None:
        return None

    def in_block(v):
        return [p for p in index[v].by_kide.values()
                if p.block_id == ctx.block_no and (ctx.unit_no is None or p.unit_id == ctx.unit_no)]
    ps = in_block(village) if village in index else []
    reason = "block_level(village+unit+block)"
    if not ps and (village is None or village in SCHEME_NAMES or ctx.header_is_scheme or ctx.village_from_header):
        hits = {v: in_block(v) for v in index}
        hits = {v: x for v, x in hits.items() if x}
        if len(hits) == 1:
            village, ps = next(iter(hits.items()))
            reason = "block_level(unit+block unique village, D-052)"
    if not ps:
        return None
    return MatchOutcome("block_level", None, score, [(p.parcel_uid, "block_level", score) for p in ps],
                        [{"reason": reason, "unit": ctx.unit_no, "block": ctx.block_no, "n_parcels": len(ps)}], village)


def run_table(conn, table: str, link_table: str, id_col: str, sql: str, index, full: bool) -> Counter:
    where = "" if full else f"WHERE {'f' if table == 'parcel_fact' else 'a'}.match_status IS NULL"
    rows = conn.execute(sql.format(where=where), {"ac": AC_TO_HA}).fetchall()
    stats: Counter = Counter()
    cache: dict = {}
    updates, links = [], []
    for rid, rv, sn, sd, hv, unit, blk, ext in rows:
        village, ctx = resolve_village(rv, hv)
        ctx.unit_no, ctx.block_no = _int(unit), _int(blk)
        ctx.extent_ha = float(ext) if ext is not None else None
        key = (village, sn, sd, ctx.unit_no, ctx.block_no, ctx.extent_ha, ctx.village_from_header, ctx.header_conflict)
        out = cache.get(key)
        if out is None:
            out = match_record(index, village, sn, sd, ctx)
            if out.status == "no_ref" and table == "acquisition_event":
                out = block_level(index, village, ctx, BLOCK_SCORE) or out
            cache[key] = out
        stats[out.status] += 1
        updates.append((rid, out.village or village, out.parcel_uid, out.status, out.score, json.dumps(out.candidates, ensure_ascii=False)))
        links.extend((rid, uid, kind, sc) for uid, kind, sc in out.links)
    if not rows:
        return stats
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _m (id bigint, vil text, uid text, st text, sc real, mc jsonb) ON COMMIT DROP")
        with cur.copy("COPY _m FROM STDIN") as cp:
            for u in updates:
                cp.write_row(u)
        cur.execute(f"""UPDATE {table} t SET match_village = _m.vil, parcel_uid = _m.uid, match_status = _m.st, match_score = _m.sc,
                        match_candidates = _m.mc, matched_at = now() FROM _m WHERE t.id = _m.id""")
        cur.execute(f"DELETE FROM {link_table} l USING _m WHERE l.{id_col} = _m.id")
        with cur.copy(f"COPY {link_table} ({id_col}, parcel_uid, link_kind, score) FROM STDIN") as cp:
            seen = set()
            for l in links:
                if (l[0], l[1]) not in seen:
                    seen.add((l[0], l[1]))
                    cp.write_row(l)
        cur.execute("DROP TABLE _m")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-match every row, not only unmatched ones")
    args = ap.parse_args()
    load_dotenv()
    t0 = time.time()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        sync_thresholds(conn)
        index = load_index(conn)
        for table, link, idc, sql in (("parcel_fact", "parcel_fact_link", "fact_id", FACT_SQL),
                                      ("acquisition_event", "acquisition_event_link", "event_id", EVENT_SQL)):
            st = run_table(conn, table, link, idc, sql, index, args.all)
            conn.commit()
            print(f"{table}: {sum(st.values())} rows  " + "  ".join(f"{k}={v}" for k, v in st.most_common()))
    print(f"runtime {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
