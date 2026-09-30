#!/usr/bin/env python3
"""
Flag high-value cards whose TCGplayer search `marketPrice` (what the nightly
crawl stores) disagrees with the per-condition DETAILED endpoint's Near Mint
market price — the sales-based number the product page shows.

Why: for a LIQUID card the search marketPrice equals the true NM market to the
cent. For an ILLIQUID card with no recent sales it drifts to a listing/asking
value and inflates (e.g. Base Set Shadowless Charizard read $10,000 in search
vs a $2,146 market on the page). Neither source is universally right — the
detailed endpoint itself picks odd variants for some vintage cards — so this
does NOT auto-correct. It just MARKS the disagreements for manual page
inspection (the existing price_corrections.csv workflow handles the fix).

Bounded cost: only cards at/above --min-price are candidates (~1.5k), and of
those only the ones whose search price MOVED since their last live check (or
whose check is > --recheck-days old) cost a request — unchanged cards keep
their verdict, still-flagged ones carry their flag row forward. Typical night:
a few minutes, not the ~40 min a full re-check of every candidate costs.

Output: pricecharting.db `tcg_nm_review` (one row per flagged card, with the
TCGplayer URL) + a printed inspection list.

Run:  .venv/bin/python verify_high_value_nm.py --min-price 250
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, db_path
import tcg_pokemon_scraper as tp

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tcg_nm_review (
            game         TEXT    NOT NULL,
            product_id   INTEGER NOT NULL,
            date         TEXT    NOT NULL,
            search_price REAL,          -- what the crawl stored (search marketPrice)
            detailed_nm  REAL,          -- closest detailed NM variant (sales-based)
            ratio        REAL,          -- search / detailed
            reason       TEXT,          -- 'disagree' | 'unverifiable'
            name         TEXT,
            set_name     TEXT,
            url          TEXT,
            checked_at   TEXT,
            PRIMARY KEY (game, product_id, date)
        );
        -- Skip-unchanged memo: the search price and verdict as of each card's
        -- last LIVE check. A card whose search price hasn't moved since then
        -- keeps its verdict without a request (the failure mode this step
        -- hunts — illiquid asking-price drift — shows up as a search-price
        -- CHANGE), re-checked at most every --recheck-days regardless.
        CREATE TABLE IF NOT EXISTS tcg_nm_verified (
            game         TEXT    NOT NULL,
            product_id   INTEGER NOT NULL,
            search_price REAL,
            reason       TEXT,          -- NULL = clean at last check
            checked_at   TEXT,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    conn.commit()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def detailed_nm_variants(session, pid, delay):
    """All Near Mint market prices (one per printing/variant) from the detailed
    endpoint, newest bucket each."""
    h = tp.fetch_price_history(session, pid, delay)
    out = []
    for s in (h.get("result") or h.get("results") or []) if h else []:
        if s.get("condition") == "Near Mint":
            buckets = s.get("buckets", [])
            v = _f(buckets[0].get("marketPrice")) if buckets else None
            if v:
                out.append(v)
    return out


def card_meta(game, pid):
    """(name, set_name) from the game's catalog DB, best-effort."""
    try:
        row = sqlite3.connect(db_path(game)).execute(
            "SELECT name, set_name FROM cards WHERE product_id=?", (pid,)).fetchone()
        return (row[0], row[1]) if row else (None, None)
    except sqlite3.OperationalError:
        return (None, None)


def tcg_url(pid):
    # Land straight on the Near Mint / English view so inspection is one click.
    return (f"https://www.tcgplayer.com/product/{pid}"
            "?Language=English&Condition=Near+Mint&page=1")


