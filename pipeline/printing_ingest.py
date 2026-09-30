#!/usr/bin/env python3
"""
Ingest ALL of a product's printing variants (Phase 1 of multi-printing
support, plan at tcg-predictor/notes/printing-plan.md).

One detailed-endpoint request per product returns a weekly NM series PER
variant (Normal / Foil / Reverse Holofoil / 1st Edition / ...). The base
variant — the one our existing single series represents — keeps flowing
under printing='' exactly as before; every OTHER variant's series is stored
labeled (printing='<variant>') in tcg_nm_history. Nothing downstream reads
labeled rows yet (unified emits base-only until Phase 3), so this step is
invisible to the site and model while the data accrues.

Base-variant rule (recorded in printing_variants.is_base):
  1. the card's pinned variant (tcg_printing_pins.csv), else
  2. the variant whose latest price is closest (log-distance) to the card's
     latest stored base series price, else
  3. the variant with the most weekly points.

State: printing_variants (pricecharting.db) — one row per (game, product_id,
variant) with sku/prices/points + checked_at driving the rotation. The card
DBs' cards.printings/base_printing columns are a DERIVED cache stamped here
(survives Sunday catalog rescrapes by being re-stamped nightly).

Nightly:  printing_ingest.py --rotate --refresh-days 30 --limit 2300 --rpm 40
Targeted: printing_ingest.py --pids pokemon:83466 ...

Always exits 0 — ingestion must never fail the nightly.
"""

import argparse
import collections
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import db_path, priced_games
import tcg_pokemon_scraper as tp
from pin_printings import load_pins, nm_variant_series

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS printing_variants (
            game         TEXT    NOT NULL,
            product_id   INTEGER NOT NULL,
            variant      TEXT    NOT NULL,
            sku_id       TEXT,
            latest_price REAL,
            points       INTEGER,
            is_base      INTEGER NOT NULL DEFAULT 0,
            checked_at   TEXT,
            PRIMARY KEY (game, product_id, variant));
        """)
    conn.commit()


def ensure_card_columns(game):
    con = sqlite3.connect(db_path(game), timeout=60)
    cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
    for col, typ in (("printings", "TEXT"), ("base_printing", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE cards ADD COLUMN {col} {typ}")
    con.commit()
    return con


def pick_base(variants, pinned_variant, base_latest):
    """The variant our existing '' series represents."""
    if pinned_variant and pinned_variant in variants:
        return pinned_variant
    if base_latest and base_latest > 0:
        best, dist = None, None
        for v, (_sku, pts) in variants.items():
            d = abs(math.log(pts[0][1] / base_latest))
            if dist is None or d < dist:
                best, dist = v, d
        if dist is not None and dist <= math.log(2.0):
            return best
    return max(variants, key=lambda v: len(variants[v][1]))


def ingest_one(conn, card_con, session, game, pid, pinned, delay):
    variants = nm_variant_series(session, pid, delay)
    now = now_iso()
    if not variants:
        conn.execute(
            "INSERT OR REPLACE INTO printing_variants VALUES (?,?,?,?,?,?,?,?)",
            (game, pid, "(none)", None, None, 0, 0, now))
        conn.commit()
        return 0
    base_latest = conn.execute(
        "SELECT price FROM tcg_nm_history WHERE game=? AND product_id=? "
        "AND printing='' ORDER BY date DESC LIMIT 1", (game, pid)).fetchone()
    base = pick_base(variants, pinned.get((game, pid)), base_latest[0] if base_latest else None)

    conn.execute("DELETE FROM printing_variants WHERE game=? AND product_id=?",
                 (game, pid))
    labeled = 0
    for variant, (sku, pts) in variants.items():
        conn.execute(
            "INSERT OR REPLACE INTO printing_variants VALUES (?,?,?,?,?,?,?,?)",
            (game, pid, variant, sku, pts[0][1], len(pts), int(variant == base), now))
        if variant != base:
            conn.executemany(
                "INSERT OR REPLACE INTO tcg_nm_history "
                "(game, product_id, printing, date, price) VALUES (?,?,?,?,?)",
                [(game, pid, variant, d, p) for d, p in pts])
            labeled += 1
    conn.commit()
    # Derived cache on the card row (base first, so the UI default is stable).
    order = [base] + sorted(v for v in variants if v != base)
    card_con.execute("UPDATE cards SET printings=?, base_printing=? WHERE product_id=?",
                     (json.dumps(order), base, pid))
    card_con.commit()
    return labeled


def main():
    ap = argparse.ArgumentParser(description="Ingest all printing variants per product.")
    ap.add_argument("--rotate", action="store_true",
                    help="process least-recently-checked cards (value-first on "
                         "first pass) up to --limit")
    ap.add_argument("--pids", nargs="*", default=[], help="game:product_id tokens")
    ap.add_argument("--games", help="comma-separated subset (default: all)")
    ap.add_argument("--refresh-days", type=float, default=30.0)
    ap.add_argument("--limit", type=int, default=2300)
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY)
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM)
    args = ap.parse_args()
    games = ([g.strip() for g in args.games.split(",")] if args.games
             else priced_games())

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    conn = sqlite3.connect(PC_DB, timeout=120)
    init_db(conn)
    session = tp.make_session()
    pinned = {(g, p): v for g, p, v in load_pins()}

    todo = []   # (game, pid)
    for token in args.pids:
        g, _, pid = token.partition(":")
        todo.append((g, int(pid)))
    if args.rotate:
        cutoff = (datetime.now(timezone.utc).timestamp()
                  - args.refresh_days * 86400)
        checked = {}
        for g, p, ca in conn.execute(
                "SELECT game, product_id, MAX(checked_at) FROM printing_variants "
                "GROUP BY game, product_id"):
            try:
                checked[(g, p)] = datetime.fromisoformat(ca).timestamp()
            except (TypeError, ValueError):
                pass
        pool = []
        for game in games:
            con = sqlite3.connect(db_path(game))
            for pid, price in con.execute(
                    "SELECT product_id, near_mint_price FROM cards "
                    "WHERE near_mint_price IS NOT NULL"):
                ts = checked.get((game, pid))
                if ts is None or ts < cutoff:
                    # never-checked first, then stalest; value breaks ties so
                    # the first sweep covers expensive cards before bulk
                    pool.append((ts or 0, -(price or 0), game, pid))
            con.close()
        pool.sort()
        todo += [(g, p) for _ts, _np, g, p in pool[:max(0, args.limit - len(todo))]]

    if not todo:
        print("nothing to ingest")
        return
    print(f"ingesting variants for {len(todo)} card(s) at {args.rpm:g} rpm")
    card_cons = {}
    stats = collections.Counter()
    for i, (game, pid) in enumerate(todo, 1):
        if game not in card_cons:
            card_cons[game] = ensure_card_columns(game)
        labeled = ingest_one(conn, card_cons[game], session, game, pid, pinned, args.delay)
        stats["multi" if labeled else "single"] += 1
        stats["labeled_series"] += labeled
        if i % 200 == 0:
            print(f"  {i}/{len(todo)} ({stats['multi']} multi-variant, "
                  f"{stats['labeled_series']} labeled series)", flush=True)
    for con in card_cons.values():
        con.close()
    print(f"done: {dict(stats)}")
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"printing_ingest: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
