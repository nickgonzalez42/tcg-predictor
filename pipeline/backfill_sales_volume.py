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
import collections
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import db_path, market_games
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import SALES_SCHEMA, record_sales

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
# Cards tomorrow's articles will discuss, written by game_articles.write_watch
# at the end of the nightly; fetched first (2026-11-07, user: "prioritize
# scraping its volume movement the day before").
WATCH_FILE = os.path.join(BASE, "ml_data", "article_watch.json")
# The home page's showcase picks (pick_showcase.py, 2026-11-09) hand their
# candidates over the same way when this crawler holds the lock and is
# crawling (while it sits out a refresh, pick_showcase fetches them itself,
# 2026-11-13); both lists go to the front, articles first. A hand-off says
# until when it is waited on and is skipped after that.
WATCH_FILES = [WATCH_FILE, os.path.join(BASE, "ml_data", "showcase_watch.json")]
WATCH_CHECK = 20     # cards between looks at the watch files (~30 s at 40 rpm; pick_showcase waits on them)
BLOCK_LIMIT = 25     # consecutive failed fetches before a cool-off
LOCK_RETRIES = 5     # write attempts when another connection holds the database
LOCK_WAIT_S = 10     # pause between those attempts (on top of the busy timeout)
COOL_OFF_S = 1800    # breaker trip: rest, then resume (transient outages heal)
MAX_TRIPS = 4        # consecutive cool-offs with no success between: give up


REFRESH_PROCS = "run_daily_refresh.sh|weekly_refresh.py"   # pgrep -f pattern


def refresh_pids():
    """pids of the nightly refresh's processes. pick_showcase looks for its
    own parent among them: the refresh this crawler waits out."""
    r = subprocess.run(["pgrep", "-f", REFRESH_PROCS], capture_output=True, text=True)
    return {int(p) for p in r.stdout.split() if p.isdigit()}


def refresh_live():
    """True while the nightly refresh is running. Long unattended runs pause
    then: the refresh crawls TCGplayer at the same politeness budget, and two
    crawls at once would double it — the exact mistake that got the
    PriceCharting IP blocked for two days (2026-10-12)."""
    return bool(refresh_pids())


