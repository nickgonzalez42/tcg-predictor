#!/usr/bin/env python3
"""
Nightly TCGplayer Near Mint price scrape — the ungraded/NM price source.

TCGplayer's daily NM market price is the value the catalog headline shows and
the forecast model's `ungraded` tier trains on. Graded ladders still come from
PriceCharting (see build_unified_history.py); this script owns ungraded only.

Why the search API and not the per-card detailed endpoint: the product-search
response already carries each card's `marketPrice`, and that number IS the Near
Mint market price — it matches the detailed endpoint's Near-Mint series exactly
(verified). One search request returns ~50 cards, so the whole catalog costs
~one request per 50 cards (a few thousand a night) instead of one-per-card
(hundreds of thousands). We reuse the catalog scraper's transport (rate limiter,
retrying session, set-partitioned enumeration) so the polite-crawl behavior and
the ~10k deep-pagination workaround are shared, not reimplemented.

Output: pricecharting.db `tcg_nm_history` (game, product_id, date, price),
append-only and date-keyed like the other price sources, so a re-run never
loses history and build_unified_history.py can rebuild from it.

Run:
    .venv/bin/python scrape_tcg_nm_prices.py --game all
    .venv/bin/python scrape_tcg_nm_prices.py --game onepiece        # one game
    .venv/bin/python scrape_tcg_nm_prices.py --game all --resume     # skip sets
                                                    # already scanned today
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, ALL_GAMES, db_path
import tcg_pokemon_scraper as tp   # transport: RateLimiter, session, retries, is_single_card
import tcg_scraper as ts           # fetch_set_list (setName aggregation)

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
TCG_NM_ONLY_CSV = os.path.join(BASE, "ml_data", "tcg_nm_only.csv")
SEARCH_URL = tp.SEARCH_URL
PAGE_SIZE = tp.PAGE_SIZE


PINS_CSV = os.path.join(BASE, "ml_data", "tcg_printing_pins.csv")
NM_BLOCKLIST_CSV = os.path.join(BASE, "ml_data", "tcg_nm_blocklist.csv")


def load_nm_only(game):
    """product_ids the search crawl must NOT price. Three curated lists:
      tcg_nm_only.csv        detailed-endpoint-owned cards — the search
                             marketPrice is unreliable for these illiquid cards
      tcg_nm_blocklist.csv   cards whose TCGplayer NM listing price is bogus
                             (e.g. Umbreon H30's lone stale $4,999.99 listing
                             vs PC sales ~$1,800) — ungraded stays PC-owned
      tcg_printing_pins.csv  multi-printing products pinned to one variant
                             (pin_printings.py) — the product-level search
                             marketPrice reflects the WRONG printing (e.g.
                             1st Edition when our series is Unlimited)
    The first two get prices from the detailed NM endpoint; blocklisted cards
    get no TCGplayer ungraded prices at all."""
    out = set()
    for path in (TCG_NM_ONLY_CSV, PINS_CSV, NM_BLOCKLIST_CSV):
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                out |= {int(r["product_id"]) for r in csv.DictReader(f)
                        if r["game"] == game and r.get("product_id")}
    return out


def _load_slug_map(game):
    """set_name -> set_url_name from the game's catalog DB.

    We paginate each set by setUrlName, NOT the display setName: TCGplayer's
    search silently IGNORES a setName filter whose value contains certain
    punctuation (e.g. 'Premium Booster -The Best-'), returning the WHOLE catalog
    instead — which both over-scrapes and mis-attributes cards. The set_url_name
    the catalog scraper already stored is the exact value the filter honors."""
    try:
        con = sqlite3.connect(db_path(game))
        m = dict(con.execute(
            "SELECT set_name, set_url_name FROM cards "
            "WHERE set_url_name IS NOT NULL AND set_url_name != '' "
            "AND set_name IS NOT NULL"))
        con.close()
        return m
    except sqlite3.OperationalError:
        return {}


def _search_payload(line, offset, size, set_url_name=None, set_name=None):
    term = {"productLineName": [line], "productTypeName": ["Cards"]}
    if set_url_name is not None:
        term["setUrlName"] = [set_url_name]     # reliable filter (see _load_slug_map)
    elif set_name is not None:
        term["setName"] = [set_name]            # fallback for sets not yet in the DB
    return {
        "algorithm": "sales_dedupe_v2", "from": offset, "size": size,
        "filters": {"term": term, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {
            "term": {"sellerStatus": "Live", "channelId": 0},
            "range": {"quantity": {"gte": 1}}, "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {"field": "product-sorting-name", "order": "asc"},
    }


def _iter_set_pages(session, line, delay, set_url_name=None, set_name=None):
    """Yield every product for one set, paginating by offset. A failure aborts
    only this set's scan (its count won't be recorded, so it retries next run)."""
    offset = 0
    while True:
        payload = _search_payload(line, offset, PAGE_SIZE, set_url_name, set_name)
        resp = tp.request_with_retries(
            session, "POST", SEARCH_URL, delay,
            params={"q": "", "isList": "false"}, data=json.dumps(payload))
        if resp is None or resp.status_code != 200:
            return
        try:
            block = (resp.json().get("results") or [{}])[0]
        except ValueError:
            return
        results = block.get("results", [])
        if not results:
            return
        for p in results:
            pid = p.get("productId")
            if pid is not None:
                try:
                    p["productId"] = int(float(pid))
                except (TypeError, ValueError):
                    pass
            yield p
        offset += len(results)
        if offset >= (block.get("totalResults") or 0):
            return


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tcg_nm_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            date       TEXT    NOT NULL,   -- YYYY-MM-DD, the scrape day
            price      REAL    NOT NULL,   -- TCGplayer Near Mint market price
            PRIMARY KEY (game, product_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_tcgnm_game_pid
            ON tcg_nm_history(game, product_id);

        -- Per-set completion marker for the day (mirrors set_counts' role in the
        -- catalog scrape): a set is recorded only after every page arrived, so a
        -- run killed mid-set leaves it unmarked and --resume re-scans exactly the
        -- unfinished sets — no card is silently stranded behind a "done" flag.
        -- scanned_at lets the live dashboard compute a crawl rate + ETA.
        CREATE TABLE IF NOT EXISTS tcg_nm_scan (
            game       TEXT    NOT NULL,
            set_name   TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            n_cards    INTEGER,
            scanned_at TEXT,
            PRIMARY KEY (game, set_name, date)
        );

        -- The night's plan per game (total sets/products from the aggregation),
        -- so the dashboard has an exact denominator for the progress bar without
        -- re-querying TCGplayer. Rewritten at each game's start.
        CREATE TABLE IF NOT EXISTS tcg_nm_plan (
            game          TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            total_sets    INTEGER,
            total_products INTEGER,
            started_at    TEXT,
            PRIMARY KEY (game, date)
        );
        """
    )
    # Migration for a pre-existing tcg_nm_scan without scanned_at.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tcg_nm_scan)")]
    if "scanned_at" not in cols:
        conn.execute("ALTER TABLE tcg_nm_scan ADD COLUMN scanned_at TEXT")
    conn.commit()


