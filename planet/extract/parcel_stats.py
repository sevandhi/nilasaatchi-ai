"""Per-parcel masked spectral statistics from Sentinel-2 L2A COGs.

Pipeline for one scene:
1. build an AOI grid — native: the scene's UTM CRS at 10 m, snapped to the 60 m S2 lattice so
   10/20 m bands align exactly; legacy: EPSG:4326 at 0.0001 deg (mimics the original GDAL spike);
2. windowed COG reads of blue/green/red/nir/swir16 (20 m -> 10 m bilinear) and SCL (nearest),
   cached per scene + grid as a uint16 GeoTIFF under ``data/s2/``;
3. rasterise the FMB parcels (reprojected to the grid CRS, optionally buffered inward by 5 m)
   with a sequential integer id ``pid`` (``Land_id`` is not always numeric, e.g. "A1");
4. valid mask = SCL in {4 vegetation, 5 bare, 6 water, 7 unclassified} and DN > 0;
5. per-parcel median of each index, ``n_total``, ``n_px`` (valid), ``valid_frac``, ``low_support``.

Outputs are *signals needing field verification* (clouds, mixed pixels, weed flush).
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    import geopandas as gpd
    import pandas as pd

    from planet.stac.search import SceneRecord

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
FMB_PATH = REPO / "Dataset" / "Geospatial_Layer" / "Park_fmb_Map.geojson"
CACHE_DIR = REPO / "data" / "s2"

VALID_SCL = (4, 5, 6, 7)
SPECTRAL = ("blue", "green", "red", "nir", "swir16")
CACHE_BANDS = SPECTRAL + ("scl",)
INDEX_BANDS: dict[str, tuple[str, ...]] = {
    "ndvi": ("nir", "red"),
    "ndwi": ("green", "nir"),
    "ndbi": ("swir16", "nir"),
    "bsi": ("swir16", "red", "nir", "blue"),
}
LOW_SUPPORT_PX = 10
DEFAULT_BUFFER_M = -5.0
EPS = 1e-6
PARCEL_ATTRS = ("Land_id", "vil_name", "unit_id", "block_id", "survey_no", "sub_div", "KIDE")

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "AWS_NO_SIGN_REQUEST": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "VSI_CACHE": "TRUE",
}


# --------------------------------------------------------------------------- grids
@dataclass(frozen=True)
class Grid:
    crs: str
    transform: Any  # affine.Affine
    width: int
    height: int
    resampling: str = "native"  # "native" (windowed read) or "legacy" (warp)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        t = self.transform
        return (t.c, t.f + t.e * self.height, t.c + t.a * self.width, t.f)

    def key(self) -> str:
        t = self.transform
        s = f"{self.crs}|{t.a:.9f},{t.b},{t.c:.6f},{t.d},{t.e:.9f},{t.f:.6f}|{self.width}x{self.height}|{self.resampling}"
        return hashlib.sha1(s.encode()).hexdigest()[:12]


def native_grid(bbox_lonlat: Sequence[float], epsg: int, res: float = 10.0, snap: float = 60.0) -> Grid:
    """Grid in the scene's UTM CRS covering ``bbox_lonlat``; edges snapped outward to ``snap`` m
    (the S2 10/20/60 m pixel lattice shares 60 m multiples), so windowed reads need no resampling
    at 10 m and 20 m bands upsample on exact pixel boundaries."""
    from affine import Affine
    from rasterio.warp import transform_bounds

    crs = f"EPSG:{epsg}"
    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", crs, *bbox_lonlat, densify_pts=21)
    minx, miny = math.floor(minx / snap) * snap, math.floor(miny / snap) * snap
    maxx, maxy = math.ceil(maxx / snap) * snap, math.ceil(maxy / snap) * snap
    w, h = round((maxx - minx) / res), round((maxy - miny) / res)
    return Grid(crs, Affine(res, 0.0, minx, 0.0, -res, maxy), w, h, "native")


def legacy_grid(bbox_lonlat: Sequence[float], res: float = 0.0001) -> Grid:
    """EPSG:4326 grid exactly as ``gdal.Warp(outputBounds=bbox, xRes=res, yRes=res)`` builds it."""
    from affine import Affine

    minx, miny, maxx, maxy = bbox_lonlat
    w = int((maxx - minx) / res + 0.5)
    h = int((maxy - miny) / res + 0.5)
    return Grid("EPSG:4326", Affine(res, 0.0, minx, 0.0, -res, maxy), w, h, "legacy")


# --------------------------------------------------------------------------- reading
def _read_band_native(href: str, grid: Grid, resampling: str) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        if str(ds.crs) != grid.crs:
            return _read_band_warp(href, grid, resampling)
        win = from_bounds(*grid.bounds, transform=ds.transform)
        return ds.read(1, window=win, out_shape=grid.shape, resampling=getattr(Resampling, resampling),
                       boundless=True, fill_value=0)


def _read_band_warp(href: str, grid: Grid, resampling: str) -> np.ndarray:
    """Windowed source read (with margin) + GDAL warp into ``grid`` (same warper as gdal.Warp)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        b = transform_bounds(grid.crs, ds.crs, *grid.bounds, densify_pts=21)
        pad = 8 * abs(ds.transform.a)
        win = from_bounds(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, transform=ds.transform)
        win = win.round_offsets().round_lengths()
        src = ds.read(1, window=win, boundless=True, fill_value=0)
        src_t = ds.window_transform(win)
        dst = np.zeros(grid.shape, dtype=src.dtype)
        with rasterio.Env(**GDAL_ENV):
            reproject(src, dst, src_transform=src_t, src_crs=ds.crs, src_nodata=ds.nodata,
                      dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=ds.nodata,
                      resampling=getattr(Resampling, resampling))
        return dst


