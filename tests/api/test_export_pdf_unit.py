"""PDF export HTML rendering is pure and DB-free; the actual Playwright rasterisation is exercised
(and skipped with a reason if browsers aren't installed) in tests/api/test_workspaces_db.py."""
from __future__ import annotations

from app.export.pdf import render_html
from app.workspace.models import WorkspaceSpec


def _spec(**overrides) -> WorkspaceSpec:
    base = {
        "title": "Melathattaparai 233", "domain": "land_acquisition", "run_id": "run123",
        "created_at": "2026-09-27T00:00:00Z", "ledger_head": "deadbeef",
        "kpis": [{"id": "k1", "label": "NDVI at award", "value": 0.76, "unit": None, "claim_id": "c1",
              "status": "verified", "confidence": 0.9}],
        "tables": [{"id": "t1", "title": "Findings",
                "columns": [{"key": "note", "label": "Note", "type": "string"}], "rows": [{"note": "x"}]}],
        "narrative": {"en": "Paper vs Planet summary", "claim_refs": ["c1"]},
        "caveats": ["signal needing field verification"],
    }
    base.update(overrides)
    return WorkspaceSpec(**base)


def test_render_html_includes_spec_content():
    html_doc = render_html(_spec())
    assert "Melathattaparai 233" in html_doc
    assert "NDVI at award" in html_doc
    assert "Findings" in html_doc
    assert "Paper vs Planet summary" in html_doc
    assert "deadbeef" in html_doc
    assert "field verification" in html_doc  # findings are leads, never legal conclusions


def test_render_html_escapes_html_in_title():
    html_doc = render_html(_spec(title="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html_doc
    assert "&lt;script&gt;" in html_doc
