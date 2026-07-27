#!/usr/bin/env python3
"""Scrape current PriceCharting prices from CONSOLE pages for the bulk-CSV
games — the self-sourced replacement for the paid price-guide download.

Generalizes scrape_gundam_prices.py to every game, but *simpler*: the bulk-CSV
games already carry a complete pc_id -> tcg-id map (built from the paid CSV and
kept in the pricecharting table), so there is no re-matching. We just:

  1. read (pc_id, product_id, pc_console) for the game from pricecharting.db,
  2. crawl each distinct console page (slug = slugify(console), overridable),
  3. join scraped prices to the known pc_id map and emit the exact bulk-CSV
     format build_pricecharting.py already reads — tcg-id = our product_id.

Console pages carry three tiers (Ungraded / Grade 9 / PSA 10); the deep tiers
still come from scrape_graded_history's rotation. A scraped pc_id that ISN'T in
our map is a new-card linking candidate -> the review CSV.

Shadow mode (default): writes pricecharting_{game}_scraped.csv, which the live
pipeline never reads. Diff it against the paid CSV with compare_pc_prices.py.
Cutover = --out-suffix '' (overwrite the real CSV the match step consumes).

Run:  .venv/bin/python scrape_pc_prices.py --games digimon
      .venv/bin/python scrape_pc_prices.py                 # all bulk-CSV games
"""
import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, priced_games
# Reuse the battle-tested console parser (proven game-agnostic in the
# 2026-07-26 diff spike: written for gundam, read digimon pages unchanged).
from scrape_gundam_prices import crawl_console, slugify, HEADER

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
# console display name -> real PC slug, for the sets where slugify() misses
# (colons, apostrophes, PC's own abbreviations). Grows as the sweep reports 404s.
SLUG_OVERRIDES_CSV = os.path.join(BASE, "ml_data", "pc_console_slugs.csv")


def bulk_games():
    """The games we currently pay PriceCharting a bulk CSV for (gundam/starwars
    already self-scrape their console pages)."""
    return [g for g in priced_games() if GAMES[g].get("pc_category")]


def load_slug_overrides(game):
    if not os.path.exists(SLUG_OVERRIDES_CSV):
        return {}
    with open(SLUG_OVERRIDES_CSV, newline="", encoding="utf-8") as f:
        return {r["console"]: r["slug"] for r in csv.DictReader(f) if r["game"] == game}


def apostrophe_slug(console):
    """slugify() strips apostrophes, but PC keeps them in the slug URL-encoded
    as %27 (e.g. Starter Deck 03: Heaven's Yellow ->
    digimon-starter-deck-03-heaven%27s-yellow). Fallback for the 'X's Y' class."""
    import re
    s = re.sub(r"[^a-z0-9']+", "-", console.lower()).strip("-")
    return re.sub(r"-+", "-", s).replace("'", "%27")


def scrape_game(conn, game, suffix, limit_sets):
    # Known matches: pc_id -> our product_id, grouped by console.
    consoles = {}          # console display name -> {pc_id: product_id}
    pcid_to_tcg = {}
    for pc_id, product_id, console in conn.execute(
            "SELECT pc_id, product_id, pc_console FROM pricecharting "
            "WHERE game=? AND pc_id IS NOT NULL AND pc_console IS NOT NULL", (game,)):
        consoles.setdefault(console, {})[pc_id] = product_id
        pcid_to_tcg[pc_id] = product_id

    overrides = load_slug_overrides(game)
    names = sorted(consoles)
    if limit_sets:
        names = names[:limit_sets]

    rows, review, missing, found = [], [], [], []
    priced_pcids = set()
    for console in names:
        slug = overrides.get(console) or slugify(console)
        products = crawl_console(slug)
        # PC keeps apostrophes as %27 where slugify strips them — retry that
        # form before giving up (handles the whole "X's Y" set class).
        if products is None and "'" in console and console not in overrides:
            alt = apostrophe_slug(console)
            if alt != slug:
                products = crawl_console(alt)
                if products is not None:
                    slug = alt
        if products is None:
            missing.append((console, slug))
            continue
        found.append((console, slug, len(products)))
        for p in products:
            pc_id = int(p["pc_id"])
            tcg = pcid_to_tcg.get(pc_id)
            if tcg is None:
                review.append((console, pc_id, p.get("name", ""), "pc_id not in our match table"))
                continue
            priced_pcids.add(pc_id)
            rows.append({
                "id": pc_id,
                "console-name": console,
                "product-name": p.get("name", ""),
                "loose-price": f"${p['ungraded']}" if p.get("ungraded") else "",
                "graded-price": f"${p['grade9']}" if p.get("grade9") else "",
                "manual-only-price": f"${p['psa10']}" if p.get("psa10") else "",
                "tcg-id": tcg,
            })

    out = os.path.join(BASE, GAMES[game]["pc_csv"].replace(".csv", f"{suffix}.csv"))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out)

    rev_out = os.path.join(BASE, "ml_data", f"{game}_price_review{suffix}.csv")
    os.makedirs(os.path.dirname(rev_out), exist_ok=True)
    with open(rev_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["console", "pc_id", "pc_name", "why"])
        w.writerows(review)

    known = len(pcid_to_tcg)
    cov = 100.0 * len(priced_pcids) / known if known else 0.0
    print(f"[{game}] consoles: {len(found)} scraped, {len(missing)} missing-slug | "
          f"priced {len(priced_pcids)}/{known} known pc_ids ({cov:.1f}% coverage) | "
          f"{len(review)} unmatched(new-card) -> {os.path.basename(out)}")
    if missing:
        print(f"[{game}] MISSING CONSOLE SLUGS (add to pc_console_slugs.csv):")
        for console, slug in missing[:20]:
            print(f"    '{console}' tried /console/{slug}")
    return cov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", help="comma-separated (default: all bulk-CSV games)")
    ap.add_argument("--out-suffix", default="_scraped",
                    help="'_scraped' (shadow, default) or '' (cutover: overwrite the live CSV)")
    ap.add_argument("--limit-sets", type=int, default=None, help="first N consoles per game (testing)")
    args = ap.parse_args()

    games = args.games.split(",") if args.games else bulk_games()
    conn = sqlite3.connect(PC_DB, timeout=60)
    for game in games:
        scrape_game(conn, game, args.out_suffix, args.limit_sets)
    conn.close()


if __name__ == "__main__":
    main()