def main():
    ap = argparse.ArgumentParser(description="Flag high-value NM price disagreements.")
    ap.add_argument("--min-price", type=float, default=250.0,
                    help="only verify cards whose stored NM is >= this (default 250)")
    ap.add_argument("--tolerance", type=float, default=1.5,
                    help="flag if search/detailed is outside [1/T, T] (default 1.5)")
    ap.add_argument("--games", help="comma-separated subset (default: all)")
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY)
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM)
    ap.add_argument("--unchanged-pct", type=float, default=1.0,
                    help="skip a card whose search price moved less than this %% "
                         "since its last live check (default 1)")
    ap.add_argument("--recheck-days", type=float, default=7.0,
                    help="live-check a card at least this often even if its "
                         "price is unchanged (default 7)")
    args = ap.parse_args()

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    conn = sqlite3.connect(PC_DB, timeout=60)
    init_db(conn)
    # Key off the most recent crawl date in the data, NOT the calendar date — a
    # crawl that runs across midnight stamps its rows with the START date, so
    # date.today() can miss them entirely.
    today = conn.execute(
        "SELECT MAX(date) FROM tcg_nm_history WHERE printing=''").fetchone()[0]
    if not today:
        print("no tcg_nm_history data yet — nothing to verify")
        conn.close()
        return
    session = tp.make_session()

    q = ("SELECT game, product_id, price FROM tcg_nm_history "
         "WHERE printing='' AND date=? AND price >= ?")
    params = [today, args.min_price]
    if args.games:
        gl = [g.strip() for g in args.games.split(",") if g.strip()]
        q += f" AND game IN ({','.join('?' * len(gl))})"
        params += gl
    q += " ORDER BY price DESC"
    targets = conn.execute(q, params).fetchall()

    # Fresh slate for today's flags so a re-run doesn't leave stale rows.
    conn.execute("DELETE FROM tcg_nm_review WHERE date=?", (today,))
    conn.commit()

    memo = {(g, p): (sp, reason, ca) for g, p, sp, reason, ca in conn.execute(
        "SELECT game, product_id, search_price, reason, checked_at FROM tcg_nm_verified")}
    now = datetime.now(timezone.utc)
    recheck_cut = (now.timestamp() - args.recheck_days * 86400)

    def memo_fresh(entry, search):
        sp, _, ca = entry
        if not sp or sp <= 0 or not ca:
            return False
        try:
            checked = datetime.fromisoformat(ca).timestamp()
        except ValueError:
            return False
        return (checked >= recheck_cut
                and abs(search - sp) <= args.unchanged_pct / 100.0 * sp)

    print(f"verifying {len(targets)} card(s) >= ${args.min_price:g} "
          f"(tolerance {args.tolerance}x)", flush=True)

    flagged = carried = skipped = checked_n = 0
    for i, (game, pid, search) in enumerate(targets, 1):
        m = memo.get((game, pid))
        if m and memo_fresh(m, search):
            skipped += 1
            if m[1]:  # still-flagged card: carry its last flag row to today
                conn.execute(
                    "INSERT OR REPLACE INTO tcg_nm_review "
                    "SELECT game, product_id, ?, search_price, detailed_nm, ratio, "
                    "       reason, name, set_name, url, checked_at "
                    "FROM tcg_nm_review WHERE game=? AND product_id=? AND date<? "
                    "ORDER BY date DESC LIMIT 1",
                    (today, game, pid, today))
                carried += 1
            continue
        checked_n += 1
        variants = detailed_nm_variants(session, pid, args.delay)
        if variants:
            best = min(variants, key=lambda v: abs(v - search))  # closest match
            ratio = search / best if best else None
            ok = ratio is not None and (1.0 / args.tolerance) <= ratio <= args.tolerance
            reason = None if ok else "disagree"
        else:
            best, ratio, reason = None, None, "unverifiable"
        if reason:
            name, setn = card_meta(game, pid)
            conn.execute(
                "INSERT OR REPLACE INTO tcg_nm_review VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (game, pid, today, search, best, ratio, reason, name, setn,
                 tcg_url(pid), now.isoformat()))
            flagged += 1
        conn.execute(
            "INSERT OR REPLACE INTO tcg_nm_verified VALUES (?,?,?,?,?)",
            (game, pid, search, reason, now.isoformat()))
        conn.commit()
        if checked_n % 50 == 0:
            print(f"  {i}/{len(targets)} seen ({checked_n} live-checked, "
                  f"{skipped} unchanged-skipped), {flagged} flagged", flush=True)
    conn.commit()

    print(f"\n{checked_n} live-checked, {skipped} skipped as unchanged "
          f"({carried} flag(s) carried forward); {flagged} newly flagged "
          f"-> tcg_nm_review", flush=True)
    rows = conn.execute(
        "SELECT game, name, set_name, search_price, detailed_nm, ratio, reason, url "
        "FROM tcg_nm_review WHERE date=? ORDER BY search_price DESC", (today,)).fetchall()
    for g, name, setn, sp, dn, r, reason, url in rows[:40]:
        dn_s = f"${dn:,.2f}" if dn else "no NM data"
        r_s = f"{r:.1f}x" if r else "  -"
        print(f"  [{g}] {(name or '?')[:34]:<34} search=${sp:>9,.2f} detailed={dn_s:>12} "
              f"{r_s:>6} {reason:<12} {url}")
    if len(rows) > 40:
        print(f"  ... and {len(rows) - 40} more (see tcg_nm_review)")
    conn.close()


if __name__ == "__main__":
    # Non-fatal: a review-flagging hiccup must never fail the nightly (the prices
    # are already stored; flags refresh next run).
    try:
        main()
    except Exception as e:
        print(f"verify_high_value_nm: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
