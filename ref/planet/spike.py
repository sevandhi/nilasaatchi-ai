"""Reproduce docs/spikes/sentinel2-spike.md with rasterio + pystac-client (``make s2-spike``).

Scenes are resolved through STAC by acquisition date + tile (baseline duplicates collapsed to the
highest ``_N`` suffix); no hrefs or scene ids are hard-coded.

Runs (grid x baseline policy)
- grid ``native`` (production geometry): scene UTM CRS (EPSG:32643), 10 m, windowed reads.
- grid ``legacy`` (``--legacy-grid``): EPSG:4326 at 0.0001 deg, bilinear bands / nearest SCL, exactly
  the grid the original GDAL spike warped to.
- baselines ``highest``: production policy, highest ``_N`` processing-baseline suffix per date.
- baselines ``spike``: the baseline variants the original spike happened to use (it mixed
  ``_0`` = baseline 03.00 for 2021-05-28 with ``_1`` = 05.00 for 2021-12-24). Hrefs are still
  resolved through STAC; only the variant suffix is pinned. ``legacy`` + ``spike`` is the
  reproduction proof and sets the exit code.

Both modes run with the -5 m inward buffer OFF (the spike had none); pass ``--buffer`` to see the
production setting. Outputs are signals needing field verification, never conclusions.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from planet.extract import parcel_stats as ps
from planet.stac import search as st

SPIKE_BBOX = (77.985, 8.755, 78.045, 8.815)  # from spikes/s2_ndvi_spike.py
TILE = "43PHK"
MIN_PX_EXCLUSIVE = 5  # the spike kept parcels with m.sum() > 5 (the write-up says ">= 5")
# Processing-baseline variant used by the original spike, where it is not the highest suffix today.
# Everything else in the spike was ``_0`` which is still the only/highest variant.
SPIKE_SUFFIX = {"2021-05-28": 0, "2021-12-24": 1}
DATES = ["2019-01-09", "2021-05-28", "2021-12-24", "2025-07-11", "2025-12-23", "2026-01-17", "2026-05-27"]

# Published numbers (docs/spikes/sentinel2-spike.md)
PUB_PARCELS = 1142
PUB_TABLE = {  # date: (median parcel NDVI, share > 0.35 [%], share < 0.15 [%])
    "2019-01-09": (0.383, 77.2, 0.0),
    "2021-05-28": (0.399, 67.0, 1.7),
    "2021-12-24": (0.714, 98.5, 0.1),
    "2025-07-11": (0.205, 3.1, 18.5),
    "2025-12-23": (0.684, 99.6, 0.0),
    "2026-01-17": (0.680, 98.5, 0.0),
    "2026-05-27": (0.239, 8.8, 7.7),
}
PUB_AMP = {"amp_pre": 0.297, "amp_post": 0.412, "n": 1142, "crop_pre": 557, "crop_post": 968, "crop_both": 532}
PUB_VILLAGE = {"Allikulam": 246, "Keelathattaparai": 135, "Melathattaparai": 84, "Umarikottai": 26,
               "Peroorani": 21, "Ramasamypuram": 17, "South Silukanpatti": 3}
PUB_233 = {"2021-05-28": 0.51, "2021-12-24": 0.78, "2025-07-11": 0.24, "2025-12-23": 0.76, "2026-05-27": 0.36}
PUB_SCENES = {"total": 170, 2019: 22, 2020: 29, 2021: 37, 2022: 17, 2023: 23, 2024: 13, 2025: 17, 2026: 12}

TOL_NDVI = 0.01
TOL_COUNT_REL = 0.03


def _today() -> date:
    return datetime.now(UTC).date()


def _count_ok(pub: float, rep: float) -> bool:
    return abs(rep - pub) <= max(TOL_COUNT_REL * abs(pub), 3)  # >= 3 parcels slack for small counts


def _share_ok(pub_pct: float, rep_pct: float, n: int) -> bool:
    return _count_ok(pub_pct * n / 100, rep_pct * n / 100)


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def resolve_scenes(dates: list[str], source: str, policy: str = "highest") -> dict[str, st.SceneRecord]:
    def one(d: str) -> st.SceneRecord:
        suffix = SPIKE_SUFFIX.get(d) if policy == "spike" else None
        return st.scene_for_date(SPIKE_BBOX, d, TILE, source=source, suffix=suffix)

    with ThreadPoolExecutor(max_workers=len(dates)) as ex:
        return dict(zip(dates, ex.map(one, dates)))


def make_grid(mode: str, scenes: dict[str, st.SceneRecord]) -> ps.Grid:
    epsg = next(iter(scenes.values())).epsg or 32643
    return ps.native_grid(SPIKE_BBOX, epsg) if mode == "native" else ps.legacy_grid(SPIKE_BBOX)


def prefetch(scenes: dict[str, st.SceneRecord], grid: ps.Grid, bands: tuple[str, ...] = ("nir", "red", "scl")) -> None:
    """Fetch all scenes' AOI windows concurrently into the per-scene cache."""
    with ThreadPoolExecutor(max_workers=len(scenes)) as ex:
        list(ex.map(lambda s: ps.read_scene(s, grid, bands=bands), scenes.values()))


