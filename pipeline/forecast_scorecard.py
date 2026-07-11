"""
Grade matured forecasts against what prices actually did, and feed the model
its own track record.

forecast_predict.py archives every first-issued forecast in predictions.db
`forecast_archive`. Each run this script:

  1. grades every archived forecast whose horizon has elapsed — fills the
     realized_* columns in place. 1m/6m/12m grade against the unified monthly
     history; 1w grades against dated PriceCharting snapshot points (so it
     starts working once the daily snapshots span a week).
  2. rebuilds `forecast_accuracy` — per (game, tier, horizon): count, log-return
     MAE, signed bias, and how often reality landed inside the model's 80%
     band (a calibrated model scores ~0.80 there).
  3. writes ml_data/{game}_extra_signals.csv — the card's and its set's
     trailing signed forecast error by month. forecast_deep.extra_signal_matrices
     picks that file up automatically, so the next retrain learns from its own
     past misses with no model-code change.

A signal value at month M is built only from outcomes already known by month M,
so training on it never leaks future prices.

Run:  .venv/bin/python forecast_scorecard.py
"""

import collections
import math
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

from games import priced_games
GAMES = priced_games()

MONTH_HORIZONS = {"1m": 1, "6m": 6, "12m": 12}
# For error normalization: a 12m miss of 0.24 and a 1m miss of 0.02 are the
# same monthly-scale error, so different horizons can share one signal.
HORIZON_MONTHS = {"1w": 7 / 30.44, "1m": 1, "6m": 6, "12m": 12}
GRACE_MONTHS = 2          # a gappy series may skip the exact due month
WEEK_TOLERANCE_DAYS = 3   # nearest snapshot this close to due date grades a 1w row


def month_add(month, k):
    """'YYYY-MM[-DD]' + k months -> 'YYYY-MM'."""
    y, m = int(month[:4]), int(month[5:7])
    y, m = y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1
    return f"{y:04d}-{m:02d}"


def month_range(first, last):
    out, m = [], first
    while m <= last:
        out.append(m)
        m = month_add(m, 1)
    return out


def unified_series(game, target):
    """product_id -> {YYYY-MM: price} from the unified monthly history."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT product_id, date, price FROM price_history_unified WHERE game=? AND grade=?",
        (game, target)).fetchall()
    out = collections.defaultdict(dict)
    for pid, d, p in rows:
        out[pid][d[:7]] = p
    return out


def snapshot_series(game, target, since):
    """product_id -> [(YYYY-MM-DD, price)] dated points, only from `since` on
    (1w grading needs just the recent snapshots, not the full history)."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT product_id, date, price FROM graded_price_history "
        "WHERE game=? AND grade=? AND date>=?", (game, target, since)).fetchall()
    out = collections.defaultdict(list)
    for pid, d, p in rows:
        out[pid].append((d[:10], p))
    return out


def realized_month(series, as_of, k):
    """Price at as_of + k months (allowing a small grace for gappy series)."""
    for g in range(GRACE_MONTHS + 1):
        m = month_add(as_of, k + g)
        p = series.get(m)
        if p and p > 0:
            return m + "-01", p
    return None


def realized_week(points, scored_at):
    """Snapshot point nearest to 7 days after issue, within tolerance."""
    due = date.fromisoformat(scored_at[:10]) + timedelta(days=7)
    best = None
    for d, p in points:
        delta = abs((date.fromisoformat(d) - due).days)
        if p and p > 0 and delta <= WEEK_TOLERANCE_DAYS and (best is None or delta < best[0]):
            best = (delta, d, p)
    return (best[1], best[2]) if best else None


def grade(conn, now_iso):
    """Fill realized_* on every archived forecast whose outcome is now known."""
    pending = conn.execute(
        "SELECT game, product_id, target, horizon, as_of, base_price, scored_at "
        "FROM forecast_archive WHERE realized_price IS NULL"
        "  AND substr(model_version, 1, 2) != '__'").fetchall()   # skip test/sample rows

    by_series = collections.defaultdict(list)   # load each price series once
    for row in pending:
        by_series[(row[0], row[2])].append(row)

    updates = []
    for (game, target), rows in sorted(by_series.items()):
        months = unified_series(game, target)
        snaps = None
        for _, pid, _, horizon, as_of, base, scored_at in rows:
            if not base or base <= 0:
                continue
            if horizon in MONTH_HORIZONS:
                res = realized_month(months.get(pid, {}), as_of, MONTH_HORIZONS[horizon])
            else:   # 1w — needs dated points, loaded lazily on first 1w row
                if snaps is None:
                    since = min(r[6][:10] for r in rows if r[3] == "1w")
                    snaps = snapshot_series(game, target, since)
                res = realized_week(snaps.get(pid, []), scored_at)
            if res is None:
                continue
            realized_at, price = res
            updates.append((price, round(math.log(price / base), 4), realized_at, now_iso,
                            game, pid, target, horizon, as_of))

    conn.executemany(
        "UPDATE forecast_archive SET realized_price=?, realized_ret=?, realized_at=?, graded_at=? "
        "WHERE game=? AND product_id=? AND target=? AND horizon=? AND as_of=?", updates)
    return len(updates)


