"""scripts/gen_schemas.py conversion logic + end-to-end run against the live app's OpenAPI doc."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.gen_schemas import build, schema_to_zod


def test_scalar_types():
    assert schema_to_zod({"type": "string"}, {}) == "z.string()"
    assert schema_to_zod({"type": "integer"}, {}) == "z.number().int()"
    assert schema_to_zod({"type": "boolean"}, {}) == "z.boolean()"


def test_ref_becomes_schema_reference():
    assert schema_to_zod({"$ref": "#/components/schemas/Foo"}, {}) == "FooSchema"


def test_nullable_anyof():
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert schema_to_zod(schema, {}) == "z.string().nullable()"


def test_enum_of_strings():
    schema = {"enum": ["a", "b"]}
    assert schema_to_zod(schema, {}) == 'z.enum(["a", "b"])'


def test_array_of_objects():
    schema = {"type": "array", "items": {"$ref": "#/components/schemas/Foo"}}
    assert schema_to_zod(schema, {}) == "z.array(FooSchema)"


def test_object_optional_fields():
    schema = {"type": "object", "required": ["a"],
             "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}
    zod = schema_to_zod(schema, {})
    assert '"a": z.string()' in zod and '"a": z.string().optional()' not in zod
    assert '"b": z.number().int().optional()' in zod


def test_build_end_to_end_against_the_real_app():
    from app.api.main import app

    js = build(app.openapi())
    assert "export const RunCreateResponseSchema" in js
    assert "export const endpoints = [" in js
    assert "import { z } from" in js
