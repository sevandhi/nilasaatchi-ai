"""Small, dependency-free markdown pipe-table parser shared by the /progress, /decisions and
/eval/summary endpoints (docs/progress.md, docs/decisions.md, docs/metrics.md are the source of
truth per CLAUDE.md; this module only reads and reshapes them, never invents numbers)."""
from __future__ import annotations

import re


def parse_tables(markdown: str) -> list[list[dict[str, str]]]:
    """Returns one list of row-dicts per pipe-table found in `markdown`, in document order."""
    lines = markdown.splitlines()
    tables: list[list[dict[str, str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                rows.append({header[k]: (cells[k] if k < len(cells) else "") for k in range(len(header))})
                j += 1
            tables.append(rows)
            i = j
        else:
            i += 1
    return tables


def first_table(markdown: str) -> list[dict[str, str]]:
    tables = parse_tables(markdown)
    return tables[0] if tables else []
