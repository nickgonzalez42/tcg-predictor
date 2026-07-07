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
import urllib.request

import certifi

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# category slug -> output CSV (the names build_pricecharting.py expects)
CATEGORIES = {
    "pokemon-cards": "pricecharting_pokemon.csv",
    "one-piece-cards": "pricecharting_onepiece.csv",
}
MIN_ROWS = {"pricecharting_pokemon.csv": 50_000, "pricecharting_onepiece.csv": 5_000}


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

    req = urllib.request.Request(url, headers={"User-Agent": "tcg-predictor weekly refresh"})
    with urllib.request.urlopen(req, timeout=600, context=SSL_CTX) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)

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


def main():
    t = token()
    for cat, out_name in CATEGORIES.items():
        download(cat, out_name, t)


if __name__ == "__main__":
    main()