def plan_all(games, session, conn, today, delay):
    """Fetch each game's set aggregation once up front, record the night's plan,
    and return {game: [(set_name, count), ...]}.

    Doing this before any scraping gives the dashboard the FULL denominator from
    t=0 (a single overall percentage, not one that resets per game) at the cost
    of one cheap aggregation request per game."""
    game_sets = {}
    for game in games:
        sets = ts.fetch_set_list(session, GAMES[game]["tcg_line"], delay)
        game_sets[game] = sets
        conn.execute("INSERT OR REPLACE INTO tcg_nm_plan VALUES (?,?,?,?,?)",
                     (game, today, len(sets), sum(c for _, c in sets),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
        print(f"[plan] {game}: {len(sets)} sets, {sum(c for _, c in sets)} products",
              flush=True)
    return game_sets


def scrape_game(game, sets, session, conn, today, delay, resume):
    line = GAMES[game]["tcg_line"]
    if not sets:
        print(f"[{game}] could not read the set aggregation — nothing scraped.",
              file=sys.stderr)
        return 0, 0

    slug_map = _load_slug_map(game)
    nm_only = load_nm_only(game)   # cards owned by the detailed-history refresh
    done_today = {s for (s,) in conn.execute(
        "SELECT set_name FROM tcg_nm_scan WHERE game=? AND date=?", (game, today))}
    todo = [(s, c) for s, c in sets if not (resume and s in done_today)]
    no_slug = sum(1 for s, _ in todo if s not in slug_map)
    print(f"[{game}] {len(sets)} sets, {sum(c for _, c in sets)} products; "
          f"scanning {len(todo)}"
          f"{f' ({len(sets) - len(todo)} already done today)' if resume else ''}"
          f"{f'; {no_slug} new set(s) via setName fallback' if no_slug else ''}.",
          flush=True)

    priced = 0
    scanned = 0
    for set_name, count in todo:
        slug = slug_map.get(set_name)
        if slug:
            raw = list(_iter_set_pages(session, line, delay, set_url_name=slug))
        else:
            # Brand-new set the catalog scrape hasn't recorded a slug for yet:
            # fall back to the setName filter (fine for most; a punctuation-heavy
            # new set may over-scan until Sunday records its slug).
            print(f"  [{game}] no catalog slug for '{set_name}' — setName fallback",
                  file=sys.stderr)
            raw = list(_iter_set_pages(session, line, delay, set_name=set_name))
        complete = len(raw) >= count
        rows = []
        for p in raw:
            if not tp.is_single_card(p):
                continue
            pid = p.get("productId")
            mp = tp._to_float(p.get("marketPrice"))
            # A null marketPrice means TCGplayer has no NM value (no recent
            # sales/listings) — record nothing rather than a fake 0.
            if pid is None or mp is None:
                continue
            if int(pid) in nm_only:   # detailed-history refresh owns these
                continue
            rows.append((game, int(pid), today, mp))
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, date, price) "
                "VALUES (?,?,?,?)", rows)
        # Only mark the set done if every page arrived — a partial set must be
        # retried next run, not sealed with a hole.
        if complete:
            conn.execute("INSERT OR REPLACE INTO tcg_nm_scan VALUES (?,?,?,?,?)",
                         (game, set_name, today, len(rows),
                          datetime.now(timezone.utc).isoformat()))
        else:
            print(f"  [{game}] set '{set_name}': {len(raw)}/{count} products "
                  f"arrived — will re-scan next run.", file=sys.stderr)
        conn.commit()
        priced += len(rows)
        scanned += 1
        if scanned % 25 == 0:
            print(f"  [{game}] {scanned}/{len(todo)} sets, {priced} NM prices "
                  f"(last: {set_name})", flush=True)
    print(f"[{game}] done: {priced} Near Mint prices from {scanned} sets.",
          flush=True)
    return priced, scanned


