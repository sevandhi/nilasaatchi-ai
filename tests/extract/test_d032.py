"""D-032: second-read routing/crop, acceptance rule, owner read status and sampling, --set heldout."""
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pipeline.extract import vlm as vlm_mod
from pipeline.extract.cache import ContentCache
from pipeline.extract.owner_reads import annotate_owner_reads, in_owner_sample, owners_agree
from pipeline.extract.page import improves_consistency


def _res(sc, results):
    return {"self_consistency": sc, "checks": [{"name": f"c{i}", "result": r} for i, r in enumerate(results)]}


@pytest.mark.parametrize("first,second,expected", [
    (_res("fail", ["fail", "pass"]), _res("pass", ["pass", "pass"]), True),
    (_res("pass", ["pass"]), _res("pass", ["pass"]), False),          # equal: keep the first
    (_res("fail", ["fail", "fail"]), _res("fail", ["fail", "pass"]), True),
    (_res("fail", ["fail", "pass"]), _res("fail", ["fail"]), False),  # fewer passes is not better
    (_res("pass", ["pass"]), _res("fail", ["fail"]), False),
    (_res("n/a", []), _res("pass", ["pass"]), True),
    (_res("fail", ["fail"]), _res("n/a", []), False),               # check-less read never wins
])
def test_improves_consistency(first, second, expected):
    assert improves_consistency(first, second) is expected


def _rec(s, sub, owner, conf=0.95):
    return {"record_type": "parcel_row", "survey_no": s, "sub_div": sub, "table_role": None, "owner_raw": owner,
            "evidence": {"confidence": conf}}


def test_owner_read_status_and_cap():
    first = [_rec("172", "1", "ராமன், த/பெ. கண்ணன்"), _rec("172", "2", "சீதா, க/பெ. ராமன்"), _rec("173", "1", None)]
    second = [_rec("172", "1", "ராமன் த/ப கண்ணன்"), _rec("172", "2", "கீதா லட்சுமி")]
    counts = annotate_owner_reads(first, second)
    assert counts == {"vlm_agreed": 1, "vlm_single": 1}
    assert first[0]["owner_read_status"] == "vlm_agreed" and first[0]["owner_confidence"] == 0.95
    assert first[1]["owner_read_status"] == "vlm_single" and first[1]["owner_confidence"] == 0.8
    assert first[1]["owner_other_read"] == "கீதா லட்சுமி"
    assert "owner_read_status" not in first[2]


def test_single_read_is_always_capped():
    recs = [_rec("1", None, "Thiru.A.Kumar, S/o.Bala", 0.99)]
    annotate_owner_reads(recs, None)
    assert recs[0]["owner_read_status"] == "vlm_single" and recs[0]["owner_confidence"] == 0.8


def test_owners_agree_threshold():
    assert owners_agree("Thiru.M. Chithiraivel, S/o.Muniasamy", "Thiru M Chithiraivel S/o Muniasamy")
    assert not owners_agree("Thiru.M. Chithiraivel", "Thiru.K. Selvaraj")


def test_owner_sample_is_deterministic_and_about_ten_percent():
    import hashlib
    shas = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(5000)]
    picked = [in_owner_sample(s) for s in shas]
    assert picked == [in_owner_sample(s) for s in shas]
    assert 0.08 < sum(picked) / len(picked) < 0.12


def _table_page(tmp_path: Path) -> Path:
    im = Image.new("RGB", (1200, 1700), "white")
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 100, 600, 1100, 1300
    for y in range(y0, y1 + 1, 100):
        d.line([(x0, y), (x1, y)], fill="black", width=3)
    for x in range(x0, x1 + 1, 250):
        d.line([(x, y0), (x, y1)], fill="black", width=3)
    p = tmp_path / "page.png"
    im.save(p)
    return p


