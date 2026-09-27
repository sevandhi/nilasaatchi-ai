"""LightGBM student for parcel-season land-use states (T3.4) + batch prediction and routing.

Training data: consensus teacher labels (``parcel_season.teacher_label``) of the ``train`` label set only.
The 50-item ``audit`` set is never used for training, calibration or any choice of hyper-parameters
(fixed below, not tuned). ``insufficient_data`` is rule-gated (obs rule) and never learnt.

Validation: leave-one-village-out (LOVO) CV. Reported: macro-F1 (over classes present in the labels),
accuracy, per-class P/R/F1, confusion matrix, all on out-of-fold predictions. Calibration: one-vs-rest
isotonic regression (pool-adjacent-violators, implemented here) fitted on the OOF probabilities, then
renormalised; its effect is measured by cross-fitting (calibrator fitted on the other villages' OOF,
applied to the held-out village) with Brier score and ECE.

Batch routing for all parcel_season rows (D-031):
- obs rule fails (``data_sufficient`` false)        -> state ``insufficient_data``, route ``rule``
- p = calibrated p(state), capped by ``features.confidence_cap`` (mixed pixels, 0.6) and at 0.6 for
  cropped / irrigated_multi in seasons with a gap > 45 d (``gap_flag``)
- p >= tau_accept (0.75)                            -> route ``student``
- else, if the item already has a teacher second opinion (Cohere, same PUBLIC panel):
    agrees -> route ``vlm_second_opinion``; disagrees -> route ``disagreement``, needs_field = true
- else                                              -> route ``pending_second_opinion`` (VLM on demand via
  ``planet.classify.landuse.landuse_state``; the batch never spends free-tier calls on 10k+ rows)
Annual rows are aggregated from their season rows (see :func:`annual_state`).

``python -m planet.classify.student [--no-db] [--limit N]``
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from planet.aoi import REPO
from planet.classify import rules
from planet.extract import parcel_stats as ps

MODEL_DIR = REPO / "data" / "models"
MODEL_VERSION = "landuse_lgbm_v1"
PRED_PARQUET = ps.CACHE_DIR / "parcel_season_states.parquet"
EVAL_DIR = REPO / "eval" / "planet"
TAU_ACCEPT = 0.75
GAP_CAP = 0.6
PARAMS = {"objective": "multiclass", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 8,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
          "verbose": -1, "seed": 7, "num_threads": 4}
NUM_ROUNDS = 200
AGREEMENT_WEIGHT = {"3of3": 1.0, "2of3": 0.7, "2of2": 0.7, "split_vlm": 0.4}
SEASON_CODE = {"kharif": 0, "rabi": 1, "summer": 2}
BASE_FEATURES = ["ndvi_max", "ndvi_min", "amplitude", "ndvi_mean", "dry_mean", "bsi_dry_mean", "ndwi_max",
                 "peak_doy", "integral", "peaks_count", "n_obs", "max_gap_days", "yoy_delta", "yoy_delta_integral",
                 "nbr_amp_med_300m", "low_support_frac"]
ANNUAL_FEATURES = ["amplitude", "dry_mean", "peaks_count", "ndvi_min", "integral"]
WC_FEATURES = ["frac_tree", "frac_shrub", "frac_grass", "frac_cropland", "frac_built", "frac_water"]


# ------------------------------------------------------------------------------------------------ features
def feature_frame(feats: pd.DataFrame, wc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Season rows with the model feature columns (annual-row context + WorldCover fractions joined)."""
    ann = feats[feats.season == "annual"].set_index(["parcel_uid", "ag_year"])[ANNUAL_FEATURES].add_prefix("ann_")
    df = feats[feats.season != "annual"].join(ann, on=["parcel_uid", "ag_year"])
    df["season_code"] = df.season.map(SEASON_CODE).astype(int)
    df["amp_minus_nbr"] = df.amplitude - df.nbr_amp_med_300m
    df["mixed"] = df.mixed_pixel.astype(int)
    if wc is not None and len(wc):
        df = df.join(wc.set_index("parcel_uid")[WC_FEATURES], on="parcel_uid")
    else:
        for c in WC_FEATURES:
            df[c] = np.nan
    return df


def feature_names() -> list[str]:
    return [*BASE_FEATURES, *[f"ann_{c}" for c in ANNUAL_FEATURES], "season_code", "amp_minus_nbr", "mixed", *WC_FEATURES]


