import math

import pandas as pd

from pipeline.raster.run import _json_safe, _parcel_uid


def test_parcel_uid_format():
    row = {"vil_name": "Allikulam", "KIDE": "37/3"}
    assert _parcel_uid(row) == "Allikulam|37/3"


def test_json_safe_replaces_nan_with_none():
    assert _json_safe(float("nan")) is None
    assert _json_safe(math.nan) is None


def test_json_safe_recurses_into_dict_and_list():
    d = {"a": float("nan"), "b": [1, float("nan"), 3], "c": {"d": float("nan")}}
    out = _json_safe(d)
    assert out == {"a": None, "b": [1, None, 3], "c": {"d": None}}


def test_json_safe_leaves_ordinary_values_untouched():
    assert _json_safe(1.5) == 1.5
    assert _json_safe("cropland") == "cropland"
    assert _json_safe(None) is None


def test_json_safe_handles_pandas_na():
    assert _json_safe(pd.NA) is None