def _cache_path(scene_id: str, grid: Grid, cache_dir: Path) -> Path:
    return Path(cache_dir) / scene_id / f"aoi_{grid.key()}.tif"


def _write_cache(path: Path, arrays: Mapping[str, np.ndarray], grid: Grid, tags: Mapping[str, Any]) -> None:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.tif")
    names = list(arrays)
    with rasterio.open(tmp, "w", driver="GTiff", width=grid.width, height=grid.height, count=len(names),
                       dtype="uint16", crs=grid.crs, transform=grid.transform, nodata=0,
                       compress="deflate", predictor=2, tiled=True, blockxsize=256, blockysize=256) as dst:
        for i, n in enumerate(names, 1):
            dst.write(arrays[n].astype("uint16"), i)
            dst.set_band_description(i, n)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
    os.replace(tmp, path)


def _read_cache(path: Path) -> dict[str, np.ndarray]:
    import rasterio

    with rasterio.open(path) as ds:
        return {ds.descriptions[i - 1]: ds.read(i) for i in range(1, ds.count + 1)}


def read_scene(scene: SceneRecord, grid: Grid, *, bands: Sequence[str] = CACHE_BANDS,
               cache_dir: Path | str = CACHE_DIR, refresh: bool = False, workers: int = 6) -> dict[str, np.ndarray]:
    """Return raw DN arrays (uint16, 0 = nodata) for ``bands`` on ``grid``, reading from the per-scene
    AOI cache when present. Spectral bands use bilinear resampling, SCL nearest."""
    path = _cache_path(scene.id, grid, Path(cache_dir))
    cached: dict[str, np.ndarray] = {}
    if path.exists() and not refresh:
        try:
            cached = _read_cache(path)
        except Exception as exc:  # noqa: BLE001 - any corrupt/partial cache -> re-read
            log.warning("cache %s unreadable (%s); refetching", path, exc)
    missing = [b for b in bands if b not in cached]
    if missing:
        reader = _read_band_native if grid.resampling == "native" else _read_band_warp

        def fetch(b: str) -> tuple[str, np.ndarray]:
            if b not in scene.hrefs:
                raise KeyError(f"scene {scene.id} has no asset {b!r}")
            return b, reader(scene.hrefs[b], grid, "nearest" if b == "scl" else "bilinear")

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(missing)))) as ex:
            cached.update(dict(ex.map(fetch, missing)))
        _write_cache(path, cached, grid, {"scene_id": scene.id, "dn_offset": scene.dn_offset,
                                          "source": scene.source, **{f"href_{b}": h for b, h in scene.hrefs.items()}})
    return {b: cached[b] for b in bands}


# --------------------------------------------------------------------------- indices / masking
def reflectance(dn: np.ndarray, dn_offset: int = 0) -> np.ndarray:
    """DN -> surface reflectance (float32). DN 0 (nodata) -> NaN."""
    dn = dn.astype("float32")
    out = (dn - dn_offset) * 1e-4
    out[dn == 0] = np.nan
    return out


def compute_index(name: str, r: Mapping[str, np.ndarray]) -> np.ndarray:
    """Normalised-difference indices on reflectance arrays (``EPS`` in the denominator, as in the spike)."""
    if name == "ndvi":
        return (r["nir"] - r["red"]) / (r["nir"] + r["red"] + EPS)
    if name == "ndwi":
        return (r["green"] - r["nir"]) / (r["green"] + r["nir"] + EPS)
    if name == "ndbi":
        return (r["swir16"] - r["nir"]) / (r["swir16"] + r["nir"] + EPS)
    if name == "bsi":
        a, b = r["swir16"] + r["red"], r["nir"] + r["blue"]
        return (a - b) / (a + b + EPS)
    raise ValueError(f"unknown index {name!r}")


def valid_mask(scl: np.ndarray, valid_classes: Iterable[int] = VALID_SCL) -> np.ndarray:
    return np.isin(scl, list(valid_classes))


# --------------------------------------------------------------------------- parcels
def load_parcels(path: Path | str = FMB_PATH) -> gpd.GeoDataFrame:
    """Read FMB polygons (read-only) and assign a sequential integer ``pid`` (1..N, file order)."""
    import geopandas as gpd
    from shapely.validation import make_valid

    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf["geometry"] = [g if g.is_valid else make_valid(g) for g in gdf.geometry]
    gdf.insert(0, "pid", np.arange(1, len(gdf) + 1, dtype="int32"))
    return gdf


