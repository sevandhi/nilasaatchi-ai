from pipeline.extract.consistency import run_checks, serial_run


def _rows(surveys, sub=None):
    return [{"record_type": "parcel_row", "survey_no": s, "sub_div": sub, "extent_ha": 1.0} for s in surveys]


def test_serial_column_read_as_survey_fails():
    checks = run_checks(_rows(["1", "2", "3", "4", "204"]), {}, {}, "parcel")
    c = next(c for c in checks if c["name"] == "survey_not_serial")
    assert c["result"] == "fail" and "1..4" in c["detail"]


def test_real_survey_numbers_pass():
    checks = run_checks(_rows(["202", "202", "203", "208"]), {}, {}, "parcel")
    assert next(c for c in checks if c["name"] == "survey_not_serial")["result"] == "pass"


def test_subdivided_or_short_tables_are_not_flagged():
    assert serial_run(_rows(["1", "2", "3"], sub="1A")) == 0
    assert serial_run(_rows(["1", "2"])) is None
