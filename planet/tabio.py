"""Table I/O for planet outputs: parquet when a parquet engine is installed, else gzip CSV.

``pyarrow`` is not (yet) a project dependency; until it is, ``write_table`` falls back to
``<stem>.csv.gz`` next to the requested ``.parquet`` path and ``read_table`` finds either.
Writes are atomic (temp file + rename) so re-runs never leave partial files.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        try:
            import fastparquet  # noqa: F401
        except ImportError:
            return False
    return True


def _csv_path(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(".parquet") + ".csv.gz")


def write_table(df: Any, path: Path | str) -> Path:
    """Write ``df`` to ``path`` (.parquet) or its ``.csv.gz`` fallback; returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if parquet_available():
        target = path
        tmp = path.with_name(path.name + ".tmp")
        df.to_parquet(tmp, index=False)
        stale = _csv_path(path)
    else:
        target = _csv_path(path)
        tmp = target.with_name(target.name + ".tmp")
        df.to_csv(tmp, index=False, compression="gzip")
        stale = path
        log.warning("no parquet engine (pyarrow); wrote %s instead of %s", target.name, path.name)
    os.replace(tmp, target)
    if stale.exists():
        stale.unlink()
    return target


def existing_table(path: Path | str) -> Path | None:
    path = Path(path)
    for p in (path, _csv_path(path)):
        if p.exists():
            return p
    return None


def read_table(path: Path | str) -> Any:
    import pandas as pd

    p = existing_table(path)
    if p is None:
        raise FileNotFoundError(path)
    return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
