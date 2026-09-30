#!/usr/bin/env python3
"""
Weekly re-check of cards deferred for missing TCGplayer market data (2026-08-18).

Cards land in ml_data/tcg_market_watch.csv when their chart could NOT be
trusted at review time: the product page's price TABLE had no market price,
or the chart contradicted it (Retro Pack Dark Magician: NM chart $22 vs
table/search $145), or the chart was a flat echo of a frozen listing (Soul
Stone: 52 weeks of $32,999.99). Policy (user): if there is no market price,
don't use the chart yet — keep the search-crawl price on display and re-check
weekly until TCGplayer's market price comes back to life.

A watched card graduates (chart imported, tcg_nm_only) when ALL hold:
  1. the TABLE carries a market price,
  2. the chart's newest NM point agrees with it (+/-25%), and
  3. the chart MOVED (>= 2 distinct prices in the last 13 weeks) — watched
     cards are search-priced, so a flat chart adds no information, only fake
     history depth; movement is what proves sales are happening again.

Run:  .venv/bin/python tcg_market_watch.py          # Sunday, via weekly_refresh
"""

import csv
import os
import sqlite3
import sys
from datetime import date, timedelta, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import (nm_series, record_nm_only, freshen_current_week,
                                     table_market_prices, chart_agrees_with_table,
                                     PC_DB)

WATCH_CSV = os.path.join(BASE, "ml_data", "tcg_market_watch.csv")
MOVE_WEEKS = 13


def load_watch():
    if not os.path.exists(WATCH_CSV):
        return []
    with open(WATCH_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_watch(rows):
    with open(WATCH_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["game", "product_id", "added", "note"])
        for r in rows:
            w.writerow([r["game"], r["product_id"], r["added"], r.get("note", "")])


def main():
    watch = load_watch()
    # Blocklisted cards (PC owns ungraded) must never graduate to a TCG chart
    # import — drop them from the watch entirely (2026-08-19: 18 no-TCG-data
    # cards moved from watch to blocklist).
    bl_csv = os.path.join(BASE, "ml_data", "tcg_nm_blocklist.csv")
    if os.path.exists(bl_csv):
        with open(bl_csv, newline="", encoding="utf-8") as f:
            blocked = {(r["game"], r["product_id"]) for r in csv.DictReader(f)}
        dropped = [r for r in watch if (r["game"], r["product_id"]) in blocked]
        if dropped:
            watch = [r for r in watch if (r["game"], r["product_id"]) not in blocked]
            save_watch(watch)
            print(f"market watch: dropped {len(dropped)} blocklisted card(s)")
    if not watch:
        print("market watch: list empty")
        return
    tp.RATE_LIMITER = tp.RateLimiter(rpm=40, min_interval=1.0)
    sess = tp.make_session()
    move_cut = (date.today() - timedelta(weeks=MOVE_WEEKS)).isoformat()
    conn = sqlite3.connect(PC_DB, timeout=120)
    keep, graduated = [], 0
    for r in watch:
        g, pid = r["game"], int(r["product_id"])
        table = table_market_prices(sess, pid, 1.0)
        pts = freshen_current_week(nm_series(sess, pid, 1.0)) if table else []
        moves = len({p for d, p in pts if d >= move_cut})
        if table and moves >= 2 and chart_agrees_with_table(pts, table):
            conn.executemany(
                "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, date, price) "
                "VALUES (?,?,?,?)", [(g, pid, d, p) for d, p in pts])
            conn.commit()
            record_nm_only(g, pid, "market watch: table price live + chart moving again")
            graduated += 1
            print(f"  graduated {g}/{pid}: table={list(table.values())} "
                  f"chart newest {pts[0]}")
        else:
            keep.append(r)
    conn.close()
    save_watch(keep)
    print(f"market watch: {len(watch)} checked, {graduated} graduated, "
          f"{len(keep)} still waiting for market data")


if __name__ == "__main__":
    # Non-fatal: a watch hiccup must never fail the nightly.
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"tcg_market_watch: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
    sys.exit(0)