def rebuild_accuracy(conn, now_iso):
    """Aggregate the graded rows into the site/model-facing scorecard table."""
    conn.executescript(
        """
        DROP TABLE IF EXISTS forecast_accuracy;
        CREATE TABLE forecast_accuracy (
            game TEXT NOT NULL, target TEXT NOT NULL, horizon TEXT NOT NULL,
            n INTEGER NOT NULL,
            ret_mae REAL,           -- mean |predicted - realized| log-return
            ret_bias REAL,          -- mean (predicted - realized): + = runs hot
            band_hit_rate REAL,     -- share of outcomes inside [low, high] (~0.80 when calibrated)
            graded_through TEXT, updated_at TEXT,
            PRIMARY KEY (game, target, horizon)
        );
        """)
    conn.execute(
        """
        INSERT INTO forecast_accuracy
        SELECT game, target, horizon, COUNT(*),
               ROUND(AVG(ABS(ret - realized_ret)), 4),
               ROUND(AVG(ret - realized_ret), 4),
               ROUND(AVG(CASE WHEN realized_price BETWEEN low AND high THEN 1.0 ELSE 0.0 END), 3),
               MAX(realized_at), ?
        FROM forecast_archive
        WHERE realized_ret IS NOT NULL AND substr(model_version, 1, 2) != '__'
        GROUP BY game, target, horizon
        """, (now_iso,))


def card_sets(game):
    """product_id -> set_name from the ml export."""
    path = os.path.join(DATA, f"{game}_cards.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, usecols=["product_id", "set_name"])
    return dict(zip(df["product_id"].astype(int),
                    df["set_name"].astype("string").fillna("")))


def cum_by_month(errs_by_month, through):
    """month -> running mean error, carried forward to `through` so the model
    always sees the latest known track record."""
    out, total, n = {}, 0.0, 0
    for m in month_range(min(errs_by_month), through):
        for e in errs_by_month.get(m, ()):
            total, n = total + e, n + 1
        out[m] = round(total / n, 4)
    return out


def write_signals(conn, game):
    """ml_data/{game}_extra_signals.csv: fcerr_card / fcerr_set by month —
    the trailing signed error (per month of horizon) of matured forecasts,
    bucketed by the month each outcome became known."""
    path = os.path.join(DATA, f"{game}_extra_signals.csv")
    graded = conn.execute(
        "SELECT product_id, horizon, ret, realized_ret, realized_at FROM forecast_archive "
        "WHERE game=? AND realized_ret IS NOT NULL"
        "  AND substr(model_version, 1, 2) != '__'", (game,)).fetchall()
    if not graded:
        if os.path.exists(path):
            os.remove(path)   # never leave a stale signal file feeding the model
        return 0

    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    set_of = card_sets(game)
    per_card = collections.defaultdict(lambda: collections.defaultdict(list))
    per_set = collections.defaultdict(lambda: collections.defaultdict(list))
    for pid, horizon, ret, realized, realized_at in graded:
        err = (ret - realized) / HORIZON_MONTHS[horizon]
        per_card[pid][realized_at[:7]].append(err)
        if set_of.get(pid):
            per_set[set_of[pid]][realized_at[:7]].append(err)

    set_sig = {s: cum_by_month(v, this_month) for s, v in per_set.items()}
    rows = {}   # (pid, month) -> [fcerr_card, fcerr_set]
    for pid, v in per_card.items():
        for m, e in cum_by_month(v, this_month).items():
            rows[(pid, m)] = [e, None]
    for pid, s in set_of.items():   # set signal covers every card in a graded set
        for m, e in set_sig.get(s, {}).items():
            rows.setdefault((pid, m), [None, None])[1] = e

    df = pd.DataFrame(
        [(pid, m, c, s) for (pid, m), (c, s) in sorted(rows.items())],
        columns=["product_id", "month", "fcerr_card", "fcerr_set"])
    df.to_csv(path, index=False)
    return len(df)


def main():
    if not os.path.exists(OUT_DB):
        print("no predictions.db yet — nothing to grade")
        return
    conn = sqlite3.connect(OUT_DB, timeout=60)
    if not conn.execute("SELECT name FROM sqlite_master WHERE name='forecast_archive'").fetchone():
        print("no forecast_archive yet — run forecast_predict.py first")
        return

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    newly = grade(conn, now_iso)
    rebuild_accuracy(conn, now_iso)
    conn.commit()

    total, done = conn.execute(
        "SELECT COUNT(*), COUNT(realized_ret) FROM forecast_archive").fetchone()
    print(f"graded {newly} newly matured forecast(s) — {done}/{total} archived rows graded")

    for game in GAMES:
        n = write_signals(conn, game)
        print(f"[{game}] " + (f"{n} extra-signal rows -> {game}_extra_signals.csv"
                              if n else "no matured outcomes yet — no signal file"))

    for g, t, h, n, mae, bias, hit in conn.execute(
            "SELECT game, target, horizon, n, ret_mae, ret_bias, band_hit_rate "
            "FROM forecast_accuracy ORDER BY game, target, horizon"):
        print(f"  {g}/{t}/{h}: n={n} retMAE={mae} bias={bias:+} 80%-band hit {hit:.0%}")
    conn.close()


if __name__ == "__main__":
    main()
