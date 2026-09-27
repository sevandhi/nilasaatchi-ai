"""CSV export: every `tables[]` entry's rows in the WorkspaceSpec (contract §4), tagged by table id.
Falls back to the map layers' feature properties (via app.export.geojson) if the spec has no
tables — e.g. a pure map result — so `fmt=csv` always returns something useful."""
from __future__ import annotations

import csv
import io

import psycopg

from app.export.geojson import build_geojson
from app.workspace.models import WorkspaceSpec


def export_csv(conn: psycopg.Connection, spec: WorkspaceSpec) -> bytes:
    rows: list[dict] = []
    if spec.tables:
        for table in spec.tables:
            for row in table.rows:
                rows.append({"_table_id": table.id, **row})
    else:
        fc = build_geojson(conn, spec)
        rows = [f["properties"] for f in fc["features"]]

    fieldnames: list[str] = []
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames or ["_table_id"], extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")
