"""T3.6 event windows: land-use state distribution per parcel around the acquisition events.

Windows (phase3 skill):
- ``pre_notification`` = [T_3(1) - 12 months, T_3(1))
- ``pending``          = [T_3(1), T_award)
- ``post_award``       = [T_award, T_possession)
- ``post_possession``  = [T_possession, today]

Event dates: per-parcel dates from P2 (``acquisition_event``) when that table exists; until then the
document-level fallbacks in ``planet/events/fallback_dates.yaml`` (parcel > village > default). Every window
records which date, its source and precision (``date_basis``).

A season row (kharif / rabi / summer) belongs to the window holding the majority of its days (seasons are
clipped to today). Distribution = counts and shares of the season states (``insufficient_data`` counted
separately and excluded from the shares), plus the mean p_state and the number of rows needing field
verification or a pending second opinion. Outputs are signals needing field verification.

``python -m planet.events [--no-db] [--limit N] [--from-parquet]``
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from planet.extract import parcel_stats as ps
from planet.features import seasons as se

FALLBACK_PATH = Path(__file__).with_name("fallback_dates.yaml")
EVENTS_PARQUET = ps.CACHE_DIR / "parcel_event_windows.parquet"
WINDOWS = ("pre_notification", "pending", "post_award", "post_possession")
CROP_STATES = ("cropped", "irrigated_multi")
CAVEATS = ("signal needing field verification", "document-level fallback dates until P2 per-parcel dates",
           "season-level states: a window edge inside a season is assigned by majority of days",
           "dominant_state 'mixed' = tie; windows mix season types, so read rabi_cropped/rabi_seasons for cultivation",
           "NE-monsoon weed flush can mimic a rainfed crop", "clouds/haze and mixed pixels")


def load_fallbacks(path: Path = FALLBACK_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _as_date(v: Any) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def event_dates(uid: str, fb: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{event: {date, precision, source, level}} with parcel > village > default precedence."""
    village = uid.split("|")[0]
    out = {}
    for ev in ("t_3_1", "t_award", "t_possession"):
        for level, src in (("parcel", fb.get("parcels", {}).get(uid, {})),
                           ("village", fb.get("villages", {}).get(village, {})),
                           ("default", fb.get("defaults", {}))):
            if ev in src:
                e = dict(src[ev])
                e["date"] = _as_date(e["date"])
                e["level"] = level
                out[ev] = e
                break
    return out


def window_bounds(ev: dict[str, dict[str, Any]], today: dt.date) -> dict[str, tuple[dt.date, dt.date]]:
    """Half-open [start, end) bounds; post_possession ends the day after ``today``."""
    t31, ta, tp = ev["t_3_1"]["date"], ev["t_award"]["date"], ev["t_possession"]["date"]
    if not t31 < ta < tp:
        raise ValueError(f"event dates out of order: {t31} {ta} {tp}")
    pre0 = (pd.Timestamp(t31) - pd.DateOffset(months=12)).date()
    return {"pre_notification": (pre0, t31), "pending": (t31, ta), "post_award": (ta, tp),
            "post_possession": (tp, today + dt.timedelta(days=1))}


def assign_window(a: dt.date, b: dt.date, bounds: dict[str, tuple[dt.date, dt.date]], today: dt.date) -> str | None:
    """Window with the majority of the season's days [a, b] (inclusive, clipped to today); None if no majority."""
    b = min(b, today)
    if b < a:
        return None
    n = (b - a).days + 1
    best, best_n = None, 0
    for w, (s, e) in bounds.items():
        lo, hi = max(a, s), min(b, e - dt.timedelta(days=1))
        k = (hi - lo).days + 1 if hi >= lo else 0
        if k > best_n:
            best, best_n = w, k
    return best if best_n * 2 > n else None


def parcel_windows(uid: str, states: pd.DataFrame, fb: dict[str, Any], today: dt.date,
                   cfg: se.SeasonConfig | None = None) -> list[dict[str, Any]]:
    cfg = cfg or se.load_config()
    ev = event_dates(uid, fb)
    bounds = window_bounds(ev, today)
    rows = {w: [] for w in WINDOWS}
    for r in states.itertuples(index=False):
        if r.season == "annual":
            continue
        a, b = se.window(int(r.ag_year), r.season, cfg)
        w = assign_window(a.date(), b.date(), bounds, today)
        if w:
            p = None if pd.isna(r.p_state) else round(float(r.p_state), 3)
            rows[w].append({"ag_year": int(r.ag_year), "season": r.season, "state": r.state, "p_state": p,
                            "route": r.route, "needs_field": bool(r.needs_field)})
    basis = json.loads(json.dumps(ev, default=str))  # dates (incl. YAML ranges) -> ISO strings
    out = []
    for w in WINDOWS:
        ss = sorted(rows[w], key=lambda x: (x["ag_year"], ["kharif", "rabi", "summer"].index(x["season"])))
        suff = [x for x in ss if x["state"] != "insufficient_data"]
        counts = pd.Series([x["state"] for x in ss], dtype=object).value_counts().to_dict()
        share = (pd.Series([x["state"] for x in suff], dtype=object).value_counts(normalize=True).round(3).to_dict()
                 if suff else {})
        ps_ = [x["p_state"] for x in suff if x["p_state"] is not None]
        crop = sum(1 for x in suff if x["state"] in CROP_STATES)
        rabi = [x for x in suff if x["season"] == "rabi"]
        rabi_crop = sum(1 for x in rabi if x["state"] in CROP_STATES)
        top = max(share.values()) if share else None
        leaders = sorted(k for k, v in share.items() if v == top) if share else []
        dominant = (leaders[0] if len(leaders) == 1 else "mixed") if leaders else None
        cav = list(CAVEATS)
        if any(v.get("precision") not in ("day",) for v in ev.values()):
            cav.append("some window edges use year/series-level fallback dates")
        if not suff:
            cav.append("no season with sufficient observations in this window")
        s0, e0 = bounds[w]
        out.append({"parcel_uid": uid, "window_name": w, "start_date": s0, "end_date": e0 - dt.timedelta(days=1),
                    "date_basis": basis, "n_seasons": len(ss), "n_sufficient": len(suff),
                    "state_counts": counts, "state_share": share,
                    "dominant_state": dominant, "crop_seasons": crop,
                    "rabi_seasons": len(rabi), "rabi_cropped": rabi_crop, "mean_p_state": round(sum(ps_) / len(ps_), 3) if ps_ else None,
                    "needs_field_count": sum(1 for x in ss if x["needs_field"]),
                    "pending_second_opinion": sum(1 for x in ss if x["route"] == "pending_second_opinion"),
                    "seasons": ss, "caveats": cav})
    return out


