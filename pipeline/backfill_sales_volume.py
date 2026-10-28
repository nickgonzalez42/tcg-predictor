#!/usr/bin/env python3
"""
Record TCGplayer sales volume for the WHOLE catalog (2026-10-14, user request).

TCGplayer's product-page chart shows "items sold" per bucket; the detailed
price-history endpoint behind it returns ~a year of buckets per card, with
quantitySold/transactionCount per variant and condition. The nm_only cohort
already captures this via backfill_tcg_nm_history (record_sales); this script
is the rotation that walks every OTHER card, value-first, on a nightly budget.
The first touch of a card lands its ~year of history, so the rotation IS the
backfill; after the first full pass each card gets re-touched once its last
fetch is older than --refresh-days, keeping the series current.

Same polite transport as every TCGplayer crawl here (shared RateLimiter and
retrying session), plus the house circuit breaker: hard refusals in a row
stop the run instead of burning the night.

Run:  .venv/bin/python backfill_sales_volume.py --game all --rpm 40 \
          --refresh-days 30 --total-limit 4000
"""

import argparse
import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import db_path, priced_games
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import SALES_SCHEMA, record_sales

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
BLOCK_LIMIT = 25     # consecutive failed fetches before a cool-off
COOL_OFF_S = 1800    # breaker trip: rest, then resume (transient outages heal)
MAX_TRIPS = 4        # consecutive cool-offs with no success between: give up


def refresh_live():
    """True while the nightly refresh is running. Long unattended runs pause
    then: the refresh crawls TCGplayer at the same politeness budget, and two
    crawls at once would double it — the exact mistake that got the
    PriceCharting IP blocked for two days (2026-10-12)."""
    r = subprocess.run(["pgrep", "-f", "run_daily_refresh.sh|weekly_refresh.py"],
                       capture_output=True)
    return r.returncode == 0


