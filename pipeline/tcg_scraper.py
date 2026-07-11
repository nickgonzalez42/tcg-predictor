"""
Generic TCGplayer catalog scraper — one config-driven scraper for every game
added after the original two (yugioh, magic, lorcana, digimon, gundam, ...).

Stores the columns all games share (name, set, rarity, number, type,
description, release date, art) plus the FULL customAttributes JSON, so each
game's unique stat line survives without a bespoke schema. NO pricing — that
comes exclusively from PriceCharting.

Reuses the battle-tested transport layer (rate limiter, retrying session,
image download) from tcg_pokemon_scraper; only the payload/enumeration differ,
because the product line is a parameter here.

Run:  .venv/bin/python tcg_scraper.py --game yugioh --new-only --no-history
      .venv/bin/python tcg_scraper.py --game magic            # full backfill
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from games import GAMES, db_path, image_dir
import tcg_pokemon_scraper as tp   # transport: RateLimiter, session, retries, images

SEARCH_URL = tp.SEARCH_URL
PAGE_SIZE = tp.PAGE_SIZE


# ----------------------------------------------------------------------------
# Search (same request shape as the dedicated scrapers, product line as arg)
# ----------------------------------------------------------------------------

def build_search_payload(line, offset, size, singles_only=True, set_name=None):
    term = {"productLineName": [line]}
    if singles_only:
        term["productTypeName"] = ["Cards"]
    if set_name is not None:
        term["setName"] = [set_name]
    return {
        "algorithm": "sales_dedupe_v2",
        "from": offset,
        "size": size,
        "filters": {"term": term, "range": {}, "match": {}},
        "listingSearch": {
            "context": {"cart": {}},
            "filters": {
                "term": {"sellerStatus": "Live", "channelId": 0},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
        },
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {"field": "product-sorting-name", "order": "asc"},
    }


def fetch_set_list(session, line, base_delay, singles_only=True):
    """[(set_name, count), ...] from the setName aggregation — one request."""
    payload = build_search_payload(line, 0, 1, singles_only=singles_only)
    resp = tp.request_with_retries(
        session, "POST", SEARCH_URL, base_delay,
        params={"q": "", "isList": "false"}, data=json.dumps(payload))
    if resp is None or resp.status_code != 200:
        return []
    try:
        block = (resp.json().get("results") or [{}])[0]
    except ValueError:
        return []
    return [(s["value"], int(s.get("count") or 0))
            for s in block.get("aggregations", {}).get("setName", []) if s.get("value")]


def _iter_query_pages(session, line, base_delay, singles_only, set_name):
    """Yield products for one set, paginating by offset. A failure aborts only
    this set's scan, not the whole run."""
    offset = 0
    while True:
        payload = build_search_payload(line, offset, PAGE_SIZE,
                                       singles_only=singles_only, set_name=set_name)
        resp = tp.request_with_retries(
            session, "POST", SEARCH_URL, base_delay,
            params={"q": "", "isList": "false"}, data=json.dumps(payload))
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "no response"
            print(f"  Search failed for set '{set_name}' at offset {offset}: {code}; "
                  f"skipping rest of this set.", file=sys.stderr)
            return
        try:
            block = (resp.json().get("results") or [{}])[0]
        except ValueError:
            print(f"  Non-JSON for set '{set_name}' at offset {offset}; skipping rest.",
                  file=sys.stderr)
            return
        results = block.get("results", [])
        if not results:
            return
        for product in results:
            pid = product.get("productId")
            if pid is not None:
                try:
                    product["productId"] = int(float(pid))
                except (TypeError, ValueError):
                    pass
            yield product
        offset += len(results)
        if offset >= (block.get("totalResults") or 0):
            return


def process_sets(session, line, base_delay, conn, img_dir, new_only,
                 limit=None, no_images=False, singles_only=True):
    """Set-partitioned crawl (large lines exceed the ~10k offset cap), with the
    SET as the atomic unit of work: enumerate its pages, upsert its cards,
    download its missing images, and only THEN record its product count.

    That ordering is what makes the crawl interruption-proof: a run killed or
    starved mid-set leaves that set unrecorded, so the next --new-only run
    re-scans exactly the unfinished sets — cards and art can never be silently
    stranded behind a "completed" marker. Without new_only every set is
    processed (the full backfill / repair pass).
    """
    sets = fetch_set_list(session, line, base_delay, singles_only=singles_only)
    if not sets:
        print("Could not read the set aggregation — nothing scanned.", file=sys.stderr)
        return 0
    known = dict(conn.execute(
        "SELECT set_name, product_count FROM set_counts").fetchall())
    todo = [(s, c) for s, c in sets if not new_only or known.get(s) != c]
    print(f"Sets to scan: {len(todo)} of {len(sets)} "
          f"({sum(c for _, c in todo)} products).", flush=True)

    processed = 0
    for set_name, count in todo:
        raw = list(_iter_query_pages(session, line, base_delay,
                                     singles_only, set_name))
        complete = len(raw) >= count
        cards = [p for p in raw if tp.is_single_card(p)]
        for p in cards:
            upsert_card(conn, p)
        conn.commit()

        art = 0
        if not no_images:
            for p in cards:
                pid = p.get("productId")
                if pid is None:
                    continue
                path = os.path.join(img_dir, f"{pid}.jpg")
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    continue
                if tp.download_image(session, pid, base_delay, img_dir):
                    conn.execute("UPDATE cards SET image_path=? WHERE product_id=?",
                                 (os.path.join(os.path.basename(img_dir), f"{pid}.jpg"), pid))
                    # Commit per image: an open write transaction across a whole
                    # set's downloads holds the DB lock for minutes and starves
                    # any pipeline step touching this game in parallel.
                    conn.commit()
                    art += 1

        processed += len(cards)
        if complete:
            conn.execute("INSERT OR REPLACE INTO set_counts VALUES (?,?,?)",
                         (set_name, count, datetime.now(timezone.utc).isoformat()))
            conn.commit()
        else:
            print(f"  Set '{set_name}': {len(raw)}/{count} products arrived — "
                  f"will re-scan next run.", file=sys.stderr)
        print(f"  [{set_name}] {len(cards)} cards, {art} new images"
              f"{'' if complete else ' (INCOMPLETE)'}", flush=True)
        if limit is not None and processed >= limit:
            print(f"  --limit reached; later sets stay unrecorded.", file=sys.stderr)
            break
    return processed


