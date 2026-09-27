"""Offline tests for planet.refresh: protocol lines, no-new-scenes path, dry run, idempotence, crash resume.

STAC, PostGIS and raster I/O are replaced by an in-memory ``FakeOps`` (same method surface as ``planet.refresh.Ops``).
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from planet import refresh as rf
from planet.features import seasons as se
from planet.stac.search import SceneRecord

UTC = dt.UTC


def rec(day: dt.date, n: int = 0, cloud: float = 10.0, tile: str = "43PHK", epsg: int = 32643) -> SceneRecord:
    sid = f"S2B_{tile}_{day:%Y%m%d}_{n}_L2A"
    return SceneRecord(id=sid, datetime=dt.datetime.combine(day, dt.time(5, 26), UTC), tile=tile, cloud=cloud,
                       hrefs={"scl": f"https://x/{sid}/SCL.tif"}, epsg=epsg)


class FakeOps:
    """In-memory stand-in: scenes table, parcel_obs scene set, control_obs scene set, per-stage call log."""

    def __init__(self, known: list[SceneRecord], stac: list[SceneRecord], valid: dict[str, float] | None = None,
                 fail_at: str | None = None):
        self.scenes = {r.id: r for r in known}
        self.valid = {r.id: 0.9 for r in known} | (valid or {})
        self.obs = set(self.scenes)
        self.ctrl = set(self.scenes)
        self.stac = stac
        self.fail_at = fail_at
        self.calls: list[str] = []
        self.state_rows: dict[str, str] = {}

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            self.fail_at = None  # fail once (simulated crash), succeed on the rerun
            raise RuntimeError(f"simulated crash in {name}")

    def latest_scene_date(self):
        return max(r.date for r in self.scenes.values()) if self.scenes else None

    def known(self, since):
        return set(self.scenes), {(r.date.isoformat(), r.tile) for r in self.scenes.values()}

    def search(self, start, end):
        self.calls.append("search")
        return [r for r in self.stac if start <= r.date]  # (no future items exist in real STAC; ignore end)

    def usability(self, recs):
        self._maybe_fail("usability")
        return {r.id: {"aoi_valid_frac": self.valid.get(r.id, 0.9)} for r in recs}, None

    def upsert_scenes(self, recs, usable, mat):
        for r in recs:
            if r.id in usable:
                self.scenes[r.id] = r
                self.valid[r.id] = usable[r.id]["aoi_valid_frac"]
        return sum(r.id in usable for r in recs)

    def _usable(self):
        return [r for r in sorted(self.scenes.values(), key=lambda x: x.datetime) if self.valid[r.id] >= 0.2]

    def pending_extract(self):
        return [r for r in self._usable() if r.id not in self.obs]

    def pending_controls(self):
        return [r for r in self._usable() if r.id not in self.ctrl]

    def extract(self, recs):
        self._maybe_fail("extract")
        self.obs.update(r.id for r in recs)
        return {"ok": [r.id for r in recs], "failed": {}, "rows": 3 * len(recs),
                "dates": {r.id: r.date.isoformat() for r in recs}}

    def extract_controls(self, recs):
        self.ctrl.update(r.id for r in recs)
        return {"ok": [r.id for r in recs], "failed": {}, "rows": 2 * len(recs)}

    def features(self, affected_from, scene_ids):
        self._maybe_fail("features")
        keys = [rf.row_key(u, 2026, "kharif") for u in ("V|1", "V|2")] + [rf.row_key("V|1", 2026, "annual")]
        return {"touched": ["V|1", "V|2", "V|3"], "written_keys": keys, "rows_in_scope": 9, "outside_changed": 0,
                "lambda": 1000.0, "archive": ["2019-01-04", affected_from.isoformat()]}

    def predict_states(self, parcels, affected_from):
        self._maybe_fail("predict")
        rows = pd.DataFrame({"parcel_uid": ["V|1", "V|3"], "ag_year": [2026, 2026], "season": ["kharif", "kharif"],
                             "state": ["cropped", "bare_fallow"]})
        ch = rows[[self.state_rows.get(rf.row_key(u, y, s)) != st
                   for u, y, s, st in zip(rows.parcel_uid, rows.ag_year, rows.season, rows.state, strict=True)]]
        return {"rows": rows, "changed": ch, "years": [2026], "model": "landuse_lgbm_v1"}

    def write_states(self, rows, changed):
        self._maybe_fail("write_states")
        for r in changed.itertuples():
            self.state_rows[rf.row_key(r.parcel_uid, r.ag_year, r.season)] = r.state
        return len(changed)

    def events(self, parcels):
        self.calls.append("events")
        return 4 * len(parcels)

    def controls_did(self):
        self.calls.append("did")
        return {"did_rows": 18}


def run_cli(ops, tmp_path, capfd, *args):
    rc = rf.main(["--json", "--state-file", str(tmp_path / "state.json"), *args], ops=ops)
    out = capfd.readouterr().out.strip().splitlines()
    return rc, out


def parse(out):
    stages = [ln.split(" ", 3) for ln in out if ln.startswith("STAGE ")]
    res = [ln for ln in out if ln.startswith("RESULT ")]
    assert len(res) == 1 and out[-1].startswith("RESULT "), out
    return stages, json.loads(res[0][len("RESULT "):])


D0 = dt.date(2026, 9, 24)
KNOWN = [rec(dt.date(2026, 9, 19)), rec(D0, cloud=99.0)]


# ------------------------------------------------------------------------------------------ pure helpers
def test_select_new_skips_known_reprocessed_and_other_crs():
    stac = [rec(dt.date(2026, 9, 19)), rec(dt.date(2026, 9, 19), n=1), rec(dt.date(2026, 9, 29)),
            rec(dt.date(2026, 9, 27)), rec(dt.date(2026, 9, 28), tile="44PKQ", epsg=32644)]
    known = {r.id for r in KNOWN}
    keys = {(r.date.isoformat(), r.tile) for r in KNOWN}
    new, repro, other = rf.select_new(stac, known, keys, 32643)
    assert [r.date for r in new] == [dt.date(2026, 9, 27), dt.date(2026, 9, 29)]  # oldest first
    assert repro == ["S2B_43PHK_20260919_1_L2A"]
    assert other == ["S2B_44PKQ_20260928_0_L2A"]


def test_scope_uses_window_end_and_halo():
    cfg = se.load_config()
    df = pd.DataFrame({"ag_year": [2025, 2025, 2025, 2026, 2026], "season": ["kharif", "rabi", "summer", "kharif", "annual"]})
    m = rf.in_scope(df, dt.date(2026, 9, 29), cfg)  # cut = 2026-07-31
    assert m.tolist() == [False, False, False, True, True]
    m2 = rf.in_scope(df, dt.date(2026, 6, 20), cfg)  # cut = 2026-04-21: summer 2025 (ends 2026-05-31) in scope
    assert m2.tolist() == [False, False, True, True, True]
    assert rf.affected_years(zip(df.ag_year, df.season, strict=True), dt.date(2026, 6, 20), cfg) == {2025, 2026}


def test_payload_and_state_change_detection():
    old = {"ndvi_max": 0.61234, "n_obs": 3, "caveats": ["a", "b"], "gap_flag": False, "_meta": {"x": 1}}
    assert not rf.payload_changed({"ndvi_max": 0.61236, "n_obs": 3, "caveats": ["a", "b"], "gap_flag": False}, old)
    assert rf.payload_changed({"ndvi_max": 0.7, "n_obs": 3, "caveats": ["a", "b"], "gap_flag": False}, old)
    assert rf.payload_changed({"ndvi_max": 0.61234, "n_obs": 4, "caveats": ["a", "b"], "gap_flag": False}, old)
    assert rf.payload_changed({"ndvi_max": 0.61234, "n_obs": 3, "caveats": ["a"], "gap_flag": False}, old)
    assert rf.payload_changed({"ndvi_max": None, "n_obs": 3, "caveats": ["a", "b"], "gap_flag": False}, old)
    assert rf.payload_changed({"x": 1}, None)
    s = {"state": "cropped", "p_state": 0.8123, "route": "student", "needs_field": False}
    assert not rf.state_changed({**s, "p_state": 0.8126}, s)
    assert rf.state_changed({**s, "p_state": 0.83}, s)
    assert rf.state_changed({**s, "route": "rule"}, s)
    assert not rf.state_changed({**s, "state": "insufficient_data", "p_state": float("nan")},
                                {**s, "state": "insufficient_data", "p_state": None})


def test_merge_snapshot_replaces_by_key_or_mask():
    old = pd.DataFrame({"k": ["a", "b", "c"], "v": [1, 2, 3]})
    new = pd.DataFrame({"k": ["b", "d"], "v": [20, 40]})
    m = rf.merge_snapshot(old, new, ["k"])
    assert sorted(zip(m.k, m.v)) == [("a", 1), ("b", 20), ("c", 3), ("d", 40)]
    m2 = rf.merge_snapshot(old, new, ["k"], drop_mask=old.k.isin(["a", "b"]))
    assert sorted(m2.k) == ["b", "c", "d"]


# ------------------------------------------------------------------------------------------ CLI protocol
def test_no_new_scenes_exit0_and_later_stages_skipped(tmp_path, capfd):
    ops = FakeOps(KNOWN, stac=list(KNOWN))
    rc, out = run_cli(ops, tmp_path, capfd)
    stages, res = parse(out)
    assert rc == 0
    assert [s[1:3] for s in stages] == [["inventory", "running"], ["inventory", "done"], ["extract", "skipped"],
                                        ["features", "skipped"], ["classify", "skipped"]]
    assert all(s[3] == "no new scenes" for s in stages[2:])
    assert res == {"new_scenes": 0, "latest_scene_date": "2026-09-24", "previous_latest_scene_date": "2026-09-24",
                   "parcels_updated": 0, "seasons_updated": 0, "dry_run": False}
    assert "extract" not in ops.calls


def test_dry_run_writes_nothing(tmp_path, capfd):
    new = [rec(dt.date(2026, 9, 26)), rec(dt.date(2026, 9, 29))]
    ops = FakeOps(KNOWN, stac=KNOWN + new)
    rc, out = run_cli(ops, tmp_path, capfd, "--dry-run", "--max-scenes", "1")
    stages, res = parse(out)
    assert rc == 0 and res["dry_run"] is True
    assert res["new_scenes"] == 2 and res["latest_scene_date"] == "2026-09-29"
    assert res["previous_latest_scene_date"] == "2026-09-24"
    assert [s[2] for s in stages[2:]] == ["skipped"] * 3
    assert "usability" not in ops.calls and len(ops.scenes) == 2
    assert not (tmp_path / "state.json").exists()


def test_full_run_then_rerun_is_noop(tmp_path, capfd):
    new = [rec(dt.date(2026, 9, 26)), rec(dt.date(2026, 9, 29)), rec(dt.date(2026, 10, 1))]
    ops = FakeOps(KNOWN, stac=KNOWN + new)
    rc, out = run_cli(ops, tmp_path, capfd, "--max-scenes", "2")
    stages, res = parse(out)
    assert rc == 0
    assert [(s[1], s[2]) for s in stages if s[2] != "running"] == [
        ("inventory", "done"), ("extract", "done"), ("features", "done"), ("classify", "done")]
    assert "capped at --max-scenes 2 of 3" in stages[1][3]
    assert res["new_scenes"] == 2 and res["latest_scene_date"] == "2026-09-29"
    # 3 feature keys + 2 state keys (V|1 kharif overlaps) -> 4 rows over V|1, V|2, V|3
    assert res["seasons_updated"] == 4 and res["parcels_updated"] == 3
    assert "did" in ops.calls and "events" in ops.calls
    assert not (tmp_path / "state.json").exists()
    hist = (tmp_path / "refresh_history.jsonl").read_text().strip().splitlines()
    assert len(hist) == 1 and json.loads(hist[0])["result"]["new_scenes"] == 2
    # second run picks up the remaining scene; third run is a no-op
    rc, out = run_cli(ops, tmp_path, capfd)
    _, res2 = parse(out)
    assert rc == 0 and res2["new_scenes"] == 1 and res2["previous_latest_scene_date"] == "2026-09-29"
    ops.calls.clear()
    rc, out = run_cli(ops, tmp_path, capfd)
    stages, res3 = parse(out)
    assert rc == 0 and res3["new_scenes"] == 0 and res3["latest_scene_date"] == "2026-10-01"
    assert [s[3] for s in stages[2:]] == ["no new scenes"] * 3
    assert ops.calls == ["search"]


def test_unusable_new_scenes_skip_extraction(tmp_path, capfd):
    new = rec(dt.date(2026, 9, 29), cloud=100.0)
    ops = FakeOps(KNOWN, stac=[*KNOWN, new], valid={new.id: 0.0})
    rc, out = run_cli(ops, tmp_path, capfd)
    stages, res = parse(out)
    assert rc == 0 and res["new_scenes"] == 1 and res["latest_scene_date"] == "2026-09-29"
    assert res["seasons_updated"] == 0
    assert [s[2] for s in stages[2:]] == ["skipped"] * 3 and "no new usable scenes" in stages[2][3]
    rc, out = run_cli(ops, tmp_path, capfd)
    assert parse(out)[1]["new_scenes"] == 0


@pytest.mark.parametrize("crash", ["usability", "extract", "features", "predict", "write_states"])
def test_crash_then_resume_completes_without_duplicates(tmp_path, capfd, crash):
    new = [rec(dt.date(2026, 9, 26)), rec(dt.date(2026, 9, 29))]
    ops = FakeOps(KNOWN, stac=KNOWN + new, fail_at=crash)
    rc, out = run_cli(ops, tmp_path, capfd)
    failed = [ln for ln in out if ln.startswith("STAGE ") and " failed " in ln]
    assert rc == 1 and len(failed) == 1 and "simulated crash" in failed[0]
    assert not any(ln.startswith("RESULT ") for ln in out)
    assert (tmp_path / "state.json").exists()
    rc, out = run_cli(ops, tmp_path, capfd)
    _, res = parse(out)
    assert rc == 0, out
    assert res["previous_latest_scene_date"] == "2026-09-24"  # from the journal, not the half-advanced DB
    assert res["new_scenes"] == 2 and res["latest_scene_date"] == "2026-09-29"
    assert res["seasons_updated"] >= 3
    assert ops.obs >= {r.id for r in new} and len(ops.scenes) == 4
    assert ops.calls.count("extract") == (2 if crash == "extract" else 1)
    rc, out = run_cli(ops, tmp_path, capfd)
    assert parse(out)[1]["new_scenes"] == 0


def test_stage_failure_line_and_exit_code(tmp_path, capfd):
    class Boom(FakeOps):
        def search(self, start, end):
            raise ConnectionError("earth-search unreachable")

    rc, out = run_cli(Boom(KNOWN, stac=[]), tmp_path, capfd)
    assert rc == 1
    assert out[-1].startswith("STAGE inventory failed ConnectionError: earth-search unreachable")


def test_max_scenes_validation(tmp_path):
    with pytest.raises(SystemExit):
        rf.main(["--max-scenes", "0", "--state-file", str(tmp_path / "s.json")], ops=FakeOps(KNOWN, []))
