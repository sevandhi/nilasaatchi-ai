"""Z-strip, validity repair and EPSG:32644 area on synthetic geometries."""
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import transform

from pipeline.gis.geom import (
    area_ha,
    area_ha_utm,
    clean_linear,
    clean_polygonal,
    from_metric,
    polygonal_part,
)
from pipeline.gis.load import fmb_rows


def test_strip_z_polygon():
    g = Polygon([(78.0, 8.78, 5), (78.001, 8.78, 5), (78.001, 8.781, 7), (78.0, 8.78, 5)])
    out, had_z, was_invalid = clean_polygonal(g)
    assert had_z and not was_invalid
    assert not out.has_z and isinstance(out, MultiPolygon)


def test_bowtie_is_repaired_to_valid_multipolygon():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    assert not bowtie.is_valid
    out, _, was_invalid = clean_polygonal(bowtie)
    assert was_invalid and out.is_valid and isinstance(out, MultiPolygon)
    assert out.area == pytest.approx(0.5)


def test_polygonal_part_drops_lines():
    from shapely.geometry import GeometryCollection
    gc = GeometryCollection([box(0, 0, 1, 1), LineString([(2, 2), (3, 3)])])
    assert polygonal_part(gc).area == pytest.approx(1.0)


def test_clean_linear_multi_and_2d():
    out, had_z = clean_linear(LineString([(78, 8, 1), (78.1, 8.1, 2)]))
    assert had_z and out.geom_type == "MultiLineString" and not out.has_z


# Local Lambert azimuthal EQUAL-AREA projection centred on the park: a square of side L metres in
# this CRS has a true (geodesic) area of exactly L^2.
_LAEA = "+proj=laea +lat_0=8.78 +lon_0=78.02 +datum=WGS84 +units=m +no_defs"
_FROM_LAEA = Transformer.from_crs(_LAEA, "EPSG:4326", always_xy=True)


def _laea_square(side_m):
    return transform(_FROM_LAEA.transform, box(0, 0, side_m, side_m))


@pytest.mark.parametrize("side_m,exp_ha", [(100, 1.0), (250, 6.25), (10, 0.01), (1000, 100.0)])
def test_geodesic_area_of_synthetic_equal_area_square(side_m, exp_ha):
    assert area_ha(_laea_square(side_m)) == pytest.approx(exp_ha, abs=1e-4)


def test_utm_area_of_synthetic_utm_square_and_distortion():
    # a 1 km square built in EPSG:32644 at the park: planar UTM area is exact, but the true area is
    # ~0.2 % smaller because the park is 3 degrees west of the UTM 44N central meridian (D-023)
    x0, y0 = 172000.0, 972000.0
    sq = from_metric(box(x0, y0, x0 + 1000, y0 + 1000))
    assert area_ha_utm(sq) == pytest.approx(100.0, abs=1e-4)
    ratio = area_ha_utm(sq) / area_ha(sq)
    assert 1.0015 < ratio < 1.0030


def test_area_ignores_non_polygonal_parts_and_rounds_to_4dp():
    from shapely.geometry import GeometryCollection
    g = GeometryCollection([_laea_square(12.3456), LineString([(78, 8.7), (78.1, 8.8)])])
    v = area_ha(g)
    assert v == round(v, 4) and v == pytest.approx(0.0152, abs=1e-4)


def _feat(**props):
    base = {"Land_id": "1", "dist_name": "Thoothukkudi", "taluk_name": "Thoothukudi", "vil_name": "Allikulam",
            "unit_id": 9, "block_id": 1, "survey_no": 16, "sub_div": None, "KIDE": "16"}
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "Polygon", "coordinates": [[[78, 8.78], [78.001, 8.78], [78.001, 8.781], [78, 8.78]]]}}


def test_fmb_rows_uid_subdiv_from_kide_and_anomalies():
    rows, anomalies = fmb_rows([
        _feat(KIDE="16/3-4-5", sub_div="38415", Land_id="A1"),   # Excel-mangled sub_div (real FMB case)
        _feat(KIDE="168", vil_name="Perurani", block_id=None),
    ])
    assert rows[0][0] == "Allikulam|16/3-4-5" and rows[0][4] == "3-4-5" and rows[0][7] == "A1"
    assert rows[1][0] == "Peroorani|168" and rows[1][4] is None and rows[1][6] is None
    assert anomalies["sub_div_differs_from_kide"] == [{"parcel_uid": "Allikulam|16/3-4-5", "sub_div": "38415"}]
    assert anomalies["null_block_id"] == ["Peroorani|168"]
    assert anomalies["district_variants"] == {"Thoothukkudi -> Thoothukudi": 2}


def test_fmb_rows_unknown_village_reported():
    rows, anomalies = fmb_rows([_feat(vil_name="Tirunelveli")])
    assert rows == [] and anomalies["unknown_village"] == ["Tirunelveli"]
