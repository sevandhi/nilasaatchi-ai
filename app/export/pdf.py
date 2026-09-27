"""PDF export: render a small self-contained HTML report from the WorkspaceSpec (title, kpis,
narrative, tables, caveats, ledger head — contract §4), then rasterise with Playwright's headless
Chromium.

If Playwright's browser binaries are not installed (`playwright install chromium`), this raises
`PlaywrightNotAvailable` (a `NotImplementedError` subclass) — the workspaces router turns that into
HTTP 501, and tests/api/test_workspaces_db.py marks the PDF assertion as skipped with this reason
(plan.md cut-list: "PDF export (keep CSV/GeoJSON)" is the fallback if it can't be made to work).
"""
from __future__ import annotations

import html

from app.workspace.models import WorkspaceSpec


class PlaywrightNotAvailable(NotImplementedError):
    pass


def render_html(spec: WorkspaceSpec) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    kpis = "".join(
        f"<li>{esc(k.label)}: <b>{esc(k.value)}</b>{esc(' ' + k.unit) if k.unit else ''} "
        f"[{esc(k.status)}, confidence {esc(k.confidence)}]</li>" for k in spec.kpis
    )
    tables = "".join(f"<li>{esc(t.title)} ({len(t.rows)} rows)</li>" for t in spec.tables)
    charts = "".join(f"<li>{esc(c.type)} — {esc(c.title)}</li>" for c in spec.charts)
    caveats = "".join(f"<li>{esc(c)}</li>" for c in spec.caveats)
    narrative = esc(spec.narrative.en) if spec.narrative and spec.narrative.en else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{esc(spec.title)}</title>
<style>
body {{ font-family: sans-serif; margin: 2em; color: #1a1a1a; }}
h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.1em; margin-top: 1.5em; }}
footer {{ margin-top: 2em; font-size: 0.8em; color: #666; }}
</style></head>
<body>
<h1>NilaSaatchi AI — Paper vs Planet</h1>
<h2>{esc(spec.title)}</h2>
<p>{narrative}</p>
<h2>KPIs</h2><ul>{kpis or '<li>(none)</li>'}</ul>
<h2>Tables</h2><ul>{tables or '<li>(none)</li>'}</ul>
<h2>Charts</h2><ul>{charts or '<li>(none)</li>'}</ul>
<h2>Caveats</h2><ul>{caveats or '<li>(none)</li>'}</ul>
<footer>Ledger head: {esc(spec.ledger_head or '(none)')} · spec version {esc(spec.spec_version)}
· run {esc(spec.run_id)} · domain {esc(spec.domain)}
<br>Findings are signals requiring field verification, not legal conclusions.</footer>
</body></html>"""


def export_pdf(spec: WorkspaceSpec) -> bytes:
    return html_to_pdf(render_html(spec))


def html_to_pdf(html_doc: str) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PlaywrightNotAvailable("playwright is not installed (uv sync)") from e
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html_doc)
                return page.pdf(format="A4", print_background=True,
                                margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
            finally:
                browser.close()
    except Exception as e:
        raise PlaywrightNotAvailable(
            f"Playwright browser binaries are not installed (run `playwright install chromium`): {e}"
        ) from e
