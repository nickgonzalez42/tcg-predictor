#!/usr/bin/env python3
"""
Nightly table of every card's past price change, per condition tier and
trend window — what the catalog's "What already happened" sorts order by.

Until 2026-10-26 the API computed these on request: sorting Magic by
"6 months, % change" meant re-reading the price history of all 110k Magic
cards (28 s on the production box, nothing cached), and five such requests
at once took the site down. The nightly refresh now does the arithmetic
once, here, and the API reads a 500k-row table instead.

One pass over price_history_unified in index order (game, grade, product_id,
date) — the base printing only, as the API's global filter reads it — and
for each series: the latest price, and for each window the last price on or
before the window's start (or the first price we have, when the series is
younger than the window), exactly the anchor rule CardMarketData.HistoryChanges
used. One row per (game, grade, product_id) with pct/usd columns for the four
windows (1w, 1m, 6m, 1y); the cutoffs follow .NET's AddDays/AddMonths/AddYears
so the numbers match what the API would have produced today.

Written into predictions.db (table card_trends, built as card_trends_new and
swapped in one transaction), which the model block pushes to prod every
night. Non-fatal: on failure the API falls back to computing on demand.

Run:  .venv/bin/python build_card_trends.py [--pc-db ..] [--out-db ..] [--as-of YYYY-MM-DD]
"""

import argparse
import calendar
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS = os.path.join(REPO, "dotnet", "API", "Data", "cards")
PC_DB = os.path.join(CARDS, "pricecharting.db")
OUT_DB = os.path.join(CARDS, "predictions.db")
WINDOWS = ("1w", "1m", "6m", "1y")


def add_months(d, months):
    """.NET DateTime.AddMonths: same day in the target month, clamped."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cutoffs(today):
    return {
        "1w": (today - timedelta(days=7)).isoformat(),
        "1m": add_months(today, -1).isoformat(),
        "6m": add_months(today, -6).isoformat(),
        "1y": add_months(today, -12).isoformat(),
    }


def build(pc_db, out_db, today):
    t0 = time.time()
    cut = cutoffs(today)
    cut_list = [(w, cut[w]) for w in WINDOWS]
    src = sqlite3.connect(f"file:{pc_db}?mode=ro", uri=True, timeout=600)
    # Autocommit mode: transactions are managed explicitly below (one for the
    # bulk load, one for the swap); Python's implicit BEGIN would otherwise
    # collide with them.
    out = sqlite3.connect(out_db, timeout=600, isolation_level=None)
    out.execute("BEGIN")
    out.execute("DROP TABLE IF EXISTS card_trends_new")
    out.execute("""CREATE TABLE card_trends_new (
        game TEXT NOT NULL, grade TEXT NOT NULL, product_id INTEGER NOT NULL,
        latest_date TEXT, latest_price REAL,
        pct_1w REAL, usd_1w REAL, pct_1m REAL, usd_1m REAL,
        pct_6m REAL, usd_6m REAL, pct_1y REAL, usd_1y REAL,
        as_of TEXT NOT NULL)""")

    rows_in = rows_out = 0
    buf = []
    cur_key = None
    first = latest = None
    anchors = {}

    def flush():
        nonlocal rows_out
        if cur_key is None or latest is None:
            return
        vals = [*cur_key, latest[0], latest[1]]
        for w, _c in cut_list:
            a = anchors.get(w) or first
            if a and a[1] > 0:
                vals += [(latest[1] / a[1] - 1) * 100, latest[1] - a[1]]
            else:
                vals += [None, None]
        vals.append(today.isoformat())
        buf.append(vals)
        rows_out += 1
        if len(buf) >= 5000:
            out.executemany("INSERT INTO card_trends_new VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf.clear()

    # The covering index (game, grade, product_id, date, price) makes this a
    # single ordered walk; rows arrive date-ascending within a series.
    q = ("SELECT game, grade, product_id, date, price FROM price_history_unified "
         "WHERE printing='' ORDER BY game, grade, product_id, date")
    for game, grade, pid, d, price in src.execute(q):
        rows_in += 1
        key = (game, grade, pid)
        if key != cur_key:
            flush()
            cur_key, first, anchors = key, (d, price), {}
        latest = (d, price)
        for w, c in cut_list:
            if d <= c:
                anchors[w] = latest
    flush()
    if buf:
        out.executemany("INSERT INTO card_trends_new VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
    out.execute("COMMIT")
    src.close()

    out.execute("BEGIN")
    out.execute("DROP TABLE IF EXISTS card_trends")
    out.execute("ALTER TABLE card_trends_new RENAME TO card_trends")
    out.execute("CREATE INDEX IF NOT EXISTS idx_card_trends_game_grade ON card_trends (game, grade)")
    out.execute("COMMIT")
    out.close()
    print(f"card_trends: {rows_out:,} series from {rows_in:,} history rows "
          f"(as of {today}, cutoffs {cut['1w']}/{cut['1m']}/{cut['6m']}/{cut['1y']}) "
          f"in {time.time() - t0:.0f}s -> {out_db}")


def main():
    ap = argparse.ArgumentParser(description="Nightly past-change table for the catalog's history sorts")
    ap.add_argument("--pc-db", default=PC_DB)
    ap.add_argument("--out-db", default=OUT_DB)
    ap.add_argument("--as-of", help="YYYY-MM-DD (default: today, UTC)")
    args = ap.parse_args()
    today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    build(args.pc_db, args.out_db, today)


if __name__ == "__main__":
    # Non-fatal for the refresh: without the table the API computes on demand
    # (slowly) — the night's push must still go out.
    try:
        main()
    except Exception as e:                      # noqa: BLE001
        print(f"build_card_trends: NON-FATAL ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