def run_mode(mode: str, scenes: dict[str, st.SceneRecord], buffer_m: float | None) -> dict[str, Any]:
    parcels = ps.load_parcels()
    grid = make_grid(mode, scenes)
    prefetch(scenes, grid)
    labels = ps.rasterise(ps.prepare_parcels(parcels, grid.crs, buffer_m), grid)
    per: dict[str, dict[int, float]] = {}
    rows = {}
    for d, sc in scenes.items():
        df = ps.extract_scene(sc, parcels, grid, labels=labels, indices=("ndvi",))
        df = df[df.n_px > MIN_PX_EXCLUSIVE]
        per[d] = dict(zip(df.pid.astype(int), df.ndvi.astype(float)))
        v = df.ndvi.to_numpy()
        rows[d] = {"scene": sc.id, "parcels": len(v), "median": float(np.median(v)),
                   "share_hi": float(100 * (v > 0.35).mean()), "share_lo": float(100 * (v < 0.15).mean())}

    W, D1, D2, W2, D3 = (per[k] for k in ("2021-12-24", "2021-05-28", "2025-07-11", "2025-12-23", "2026-05-27"))
    ks = [k for k in W if all(k in r for r in (D1, D2, W2, D3))]
    amp_pre = np.array([W[k] - D1[k] for k in ks])
    amp_post = np.array([W2[k] - D3[k] for k in ks])
    crop_pre, crop_post = amp_pre > 0.3, amp_post > 0.3
    vil = dict(zip(parcels.pid.astype(int), parcels.vil_name))
    by_vil = collections.Counter(vil[k] for k, a, b in zip(ks, crop_pre, crop_post) if a and b)
    amp = {"amp_pre": float(np.median(amp_pre)), "amp_post": float(np.median(amp_post)), "n": len(ks),
           "crop_pre": int(crop_pre.sum()), "crop_post": int(crop_post.sum()),
           "crop_both": int((crop_pre & crop_post).sum())}

    mel = parcels[(parcels.vil_name == "Melathattaparai") & parcels.survey_no.isin([233, 235])]
    series = {int(r.survey_no): {d: per[d].get(int(r.pid)) for d in DATES} for r in mel.itertuples()}
    return {"mode": mode, "grid": {"crs": grid.crs, "shape": grid.shape, "res": grid.transform.a},
            "buffer_m": buffer_m, "table": rows, "amp": amp, "by_village": dict(by_vil), "survey": series}


