#!/usr/bin/env python3
"""
Second-pass NM pricing: rescue cards the search crawl can't price.

TCGplayer's search API only carries a marketPrice while a card has active
listings — illiquid cards (event promos, trophies, vintage) drop off it, and
their nightly NM series goes stale even though the product page's sales-based
chart still shows a market price (e.g. Chopper Treasure Cup 2024: no search
price, chart steady at $1,265). This pass finds priced, visible cards whose
NM series stopped advancing, reads the chart (detailed endpoint), and if it
carries a RECENT Near Mint series, adopts the card as tcg_nm_only: full weekly
series written, search crawl skips it, nightly tcg-nm-backfill keeps it fresh.
Cards whose chart is empty or itself stale are left alone — there is no price
to show, and the forecast's stale-anchor gate keeps them off the movers pages.

Nightly (default): only cards that went stale RECENTLY (last NM point 3-45
days old) — a small, fast set that catches new search-pricing failures within
days. Weekly --all (Sunday): every stale visible card, including ones with no
NM series at all, so nothing stays lost forever.

Run:  .venv/bin/python rescue_stale_nm.py            # nightly narrow window
      .venv/bin/python rescue_stale_nm.py --all      # Sunday full sweep
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone


def _utc_today():
    # UTC day basis (2026-08-14): cutoffs must share the batch labels' clock.
    return datetime.now(timezone.utc).date()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import priced_games, db_path
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import (nm_series, record_nm_only, freshen_current_week,
                                     table_market_prices, chart_agrees_with_table,
                                     TCG_NM_ONLY_CSV, PC_DB)

FRESH_DAYS = 3      # a point newer than this = the crawl is pricing it; skip
STALE_MAX = 45      # nightly window: went stale within this long (else Sunday's job)
CHART_RECENT = 45   # adopt only if the chart's newest point is at most this old


PINS_CSV = os.path.join(BASE, "ml_data", "tcg_printing_pins.csv")
NM_BLOCKLIST_CSV = os.path.join(BASE, "ml_data", "tcg_nm_blocklist.csv")


def nm_only_pids():
    """Cards rescue must never touch: tcg_nm_only (the backfill owns them),
    printing-PINNED cards (pin_printings owns their base series; their weekly
    bucket dates always look 'stale' here, and re-seeding would re-pick the
    wrong printing — that exact bug poisoned 314 pins on 2026-08-08), and
    NM-BLOCKLISTED cards (their TCGplayer price is bogus; PC owns ungraded —
    a rescue adoption would re-import the bad listing)."""
    out = set()
    for path in (TCG_NM_ONLY_CSV, PINS_CSV, NM_BLOCKLIST_CSV):
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                out |= {(r["game"], int(r["product_id"]))
                        for r in csv.DictReader(f) if r.get("product_id")}
    return out


def candidates(conn, game, all_mode):
    """Visible priced cards whose NM series stopped advancing (or never began,
    --all only), excluding tcg_nm_only (the backfill already owns those)."""
    fresh_cut = (_utc_today() - timedelta(days=FRESH_DAYS)).isoformat()
    stale_cut = (_utc_today() - timedelta(days=STALE_MAX)).isoformat()
    last = dict(conn.execute(
        "SELECT product_id, MAX(date) FROM tcg_nm_history WHERE game=? AND printing='' "
        "GROUP BY product_id", (game,)))
    cc = sqlite3.connect(db_path(game), timeout=60)
    visible = [pid for (pid,) in cc.execute(
        "SELECT product_id FROM cards "
        "WHERE near_mint_price IS NOT NULL AND image_path IS NOT NULL")]
    cc.close()
    skip = nm_only_pids()
    out = []
    for pid in visible:
        if (game, pid) in skip:
            continue
        d = last.get(pid)
        if d is None:
            if all_mode:                       # never search-priced: Sunday only
                out.append(pid)
        elif d < fresh_cut and (all_mode or d >= stale_cut):
            out.append(pid)
    return out


def main():
    ap = argparse.ArgumentParser(description="Second-pass NM pricing from the sales chart")
    ap.add_argument("--all", action="store_true",
                    help="full sweep incl. long-stale and never-priced cards (Sunday)")
    ap.add_argument("--max-probes", type=int, default=500,
                    help="bound the chart fetches per run")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--rpm", type=float, default=40)
    args = ap.parse_args()

    recent_cut = (_utc_today() - timedelta(days=CHART_RECENT)).isoformat()
    conn = sqlite3.connect(PC_DB, timeout=120)
    todo = [(g, pid) for g in priced_games()
            for pid in candidates(conn, g, args.all)]
    if len(todo) > args.max_probes:
        print(f"{len(todo)} stale candidates; probing {args.max_probes} this run "
              f"(rest next run)")
        todo = todo[:args.max_probes]
    if not todo:
        conn.close()
        print("no stale candidates — every visible card priced recently")
        return

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    sess = tp.make_session()
    adopted = left = gated = 0
    for g, pid in todo:
        pts = nm_series(sess, pid, args.delay)
        pts = freshen_current_week(pts)
        if pts and pts[0][0] >= recent_cut:
            # Table-first gate (user policy 2026-08-18): the chart is only
            # trusted when the product page's price TABLE carries a market
            # price the chart agrees with — a contradicting chart (NM $22 vs
            # table $145) is a thin condition sub-series, not the market.
            if not chart_agrees_with_table(
                    pts, table_market_prices(sess, pid, args.delay)):
                gated += 1
                continue
            conn.executemany(
                "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, date, price) "
                "VALUES (?,?,?,?)", [(g, pid, d, p) for d, p in pts])
            conn.commit()
            record_nm_only(g, pid, "search NM dropped off; chart sales series (auto-rescue)")
            adopted += 1
        else:
            left += 1
    conn.close()
    print(f"probed {len(todo)}: {adopted} adopted from the chart, "
          f"{gated} gated (chart disagrees with table / no table market price), "
          f"{left} without recent chart data (left stale)")


if __name__ == "__main__":
    # Non-fatal: a rescue hiccup must never fail the nightly.
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"rescue_stale_nm: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
    sys.exit(0)
