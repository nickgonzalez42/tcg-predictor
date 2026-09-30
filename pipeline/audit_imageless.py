#!/usr/bin/env python3
"""
Weekly audit of catalog cards with NO image (image_path IS NULL).

These cards are stored and priced but deliberately kept out of the model (art
gate: no CLIP embedding) and the site (VisibleInCatalog requires art). TCGplayer
does eventually photograph some of them, and removes others outright, so once a
week we re-check each one:

  image now on the CDN  -> download it. The same run's s3-upload + art-sync
                           steps then push it to S3 and flip image_path, and
                           ml-embed embeds it — the card enters the model and
                           the site with no further action.
  product gone (404)    -> TCGplayer deleted the listing; delete the card from
                           our catalog too (mirrors the source of truth). Its
                           history rows are left in place (harmless — the card
                           is invisible without a catalog row).
  still no image        -> unchanged; re-checked next week.

Run (Sunday step in weekly_refresh):  .venv/bin/python audit_imageless.py
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from games import priced_games, db_path, image_dir
import tcg_pokemon_scraper as tp

DETAILS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{pid}/details"


def product_exists(session, pid, delay):
    """True/False from the details endpoint; None on transient trouble (treat
    as 'still there' — deletion needs a definitive 404, never a hiccup)."""
    try:
        resp = tp.request_with_retries(session, "GET", DETAILS_URL.format(pid=pid),
                                       delay, retry_forbidden=False)
        if resp is None:
            return None
        if resp.status_code == 200:
            return True
        if resp.status_code in (404, 410):
            return False
        return None
    except Exception:
        return None


def audit_game(game, session, delay, limit, refresh_days):
    conn = sqlite3.connect(db_path(game), timeout=60)
    # Rotation state: least-recently-checked first (never-checked = NULL sorts
    # first), skipping cards re-checked within refresh_days. This lets the audit
    # run NIGHTLY over a bounded --limit slice instead of scanning all ~5k
    # imageless cards every Sunday (which ran 3+ hours and hammered TCGplayer).
    conn.execute("CREATE TABLE IF NOT EXISTS imageless_checked "
                 "(product_id INTEGER PRIMARY KEY, checked_at TEXT)")
    sql = ("SELECT c.product_id FROM cards c "
           "LEFT JOIN imageless_checked k ON k.product_id = c.product_id "
           "WHERE c.image_path IS NULL ")
    params = []
    if refresh_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=refresh_days)).isoformat()
        sql += "AND (k.checked_at IS NULL OR k.checked_at < ?) "
        params.append(cutoff)
    sql += "ORDER BY k.checked_at IS NOT NULL, k.checked_at LIMIT ?"
    params.append(limit if limit else -1)
    pids = [pid for (pid,) in conn.execute(sql, params)]
    if not pids:
        conn.close()
        print(f"[{game}] no imageless cards due for a check")
        return

    now = datetime.now(timezone.utc).isoformat()
    gained = deleted = unchanged = 0
    img_dir = image_dir(game)
    for pid in pids:
        # 1) did TCGplayer add art? (403 = still no CDN image, skipped fast)
        path = tp.download_image(session, pid, delay, img_dir)
        if path:
            gained += 1        # art-sync flips image_path once it's on S3
        else:
            # 2) no art — is the product itself gone?
            exists = product_exists(session, pid, delay)
            if exists is False:
                conn.execute("DELETE FROM cards WHERE product_id=?", (pid,))
                conn.execute("DELETE FROM imageless_checked WHERE product_id=?", (pid,))
                deleted += 1
                continue
            unchanged += 1
        conn.execute("INSERT OR REPLACE INTO imageless_checked VALUES (?,?)", (pid, now))
    conn.commit()
    conn.close()
    print(f"[{game}] {len(pids)} imageless checked: {gained} gained art, "
          f"{deleted} deleted on TCGplayer (removed), {unchanged} unchanged")


def main():
    ap = argparse.ArgumentParser(description="Imageless-card audit vs TCGplayer (nightly rotation)")
    ap.add_argument("--games", help="comma-separated (default: all priced games)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--rpm", type=float, default=60)
    ap.add_argument("--limit", type=int, default=None,
                    help="max cards PER GAME this run (the nightly rotation slice)")
    ap.add_argument("--refresh-days", type=float, default=None,
                    help="re-check a card once its last check is older than this "
                         "many days; without it every imageless card is checked")
    args = ap.parse_args()

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    session = tp.make_session()
    for game in (args.games.split(",") if args.games else priced_games()):
        audit_game(game, session, args.delay, args.limit, args.refresh_days)


if __name__ == "__main__":
    # Non-fatal: an audit hiccup must never fail the weekly refresh.
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"audit_imageless: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
    sys.exit(0)
