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
for each series: the latest price, and for each window the price as it stood
at the window's start (see window_change for how that anchor is read without
being fooled by launch listings, stray points or the PriceCharting/TCGplayer
seam). One row per (game, grade, product_id) with pct/usd columns for the four
windows (1w, 1m, 6m, 1y); the cutoffs follow .NET's AddDays/AddMonths/AddYears.
The API shows these same numbers on the tiles (CardMarketData.ApplyMarket), so
the order of a "What already happened" sort always matches what the tiles say.

Written into predictions.db (table card_trends, built as card_trends_new and
swapped in one transaction), which the model block pushes to prod every
night. Non-fatal: on failure the API falls back to computing on demand.

Run:  .venv/bin/python build_card_trends.py [--pc-db ..] [--out-db ..] [--as-of YYYY-MM-DD]
"""

import argparse
import bisect
import calendar
import os
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS = os.path.join(REPO, "dotnet", "API", "Data", "cards")
PC_DB = os.path.join(CARDS, "pricecharting.db")
OUT_DB = os.path.join(CARDS, "predictions.db")
WINDOWS = ("1w", "1m", "6m", "1y")
# A new card's first two weeks are presale and launch listings (a $9.99 ask on
# a common that settles at $0.10 a month later): no change is measured from them.
LAUNCH_SKIP_DAYS = 14
# Where a series switches source (PriceCharting's history -> TCGplayer's market
# price, the July 2026 seam) and the two disagree by more than this factor at
# the switch, the change is measured from the switch instead, or not at all.
SEAM_MAX_STEP = 2.0
# One step between consecutive points bigger than this (either way) that the
# series keeps means the source started tracking a different item.
STEP_BREAK = 10.0
# Preorder asks can sit for months before a set comes out (a Star Trek card at
# $99.95 from July to its November release, $3.47 the week it shipped), so
# where the catalog knows a card's release date, its launch weeks run until a
# week after it.
RELEASE_SETTLE_DAYS = 7


def release_dates():
    """{(game, product_id): 'YYYY-MM-DD'} from each catalog's release_date, or,
    for a schema without one (One Piece), its set's TCGplayer publish date from
    the cache game_articles keeps (ml_data/tcgcsv_groups_<game>.json). Never
    touches the network; a game it can't date just keeps the first-price rule."""
    import json
    from games import BASE, GAMES, db_path
    out = {}
    for game in GAMES:
        path = db_path(game)
        if not os.path.exists(path):
            continue
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
            cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
            if "release_date" in cols:
                for pid, rel in con.execute(
                        "SELECT product_id, release_date FROM cards WHERE release_date > ''"):
                    out[(game, pid)] = rel[:10]
            else:
                with open(os.path.join(BASE, "ml_data", f"tcgcsv_groups_{game}.json"),
                          encoding="utf-8") as f:
                    groups = {g["name"].strip(): (g.get("publishedOn") or "")[:10]
                              for g in json.load(f).get("results", []) if g.get("name")}
                for pid, set_name in con.execute("SELECT product_id, set_name FROM cards"):
                    if groups.get((set_name or "").strip()):
                        out[(game, pid)] = groups[set_name.strip()]
            con.close()
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            print(f"card_trends: no release dates for {game} ({type(e).__name__}); "
                  "its launch weeks run from the first price")
    return out


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


def broken(pts, i):
    """True when the series steps by more than STEP_BREAK between consecutive
    points after index i and stays there: the source changed what it tracks
    (a trophy card priced as its $8 base print for a year, then at $35,500), so
    no change measured across the step means anything. A single stray point
    that the next one undoes is not a break."""
    last = len(pts) - 1
    k = i
    while k < last:
        a, b = pts[k][1], pts[k + 1][1]
        if a > 0 and b > 0 and not 1 / STEP_BREAK <= b / a <= STEP_BREAK:
            if k + 1 == last:
                return True
            c = pts[k + 2][1]
            if c > 0 and 1 / 3 <= c / a <= 3:
                k += 2
                continue
            return True
        k += 1
    return False