def prepare_parcels(gdf: gpd.GeoDataFrame, crs: str, buffer_m: float | None = DEFAULT_BUFFER_M) -> gpd.GeoDataFrame:
    """Reproject to ``crs``; optionally buffer inward (negative ``buffer_m``) in a metric CRS.
    A parcel whose buffered geometry is empty keeps its original geometry (``buffered=False``)."""
    out = gdf.to_crs(crs)
    out["buffered"] = False
    if buffer_m:
        metric = out if out.crs.is_projected else out.to_crs(out.estimate_utm_crs())
        buf = metric.geometry.buffer(buffer_m)
        ok = np.array([g is not None and not g.is_empty for g in buf])
        buf = buf.to_crs(out.crs) if metric is not out else buf
        out.loc[ok, "geometry"] = buf[ok]
        out.loc[ok, "buffered"] = True
    return out


def rasterise(gdf: gpd.GeoDataFrame, grid: Grid, id_col: str = "pid") -> np.ndarray:
    """Burn integer ids (0 = background). Later features overwrite earlier ones, as in gdal.Rasterize."""
    from rasterio.features import rasterize

    shapes = ((geom, int(v)) for geom, v in zip(gdf.geometry, gdf[id_col]) if geom is not None and not geom.is_empty)
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="int32",
                     all_touched=False)


# --------------------------------------------------------------------------- statistics
def grouped_median(labels: np.ndarray, values: np.ndarray, n_labels: int) -> np.ndarray:
    """Median of ``values`` per integer label in 0..n_labels (NaN where a label has no values).
    Matches ``np.median`` (mean of the two middle values for even counts)."""
    labels = np.asarray(labels).ravel()
    values = np.asarray(values, dtype="float64").ravel()
    keep = np.isfinite(values)
    labels, values = labels[keep], values[keep]
    order = np.lexsort((values, labels))
    lab, val = labels[order], values[order]
    counts = np.bincount(lab, minlength=n_labels + 1)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    out = np.full(n_labels + 1, np.nan)
    has = counts > 0
    lo = starts[has] + (counts[has] - 1) // 2
    hi = starts[has] + counts[has] // 2
    out[has] = (val[lo] + val[hi]) / 2.0
    return out


def parcel_stats(labels: np.ndarray, indices: Mapping[str, np.ndarray], valid: np.ndarray,
                 n_labels: int | None = None) -> pd.DataFrame:
    """Per-label ``n_total``, ``n_px`` (valid pixels), ``valid_frac``, ``low_support`` and the median
    of each index over valid pixels. Rows only for labels present on the grid."""
    import pandas as pd

    labels = np.asarray(labels)
    n = int(n_labels if n_labels is not None else labels.max(initial=0))
    total = np.bincount(labels.ravel(), minlength=n + 1)
    fg = labels > 0
    sel = fg & valid
    n_px = np.bincount(labels[sel], minlength=n + 1)
    df = pd.DataFrame({"pid": np.arange(n + 1), "n_total": total, "n_px": n_px})
    for name, arr in indices.items():
        df[name] = grouped_median(labels[sel], arr[sel], n)
    df = df[(df.pid > 0) & (df.n_total > 0)].copy()
    df["valid_frac"] = df.n_px / df.n_total
    df["low_support"] = df.n_px < LOW_SUPPORT_PX
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- one-call API
def extract_scene(scene: SceneRecord, parcels: gpd.GeoDataFrame, grid: Grid, *,
                  buffer_m: float | None = DEFAULT_BUFFER_M, indices: Sequence[str] = ("ndvi",),
                  cache_dir: Path | str = CACHE_DIR, labels: np.ndarray | None = None) -> pd.DataFrame:
    """Per-parcel stats for one scene. ``parcels`` must carry ``pid`` (see :func:`load_parcels`).
    Pass a precomputed ``labels`` raster to reuse rasterisation across scenes on the same grid."""
    need = sorted({b for i in indices for b in INDEX_BANDS[i]} | {"scl"})
    dn = read_scene(scene, grid, bands=need, cache_dir=cache_dir)
    if labels is None:
        labels = rasterise(prepare_parcels(parcels, grid.crs, buffer_m), grid)
    refl = {b: reflectance(dn[b], scene.dn_offset) for b in need if b != "scl"}
    ok = valid_mask(dn["scl"])
    for b in refl.values():
        ok &= np.isfinite(b)
    stats = parcel_stats(labels, {i: compute_index(i, refl) for i in indices}, ok, int(parcels.pid.max()))
    attrs = [c for c in PARCEL_ATTRS if c in parcels.columns]
    stats = stats.merge(parcels[["pid", *attrs]], on="pid", how="left")
    stats["scene_id"] = scene.id
    stats["date"] = scene.date.isoformat()
    stats["href"] = scene.hrefs.get("red")
    return stats
