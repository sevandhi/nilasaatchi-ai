"""Whittaker smoother for irregular satellite time series (Eilers 2003).

Observations are placed on a regular daily grid with weight 1 (0 on days without an observation);
``(W + lam * D'D) z = W y`` with 2nd-order differences is solved as a symmetric banded system. A
robust pass (Tukey bisquare on the residuals) down-weights undetected cloud/haze outliers.

Rough intuition for daily steps: the half-power period is ~ 2*pi*lam**0.25 days, so lam = 1e3 keeps
~35-day features, 1e4 ~63 days, 1e5 ~110 days.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def _penalty_bands(n: int, lam: float) -> np.ndarray:
    """Upper-banded form (3 x n, for ``solveh_banded(lower=False)``) of lam * D2'D2."""
    from scipy import sparse

    ab = np.zeros((3, n))
    if n >= 3:
        d2 = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n))
        p = (d2.T @ d2).tocsr()
        for k in (0, 1, 2):
            ab[2 - k, k:] = p.diagonal(k)
    return ab * lam


def whittaker(y: np.ndarray, w: np.ndarray, lam: float) -> np.ndarray:
    """Weighted Whittaker smooth of ``y`` (NaN where ``w`` == 0) on a regular grid."""
    from scipy.linalg import solveh_banded

    y = np.asarray(y, dtype=float)
    w = np.asarray(w, dtype=float).copy()
    w[~np.isfinite(y)] = 0.0
    yy = np.where(w > 0, y, 0.0)
    n = len(y)
    if n < 3 or (w > 0).sum() < 2:
        m = np.nanmean(np.where(w > 0, y, np.nan)) if (w > 0).any() else np.nan
        return np.full(n, m)
    ab = _penalty_bands(n, lam)
    ab[2] += w
    ab[2] += 1e-9  # keeps the system positive definite for long unobserved stretches
    return solveh_banded(ab, w * yy, lower=False, check_finite=False)


def robust_whittaker(y: np.ndarray, w: np.ndarray, lam: float, iterations: int = 2,
                     c: float = 4.685, scale_floor: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    """Whittaker with ``iterations`` bisquare reweighting passes. Returns (z, final weights).

    The residual scale is floored at ``scale_floor`` (NDVI units): on clean series the MAD is tiny and
    an unfloored bisquare would reject the real peak points the smoother under-fits."""
    w0 = np.asarray(w, dtype=float)
    wk = w0.copy()
    z = whittaker(y, wk, lam)
    for _ in range(iterations):
        obs = wk > 0
        if obs.sum() < 4:
            break
        r = np.where(w0 > 0, y - z, 0.0)
        s = max(1.4826 * float(np.median(np.abs(r[w0 > 0]))), scale_floor)
        if not np.isfinite(s):
            break
        u = r / (c * s)
        rob = np.where(np.abs(u) < 1, (1 - u**2) ** 2, 0.0)
        wk = w0 * rob
        z = whittaker(y, wk, lam)
    return z, wk


def to_daily(days: np.ndarray, values: np.ndarray, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Scatter observations (integer day offsets) onto a daily grid; same-day duplicates are averaged."""
    y = np.zeros(n_days)
    w = np.zeros(n_days)
    days = np.asarray(days, dtype=int)
    ok = np.isfinite(values) & (days >= 0) & (days < n_days)
    np.add.at(y, days[ok], np.asarray(values)[ok])
    np.add.at(w, days[ok], 1.0)
    has = w > 0
    y[has] /= w[has]
    w[has] = 1.0
    y[~has] = np.nan
    return y, w


def smooth_series(days: np.ndarray, values: np.ndarray, n_days: int, lam: float,
                  iterations: int = 2) -> np.ndarray:
    y, w = to_daily(days, values, n_days)
    z, _ = robust_whittaker(y, w, lam, iterations)
    return z


def cv_lambda(series: Sequence[tuple[np.ndarray, np.ndarray]], n_days: int, grid: Sequence[float],
              holdout: float = 0.15, repeats: int = 3, seed: int = 0, iterations: int = 2) -> dict[float, float]:
    """Hold-out RMSE per lambda over several (days, values) series. Deterministic (fixed seed)."""
    rng = np.random.default_rng(seed)
    splits = []
    for days, vals in series:
        ok = np.flatnonzero(np.isfinite(vals))
        for _ in range(repeats):
            if len(ok) < 10:
                continue
            k = max(1, round(holdout * len(ok)))
            # never hold out the first/last obs (no extrapolation)
            test = rng.choice(ok[1:-1], size=min(k, len(ok) - 2), replace=False)
            splits.append((days, vals, test))
    out = {}
    for lam in grid:
        err = []
        for days, vals, test in splits:
            train = np.ones(len(vals), dtype=bool)
            train[test] = False
            z = smooth_series(days[train], vals[train], n_days, lam, iterations)
            err.append(vals[test] - z[days[test]])
        e = np.concatenate(err) if err else np.array([np.nan])
        out[float(lam)] = float(np.sqrt(np.nanmean(e**2)))
    return out