def report(res: dict[str, Any]) -> bool:
    ok_all = True
    t, amp = res["table"], res["amp"]
    print(f"\n=== mode={res['mode']}  grid={res['grid']['crs']} {res['grid']['shape']} res={res['grid']['res']}"
          f"  buffer={res['buffer_m']} ===")
    print(f"{'date':<11} {'scene':<26} {'parcels':>12} {'median NDVI':>17} {'share>0.35 %':>17} {'share<0.15 %':>17}  status")
    for d in DATES:
        r = t[d]
        pm, ph, pl = PUB_TABLE[d]
        n = r["parcels"]
        oks = [abs(r["median"] - pm) <= TOL_NDVI, _share_ok(ph, r["share_hi"], n), _share_ok(pl, r["share_lo"], n)]
        ok_all &= all(oks)
        print(f"{d:<11} {r['scene']:<26} {PUB_PARCELS:>5}/{n:<6} {pm:>7.3f}/{r['median']:<7.3f}  "
              f"{ph:>6.1f}/{r['share_hi']:<6.1f}    {pl:>6.1f}/{r['share_lo']:<6.1f}    {_mark(all(oks))}")
    print("(cells are published/reproduced; parcel count is informational — see amplitude n)")
    print("\nSeasonal amplitude (post-monsoon - dry), parcels valid on all 5 dates:")
    for k, label, is_ndvi in [("n", "parcels (n)", False), ("amp_pre", "median amp 2021", True),
                              ("amp_post", "median amp 2025-26", True), ("crop_pre", "crop-like pre", False),
                              ("crop_post", "crop-like post", False), ("crop_both", "crop-like both", False)]:
        pub, rep = PUB_AMP[k], amp[k]
        ok = abs(rep - pub) <= TOL_NDVI if is_ndvi else _count_ok(pub, rep)
        ok_all &= ok
        fmt = "{:.3f}" if is_ndvi else "{}"
        print(f"  {label:<20} {fmt.format(pub):>7} / {fmt.format(rep):<7} {_mark(ok)}")
    print("  crop-like both, by village (published/reproduced):")
    for v, pub in PUB_VILLAGE.items():
        rep = res["by_village"].get(v, 0)
        ok = _count_ok(pub, rep)
        ok_all &= ok
        print(f"    {v:<20} {pub:>4} / {rep:<4} {_mark(ok)}")
    print("\nMelathattaparai surveys 233/235 NDVI series:")
    for s, ser in sorted(res["survey"].items()):
        cells = []
        for d in DATES:
            v = ser.get(d)
            txt = "  -  " if v is None else f"{v:.2f}"
            if s == 233 and d in PUB_233:
                ok = v is not None and abs(v - PUB_233[d]) <= TOL_NDVI + 0.005  # published at 2 d.p.
                ok_all &= ok
                txt = f"{PUB_233[d]:.2f}/{txt} {_mark(ok)}"
            cells.append(f"{d}: {txt}")
        print(f"  {s}: " + "; ".join(cells))
    print(f"\nmode={res['mode']}: {'ALL PASS' if ok_all else 'SOME FAIL'} "
          f"(tolerance ±{TOL_NDVI} NDVI; counts ±{int(TOL_COUNT_REL * 100)}% or ±3)")
    return ok_all


def scene_counts(source: str) -> dict[str, Any]:
    recs = st.search_scenes(SPIKE_BBOX, "2019-01-01", _today().isoformat(), tiles=[TILE], source=source,
                            dedup=False)
    raw = len(recs)
    ded = st.search_scenes(SPIKE_BBOX, "2019-01-01", _today().isoformat(), tiles=[TILE], source=source)
    by_year: dict[int, dict[str, int]] = collections.defaultdict(lambda: {"all": 0, "lt20": 0})
    for r in ded:
        by_year[r.date.year]["all"] += 1
        if r.cloud is not None and r.cloud < 20:
            by_year[r.date.year]["lt20"] += 1
    raw_lt20 = sum(1 for r in recs if r.cloud is not None and r.cloud < 20)
    any_lt20 = len({r.date for r in recs if r.cloud is not None and r.cloud < 20})
    return {"raw_items": raw, "raw_lt20": raw_lt20, "dates_any_variant_lt20": any_lt20, "dedup": len(ded),
            "dedup_lt20": sum(v["lt20"] for v in by_year.values()),
            "by_year": {y: by_year[y] for y in sorted(by_year)}}