def test_second_read_uses_task_and_zoomed_crop(tmp_path, monkeypatch):
    page = _table_page(tmp_path)
    box, method = vlm_mod.table_crop_box(page)
    assert box is not None and method.startswith("grid")
    l, t, r, b = box
    assert t > 400 and b < 1500                       # the heading area is not in the crop
    calls = []

    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None, producer_vendor=None):
        calls.append((task, payload["instruction"], images[0]))
        return types.SimpleNamespace(ok=True, data={"tables": []}, model_id="bedrock-ministral-8b", tokens_in=1,
                                     tokens_out=1, shadow_cost_usd=0.0, error=None)

    fake = types.ModuleType("app.router")
    fake.call = fake_call
    monkeypatch.setitem(sys.modules, "app.router", fake)
    out = vlm_mod.transcribe(page, "LDR", "PII", "second", cache=ContentCache(tmp_path / "c"))
    assert out["ok"] and out["crop_method"].startswith("grid")
    task, prompt, img = calls[0]
    assert task == "table_second_read_pii"
    assert "canonical order" in prompt
    import io
    w, h = Image.open(io.BytesIO(img)).size
    assert max(w, h) == 2000                         # upscaled crop = genuine zoom
    # primary stays on the whole page with the primary prompt
    vlm_mod.transcribe(page, "LDR", "PII", "primary", cache=ContentCache(tmp_path / "c"))
    assert calls[1][0] == "table_read_pii" and "canonical order" not in calls[1][1]


def test_prompts_carry_no_golden_values():
    root = Path(__file__).resolve().parents[2]
    for name in ("extract_table_page.md", "extract_table_second.md"):
        text = (root / "prompts" / name).read_text(encoding="utf-8")
        for v in ("0.95.50", "53.50", "1,35,496", "173/1", "6,7,8,10A"):
            assert v not in text, (name, v)


def test_heldout_set_missing_files_fail_cleanly(monkeypatch, tmp_path):
    from eval.extraction import run
    monkeypatch.setitem(run.SETS, "heldout", {"pages": tmp_path / "pages.csv", "labels": tmp_path / "labels.jsonl",
                                              "images": tmp_path, "out": tmp_path / "out"})
    monkeypatch.setattr(sys, "argv", ["run", "--set", "heldout", "--no-live"])
    with pytest.raises(SystemExit, match="missing"):
        run.main()


def test_heldout_set_image_column(tmp_path):
    from eval.extraction.run import image_for
    cfg = {"images": tmp_path}
    assert image_for({"page_id": "h01"}, cfg) == tmp_path / "h01.png"
    assert image_for({"page_id": "h01", "image": "/abs/x.png"}, cfg) == Path("/abs/x.png")


from pipeline.extract.page import apply_review_policy  # noqa: E402


def _page(sc, flags=()):
    return {"self_consistency": sc, "confidence": 0.9, "flags": list(flags), "checks": []}


@pytest.mark.parametrize("reasons,sc,flags,decision,status", [
    (["low_confidence:0.5", "second_read_no_improvement"], "pass", [], "kept_first", "accepted_unverified"),
    (["header_vote_conflict"], "n/a", [], None, "accepted_unverified"),
    (["self_consistency_fail", "second_read_no_improvement"], "fail", [], "kept_first", "needs_human"),
    (["handwriting"], "pass", [], None, "needs_human"),
    (["blurred_newsprint"], "pass", [], None, "needs_human"),
    (["low_confidence:0.2"], "n/a", ["prose_as_table:5"], "kept_first", "needs_human"),
    (["low_confidence:0.5", "second_read_improved_but_flagged"], "pass", [], "second_improved",
     "second_read_accepted"),
])
def test_d033_review_policy(reasons, sc, flags, decision, status):
    st, rs, res = apply_review_policy("needs_human", reasons, _page(sc, flags), decision)
    assert st == status
    if st != "needs_human":
        assert res["confidence"] == 0.7 and res["confidence_cap"] == "D-033"
