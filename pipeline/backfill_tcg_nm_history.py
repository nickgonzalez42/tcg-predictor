#!/usr/bin/env python3
"""
Backfill a card's ungraded price HISTORY from TCGplayer's Near Mint chart.

The nightly crawl (scrape_tcg_nm_prices.py) captures only TODAY's NM price. For
cards whose PriceCharting ungraded history is wrong (PC crossed tcg-ids, carried
phantom pre-release prices, absorbed a base card's market, ...) we want the
whole ungraded series to come from TCGplayer instead. TCGplayer's product page
shows a ~1-year weekly NM chart, and the same data is available from the detailed
price endpoint (infinite-api .../price/history/{pid}/detailed?range=annual) — one
request per card gives ~52 weekly Near Mint points.

This writes those weekly points into tcg_nm_history at their real dates (so
build_unified_history.py buckets them to monthly like any other TCGplayer NM),
and records the card in tcg_nm_only.csv so build_unified sources its ungraded
tier EXCLUSIVELY from TCGplayer — no PriceCharting ungraded, no PC-era
price_corrections (those were workarounds for the bad PC data we're replacing).
Graded tiers are untouched (still PriceCharting).

Run (game:product_id tokens):
    .venv/bin/python backfill_tcg_nm_history.py pokemon:228485 onepiece:590988
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES
import tcg_pokemon_scraper as tp

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
TCG_NM_ONLY_CSV = os.path.join(BASE, "ml_data", "tcg_nm_only.csv")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def nm_series(session, pid, delay):
    """The best Near Mint weekly series for a product: [(YYYY-MM-DD, price)].

    A product can carry several printings (variants); we take the NM variant with
    the most non-zero weekly points (the printing TCGplayer actually has a market
    for), tie-broken by the most recent price."""
    h = tp.fetch_price_history(session, pid, delay)
    best, best_key = [], (-1, -1.0)
    for s in (h.get("result") or h.get("results") or []) if h else []:
        if s.get("condition") != "Near Mint":
            continue
        pts = []
        for b in s.get("buckets", []):
            d = b.get("bucketStartDate") or b.get("date")
            p = _f(b.get("marketPrice"))
            if d and p:                       # skip $0 / null weeks
                pts.append((d[:10], p))
        if not pts:
            continue
        key = (len(pts), pts[0][1])           # most points, then newest price
        if key > best_key:
            best, best_key = pts, key
    return best


def freshen_current_week(pts):
    """[(date, price)] newest-first. The newest weekly bucket covers the
    CURRENT week: its price is this week's market, but its start-of-week date
    makes the card read days-stale on site (e.g. "as of Aug 3" for a price
    fetched Aug 9). Restamp the newest point to the fetch day when today falls
    inside its week."""
    if not pts:
        return pts
    from datetime import date as _date, timedelta as _td
    d0, p0 = pts[0]
    try:
        start = _date.fromisoformat(d0)
    except ValueError:
        return pts
    # UTC date (2026-08-14): matches the batch labels AND TCGplayer's own UTC
    # week boundaries, so the future-bucket clamp below is now a rare guard.
    today = datetime.now(timezone.utc).date()
    if start < today < start + _td(days=7):
        return [(today.isoformat(), p0)] + list(pts[1:])
    if start > today:
        # TCGplayer's week boundary can open a bucket AHEAD of local time —
        # never write a future-dated point ("as of tomorrow" on the site).
        return [(today.isoformat(), p0)] + list(pts[1:])
    return pts


def table_market_prices(session, pid, delay):
    """Non-null marketPrice per printingType from the product-page TABLE.
    Empty dict = TCGplayer has no market price for the product at all."""
    rows = tp.fetch_pricepoints(session, pid, delay) or []
    out = {}
    for r in rows:
        p = _f(r.get("marketPrice"))
        if p:
            out[r.get("printingType") or "?"] = p
    return out


def chart_agrees_with_table(pts, table, tol=0.25):
    """Is the chart's newest NM price within tol of ANY table market price?

    Policy (user, 2026-08-18): the table is checked FIRST — a chart series
    that contradicts the page's market price (NM $22 vs table $145) is a thin
    condition sub-series, not the market, and must not be imported."""
    if not pts or not table:
        return False
    p = pts[0][1]
    return any(abs(p / t - 1.0) <= tol for t in table.values())


def record_nm_only(game, pid, note):
    """Add (game, pid) to tcg_nm_only.csv (idempotent) so build_unified sources
    ungraded from TCGplayer alone for this card."""
    rows = []
    if os.path.exists(TCG_NM_ONLY_CSV):
        with open(TCG_NM_ONLY_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    if any(r["game"] == game and int(r["product_id"]) == pid for r in rows):
        return
    exists = os.path.exists(TCG_NM_ONLY_CSV)
    with open(TCG_NM_ONLY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["game", "product_id", "note"])
        w.writerow([game, pid, note])


def main():
    ap = argparse.ArgumentParser(description="Backfill TCGplayer NM history for cards.")
    ap.add_argument("cards", nargs="*", help="game:product_id tokens")
    ap.add_argument("--refresh-existing", action="store_true",
                    help="re-pull every card already in tcg_nm_only.csv (nightly refresh)")
    ap.add_argument("--history-only", action="store_true",
                    help="seed NM history but do NOT mark tcg_nm_only — the card keeps "
                         "getting its CURRENT price from the search crawl (for normal, "
                         "non-inflated cards that just need a history series)")
    ap.add_argument("--note", default="ungraded from TCGplayer NM (PC ungraded wrong)")
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY)
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM)
    args = ap.parse_args()

    targets = []
    for tok in args.cards:
        game, _, pid = tok.partition(":")
        if game not in GAMES or not pid.isdigit():
            ap.error(f"bad token {tok!r} — expected game:product_id")
        targets.append((game, int(pid)))
    # Blocklist trumps every TCG-pricing list (2026-08-17: Umbreon H30 sat in
    # BOTH tcg_nm_only and the blocklist; this refresh re-wrote its bogus
    # $4,999.99 series the night after the blocklist repair).
    blocked = set()
    bl = os.path.join(os.path.dirname(TCG_NM_ONLY_CSV), "tcg_nm_blocklist.csv")
    if os.path.exists(bl):
        with open(bl, newline="", encoding="utf-8") as f:
            blocked = {(r["game"], int(r["product_id"])) for r in csv.DictReader(f)
                       if r.get("product_id")}
    if args.refresh_existing and os.path.exists(TCG_NM_ONLY_CSV):
        with open(TCG_NM_ONLY_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("product_id") and (r["game"], int(r["product_id"])) not in blocked:
                    targets.append((r["game"], int(r["product_id"])))
    targets = [t for t in targets if t not in blocked]
    targets = list(dict.fromkeys(targets))   # dedupe, preserve order
    if not targets:
        ap.error("no cards given (pass game:product_id tokens or --refresh-existing)")

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    session = tp.make_session()
    conn = sqlite3.connect(PC_DB, timeout=60)

    total = 0
    for game, pid in targets:
        pts = nm_series(session, pid, args.delay)
        if not pts:
            print(f"[{game}:{pid}] no Near Mint series on TCGplayer — skipped",
                  file=sys.stderr)
            continue
        # In history-only mode, don't seed the CURRENT week — the search crawl
        # owns today's price for these cards, and a stale detailed point (~2 days
        # old) shouldn't win the current-month bucket.
        rows = pts[1:] if args.history_only else freshen_current_week(pts)
        conn.executemany(
            "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, date, price) "
            "VALUES (?,?,?,?)",
            [(game, pid, d, p) for d, p in rows])
        conn.commit()
        if not args.history_only:
            record_nm_only(game, pid, args.note)
        lo, hi = pts[-1], pts[0]
        print(f"[{game}:{pid}] wrote {len(pts)} weekly NM points "
              f"{lo[0]} ${lo[1]:,.2f} -> {hi[0]} ${hi[1]:,.2f}")
        total += len(pts)

    conn.close()
    print(f"\nDone. {total} historical NM points written; "
          f"cards marked TCGplayer-only in {os.path.normpath(TCG_NM_ONLY_CSV)}")


if __name__ == "__main__":
    # Non-fatal in the nightly (--refresh-existing): a hiccup refreshing a dozen
    # cards must not fail the whole run. Manual runs still see the error text.
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"backfill_tcg_nm_history: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