def main():
    ap = argparse.ArgumentParser(description="Scrape TCGplayer Near Mint prices.")
    ap.add_argument("--game", default="all",
                    help="'all' or a single game name (default: all)")
    ap.add_argument("--games", help="comma-separated subset (overrides --game)")
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY,
                    help="minimum seconds between requests (politeness)")
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM,
                    help="hard ceiling on requests per rolling minute")
    ap.add_argument("--resume", action="store_true",
                    help="skip sets already fully scanned today (restart a long run)")
    args = ap.parse_args()

    if args.games:
        games = [g.strip() for g in args.games.split(",") if g.strip()]
    elif args.game == "all":
        games = list(ALL_GAMES)
    else:
        games = [args.game]
    bad = [g for g in games if g not in GAMES]
    if bad:
        ap.error(f"unknown game(s): {', '.join(bad)}")

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    print(f"TCGplayer NM scrape — rate limit: <= {args.rpm:g} req/min, "
          f">= {args.delay:g}s between requests. Games: {', '.join(games)}")

    # Batch label = UTC date (2026-08-14): the 22:00 CDT start is already the
    # next UTC day, so the label matches the morning the batch goes live.
    today = datetime.now(timezone.utc).date().isoformat()
    session = tp.make_session()
    conn = sqlite3.connect(PC_DB, timeout=60)
    init_db(conn)

    game_sets = plan_all(games, session, conn, today, args.delay)

    total_priced = 0
    for game in games:
        priced, _ = scrape_game(game, game_sets.get(game, []), session, conn,
                                today, args.delay, args.resume)
        total_priced += priced

    conn.close()
    print(f"\nDone. {total_priced} Near Mint prices for {today} "
          f"-> {os.path.normpath(PC_DB)} (tcg_nm_history)")


if __name__ == "__main__":
    main()