def print_counts(c: dict[str, Any]) -> None:
    print(f"\nSTAC inventory tile {TILE}, 2019-01-01..{_today()} "
          f"(raw items {c['raw_items']}, after baseline dedup {c['dedup']}):")
    print(f"  {'year':<6} {'all':>5} {'<20% cloud':>11} {'published <20%':>15}")
    for y, v in c["by_year"].items():
        print(f"  {y:<6} {v['all']:>5} {v['lt20']:>11} {PUB_SCENES.get(y, '-')!s:>15}")
    print(f"  {'total':<6} {c['dedup']:>5} {c['dedup_lt20']:>11} {PUB_SCENES['total']:>15}"
          f"   (raw <20% incl. baseline duplicates: {c['raw_lt20']}; distinct dates with any variant"
          f" <20%: {c['dates_any_variant_lt20']})")
    print("  note: the published 170 counted processing-baseline duplicates; <20% here uses the kept"
          " (highest-suffix) variant's scene cloud cover")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", choices=["native", "legacy", "both"], default="both")
    ap.add_argument("--legacy-grid", action="store_true", help="shortcut for --grid legacy --baselines spike")
    ap.add_argument("--baselines", choices=["highest", "spike", "both"], default="both")
    ap.add_argument("--buffer", action="store_true", help=f"apply the {ps.DEFAULT_BUFFER_M} m inward buffer")
    ap.add_argument("--source", default="auto", choices=["auto", "earth-search", "planetary-computer"])
    ap.add_argument("--limit", type=int, default=None, help="only the first N dates (fetch/cache smoke test)")
    ap.add_argument("--no-counts", action="store_true", help="skip the 2019->today STAC inventory count")
    ap.add_argument("--out", default=str(ps.CACHE_DIR / "spike_result.json"))
    a = ap.parse_args(argv)
    if a.legacy_grid:
        a.grid, a.baselines = "legacy", "spike"

    t0 = time.time()
    dates = DATES[: a.limit] if a.limit else DATES
    policies = ["highest", "spike"] if a.baselines == "both" else [a.baselines]
    grids = ["native", "legacy"] if a.grid == "both" else [a.grid]
    scene_sets = {p: resolve_scenes(dates, a.source, p) for p in policies}
    for p, scenes in scene_sets.items():
        print(f"Resolved scenes via STAC (tile {TILE}, baseline policy '{p}'):")
        for d, s in scenes.items():
            dup = f"  (other variants: {', '.join(s.duplicates)})" if s.duplicates else ""
            print(f"  {d}  {s.id}  cloud={s.cloud:.2f}  baseline={s.processing_baseline}  src={s.source}{dup}")
    if a.limit:
        for scenes in scene_sets.values():
            for g in grids:
                prefetch(scenes, make_grid(g, scenes))
        print(f"--limit {a.limit}: fetched/cached {len(dates)} date(s) in {time.time() - t0:.1f} s")
        return 0

    buffer_m = ps.DEFAULT_BUFFER_M if a.buffer else None
    results, status = {}, {}
    for p in policies:
        for g in grids:
            key = f"{g}+{p}"
            tm = time.time()
            res = run_mode(g, scene_sets[p], buffer_m)
            res["mode"] = key
            status[key] = report(res)
            res["seconds"] = round(time.time() - tm, 1)
            results[key] = res
            print(f"{key} took {res['seconds']} s")

    counts = None
    if not a.no_counts:
        counts = scene_counts(a.source)
        print_counts(counts)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"scenes": {p: {d: s.to_dict() for d, s in sc.items()} for p, sc in scene_sets.items()},
                               "results": results, "status": status, "counts": counts}, indent=1, default=str))
    print("\nSummary: " + ", ".join(f"{m}={_mark(v)}" for m, v in status.items())
          + f"; total {time.time() - t0:.1f} s; results -> {out}")
    # Reproduction proof: legacy grid + spike baselines when run, else whatever ran first.
    proof = status.get("legacy+spike", next(iter(status.values())))
    return 0 if proof else 1


if __name__ == "__main__":
    sys.exit(main())