# -------------------------------------------------------------------------------------------- isotonic PAV
def pav(x: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic (non-decreasing) fit of y on x. Returns breakpoints (x_knots, y_knots) for np.interp."""
    o = np.argsort(x, kind="mergesort")
    xs, ys = np.asarray(x, float)[o], np.asarray(y, float)[o]
    ws = np.ones_like(ys) if w is None else np.asarray(w, float)[o]
    val, wt, cnt = [], [], []
    for yi, wi in zip(ys, ws, strict=True):
        val.append(yi)
        wt.append(wi)
        cnt.append(1)
        while len(val) > 1 and val[-2] > val[-1]:
            v2, w2, c2 = val.pop(), wt.pop(), cnt.pop()
            v1, w1, c1 = val.pop(), wt.pop(), cnt.pop()
            val.append((v1 * w1 + v2 * w2) / (w1 + w2))
            wt.append(w1 + w2)
            cnt.append(c1 + c2)
    fitted = np.repeat(val, cnt)
    # knots: block ends
    idx = np.cumsum(cnt) - 1
    starts = idx - np.array(cnt) + 1
    xk = np.concatenate([[xs[s], xs[e]] for s, e in zip(starts, idx, strict=True)])
    yk = np.repeat(val, 2)
    del fitted
    return xk, yk


class Calibrator:
    """One-vs-rest isotonic calibration of multiclass probabilities, then renormalisation."""

    def __init__(self, classes: Sequence[str]):
        self.classes = list(classes)
        self.maps: dict[str, tuple[list[float], list[float]]] = {}

    def fit(self, proba: np.ndarray, y: np.ndarray) -> Calibrator:
        for j, c in enumerate(self.classes):
            t = (y == c).astype(float)
            if t.sum() == 0 or t.sum() == len(t):
                continue
            xk, yk = pav(proba[:, j], t)
            self.maps[c] = (xk.tolist(), yk.tolist())
        return self

    def transform(self, proba: np.ndarray) -> np.ndarray:
        out = proba.copy()
        for j, c in enumerate(self.classes):
            if c in self.maps:
                xk, yk = self.maps[c]
                out[:, j] = np.interp(proba[:, j], xk, yk)
        out = np.clip(out, 1e-6, None)
        return out / out.sum(axis=1, keepdims=True)

    def to_json(self) -> dict[str, Any]:
        return {"classes": self.classes, "maps": self.maps}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Calibrator:
        c = cls(d["classes"])
        c.maps = {k: (v[0], v[1]) for k, v in d["maps"].items()}
        return c


# ------------------------------------------------------------------------------------------------- metrics
def confusion(y: Sequence[str], p: Sequence[str], classes: Sequence[str]) -> pd.DataFrame:
    m = pd.DataFrame(0, index=pd.Index(classes, name="true"), columns=pd.Index(classes, name="pred"))
    for a, b in zip(y, p, strict=True):
        if a in m.index and b in m.columns:
            m.loc[a, b] += 1
    return m


def prf(cm: pd.DataFrame) -> pd.DataFrame:
    tp = np.diag(cm.to_numpy()).astype(float)
    prec = np.divide(tp, cm.sum(axis=0).to_numpy(), out=np.zeros_like(tp), where=cm.sum(axis=0).to_numpy() > 0)
    rec = np.divide(tp, cm.sum(axis=1).to_numpy(), out=np.zeros_like(tp), where=cm.sum(axis=1).to_numpy() > 0)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros_like(tp), where=(prec + rec) > 0)
    return pd.DataFrame({"precision": prec, "recall": rec, "f1": f1, "support": cm.sum(axis=1).to_numpy()},
                        index=cm.index)


def macro_f1(y: Sequence[str], p: Sequence[str], classes: Sequence[str]) -> float:
    t = prf(confusion(y, p, classes))
    t = t[t.support > 0]
    return float(t.f1.mean()) if len(t) else float("nan")


def brier(proba: np.ndarray, y: np.ndarray, classes: Sequence[str]) -> float:
    onehot = (np.asarray(y)[:, None] == np.asarray(classes)[None, :]).astype(float)
    return float(((proba - onehot) ** 2).sum(axis=1).mean())


def ece(proba: np.ndarray, y: np.ndarray, classes: Sequence[str], bins: int = 10) -> float:
    conf = proba.max(axis=1)
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    acc = (pred == np.asarray(y)).astype(float)
    e, edges = 0.0, np.linspace(0, 1, bins + 1)
    for lo, hi in itertools.pairwise(edges):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(acc[m].mean() - conf[m].mean())
    return float(e)


# ------------------------------------------------------------------------------------------------ training
def _train(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, classes: Sequence[str]) -> Any:
    import lightgbm as lgb

    yi = np.array([list(classes).index(v) for v in y])
    # balanced class weights x consensus weights
    cw = {c: len(y) / (len(classes) * max((y == c).sum(), 1)) for c in classes}
    ww = w * np.array([cw[v] for v in y])
    ds = lgb.Dataset(X, label=yi, weight=ww, free_raw_data=False)
    return lgb.train({**PARAMS, "num_class": len(classes)}, ds, num_boost_round=NUM_ROUNDS)


def lovo_cv(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, groups: np.ndarray, classes: Sequence[str]
            ) -> tuple[np.ndarray, pd.DataFrame]:
    oof = np.zeros((len(y), len(classes)))
    folds = []
    for g in sorted(set(groups)):
        te = groups == g
        tr = ~te
        present = [c for c in classes if (y[tr] == c).any()]
        mdl = _train(X[tr], y[tr], w[tr], present)
        pr = mdl.predict(X[te])
        full = np.zeros((te.sum(), len(classes)))
        for j, c in enumerate(present):
            full[:, list(classes).index(c)] = pr[:, j]
        oof[te] = full
        pred = np.asarray(classes)[full.argmax(axis=1)]
        folds.append({"village": g, "n": int(te.sum()), "acc": float((pred == y[te]).mean()),
                      "macro_f1": macro_f1(y[te], pred, classes)})
    return oof, pd.DataFrame(folds)


def crossfit_calibration(oof: np.ndarray, y: np.ndarray, groups: np.ndarray, classes: Sequence[str]) -> np.ndarray:
    out = np.zeros_like(oof)
    for g in sorted(set(groups)):
        te = groups == g
        cal = Calibrator(classes).fit(oof[~te], y[~te])
        out[te] = cal.transform(oof[te])
    return out


def load_training(conn: Any | None = None) -> pd.DataFrame:
    from planet.classify.teacher import LABEL_DIR

    lab = pd.read_csv(LABEL_DIR / "consensus.csv")
    return lab


def train_and_eval(feats: pd.DataFrame, wc: pd.DataFrame | None, labels: pd.DataFrame) -> dict[str, Any]:
    ff = feature_frame(feats, wc)
    tr = labels[(labels.label_set == "train") & labels.final.notna() & (labels.final != "insufficient_data")]
    tr = tr[["item_id", "parcel_uid", "village", "ag_year", "season", "final", "agreement", "prior_state"]]
    tr = tr.merge(ff, on=["parcel_uid", "ag_year", "season"], how="inner")
    classes = [c for c in rules.MODEL_STATES if (tr.final == c).any()]
    X = tr[feature_names()].astype(float)
    y = tr.final.to_numpy()
    w = tr.agreement.map(AGREEMENT_WEIGHT).fillna(0.5).to_numpy()
    groups = tr.village.to_numpy()
    oof, folds = lovo_cv(X, y, w, groups, classes)
    pred = np.asarray(classes)[oof.argmax(axis=1)]
    cm = confusion(y, pred, classes)
    cal_oof = crossfit_calibration(oof, y, groups, classes)
    pred_cal = np.asarray(classes)[cal_oof.argmax(axis=1)]
    # prior-only baseline on the same rows (what the rules alone would score vs the consensus)
    base = tr.prior_state.to_numpy()
    metrics = {
        "model_version": MODEL_VERSION, "n_train": len(tr), "classes": classes,
        "class_counts": tr.final.value_counts().to_dict(),
        "lovo_macro_f1": macro_f1(y, pred, classes), "lovo_accuracy": float((pred == y).mean()),
        "lovo_macro_f1_calibrated_argmax": macro_f1(y, pred_cal, classes),
        "prior_rules_macro_f1_same_rows": macro_f1(y, base, classes),
        "prior_rules_accuracy_same_rows": float((base == y).mean()),
        "brier_raw": brier(oof, y, classes), "brier_calibrated_crossfit": brier(cal_oof, y, classes),
        "ece_raw": ece(oof, y, classes), "ece_calibrated_crossfit": ece(cal_oof, y, classes),
        "accept_rate_at_tau": float((cal_oof.max(axis=1) >= TAU_ACCEPT).mean()),
        "accuracy_when_accepted": float((pred_cal == y)[cal_oof.max(axis=1) >= TAU_ACCEPT].mean())
        if (cal_oof.max(axis=1) >= TAU_ACCEPT).any() else None,
        "per_class": prf(cm).round(3).reset_index().rename(columns={"true": "class"}).to_dict("records"),
        "confusion": {"labels": classes, "matrix": cm.to_numpy().tolist()},
        "folds": folds.round(3).to_dict("records"),
        "params": PARAMS, "num_rounds": NUM_ROUNDS, "features": feature_names(), "tau_accept": TAU_ACCEPT,
        "note": "OOF = leave-one-village-out; audit set excluded; hyper-parameters fixed a priori (not tuned)",
    }
    final = _train(X, y, w, classes)
    cal = Calibrator(classes).fit(oof, y)
    imp = dict(zip(feature_names(), final.feature_importance("gain").round(1).tolist(), strict=True))
    metrics["feature_importance_gain"] = dict(sorted(imp.items(), key=lambda kv: -kv[1])[:15])
    return {"model": final, "calibrator": cal, "classes": classes, "metrics": metrics, "cm": cm,
            "oof": pd.DataFrame({"item_id": tr.item_id, "true": y, "pred": pred, "pred_cal": pred_cal,
                                 "p_cal": cal_oof.max(axis=1), "village": groups})}


def save_model(res: dict[str, Any]) -> dict[str, Path]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    mp = MODEL_DIR / f"{MODEL_VERSION}.txt"
    res["model"].save_model(str(mp))
    cp = MODEL_DIR / f"{MODEL_VERSION}.calibration.json"
    cp.write_text(json.dumps(res["calibrator"].to_json()))
    mt = MODEL_DIR / f"{MODEL_VERSION}.metrics.json"
    mt.write_text(json.dumps(res["metrics"], indent=1, default=str))
    return {"model": mp, "calibration": cp, "metrics": mt}


def load_model(version: str = MODEL_VERSION) -> tuple[Any, Calibrator]:
    import lightgbm as lgb

    mdl = lgb.Booster(model_file=str(MODEL_DIR / f"{version}.txt"))
    cal = Calibrator.from_json(json.loads((MODEL_DIR / f"{version}.calibration.json").read_text()))
    return mdl, cal


# ------------------------------------------------------------------------------------------ predict/route
def predict_frame(ff: pd.DataFrame, mdl: Any, cal: Calibrator) -> pd.DataFrame:
    proba = cal.transform(mdl.predict(ff[feature_names()].astype(float)))
    classes = np.asarray(cal.classes)
    out = ff[["parcel_uid", "ag_year", "season", "data_sufficient", "gap_flag", "confidence_cap", "mixed_pixel",
              "caveats"]].copy()
    out["state"] = classes[proba.argmax(axis=1)]
    out["p_raw"] = proba.max(axis=1)
    out["probs"] = [dict(zip(classes, np.round(r, 4).tolist(), strict=True)) for r in proba]
    return out


def route(pred: pd.DataFrame, second: dict[tuple[str, int, str], dict[str, Any]] | None = None,
          tau: float = TAU_ACCEPT) -> pd.DataFrame:
    """Apply caps, the obs rule and the routing policy (module docstring). ``second`` maps
    (parcel_uid, ag_year, season) -> {state, model_id, confidence} from a stored VLM second opinion."""
    second = second or {}
    df = pred.copy()
    cap = df.confidence_cap.astype(float).fillna(1.0).clip(upper=1.0)
    gcap = np.where(df.gap_flag.astype(bool) & df.state.isin(["cropped", "irrigated_multi"]), GAP_CAP, 1.0)
    df["p_state"] = np.minimum(df.p_raw, np.minimum(cap, gcap))
    df["cap_reason"] = [
        ",".join(x for x in (("mixed_pixel_cap" if c < 1.0 else ""), ("gap_gt_45d_crop_cap" if g < 1.0 else "")) if x)
        for c, g in zip(cap, gcap, strict=True)]
    routes, needs, so = [], [], []
    for r in df.itertuples(index=False):
        if not bool(r.data_sufficient):
            routes.append("rule")
            needs.append(False)
            so.append(None)
            continue
        if r.p_state >= tau:
            routes.append("student")
            needs.append(False)
            so.append(None)
            continue
        s = second.get((r.parcel_uid, int(r.ag_year), r.season))
        if s is None:
            routes.append("pending_second_opinion")
            needs.append(False)
            so.append(None)
        elif s["state"] == r.state:
            routes.append("vlm_second_opinion")
            needs.append(False)
            so.append(s)
        else:
            routes.append("disagreement")
            needs.append(True)
            so.append(s)
    df["route"] = routes
    df["needs_field"] = needs
    df["second_opinion"] = so
    df.loc[~df.data_sufficient.astype(bool), ["state", "p_state"]] = ["insufficient_data", np.nan]
    return df


SEASONS = ("kharif", "rabi", "summer")


def annual_state(season_rows: pd.DataFrame) -> pd.DataFrame:
    """Annual row per (parcel, ag_year) from its season rows:
    irrigated_multi if >= 2 cropped/irrigated seasons (or any irrigated_multi); cropped if one; else the
    most frequent sufficient state; insufficient_data if no season is sufficient. p = min over the
    seasons that decide it; route/needs_field propagate (any disagreement -> needs_field)."""
    out = []
    for (uid, y), g in season_rows.groupby(["parcel_uid", "ag_year"], sort=False):
        ok = g[g.state != "insufficient_data"]
        crop = ok[ok.state.isin(["cropped", "irrigated_multi"])]
        if ok.empty:
            st, p, dec = "insufficient_data", np.nan, g
        elif len(crop) >= 2 or (crop.state == "irrigated_multi").any():
            st, dec = "irrigated_multi", crop
            p = float(crop.p_state.min())
        elif len(crop) == 1:
            st, dec = "cropped", crop
            p = float(crop.p_state.min())
        else:
            vc = ok.state.value_counts()
            st = str(vc.index[0])
            dec = ok[ok.state == st]
            p = float(dec.p_state.min())
        rts = set(g.route)
        rt = ("disagreement" if "disagreement" in rts else "pending_second_opinion" if "pending_second_opinion" in rts
              else "rule" if st == "insufficient_data" else "aggregate")
        out.append({"parcel_uid": uid, "ag_year": int(y), "season": "annual", "state": st, "p_state": p,
                    "route": rt, "needs_field": bool(g.needs_field.any()),
                    "cap_reason": "", "probs": None, "p_raw": np.nan,
                    "decided_by": sorted(dec.season.tolist())})
    return pd.DataFrame(out)


SO_PREFERENCE = {"second_opinion": 0, "cohere": 1, "gemini": 2}


def second_opinions(conn: Any) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Stored VLM opinions per parcel-season: a runtime ``second_opinion`` first, then the Cohere teacher,
    then the Gemini teacher (the skill's "Gemini visual second opinion"). Only ok (valid) rows count."""
    rows = conn.execute("SELECT parcel_uid, ag_year, season, state, model_id, confidence, teacher FROM planet_teacher_label "
                        "WHERE ok AND teacher IN ('second_opinion', 'cohere', 'gemini')").fetchall()
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    for uid, y, s, st_, mid, conf, t in sorted(rows, key=lambda r: -SO_PREFERENCE[r[6]]):
        out[(uid, int(y), s)] = {"state": st_, "model_id": mid, "confidence": conf, "teacher": t}
    return out


def _clean(v: Any) -> Any:
    """JSON-safe: NaN/NaT -> None, numpy scalars -> python, recursively."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if v is pd.NaT:
        return None
    return v


def write_states(conn: Any, df: pd.DataFrame) -> int:
    from psycopg.types.json import Jsonb

    sql = ("UPDATE parcel_season SET state = %s, p_state = %s, route = %s, needs_field = %s, model_version = %s, "
           "classify_meta = %s, classified_at = now() WHERE parcel_uid = %s AND ag_year = %s AND season = %s")
    n = 0
    with conn.cursor() as cur:
        batch = []
        for r in df.to_dict("records"):
            meta = {"probs": r.get("probs"), "p_raw": None if pd.isna(r.get("p_raw")) else float(r["p_raw"]),
                    "cap_reason": r.get("cap_reason") or None, "second_opinion": r.get("second_opinion"),
                    "decided_by": r.get("decided_by"), "tau_accept": TAU_ACCEPT,
                    "caveats": ["signal needing field verification", "clouds/haze may remain after SCL masking",
                                "mixed pixels at parcel edges", "NE-monsoon weed flush can mimic a rainfed crop"]}
            p = r.get("p_state")
            batch.append((r["state"], None if p is None or pd.isna(p) else float(p), r["route"], bool(r["needs_field"]),
                          MODEL_VERSION, Jsonb(_clean(meta)), r["parcel_uid"],
                          int(r["ag_year"]), r["season"]))
            if len(batch) >= 2000:
                cur.executemany(sql, batch)
                n += len(batch)
                batch = []
        if batch:
            cur.executemany(sql, batch)
            n += len(batch)
    conn.commit()
    return n


def eval_report(res: dict[str, Any]) -> list[str]:
    m = res["metrics"]
    lines = [f"student {m['model_version']}: n_train {m['n_train']} classes {m['class_counts']}",
             (f"LOVO macro-F1 {m['lovo_macro_f1']:.3f}  accuracy {m['lovo_accuracy']:.3f}  "
             f"(rules prior on same rows: macro-F1 {m['prior_rules_macro_f1_same_rows']:.3f}, acc {m['prior_rules_accuracy_same_rows']:.3f})"),
             (f"calibration (cross-fit): Brier {m['brier_raw']:.3f} -> {m['brier_calibrated_crossfit']:.3f}; "
             f"ECE {m['ece_raw']:.3f} -> {m['ece_calibrated_crossfit']:.3f}; accept rate @ {TAU_ACCEPT} "
             f"{m['accept_rate_at_tau']:.2f}, accuracy when accepted {m['accuracy_when_accepted']}"),
             "confusion (rows = consensus label, cols = OOF prediction):", res["cm"].to_string(),
             pd.DataFrame(m["per_class"]).to_string(index=False), pd.DataFrame(m["folds"]).to_string(index=False)]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.classify.student", description=__doc__.split("\n")[0])
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="predict only the first N parcels (smoke test)")
    args = ap.parse_args(argv)
    import psycopg

    from planet.extract.run import dsn
    from planet.extract.worldcover import OUT_PATH as WC_PATH
    from planet.features.build import FEATURES_PARQUET

    t0 = time.time()
    feats = pd.read_parquet(FEATURES_PARQUET)
    wc = pd.read_parquet(WC_PATH) if WC_PATH.exists() else None
    labels = load_training()
    res = train_and_eval(feats, wc, labels)
    paths = save_model(res)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "student_metrics.json").write_text(json.dumps(res["metrics"], indent=1, default=str))
    res["cm"].to_csv(EVAL_DIR / "student_confusion.csv")
    res["oof"].to_csv(EVAL_DIR / "student_oof.csv", index=False)
    print("\n".join(eval_report(res)))
    print("saved", {k: str(v) for k, v in paths.items()})

    ff = feature_frame(feats, wc)
    if args.limit:
        keep = ff.parcel_uid.drop_duplicates().head(args.limit)
        ff = ff[ff.parcel_uid.isin(keep)]
    pred = predict_frame(ff, res["model"], res["calibrator"])
    second: dict = {}
    if not args.no_db:
        with psycopg.connect(dsn()) as conn:
            second = second_opinions(conn)
    seasons = route(pred, second)
    annual = annual_state(seasons)
    allrows = pd.concat([seasons, annual], ignore_index=True)
    allrows.drop(columns=["probs", "second_opinion", "decided_by"], errors="ignore").to_parquet(PRED_PARQUET, index=False)
    print(f"predicted {len(allrows)} rows ({len(seasons)} season + {len(annual)} annual)")
    print(allrows.groupby("season").state.value_counts().unstack(fill_value=0).to_string())
    print(allrows.groupby("season").route.value_counts().unstack(fill_value=0).to_string())
    print("needs_field:", int(allrows.needs_field.sum()))
    if not args.no_db:
        with psycopg.connect(dsn()) as conn:
            n = write_states(conn, allrows)
        print(f"parcel_season updated {n}")
    print(f"runtime {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