def watch_targets(conn, games):
    """[(game, pid, 0.0, True)] for the watch lists (articles, then the
    showcase picks), in their order, minus cards already fetched since each
    list was made (the hourly relaunches must not refetch them) and minus a
    list whose day has passed, or whose wait has: past a hand-off's "until"
    pick_showcase has judged those cards on what is stored, and a list its
    run left behind must not send this crawl after them hours later. Empty
    when there is no list."""
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    for path in WATCH_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                w = json.load(f)
        except (OSError, ValueError):
            continue
        made = w.get("made") or ""
        if (w.get("for") or "") < today:
            continue
        until = str(w.get("until") or "")
        if until and until < now.isoformat(timespec="seconds"):
            continue
        cards = [c for c in w.get("cards", []) if c.get("game") in games]
        fetched = {}
        for g in {c["game"] for c in cards}:
            fetched.update({(g, pid): f or "" for pid, f in conn.execute(
                "SELECT product_id, fetched_at FROM tcg_sales_scan WHERE game=?", (g,))})
        n = 0
        for c in cards:
            key = (c["game"], int(c["pid"]))
            if key in seen or fetched.get(key, "") >= made:
                continue
            seen.add(key)
            out.append((c["game"], int(c["pid"]), 0.0, True))
            n += 1
        if n:
            print(f"watch list: {n} card(s) from {os.path.basename(path)} (for {w.get('for')}) go first",
                  flush=True)
    return out


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

    games = market_games() if args.game == "all" else [args.game]   # 2026-11-04: CSV-only games too
    # 600 s busy timeout (2026-11-02, was 60): the graded crawl writes to this
    # database all day and a transaction of its could outlast a minute.
    conn = sqlite3.connect(PC_DB, timeout=600)
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

    watch = watch_targets(conn, set(games))
    watch_seen = {(g, p) for g, p, _m, _e in watch}
    queue = collections.deque(watch + [t for t in targets if (t[0], t[1]) not in watch_seen])
    total = len(queue)
    def watch_stamp():
        """The watch files' modification times, so a new or rewritten list
        in either file is noticed."""
        out = []
        for path in WATCH_FILES:
            try:
                out.append(os.path.getmtime(path))
            except OSError:
                out.append(None)
        return tuple(out)
    watch_mtime = watch_stamp()

    def splice_watch():
        """A new or changed watch list goes to the front of the queue. A run
        spans days (the 10:00 launch just exits while one is going), and the
        nightly writes the list at ~07:00, usually while this run is paused
        for the refresh or hours into its list; without this the list would
        wait for whichever run happened to start next."""
        nonlocal watch_mtime, total
        m = watch_stamp()
        if m == watch_mtime:
            return
        watch_mtime = m
        fresh = [t for t in watch_targets(conn, set(games)) if (t[0], t[1]) not in watch_seen]
        for t in reversed(fresh):
            queue.appendleft(t)
        watch_seen.update((t[0], t[1]) for t in fresh)
        total += len(fresh)

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    session = tp.make_session()
    ok = empty = fail = rows_total = 0
    streak = trips = 0
    skip = set()
    i = 0
    while queue:
        game, pid, _mp, _ever = queue.popleft()
        i += 1
        if i % WATCH_CHECK == 0:
            splice_watch()
        if (game, pid) in skip:
            continue
        # Yield to the nightly refresh (checked on the first card and every
        # 10 after, ~15 s at 40rpm, so a refresh that has just started is
        # noticed before more than a commit or two can meet its crawl on
        # pricecharting.db, and a run launched while one is live fetches
        # nothing: pick_showcase may be fetching then, 2026-11-13). After
        # a pause, drop targets the refresh's own tcg-sales step fetched.
        if not args.no_yield and (i == 1 or i % 10 == 0) and refresh_live():
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
            splice_watch()
        try:
            h = tp.fetch_price_history(session, pid, args.delay)
        except Exception as e:
            fail += 1
            streak += 1
            if streak >= BLOCK_LIMIT:
                trips += 1
                if trips >= MAX_TRIPS:
                    print(f"{MAX_TRIPS} cool-offs with no success between — giving up; "
                          f"{len(queue):,} stay due for the next run.", flush=True)
                    break
                print(f"{BLOCK_LIMIT} consecutive failures ({type(e).__name__}) — "
                      f"cooling off {COOL_OFF_S // 60} min "
                      f"(trip {trips}/{MAX_TRIPS})", flush=True)
                conn.commit()
                time.sleep(COOL_OFF_S)
                streak = 0
            continue
        streak = trips = 0
        # Write + commit per card, retrying a lock (2026-11-02): the graded
        # crawl shares this database all day, and a write that met its open
        # transaction used to wait 60 s, raise "database is locked" and kill
        # the process — 189 crash-relaunches between 10-29 and 11-02, each
        # discarding up to 19 fetched-but-uncommitted cards. Now the busy
        # wait is retried, and our own transaction never spans a fetch.
        n = None
        for attempt in range(1, LOCK_RETRIES + 1):
            try:
                n = record_sales(conn, game, pid, h)
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == LOCK_RETRIES:
                    raise
                conn.rollback()
                print(f"  database locked on write (attempt {attempt}/{LOCK_RETRIES}) — "
                      f"retrying in {LOCK_WAIT_S}s", flush=True)
                time.sleep(LOCK_WAIT_S)
        rows_total += n
        ok += 1
        if n == 0:
            empty += 1          # scanned + marked; no sales buckets to keep
        if i % 500 == 0:
            print(f"  {i}/{total} ok={ok} empty={empty} fail={fail} "
                  f"rows={rows_total} ({datetime.now(timezone.utc):%m-%d %H:%M} UTC)",
                  flush=True)
    conn.commit()
    conn.close()
    print(f"DONE: ok={ok} (empty={empty}) fail={fail} bucket rows={rows_total}",
          flush=True)


if __name__ == "__main__":
    main()