def compute(states: pd.DataFrame, fb: dict[str, Any] | None = None, today: dt.date | None = None) -> pd.DataFrame:
    fb = fb or load_fallbacks()
    today = today or dt.datetime.now(dt.UTC).date()
    cfg = se.load_config()
    out = []
    for uid, g in states.groupby("parcel_uid", sort=True):
        out.extend(parcel_windows(uid, g, fb, today, cfg))
    return pd.DataFrame(out)


def load_states(conn: Any | None, from_parquet: bool) -> pd.DataFrame:
    if from_parquet or conn is None:
        from planet.classify.student import PRED_PARQUET

        return pd.read_parquet(PRED_PARQUET)
    rows = conn.execute("SELECT parcel_uid, ag_year, season, state, p_state, route, needs_field FROM parcel_season "
                        "WHERE parcel_uid IS NOT NULL AND state IS NOT NULL").fetchall()
    return pd.DataFrame(rows, columns=["parcel_uid", "ag_year", "season", "state", "p_state", "route", "needs_field"])


def upsert(conn: Any, df: pd.DataFrame, version: str) -> int:
    from psycopg.types.json import Jsonb

    sql = ("INSERT INTO parcel_event_window (parcel_uid, window_name, start_date, end_date, date_basis, n_seasons, "
           "state_counts, state_share, dominant_state, seasons, caveats, events_version, computed_at) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) ON CONFLICT (parcel_uid, window_name) DO UPDATE SET "
           "start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, date_basis = EXCLUDED.date_basis, "
           "n_seasons = EXCLUDED.n_seasons, state_counts = EXCLUDED.state_counts, state_share = EXCLUDED.state_share, "
           "dominant_state = EXCLUDED.dominant_state, seasons = EXCLUDED.seasons, caveats = EXCLUDED.caveats, "
           "events_version = EXCLUDED.events_version, computed_at = now()")
    batch = []
    for r in df.to_dict("records"):
        extra = {k: r[k] for k in ("n_sufficient", "crop_seasons", "rabi_seasons", "rabi_cropped", "mean_p_state",
                                   "needs_field_count", "pending_second_opinion")}
        batch.append((r["parcel_uid"], r["window_name"], r["start_date"], r["end_date"],
                      Jsonb(json.loads(json.dumps({**r["date_basis"], "summary": extra}, default=str))),
                      int(r["n_seasons"]), Jsonb(r["state_counts"]), Jsonb(r["state_share"]), r["dominant_state"],
                      Jsonb(r["seasons"]), list(r["caveats"]), version))
    with conn.cursor() as cur:
        cur.executemany(sql, batch)
    conn.commit()
    return len(batch)


def summary(df: pd.DataFrame) -> list[str]:
    lines = []
    for w in WINDOWS:
        g = df[df.window_name == w]
        lines.append(f"{w:17s} parcels {len(g)}  median seasons {g.n_seasons.median():.0f}  "
                     f"dominant {g.dominant_state.value_counts(dropna=False).to_dict()}  "
                     f"any crop season {(g.crop_seasons > 0).mean():.2f}  "
                     f"rabi cropped/observed {int(g.rabi_cropped.sum())}/{int(g.rabi_seasons.sum())}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.events", description=__doc__.split("\n")[0])
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--from-parquet", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    import psycopg

    from planet.extract.run import dsn

    fb = load_fallbacks()
    conn = None if args.no_db else psycopg.connect(dsn())
    try:
        states = load_states(conn, args.from_parquet)
        if args.limit:
            states = states[states.parcel_uid.isin(states.parcel_uid.drop_duplicates().head(args.limit))]
        df = compute(states, fb)
        df.drop(columns=["seasons"]).assign(
            date_basis=df.date_basis.map(json.dumps), state_counts=df.state_counts.map(json.dumps),
            state_share=df.state_share.map(json.dumps), caveats=df.caveats.map(json.dumps),
        ).to_parquet(EVENTS_PARQUET, index=False)
        print("\n".join(summary(df)))
        if conn is not None:
            print(f"parcel_event_window upserted {upsert(conn, df, fb.get('version', 'events'))}")
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