def gather(game, conn, refresh_days, limit, fast_price=None, fast_days=None,
           new_only=False):
    """(game, product_id, nm_price) due for a fetch — value-first.

    Value comes from tcg_nm_history (uniform across games) rather than the
    cards DBs, whose price columns differ by game. SQLite's bare-column rule
    makes MAX(date) pick each card's newest price row.

    Two cadences (2026-10-28, user: every card weekly is ~16 h of crawling a
    day, so the cards people actually look at get the weekly touch): a card
    priced at or above --fast-tier-price is due again after --fast-refresh-days,
    everything else after --refresh-days."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=refresh_days)).isoformat()
    fast_cutoff = ((now - timedelta(days=fast_days)).isoformat()
                   if fast_price is not None and fast_days else None)
    price = {pid: p for pid, p, _d in conn.execute(
        "SELECT product_id, price, MAX(date) FROM tcg_nm_history "
        "WHERE game=? GROUP BY product_id", (game,))}
    done = set()
    for pid, fetched in conn.execute(
            "SELECT product_id, fetched_at FROM tcg_sales_scan WHERE game=?", (game,)):
        if not fetched:
            continue
        due_after = (fast_cutoff if fast_cutoff and (price.get(pid) or 0) >= fast_price
                     else cutoff)
        if fetched > due_after:
            done.add(pid)
    ever = set(r[0] for r in conn.execute(
        "SELECT product_id FROM tcg_sales_scan WHERE game=?", (game,)))
    # Backfill phase (2026-10-28, user: "go through every card first"): a card
    # that has EVER been scanned is done — no card is fetched twice until the
    # whole catalog has been covered once. The tiered refresh waits.
    if new_only:
        done = set(ever)
    cards = sqlite3.connect(db_path(game))
    pids = [r[0] for r in cards.execute("SELECT product_id FROM cards")]
    cards.close()
    # 4th field: was this card ever scanned? A card with NO data yet always
    # outranks a stale refresh of one that already has a year of weekly
    # buckets (2026-10-28) — during the backfill, first coverage beats a
    # slightly fresher re-fetch. Once every card has been scanned once, the
    # flag is uniformly True and ordering falls back to pure value-first,
    # i.e. the weekly/monthly maintenance the tiers describe.
    targets = [(game, pid, price.get(pid) or 0.0, pid in ever)
               for pid in pids if pid not in done]
    targets.sort(key=lambda t: (t[3], -t[2]))
    if limit:
        targets = targets[:limit]
    fresh_new = sum(1 for t in targets if not t[3])
    print(f"[{game}] {len(targets)} due ({fresh_new:,} never scanned) | "
          f"{len(done)} fresh", flush=True)
    return targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all")
    ap.add_argument("--rpm", type=float, default=40.0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--refresh-days", type=float, default=30.0)
    ap.add_argument("--fast-tier-price", type=float, default=None,
                    help="cards at or above this NM price re-fetch after "
                         "--fast-refresh-days instead of --refresh-days")
    ap.add_argument("--fast-refresh-days", type=float, default=7.0)
    ap.add_argument("--new-only", action="store_true",
                    help="backfill: only cards never scanned; skip all refreshes "
                         "until the whole catalog has been covered once")
    ap.add_argument("--limit", type=int, default=None, help="cap per game")
    ap.add_argument("--total-limit", type=int, default=None,
                    help="cap the whole run; games compete value-first above "
                         "a per-game floor so cheap games keep rotating")
    ap.add_argument("--no-yield", action="store_true",
                    help="skip the yield-to-refresh pause. REQUIRED when this "
                         "script runs AS a refresh step: the guard pgreps for "
                         "weekly_refresh and would find its own parent and "
                         "sleep forever (2026-10-16: the first in-refresh run "
                         "deadlocked exactly this way for 5 hours)")
    args = ap.parse_args()

    # One crawl at a time (2026-10-28): the daytime launchd agent relaunches
    # this hourly; a run that finds the lock held just leaves quietly.
    lock_path = os.path.join(BASE, ".sales_volume.lock")
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another sales crawl is running — exiting", flush=True)
        return

    games = priced_games() if args.game == "all" else [args.game]
    conn = sqlite3.connect(PC_DB, timeout=60)
    # A 50-card batch (~13k bucket rows) spilled SQLite's 2 MB page cache,
    # which escalates the transaction to an EXCLUSIVE lock for the rest of
    # the batch — readers saw "database is locked" ~90% of the time
    # (2026-10-28). A bigger cache keeps the writer on a RESERVED lock,
    # and smaller batches shorten what readers ever wait for.
    conn.execute("PRAGMA cache_size=-65536")     # 64 MB
    conn.executescript(SALES_SCHEMA)

    targets = []
    for game in games:
        targets.extend(gather(game, conn, args.refresh_days, args.limit,
                              args.fast_tier_price, args.fast_refresh_days,
                              args.new_only))
    # Global order (no per-game budget): every never-scanned card, value-first,
    # before any refresh — so coverage of the whole catalog finishes before a
    # single card is fetched twice.
    if not args.total_limit:
        targets.sort(key=lambda t: (t[3], -t[2]))
    if args.total_limit and len(targets) > args.total_limit:
        # Same per-game floor as the graded rotation (2026-09-29 lesson):
        # pure global value ranking starves the cheap games entirely.
        floor = max(1, args.total_limit // (4 * max(1, len(games))))
        by_game = {}
        for t in targets:
            by_game.setdefault(t[0], []).append(t)
        keep, rest = [], []
        for ts in by_game.values():
            keep.extend(ts[:floor])
            rest.extend(ts[floor:])
        rest.sort(key=lambda t: (t[3], -t[2]))
        print(f"total budget: {args.total_limit} of {len(targets)} due "
              f"(per-game floor {floor}, remainder by price)", flush=True)
        targets = keep + rest[:max(0, args.total_limit - len(keep))]

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    session = tp.make_session()
    ok = empty = fail = rows_total = 0
    streak = trips = 0
    skip = set()
    for i, (game, pid, _mp, _ever) in enumerate(targets, 1):
        if (game, pid) in skip:
            continue
        # Yield to the nightly refresh (checked every 10 cards, ~15 s at
        # 40rpm, so a refresh that has just started is noticed before more
        # than a commit or two can meet its crawl on pricecharting.db). After
        # a pause, drop targets the refresh's own tcg-sales step fetched.
        if not args.no_yield and i % 10 == 0 and refresh_live():
            print(f"  refresh is live — pausing ({datetime.now(timezone.utc):%H:%M} UTC)",
                  flush=True)
            conn.commit()
            while refresh_live():
                time.sleep(600)
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=args.refresh_days)).isoformat()
            skip = set((g, p) for g, p in conn.execute(
                "SELECT game, product_id FROM tcg_sales_scan WHERE fetched_at > ?",
                (cutoff,)))
            print(f"  refresh finished — resuming ({len(skip):,} now fresh)", flush=True)
        try:
            h = tp.fetch_price_history(session, pid, args.delay)
        except Exception as e:
            fail += 1
            streak += 1
            if streak >= BLOCK_LIMIT:
                trips += 1
                if trips >= MAX_TRIPS:
                    print(f"{MAX_TRIPS} cool-offs with no success between — giving up; "
                          f"{len(targets) - i:,} stay due for the next run.", flush=True)
                    break
                print(f"{BLOCK_LIMIT} consecutive failures ({type(e).__name__}) — "
                      f"cooling off {COOL_OFF_S // 60} min "
                      f"(trip {trips}/{MAX_TRIPS})", flush=True)
                conn.commit()
                time.sleep(COOL_OFF_S)
                streak = 0
            continue
        streak = trips = 0
        n = record_sales(conn, game, pid, h)
        rows_total += n
        ok += 1
        if n == 0:
            empty += 1          # scanned + marked; no sales buckets to keep
        if ok % 20 == 0:
            conn.commit()
        if i % 500 == 0:
            print(f"  {i}/{len(targets)} ok={ok} empty={empty} fail={fail} "
                  f"rows={rows_total} ({datetime.now(timezone.utc):%m-%d %H:%M} UTC)",
                  flush=True)
    conn.commit()
    conn.close()
    print(f"DONE: ok={ok} (empty={empty}) fail={fail} bucket rows={rows_total}",
          flush=True)


if __name__ == "__main__":
    main()
