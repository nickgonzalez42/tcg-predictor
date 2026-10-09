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
TOLERANCE_DAYS = 3        # nearest dated point this close to a due date grades a row
CARRY_DAYS = 31           # else: newest at-or-before due, if at most this old
                          # (matches the site's forward-filled displayed price)
DATED_DUE_DAYS = 28       # a nightly 1m cohort is due 28 days after issue


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


def unified_series(game, target, printing=""):
    """product_id -> {YYYY-MM: price} from the unified monthly history for
    ONE printing ('' = base)."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT product_id, date, price FROM price_history_unified WHERE printing=? AND game=? AND grade=?",
        (printing, game, target)).fetchall()
    out = collections.defaultdict(dict)
    for pid, d, p in rows:
        out[pid][d[:7]] = p
    return out


def snapshot_series(game, target, since, printing=""):
    """product_id -> [(YYYY-MM-DD, price)] dated points, only from `since` on
    (1w grading needs just the recent snapshots, not the full history).

    UNGRADED reads tcg_nm_history: since the paid daily snapshots retired
    (2026-07-31), graded_price_history only gains ~monthly chart buckets, and
    exact-window 1w grading stalled (75k pending by 2026-08-14) — the nightly
    TCGplayer NM batches are the dated daily truth now. Both reads filter
    printing (previously mixed printings once the labeled rows landed)."""
    con = sqlite3.connect(PC_DB, timeout=30)
    if target == "ungraded":
        rows = con.execute(
            "SELECT product_id, date, price FROM tcg_nm_history "
            "WHERE game=? AND printing=? AND date>=?", (game, printing, since)).fetchall()
    else:
        rows = con.execute(
            "SELECT product_id, date, price FROM graded_price_history "
            "WHERE game=? AND grade=? AND printing=? AND date>=?",
            (game, target, printing, since)).fetchall()
    out = collections.defaultdict(list)
    for pid, d, p in rows:
        out[pid].append((d[:10], p))
    for v in out.values():
        v.sort()
    return out


def realized_month(series, as_of, k):
    """Price at as_of + k months (allowing a small grace for gappy series)."""
    for g in range(GRACE_MONTHS + 1):
        m = month_add(as_of, k + g)
        p = series.get(m)
        if p and p > 0:
            return m + "-01", p
    return None


def realized_dated(points, scored_at, days, today=None):
    """Dated point nearest to `days` after issue, within tolerance; failing
    that, the newest point at-or-before the due date if it's fresh enough
    (CARRY_DAYS) — i.e. the price the SITE displayed on the due day. Sparse
    series (flat-priced cards dedupe to value-change points) otherwise never
    grade.

    A row only grades once its due date has PASSED: without this gate the
    carry-forward branch graded PENDING forecasts with today's price
    (2026-08-15: the whole first nightly cohort, due Sep 12, matured 28 days
    early)."""
    due = date.fromisoformat(scored_at[:10]) + timedelta(days=days)
    if today is not None and due > today:
        return None
    best = None
    for d, p in points:
        delta = abs((date.fromisoformat(d) - due).days)
        if p and p > 0 and delta <= TOLERANCE_DAYS and (best is None or delta < best[0]):
            best = (delta, d, p)
    if best:
        return best[1], best[2]
    prev = None
    for d, p in points:                     # points are date-sorted
        if d <= due.isoformat() and p and p > 0:
            prev = (d, p)
    if prev and (due - date.fromisoformat(prev[0])).days <= CARRY_DAYS:
        return prev
    return None


def grade(conn, now_iso):
    """Fill realized_* on every archived forecast whose outcome is now known."""
    pending = conn.execute(
        "SELECT game, product_id, target, horizon, as_of, base_price, scored_at, printing "
        "FROM forecast_archive WHERE realized_price IS NULL"
        "  AND substr(model_version, 1, 2) != '__'").fetchall()   # skip test/sample rows

    by_series = collections.defaultdict(list)   # load each price series once
    for row in pending:
        by_series[(row[0], row[2], row[7])].append(row)

    updates = []
    for (game, target, printing), rows in sorted(by_series.items()):
        months = unified_series(game, target, printing)
        snaps = None
        for _, pid, _, horizon, as_of, base, scored_at, _pr in rows:
            if not base or base <= 0:
                continue
            if horizon in ("1m", "launch1m") and target == "ungraded":
                # Nightly 1m cohorts (2026-08-14) grade against dated
                # TCGplayer points: due = issue + 28 days, matching the due
                # date the site displays. Snapshots load lazily with
                # CARRY_DAYS of lead-in so the carry-forward fallback has a
                # candidate even when the last reprice predates issue.
                if snaps is None:
                    first = min(r[6][:10] for r in rows if r[3] in ("1m", "launch1m"))
                    since = (date.fromisoformat(first)
                             - timedelta(days=CARRY_DAYS)).isoformat()
                    snaps = snapshot_series(game, target, since, printing)
                res = realized_dated(snaps.get(pid, []), scored_at, DATED_DUE_DAYS,
                                     today=date.fromisoformat(now_iso[:10]))
            elif horizon in MONTH_HORIZONS:
                # Due gate for month-bucket grading too (2026-09-12): a 1m
                # graded-tier forecast issued Aug 31 was scoring against
                # Sep 1's bucket — days elapsed, not a month — and "predicting
                # the present" to the penny. The horizon must actually pass
                # before the row grades (~25d per horizon month).
                due = (date.fromisoformat(as_of[:10])
                       + timedelta(days=25 * MONTH_HORIZONS[horizon]))
                if date.fromisoformat(now_iso[:10]) < due:
                    continue
                res = realized_month(months.get(pid, {}), as_of, MONTH_HORIZONS[horizon])
            else:
                continue   # 1w retired 2026-08-14; strays are purged in main()
            if res is None:
                continue
            realized_at, price = res
            updates.append((price, round(math.log(price / base), 4), realized_at, now_iso,
                            game, pid, target, horizon, as_of, printing))

    conn.executemany(
        "UPDATE forecast_archive SET realized_price=?, realized_ret=?, realized_at=?, graded_at=? "
        "WHERE game=? AND product_id=? AND target=? AND horizon=? AND as_of=? AND printing=?", updates)
    return len(updates)


def ensure_monthly_accuracy(conn):
    """Full-cohort accuracy per (game, tier, horizon, month), persisted ONCE
    at compaction time so pruning can never rewrite the public track record."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_accuracy_monthly (
            game TEXT NOT NULL, target TEXT NOT NULL, horizon TEXT NOT NULL,
            month TEXT NOT NULL, n INTEGER NOT NULL,
            ret_mae REAL, ret_bias REAL, band_hit_rate REAL,
            graded_through TEXT,
            PRIMARY KEY (game, target, horizon, month)
        )
        """)


def ensure_weekly_coverage(conn):
    """Full-cohort WEEKLY coverage tallies, banked additively as compaction
    deletes rows (2026-10-09): median-error survivors are coverage-biased —
    the first compaction lifted a landing week's displayed band coverage
    from 52.8% to 84.6% in the Friday report. Weeks key on the FRIDAY they
    start (report-aligned). Ungraded dated stream only (what the report's
    week-over-week section reads)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_coverage_weekly (
            week TEXT PRIMARY KEY,
            n INTEGER NOT NULL,
            band_hits INTEGER NOT NULL,
            abs_err_sum REAL NOT NULL,
            dir_n INTEGER NOT NULL,
            dir_hits INTEGER NOT NULL
        )
        """)


# SQLite %w: Sunday=0 … Friday=5. (w+2)%7 days back = the week's Friday.
WEEK_FRI = ("date(substr(realized_at, 1, 10), '-' || "
            "((CAST(strftime('%w', substr(realized_at, 1, 10)) AS INTEGER) + 2) % 7)"
            " || ' days')")


def compact_1m(conn, now_iso):
    """Keep ONE REAL forecast per card-month once the month has fully matured.

    Nightly 1m rows key as_of to the ISSUE date. Once an issue month M's last
    cohort has come due (M+1's 1st + 28 days), each (game, card, tier,
    printing) keeps its MEDIAN-|error| graded row — a genuine forecast with
    its real issue date, real base price and real outcome, representing the
    month's typical call — chosen among full-horizon rows when any exist
    (see step 2) — and the rest are deleted. NO synthetic averaging
    (2026-08-29, user decision): the chart draws past forecasts from the
    actual price on the generation day to the actual predicted price, and an
    averaged row has neither. Keeping the BEST row is equally banned — that
    would turn the visible dots and the scorecard into a highlight reel.

    Before any deletion, the month's full-cohort stats are persisted to
    forecast_accuracy_monthly (INSERT once, never overwritten), so the
    published accuracy numbers keep counting every forecast ever graded.
    Long-term growth is one row per card-month, same as the old scheme.
    """
    today = date.fromisoformat(now_iso[:10])
    ensure_monthly_accuracy(conn)
    done = {m for (m,) in conn.execute(
        "SELECT DISTINCT month FROM forecast_accuracy_monthly WHERE horizon='1m'")}
    months = [m for (m,) in conn.execute(
        "SELECT DISTINCT substr(as_of, 1, 7) FROM forecast_archive "
        "WHERE horizon='1m' AND substr(model_version, 1, 2) != '__' "
        "GROUP BY substr(as_of, 1, 7) "
        "HAVING COUNT(DISTINCT as_of) > 1")]
    total = 0
    for m in months:
        matured = (date.fromisoformat(month_add(m, 1) + "-01")
                   + timedelta(days=DATED_DUE_DAYS))
        if matured > today or m in done:
            continue
        # 1. Persist the month's FULL stats first (headline accuracy filters
        #    printing='' — the persisted stats mirror that).
        conn.execute(
            "INSERT OR IGNORE INTO forecast_accuracy_monthly "
            "SELECT game, target, '1m', ?, COUNT(*), "
            "  ROUND(AVG(ABS(ret - realized_ret)), 4), "
            "  ROUND(AVG(ret - realized_ret), 4), "
            "  ROUND(AVG(CASE WHEN realized_price BETWEEN low AND high "
            "            THEN 1.0 ELSE 0.0 END), 3), "
            "  MAX(realized_at) "
            "FROM forecast_archive "
            "WHERE horizon='1m' AND substr(as_of, 1, 7)=? AND printing='' "
            "  AND realized_ret IS NOT NULL AND substr(model_version, 1, 2) != '__' "
            "GROUP BY game, target", (m, m))
        # 2. Median-|error| representative per card: sort each card-month's
        #    graded rows by error then date, keep the middle one.
        #    Month-bucket cohorts (graded tiers) aim every daily row at the
        #    same next-month date, so a row issued the 31st is a 1-day call;
        #    the representative — the row the chart shows as "the" past 1M
        #    forecast — must be a full-horizon one whenever the card has any
        #    (elapsed >= 25d, the due gate's own standard). Dated nightly rows
        #    are always ~28d out, so this changes nothing for them.
        by_card = {}
        for rowid, g, pid, t, pr, err, as_of, elapsed in conn.execute(
                "SELECT rowid, game, product_id, target, printing, "
                "  ABS(COALESCE(ret, 0) - realized_ret), as_of, "
                "  julianday(substr(realized_at, 1, 10)) "
                "    - julianday(substr(as_of, 1, 10)) "
                "FROM forecast_archive "
                "WHERE horizon='1m' AND substr(as_of, 1, 7)=? "
                "  AND realized_ret IS NOT NULL "
                "  AND substr(model_version, 1, 2) != '__'", (m,)):
            by_card.setdefault((g, pid, t, pr), []).append((err, as_of, rowid, elapsed))
        keep = []
        for lst in by_card.values():
            pool = [r for r in lst if r[3] is not None and r[3] >= 25] or lst
            pool.sort(key=lambda r: (r[0], r[1], r[2]))
            keep.append(pool[len(pool) // 2][2])
        conn.execute("DROP TABLE IF EXISTS temp.keep_1m")
        conn.execute("CREATE TEMP TABLE keep_1m (rowid INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO temp.keep_1m VALUES (?)", [(k,) for k in keep])
        # Bank the weekly tallies of the rows about to be DELETED (survivors
        # keep reporting through the live query; deleted + live = full cohort).
        ensure_weekly_coverage(conn)
        for wk, n_, hits, errs, dn, dh in conn.execute(
                f"SELECT {WEEK_FRI}, COUNT(*), "
                "  SUM(CASE WHEN realized_price BETWEEN low AND high THEN 1 ELSE 0 END), "
                "  SUM(ABS(COALESCE(ret, 0) - realized_ret)), "
                "  SUM(CASE WHEN ABS(COALESCE(ret, 0)) >= 0.01 AND ABS(realized_ret) >= 0.01 "
                "      THEN 1 ELSE 0 END), "
                "  SUM(CASE WHEN ABS(COALESCE(ret, 0)) >= 0.01 AND ABS(realized_ret) >= 0.01 "
                "      AND (COALESCE(ret, 0) > 0) = (realized_ret > 0) THEN 1 ELSE 0 END) "
                "FROM forecast_archive "
                "WHERE horizon='1m' AND substr(as_of, 1, 7)=? AND realized_ret IS NOT NULL "
                "  AND substr(model_version, 1, 2) != '__' AND target='ungraded' "
                "  AND printing='' AND strftime('%d', substr(realized_at, 1, 10)) != '01' "
                "  AND rowid NOT IN (SELECT rowid FROM temp.keep_1m) "
                "GROUP BY 1", (m,)):
            conn.execute(
                "INSERT INTO forecast_coverage_weekly VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(week) DO UPDATE SET n = n + excluded.n, "
                "  band_hits = band_hits + excluded.band_hits, "
                "  abs_err_sum = abs_err_sum + excluded.abs_err_sum, "
                "  dir_n = dir_n + excluded.dir_n, dir_hits = dir_hits + excluded.dir_hits",
                (wk, n_, hits or 0, errs or 0.0, dn or 0, dh or 0))
        n = conn.execute(
            "DELETE FROM forecast_archive "
            "WHERE horizon='1m' AND substr(as_of, 1, 7)=? "
            "  AND realized_ret IS NOT NULL "
            "  AND substr(model_version, 1, 2) != '__' "
            "  AND rowid NOT IN (SELECT rowid FROM temp.keep_1m)", (m,)).rowcount
        conn.execute("DROP TABLE temp.keep_1m")
        total += n
    return total


def _r2(v):
    return None if v is None else round(v, 2)


def _r4(v):
    return None if v is None else round(v, 4)


def rebuild_accuracy(conn, now_iso):
    """Aggregate graded rows into the site/model-facing scorecard table.

    Two sources (2026-08-29): live archive rows, PLUS the persisted
    full-cohort monthly stats for months the 1m compactor has pruned to
    representatives. Compacted months are excluded from the live scan so the
    surviving representative rows are never double-counted — and so the
    headline numbers always reflect every forecast ever graded, not just the
    rows that happened to be retained.
    """
    ensure_monthly_accuracy(conn)
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
    live = conn.execute(
        """
        SELECT game, target, horizon, COUNT(*),
               SUM(ABS(ret - realized_ret)),
               SUM(ret - realized_ret),
               SUM(CASE WHEN realized_price BETWEEN low AND high THEN 1.0 ELSE 0.0 END),
               MAX(realized_at)
        FROM forecast_archive
        WHERE realized_ret IS NOT NULL AND printing='' AND substr(model_version, 1, 2) != '__'
          AND NOT (horizon = '1m' AND substr(as_of, 1, 7) IN
                   (SELECT DISTINCT month FROM forecast_accuracy_monthly WHERE horizon='1m'))
        GROUP BY game, target, horizon
        """).fetchall()
    stats = {}   # (game, target, horizon) -> [n, sum_abs, sum_err, sum_hit, through]
    for g, t, h, n, sa, se, sh, thru in live:
        stats[(g, t, h)] = [n, sa or 0.0, se or 0.0, sh or 0.0, thru or ""]
    for g, t, h, n, mae, bias, hit, thru in conn.execute(
            "SELECT game, target, horizon, n, ret_mae, ret_bias, band_hit_rate, "
            "  graded_through FROM forecast_accuracy_monthly"):
        s = stats.setdefault((g, t, h), [0, 0.0, 0.0, 0.0, ""])
        s[0] += n
        s[1] += (mae or 0.0) * n
        s[2] += (bias or 0.0) * n
        s[3] += (hit or 0.0) * n
        s[4] = max(s[4], thru or "")
    conn.executemany(
        "INSERT INTO forecast_accuracy VALUES (?,?,?,?,?,?,?,?,?)",
        [(g, t, h, s[0], round(s[1] / s[0], 4), round(s[2] / s[0], 4),
          round(s[3] / s[0], 3), s[4], now_iso)
         for (g, t, h), s in stats.items() if s[0]])


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
        "WHERE game=? AND realized_ret IS NOT NULL AND printing=''"
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
        if horizon not in HORIZON_MONTHS:
            continue   # launch1m: separate track record, never a model signal
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
    # 1w retired 2026-08-14 (was the 1m forecast pro-rated to 7 days; a true
    # weekly model arrives with the daily data, ~Oct 2026). Idempotent.
    purged = conn.execute("DELETE FROM forecast_archive WHERE horizon='1w'").rowcount
    if purged:
        print(f"purged {purged} retired 1w row(s)")
    # One-time re-grade (2026-08-14): legacy ungraded 1m rows were graded
    # against MONTH buckets (realized_at = 'YYYY-MM-01', a label — a Jul 10
    # forecast read as landing Aug 1 instead of its true Aug 7 due date).
    # Dated grading now exists, and the dated TCGplayer series covers the
    # whole archive era (starts 2026-07-09) — so reset those rows and let
    # tonight's grade() redo them at issue + 28d. graded_at < the deploy date
    # makes this a no-op on every later run.
    regrade = conn.execute(
        "UPDATE forecast_archive SET realized_price=NULL, realized_ret=NULL, "
        "  realized_at=NULL, graded_at=NULL "
        "WHERE horizon='1m' AND target='ungraded' AND graded_at < '2026-08-15' "
        "  AND realized_at LIKE '____-__-01'").rowcount
    if regrade:
        print(f"re-grading {regrade} month-bucket-graded ungraded 1m row(s) with dated outcomes")
    # Repair (2026-08-15): the first dated-grading run matured PENDING rows
    # early (carry-forward lacked a due-has-passed gate — fixed in
    # realized_dated). Un-grade anything graded before its own due date; the
    # fixed grader re-grades each at true maturity. Empty forever after.
    early = conn.execute(
        "UPDATE forecast_archive SET realized_price=NULL, realized_ret=NULL, "
        "  realized_at=NULL, graded_at=NULL "
        "WHERE horizon='1m' AND graded_at IS NOT NULL "
        "  AND date(substr(scored_at, 1, 10), '+28 day') > substr(graded_at, 1, 10)").rowcount
    if early:
        print(f"un-graded {early} prematurely matured 1m row(s)")
    # One-time repair (2026-09-12): graded-tier 1m rows issued near month-end
    # were month-graded days later against the next bucket — present-tense
    # "wins" matching to the penny. Un-grade; the gated path re-grades them
    # once the month has genuinely elapsed. Idempotent.
    early2 = conn.execute(
        "UPDATE forecast_archive SET realized_price=NULL, realized_ret=NULL, "
        "  realized_at=NULL, graded_at=NULL "
        "WHERE realized_ret IS NOT NULL AND horizon='1m' AND target != 'ungraded' "
        "  AND julianday(realized_at) - julianday(substr(as_of, 1, 10)) < 25").rowcount
    if early2:
        print(f"un-graded {early2} prematurely month-graded graded-tier 1m row(s)")
    # Review-driven forecast purges (nm_price_review.py queues cards whose
    # price data was repaired — e.g. a bogus TCG listing replaced by PC).
    # Only FRESH queue entries act (3 days): tonight's clean regeneration must
    # not be deleted by the same entry tomorrow.
    purge_q = os.path.join(BASE, "ml_data", "forecast_purge_queue.csv")
    if os.path.exists(purge_q):
        import csv as _csv
        # 5 days (2026-09-20): runs are Mon/Thu now — a repair queued right
        # after a Thursday run must still be fresh when Monday's consumes it.
        fresh_cut = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        with open(purge_q, newline="", encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                if r.get("queued_at", "") >= fresh_cut:
                    # Optional `target` column scopes the purge to one tier
                    # (2026-08-18 bulk wipe: ungraded only, graded record kept);
                    # absent/empty = purge every tier (the pc_right repairs).
                    tgt = (r.get("target") or "").strip()
                    cond = "game=? AND product_id=?" + (" AND target=?" if tgt else "")
                    key = [r["game"], int(r["product_id"])] + ([tgt] if tgt else [])
                    n1 = conn.execute(f"DELETE FROM forecasts WHERE {cond}", key).rowcount
                    n2 = conn.execute(f"DELETE FROM forecast_archive WHERE {cond}", key).rowcount
                    if n1 or n2:
                        print(f"purge queue: {r['game']}/{r['product_id']}"
                              f"{'/' + tgt if tgt else ''} "
                              f"-> {n1} forecasts + {n2} archive rows removed")
    newly = grade(conn, now_iso)
    compacted = compact_1m(conn, now_iso)
    if compacted:
        print(f"compacted {compacted} matured nightly 1m row(s) — kept each card's "
              f"median-error forecast; full-month stats persisted")
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
