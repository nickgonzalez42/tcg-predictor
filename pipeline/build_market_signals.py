#!/usr/bin/env python3
"""
Monthly sales-volume and listing-supply signals for the forecast model
(2026-11-01, user request: feed sales volume and supply to the model, for
every card and every printing).

Writes two CSVs per game into ml_data/, in the shape forecast_deep's signal
loader already understands, plus a `printing` column so a signal can land on
ONE printing's row rather than every row of the card:

  {game}_volume_signals.csv   product_id, printing, month,
                              vol_units   log1p(Near Mint units sold PER WEEK that month)
                              vol_txn     log1p(separate sales per week)
                              vol_all     log1p(units per week across every condition)
                              vol_chg3    vol_units now minus three months ago

Per week, not per month: the month the model is forecasting from is usually
still in progress (one to four closed weeks), and a raw monthly total would
read as a volume collapse every time. Dividing by the weeks actually observed
in that month keeps the current month on the same scale as the training
months (which also vary between four and five weeks).
  {game}_supply_signals.csv   product_id, printing (always base), month,
                              sup_list    log1p(mean listings on offer that month)
                              sup_fill    log1p(mean fulfillable quantity)
                              sup_ask     mean lowest ask / market price (>1 = asks
                                          sitting above the market, thin supply)

Volume comes from tcg_sales_history (the daytime crawl: ~13 months of weekly
buckets per card and variant, zero weeks included). Two kinds of partial
week are dropped: the week open today, and the week that was open when the
card was last scanned (its bucket stays frozen partial until the next visit,
tcg_sales_scan.fetched_at says when that was). So every week counted is a
complete one. A sales variant is matched to the model's printing rows by
name (the two tables use the same names) and the card's BASE row ('') takes
the variant tcg_csv_base says is its base.

Supply comes from tcg_depth_history (the nightly price step): listings and
fulfillable quantity per card per day since 2026-10-16. It is per card, not
per printing, so it lands on the base row.

Months are calendar months, the model's own bucket. Nothing here is gated:
the files are cheap to build and the model decides at load time (flags
TCG_FC_FEAT_VOL / TCG_FC_FEAT_SUPPLY in forecast_deep) whether to read them.
"""

import argparse
import collections
import datetime
import math
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, market_games

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
DATA = os.path.join(BASE, "ml_data")


def this_monday(today=None):
    today = today or datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def base_variant(conn, game):
    """product_id -> the sales variant that is this card's base ('') row."""
    return {pid: pr for pid, pr in conn.execute(
        "SELECT product_id, printing FROM tcg_csv_base WHERE game=?", (game,))}


def scan_cutoff(conn, game):
    """product_id -> first bucket_date (ISO Monday) that was still open when
    the card was last scanned; buckets on or after it are partial."""
    out = {}
    for pid, fetched in conn.execute(
            "SELECT product_id, fetched_at FROM tcg_sales_scan WHERE game=?", (game,)):
        try:
            d = datetime.date.fromisoformat(str(fetched)[:10])
        except ValueError:
            continue
        out[pid] = this_monday(d).isoformat()
    return out


def volume_signals(conn, game, open_week):
    base = base_variant(conn, game)
    cutoff = scan_cutoff(conn, game)
    units = collections.defaultdict(float)   # (pid, variant, month) -> NM units
    txn = collections.defaultdict(float)
    allc = collections.defaultdict(float)    # every condition
    weeks = collections.defaultdict(set)     # (pid, variant, month) -> closed weeks seen
    for pid, variant, cond, d, q, t in conn.execute(
            "SELECT product_id, variant, condition, bucket_date, quantity_sold, transaction_count "
            "FROM tcg_sales_history WHERE game=? AND bucket_date < ?", (game, open_week.isoformat())):
        if d >= cutoff.get(pid, "9999"):
            continue      # frozen partial week from the last scan
        m = d[:7]
        q = q or 0.0
        allc[(pid, variant, m)] += q
        if cond == "Near Mint":
            units[(pid, variant, m)] += q
            txn[(pid, variant, m)] += (t or 0.0)
            weeks[(pid, variant, m)].add(d)
    # The model's row for a variant is the printing of the same name, except
    # the base variant, which is the '' row.
    rows = []
    months_by = collections.defaultdict(dict)
    for key, q in units.items():
        pid, variant, m = key
        n = len(weeks[key]) or 1
        pr = "" if base.get(pid, "Normal") == variant else variant
        months_by[(pid, pr)][m] = (q / n, txn[key] / n, allc[key] / n)
    for (pid, pr), by_m in months_by.items():
        ms = sorted(by_m)
        for i, m in enumerate(ms):
            q, t, a = by_m[m]
            v = math.log1p(q)
            # three months back by index over the months this card has; the
            # series is contiguous (zero weeks are explicit rows).
            back = ms[i - 3] if i >= 3 else None
            chg = (v - math.log1p(by_m[back][0])) if back else float("nan")
            rows.append((pid, pr, m, round(v, 4), round(math.log1p(t), 4),
                         round(math.log1p(a), 4), round(chg, 4) if back else ""))
    return pd.DataFrame(rows, columns=["product_id", "printing", "month",
                                       "vol_units", "vol_txn", "vol_all", "vol_chg3"])


def supply_signals(conn, game):
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0])   # (pid, month) -> sums + counts
    for pid, d, tl, mf, low, mkt in conn.execute(
            "SELECT product_id, date, total_listings, max_fulfillable, lowest_price, market_price "
            "FROM tcg_depth_history WHERE game=?", (game,)):
        a = acc[(pid, d[:7])]
        if tl is not None:
            a[0] += math.log1p(tl); a[3] += 1
        if mf is not None:
            a[1] += math.log1p(mf)
        if low and mkt and mkt > 0:
            a[2] += low / mkt; a[4] += 1
    rows = []
    for (pid, m), (sl, sf, sa, n, na) in acc.items():
        if not n:
            continue
        rows.append((pid, "", m, round(sl / n, 4), round(sf / n, 4),
                     round(sa / na, 4) if na else ""))
    return pd.DataFrame(rows, columns=["product_id", "printing", "month",
                                       "sup_list", "sup_fill", "sup_ask"])


def main():
    ap = argparse.ArgumentParser(description="Build monthly volume + supply signals")
    ap.add_argument("--game", action="append", help="one game (repeatable); default every priced game")
    args = ap.parse_args()
    conn = sqlite3.connect(PC_DB, timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    open_week = this_monday()
    games = args.game or market_games()
    for game in games:
        if game not in GAMES:
            print(f"[{game}] unknown game, skipped")
            continue
        vol = volume_signals(conn, game, open_week)
        sup = supply_signals(conn, game)
        vol.to_csv(os.path.join(DATA, f"{game}_volume_signals.csv"), index=False)
        sup.to_csv(os.path.join(DATA, f"{game}_supply_signals.csv"), index=False)
        print(f"[{game}] volume {len(vol):,} rows / {vol['product_id'].nunique():,} cards | "
              f"supply {len(sup):,} rows / {sup['product_id'].nunique():,} cards", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