def window_change(pts, dates, cutoff, today, released=None):
    """(pct, usd) of the latest price against the window's anchor, or None.

    pts: [(date, price, source)] date-ascending for one series; dates: the
    same dates, for bisect. The anchor is the price as it stood at the window's
    start (the last point on or before it), read so one bad point can't own the
    ranking (2026-11-13, user: "the history percent order on the catalog is not
    working" -- the top and bottom of every past-change sort were artifacts):
      - a card's first LAUNCH_SKIP_DAYS days, and everything up to
        RELEASE_SETTLE_DAYS after its release date (`released`, when known),
        are presale and launch asks (a $9.99 common that settles at $0.09), so
        a window reaching back into them measures from the first price after
        them, and a card still inside them has no change yet;
      - the anchor is the median of its point and its same-source neighbours
        when it has one on each side, so a single stray listing is not the
        baseline;
      - when the anchor's source differs from today's, the new source's start
        (the median of its first three points before today, since its very
        first point is sometimes a stray) is checked against the old source's
        last price; if they disagree by more than SEAM_MAX_STEP (the unscaled
        PriceCharting -> TCGplayer seam, or PriceCharting's November 2025 break
        behind it), the change is measured from that new-source start when it
        lies in the window's first half, and otherwise there is no number;
      - a series that jumps by more than STEP_BREAK and stays there (broken())
        has no number for the window.
    """
    latest_d, latest_p, latest_s = pts[-1]
    last = len(pts) - 1
    launch_end = (date.fromisoformat(pts[0][0][:10])
                  + timedelta(days=LAUNCH_SKIP_DAYS)).isoformat()
    if released:
        launch_end = max(launch_end, (date.fromisoformat(released[:10])
                                      + timedelta(days=RELEASE_SETTLE_DAYS)).isoformat())
    launch = cutoff < launch_end
    if launch:
        i = bisect.bisect_left(dates, launch_end)
        if i >= last:
            return None
    else:
        i = bisect.bisect_right(dates, cutoff) - 1
    if pts[i][2] != latest_s:
        j = next(k for k in range(i + 1, len(pts)) if pts[k][2] == latest_s)
        head = [pts[k][1] for k in range(j, min(j + 3, last))]
        if not head:
            return None
        start = statistics.median(head)
        prev = pts[j - 1][1]
        step = start / prev if prev > 0 else float("inf")
        if not 1 / SEAM_MAX_STEP <= step <= SEAM_MAX_STEP:
            c = date.fromisoformat(cutoff)
            if (pts[j][0] > (c + (today - c) / 2).isoformat() or start <= 0
                    or broken([(pts[j][0], start, latest_s)] + pts[j + 1:], 0)):
                return None
            return (latest_p / start - 1) * 100, latest_p - start
    anchor = pts[i][1]
    if not launch and 0 < i < last - 1 and pts[i - 1][2] == pts[i][2] == pts[i + 1][2]:
        anchor = sorted((pts[i - 1][1], anchor, pts[i + 1][1]))[1]
    # The break scan starts from the anchor as read, so a stray anchor point
    # that the median replaced does not count as a step of its own.
    if anchor <= 0 or broken([(pts[i][0], anchor, pts[i][2])] + pts[i + 1:], 0):
        return None
    return (latest_p / anchor - 1) * 100, latest_p - anchor


def build(pc_db, out_db, today):
    t0 = time.time()
    rel = release_dates()
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
    pts = []

    def flush():
        nonlocal rows_out
        if cur_key is None or not pts:
            return
        dates = [p[0] for p in pts]
        released = rel.get((cur_key[0], cur_key[2]))
        vals = [*cur_key, pts[-1][0], pts[-1][1]]
        for _w, c in cut_list:
            ch = window_change(pts, dates, c, today, released)
            vals += list(ch) if ch else [None, None]
        vals.append(today.isoformat())
        buf.append(vals)
        rows_out += 1
        if len(buf) >= 5000:
            out.executemany("INSERT INTO card_trends_new VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf.clear()

    # The index (game, grade, product_id, date, price) orders the walk; rows
    # arrive date-ascending within a series (the printing filter and the
    # source already read each row, so selecting source costs nothing more).
    q = ("SELECT game, grade, product_id, date, price, source FROM price_history_unified "
         "WHERE printing='' ORDER BY game, grade, product_id, date")
    for game, grade, pid, d, price, source in src.execute(q):
        rows_in += 1
        key = (game, grade, pid)
        if key != cur_key:
            flush()
            cur_key, pts = key, []
        pts.append((d, price, source or ""))
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