# ----------------------------------------------------------------------------
# Storage — the shared columns + the full attribute blob
# ----------------------------------------------------------------------------

def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            product_id        INTEGER PRIMARY KEY,
            name              TEXT,
            clean_name        TEXT,
            set_name          TEXT,
            set_url_name      TEXT,
            rarity            TEXT,
            card_number       TEXT,
            card_type         TEXT,
            description       TEXT,
            release_date      TEXT,
            product_url       TEXT,
            image_url         TEXT,
            image_path        TEXT,
            near_mint_price   REAL,     -- set by backfill_nm_price (PriceCharting)
            custom_attributes TEXT,     -- full JSON blob: the game-specific stat line
            raw_json          TEXT,
            scraped_at        TEXT
        );

        -- Per-set product counts at the last COMPLETE scan (see --new-only).
        CREATE TABLE IF NOT EXISTS set_counts (
            set_name      TEXT PRIMARY KEY,
            product_count INTEGER NOT NULL,
            checked_at    TEXT
        );
        """
    )
    conn.commit()


def upsert_card(conn, product):
    ca = product.get("customAttributes") or {}
    pid = product.get("productId")
    slug = product.get("setUrlName")
    conn.execute(
        """
        INSERT INTO cards (
            product_id, name, clean_name, set_name, set_url_name, rarity,
            card_number, card_type, description, release_date, product_url,
            image_url, image_path, custom_attributes, raw_json, scraped_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_id) DO UPDATE SET
            name=excluded.name,
            clean_name=excluded.clean_name,
            set_name=excluded.set_name,
            set_url_name=excluded.set_url_name,
            rarity=excluded.rarity,
            card_number=excluded.card_number,
            card_type=excluded.card_type,
            description=excluded.description,
            release_date=excluded.release_date,
            product_url=excluded.product_url,
            image_url=excluded.image_url,
            image_path=COALESCE(excluded.image_path, cards.image_path),
            custom_attributes=excluded.custom_attributes,
            raw_json=excluded.raw_json,
            scraped_at=excluded.scraped_at
        """,
        (
            pid,
            product.get("productName"),
            product.get("cleanName"),
            product.get("setName") or slug,
            slug,
            product.get("rarityName") or tp._attr(ca, "rarityDbName"),
            tp._attr(ca, "number"),
            tp._attr(ca, "cardType", "cardTypeB"),
            tp._attr(ca, "description", "text", "cardText", "oracleText"),
            tp._attr(ca, "releaseDate") or product.get("releaseDate"),
            product.get("productUrlName"),
            tp.IMAGE_URL.format(pid=pid),
            None,
            json.dumps(ca, ensure_ascii=False),
            json.dumps(product, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    generic = [g for g, cfg in GAMES.items() if cfg["scraper"][0] == "tcg_scraper.py"]
    ap = argparse.ArgumentParser(description="Generic TCGplayer catalog scraper.")
    ap.add_argument("--game", required=True, choices=generic)
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY)
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM)
    ap.add_argument("--limit", type=int, default=None, help="first N products (testing)")
    ap.add_argument("--no-images", action="store_true", help="skip image download")
    ap.add_argument("--no-history", action="store_true",
                    help="accepted for step-list symmetry (this scraper never fetches pricing)")
    ap.add_argument("--new-only", action="store_true",
                    help="scan only sets whose product count changed since the last complete scan")
    args = ap.parse_args()

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    print(f"[{args.game}] rate limit: <= {args.rpm:g} requests/min, "
          f">= {args.delay:g}s between requests.")

    line = GAMES[args.game]["tcg_line"]
    img_dir = image_dir(args.game)
    session = tp.make_session()
    conn = sqlite3.connect(db_path(args.game))
    init_db(conn)

    mode = "changed sets only (--new-only)" if args.new_only else "all sets"
    print(f"Scanning {mode}, set by set (cards + art per set)...")
    processed = process_sets(session, line, args.delay, conn, img_dir,
                             new_only=args.new_only, limit=args.limit,
                             no_images=args.no_images)

    cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    art = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE image_path IS NOT NULL AND image_path != ''"
    ).fetchone()[0]
    conn.close()
    print(f"\nDone. {processed} cards processed this run; {cards} cards total "
          f"({art} with art) -> {db_path(args.game)}")


if __name__ == "__main__":
    main()
