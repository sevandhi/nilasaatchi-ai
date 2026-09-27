"""Domain packs: everything domain-specific (tools, SQL allow-list, schema card, glossary, prompts, slot
extraction, extra deterministic checks) lives here. The agent graph (app/agent) is domain-agnostic: a new
domain = a new pack registered in PACKS, with no change to graph control flow.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from app.agent.state import Attachment, MissingInfo, Slots

DEFAULT_PACK = "land_acquisition"


@dataclass
class SlotResult:
    slots: Slots
    missing: list[MissingInfo] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class DomainPack:
    name: str
    title: str
    root: Path
    build_tools: Callable[[], list]                      # -> list[Tool]
    sql_allowlist: frozenset[str]
    extract_slots: Callable[[str, list[Attachment]], SlotResult]
    detect: Callable[[str, list[Attachment]], float]     # 0..1 affinity for auto-selection
    checks: dict[str, Callable] = field(default_factory=dict)
    schema_notes: str = ""
    sql_examples: list[dict] = field(default_factory=list)
    critic_tools: list[str] = field(default_factory=list)
    resolve_slots: Callable[[Slots, str], Slots] | None = None       # enrich slots from the KG (e.g. ids)
    default_plan: Callable[[Slots], dict] | None = None              # deterministic plan when planning fails
    non_name_words: frozenset[str] = frozenset()   # document vocabulary that OCR mis-filed as owner names

    def prompt(self, name: str) -> str:
        p = self.root / "prompts" / f"{name}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def glossary(self) -> str:
        p = self.root / "glossary.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def registry(self):
        from app.tools.registry import ToolRegistry
        return ToolRegistry(self.build_tools())


@cache
def _packs() -> dict[str, DomainPack]:
    from app.domains.agri_claims.pack import PACK as AGRI
    from app.domains.land_acquisition.pack import PACK as LAND
    return {LAND.name: LAND, AGRI.name: AGRI}


def get_pack(name: str) -> DomainPack:
    packs = _packs()
    if name not in packs:
        raise KeyError(f"unknown domain pack {name!r}; known: {sorted(packs)}")
    return packs[name]


def pack_names() -> list[str]:
    return sorted(_packs())


def select_pack(request: str, attachments: list[Attachment], explicit: str | None = None,
                default: str = DEFAULT_PACK) -> tuple[DomainPack, dict[str, float]]:
    """Deterministic pack choice: explicit > highest detect() score (ties -> default)."""
    if explicit:
        return get_pack(explicit), {explicit: 1.0}
    scores = {n: round(float(p.detect(request, attachments)), 3) for n, p in _packs().items()}
    best = max(sorted(scores), key=lambda n: (scores[n], n == default))
    if scores[best] <= scores.get(default, 0):
        best = default
    return get_pack(best), scores


def any_to_attachment(a: Any) -> Attachment:
    return a if isinstance(a, Attachment) else Attachment.model_validate(a)
