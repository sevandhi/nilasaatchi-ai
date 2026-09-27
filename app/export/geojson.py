"""GeoJSON export: every `map.layers` entry in the WorkspaceSpec (app/agent/state.py, contract §4).

Layers of kind `reference` carry `ref_layer` (a name in `app.api.layers.LAYER_REGISTRY`) instead of
inline geometry — those are resolved against Postgres at full fidelity (no zoom simplification), so
the export stays reproducible from the spec alone for parcel/polygon/point layers (geojson already
inline) while still letting the agent reference shared background layers by name without inlining
1,242 parcels into every run's result."""
from __future__ import annotations

import json
from typing import Any

import psycopg

from app.api.layers import LAYER_NAMES, layer_geojson
from app.workspace.models import WorkspaceSpec


def build_geojson(conn: psycopg.Connection, spec: WorkspaceSpec) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    layer_names: list[str] = []
    for layer in spec.map.layers:
        layer_names.append(layer.id)
        if layer.geojson:
            fc = layer.geojson
            layer_features = fc.get("features", []) if isinstance(fc, dict) and "features" in fc else \
                ([fc] if isinstance(fc, dict) and fc.get("type") == "Feature" else [])
        elif layer.ref_layer and layer.ref_layer in LAYER_NAMES:
            layer_features = layer_geojson(conn, layer.ref_layer, bbox=None, zoom=None)["features"]
        else:
            layer_features = []
        for f in layer_features:
            f = dict(f)
            f["properties"] = {**f.get("properties", {}), "_layer_id": layer.id, "_layer_title": layer.title}
            features.append(f)
    return {"type": "FeatureCollection", "features": features,
           "properties": {"workspace": spec.title, "spec_version": spec.spec_version,
                          "ledger_head": spec.ledger_head, "layers": layer_names}}


def export_geojson(conn: psycopg.Connection, spec: WorkspaceSpec) -> bytes:
    return json.dumps(build_geojson(conn, spec), default=str).encode("utf-8")
