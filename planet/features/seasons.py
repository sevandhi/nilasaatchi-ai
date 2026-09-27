"""Season calendar (``config/seasons.yaml``): date -> (ag_year, season) and season windows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "config" / "seasons.yaml"


@dataclass(frozen=True)
class SeasonConfig:
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw.get("version", "seasons"))

    @property
    def start_month(self) -> int:
        return int(self.raw.get("ag_year_start_month", 6))

    @property
    def seasons(self) -> dict[str, list[int]]:
        return {k: [int(m) for m in v] for k, v in self.raw["seasons"].items()}

    @property
    def annual(self) -> str:
        return str(self.raw.get("annual_label", "annual"))

    @property
    def dry_months(self) -> list[int]:
        return [int(m) for m in self.raw.get("dry_months", [])]

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def validate(self) -> None:
        months = sorted(m for v in self.seasons.values() for m in v)
        if months != list(range(1, 13)):
            raise ValueError(f"seasons must partition months 1..12 exactly once, got {months}")


@lru_cache(maxsize=4)
def _load(path: str) -> SeasonConfig:
    cfg = SeasonConfig(yaml.safe_load(Path(path).read_text()))
    cfg.validate()
    return cfg


def load_config(path: Path | str = CONFIG_PATH) -> SeasonConfig:
    return _load(str(path))


def ag_year_of(d: date | pd.Timestamp, start_month: int = 6) -> int:
    return d.year if d.month >= start_month else d.year - 1


def season_of_month(month: int, cfg: SeasonConfig) -> str:
    for name, months in cfg.seasons.items():
        if month in months:
            return name
    raise ValueError(month)


def assign(dates: Any, cfg: SeasonConfig | None = None) -> pd.DataFrame:
    """Vectorised: dates -> DataFrame(ag_year, season)."""
    cfg = cfg or load_config()
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    m = d.month.to_numpy()
    ag = np.where(m >= cfg.start_month, d.year.to_numpy(), d.year.to_numpy() - 1)
    lut = np.empty(13, dtype=object)
    for name, months in cfg.seasons.items():
        for mm in months:
            lut[mm] = name
    return pd.DataFrame({"ag_year": ag.astype(int), "season": lut[m]})


def _month_start(year: int, month: int) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month, day=1)


def window(ag_year: int, season: str, cfg: SeasonConfig | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inclusive [start, end] dates of ``season`` (or the annual row) in ``ag_year``.
    Season months are ordered as they fall within the ag-year (Oct, Nov, Dec, Jan, Feb for rabi)."""
    cfg = cfg or load_config()
    sm = cfg.start_month
    if season == cfg.annual:
        months = [((sm - 1 + i) % 12) + 1 for i in range(12)]
    else:
        months = sorted(cfg.seasons[season], key=lambda mm: (mm - sm) % 12)
    first, last = months[0], months[-1]
    y0 = ag_year if first >= sm else ag_year + 1
    y1 = ag_year if last >= sm else ag_year + 1
    end = _month_start(y1, last) + pd.offsets.MonthEnd(0)
    return _month_start(y0, first), end


def windows(first: pd.Timestamp, last: pd.Timestamp, cfg: SeasonConfig | None = None
            ) -> list[tuple[int, str, pd.Timestamp, pd.Timestamp]]:
    """All (ag_year, season, start, end) windows overlapping [first, last], seasons then the annual row."""
    cfg = cfg or load_config()
    out = []
    for y in range(ag_year_of(first, cfg.start_month), ag_year_of(last, cfg.start_month) + 1):
        for s in [*cfg.seasons, cfg.annual]:
            a, b = window(y, s, cfg)
            if b >= first and a <= last:
                out.append((y, s, a, b))
    return out
