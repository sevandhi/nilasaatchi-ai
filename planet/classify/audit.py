"""Blind qa audit pack (T3.4) and its scorer.

``pack``: copies the PUBLIC panels of the 50 ``audit`` items to ``eval/planet/audit/`` as ``A01.png`` ...
with ``index.csv`` (audit id, parcel, season, panel sha256, chip scene ids/dates) and empty columns for
the qa-evaluator (``qa_state``, ``qa_confidence``, ``qa_notes``). No teacher, prior or student label is
written to the pack. State definitions: ``prompts/satellite_teacher.md`` (section "States").

``score``: once qa has filled ``index.csv``, reports teacher-vs-audit (Gemini, Cohere, prior, consensus)
and student-vs-audit agreement + macro-F1 -> ``eval/planet/audit_scores.json``. The audit items are
never used to train, calibrate or tune the student.

``python -m planet.classify.audit [pack|score]``
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence

import pandas as pd

from planet.aoi import REPO
from planet.classify import rules
from planet.classify import teacher as te

AUDIT_DIR = REPO / "eval" / "planet" / "audit"
INDEX = AUDIT_DIR / "index.csv"
QA_COLS = ["qa_state", "qa_confidence", "qa_notes"]


def pack() -> pd.DataFrame:
    from planet.classify import panel as pn

    items = te.load_items()
    aud = items[items.label_set == "audit"].sort_values("item_id").reset_index(drop=True)
    ctx = pn.Context.load()
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    prev = pd.read_csv(INDEX) if INDEX.exists() else None
    rows = []
    for i, it in enumerate(aud.to_dict("records"), 1):
        aid = f"A{i:02d}"
        src = pn.PANEL_DIR / "items" / f"{pn.cr.safe_name(it['item_id'])}.png"
        pm = pn.render_panel(ctx, it["parcel_uid"], int(it["ag_year"]), it["season"], src)
        shutil.copyfile(pm["path"], AUDIT_DIR / f"{aid}.png")
        rows.append({"audit_id": aid, "parcel_uid": it["parcel_uid"], "ag_year": int(it["ag_year"]),
                     "season": it["season"], "panel": f"{aid}.png", "panel_sha256": pm["sha256"],
                     "chip_dates": ";".join(f"{k}={v['date']}" for k, v in pm["chips"].items()),
                     "scene_ids": ";".join(pm["scene_ids"]), "qa_state": "", "qa_confidence": "", "qa_notes": ""})
    df = pd.DataFrame(rows)
    if prev is not None and set(QA_COLS) <= set(prev.columns):  # never overwrite qa's answers
        keep = prev.set_index("audit_id")[QA_COLS]
        df = df.set_index("audit_id")
        df.update(keep.where(keep.notna() & (keep.astype(str) != "")))
        df = df.reset_index()
    df.to_csv(INDEX, index=False)
    (AUDIT_DIR / "instructions.txt").write_text(
        "Blind audit of 50 parcel-seasons (qa-evaluator). Label each panel independently from the image only.\n"
        "Allowed qa_state values: " + ", ".join(rules.STATES) + ".\n"
        "State definitions and the panel layout: prompts/satellite_teacher.md (sections 'The image' and 'States').\n"
        "Fill qa_state, qa_confidence (0-1) and qa_notes in index.csv; do not open data/s2/labels/ or the DB labels\n"
        "before finishing. Then run: python -m planet.classify.audit score\n")
    return df


def score() -> dict:
    from planet.classify import student as st

    idx = pd.read_csv(INDEX)
    idx = idx[idx.qa_state.notna() & (idx.qa_state.astype(str).str.strip() != "")]
    if idx.empty:
        raise SystemExit("index.csv has no qa_state answers yet")
    idx["item_id"] = [te.item_id(u, y, s) for u, y, s in zip(idx.parcel_uid, idx.ag_year, idx.season, strict=True)]
    cons = pd.read_csv(te.LABEL_DIR / "consensus.csv")
    pred = pd.read_parquet(st.PRED_PARQUET)
    pred["item_id"] = [te.item_id(u, y, s) for u, y, s in zip(pred.parcel_uid, pred.ag_year, pred.season, strict=True)]
    df = idx.merge(cons[["item_id", "gemini", "cohere", "prior", "final"]], on="item_id", how="left") \
            .merge(pred[["item_id", "state", "p_state", "route"]].rename(columns={"state": "student"}), on="item_id", how="left")
    classes = list(rules.STATES)
    out: dict = {"n_audited": len(df)}
    for c in ("gemini", "cohere", "prior", "final", "student"):
        m = df[c].notna()
        out[c] = {"n": int(m.sum()), "agreement": round(float((df.loc[m, c] == df.loc[m, "qa_state"]).mean()), 3) if m.any() else None,
                  "macro_f1": round(st.macro_f1(df.loc[m, "qa_state"], df.loc[m, c], classes), 3) if m.any() else None}
    acc = df[df.route == "student"]
    out["student_when_accepted"] = {"n": len(acc), "agreement": round(float((acc.student == acc.qa_state).mean()), 3) if len(acc) else None}
    out["confusion_student_vs_audit"] = st.confusion(df.qa_state, df.student.fillna("none"), classes).to_dict()
    (REPO / "eval" / "planet" / "audit_scores.json").write_text(json.dumps(out, indent=1))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.classify.audit")
    ap.add_argument("step", choices=["pack", "score"])
    a = ap.parse_args(argv)
    if a.step == "pack":
        df = pack()
        print(f"audit pack: {len(df)} panels -> {AUDIT_DIR}")
        print(df.groupby("season").size().to_dict())
    else:
        print(json.dumps(score(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
