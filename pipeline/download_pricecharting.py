"""
Download fresh PriceCharting price-guide CSVs (one per game category).

Uses the API token from .env (PRICECHARTING_TOKEN). Writes atomically:
download to *.tmp, sanity-check the header and row count, then replace the
live CSV (keeping the previous one as *.prev). build_pricecharting.py reads
these files.

Run:  .venv/bin/python download_pricecharting.py
"""

import os
import ssl
import time
import urllib.error
import urllib.request

import certifi

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# category slug -> output CSV (+ per-file sanity floor), from the game registry
from games import GAMES, priced_games
# Only games with a bulk category slug — gundam's CSV comes from
# scrape_gundam_prices.py (per-set console pages) instead.
CATEGORIES = {GAMES[g]["pc_category"]: GAMES[g]["pc_csv"]
              for g in priced_games() if GAMES[g]["pc_category"]}
MIN_ROWS = {GAMES[g]["pc_csv"]: GAMES[g]["pc_min_rows"]
            for g in priced_games() if GAMES[g]["pc_category"]}


def token():
    with open(os.path.join(BASE, ".env")) as f:
        for line in f:
            if line.startswith("PRICECHARTING_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("PRICECHARTING_TOKEN not found in .env")


def download(cat, out_name, t):
    url = f"https://www.pricecharting.com/price-guide/download-custom?t={t}&category={cat}"
    out = os.path.join(BASE, out_name)
    tmp = out + ".tmp"

    _fetch_csv(url, tmp, cat)

    with open(tmp, encoding="utf-8", errors="ignore") as f:
        header = f.readline()
        rows = sum(1 for _ in f)
    if not header.startswith("id,console-name,product-name"):
        os.remove(tmp)
        raise SystemExit(f"[{cat}] unexpected response (not a price-guide CSV) — check the token/category")
    if rows < MIN_ROWS[out_name]:
        os.remove(tmp)
        raise SystemExit(f"[{cat}] only {rows} rows (expected >= {MIN_ROWS[out_name]}) — refusing to replace")

    if os.path.exists(out):
        os.replace(out, out + ".prev")
    os.replace(tmp, out)
    print(f"[{cat}] {rows} rows -> {out_name}")


def _fetch_csv(url, tmp, cat):
    """Download to tmp, riding out 429 rate limits (with backoff) and — in
    TCG_PATIENT mode — full network outages (5-minute retry rounds)."""
    patient = bool(os.environ.get("TCG_PATIENT"))
    attempt = 0
    while attempt < 7:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tcg-predictor weekly refresh"})
            with urllib.request.urlopen(req, timeout=600, context=SSL_CTX) as resp, open(tmp, "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
            return
        except urllib.error.HTTPError as e:
            # 429 = rate limit, 5xx = transient upstream trouble (e.g. the
            # 2026-07-20 refresh died on a brief 503). Both are worth riding
            # out; anything else (403 bad token, 404) fails fast.
            if e.code != 429 and e.code < 500:
                raise
            wait = 60 * attempt
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            print(f"[{cat}] HTTP {e.code} — backing off {wait:.0f}s "
                  f"(attempt {attempt}/7)", flush=True)
            time.sleep(wait)
        except Exception as e:
            if not patient:
                raise
            print(f"[{cat}] network error ({type(e).__name__}) — waiting 5 min", flush=True)
            time.sleep(300)
            attempt = 0
    raise SystemExit(f"[{cat}] still failing after repeated backoff — giving up")


def main():
    t = token()
    for cat, out_name in CATEGORIES.items():
        download(cat, out_name, t)
        time.sleep(5)   # a little space between bulk downloads


if __name__ == "__main__":
    main()
