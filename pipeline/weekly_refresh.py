"""
One-command weekly refresh: TCGplayer + PriceCharting for both games, end to end.

Run with the pipeline venv (which lives in the sibling one-piece/ data dir); from
the pipeline/ directory:

    ../../one-piece/.venv/bin/python weekly_refresh.py            # run everything
    ../../one-piece/.venv/bin/python weekly_refresh.py --list     # show the steps
    ../../one-piece/.venv/bin/python weekly_refresh.py --from unify   # resume after a failure
    ../../one-piece/.venv/bin/python weekly_refresh.py --only nm-price

Steps (in order):
  tcg-onepiece   TCGplayer One Piece: new cards get full info/images; existing
                 cards get new weekly price buckets (append-only).
  tcg-pokemon    Same for Pokémon.
  pc-download    Fresh PriceCharting CSVs for both games (2 requests).
  pc-match       Rebuild the current graded-price snapshot (matches new cards by
                 tcg-id) and APPEND today's snapshot into graded_price_history.
  pc-graded-new  Chart-page crawl for cards with no graded history yet (i.e. new
                 cards only) — serial, 1 req/s, polite.
  unify          Rebuild price_history_unified from the append-only sources.
  nm-price       Refresh the near_mint_price column the catalog sorts/displays by.
  ml-export      Re-export card features for the model.
  ml-embed       CLIP-embed images (resumable; only new images are processed).
  forecast       Retrain + regenerate all forecasts into predictions.db.

Every data source is append-only (INSERT OR REPLACE on date-keyed tables), so
old price history is never deleted; derived tables (unified, forecasts) are
rebuilt from those sources each run.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

from _paths import DATA_DIR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # the scripts (this repo)
PY = os.path.join(DATA_DIR, ".venv", "bin", "python")      # the venv (sibling one-piece/)

STEPS = [
    ("tcg-onepiece",  ["tcg_onepiece_scraper.py", "--refresh-days", "7"]),
    ("tcg-pokemon",   ["tcg_pokemon_scraper.py", "--refresh-days", "7"]),
    ("pc-download",   ["download_pricecharting.py"]),
    ("pc-match",      ["build_pricecharting.py"]),
    ("pc-graded-new", ["scrape_graded_history.py", "--game", "all", "--resume",
                       "--workers", "1", "--delay", "1.0"]),
    ("unify",         ["build_unified_history.py"]),
    ("nm-price",      ["backfill_nm_price.py"]),
    ("ml-export",     ["export_for_ml.py"]),
    ("ml-embed",      ["embed_images.py"]),
    ("art-comps",     ["art_comps.py"]),
    ("forecast",      ["forecast_predict.py"]),
]


def main():
    names = [n for n, _ in STEPS]
    ap = argparse.ArgumentParser(description="Weekly data refresh (both games, both sources)")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    ap.add_argument("--from", dest="start", choices=names, help="start at this step (resume)")
    ap.add_argument("--only", choices=names, help="run a single step")
    args = ap.parse_args()

    if args.list:
        for n, cmd in STEPS:
            print(f"{n:14} {' '.join(cmd)}")
        return

    todo = STEPS
    if args.only:
        todo = [s for s in STEPS if s[0] == args.only]
    elif args.start:
        todo = STEPS[names.index(args.start):]

    print(f"weekly refresh — {datetime.now():%Y-%m-%d %H:%M} — {len(todo)} step(s)\n", flush=True)
    timings = []
    for name, cmd in todo:
        print(f"=== {name}: {' '.join(cmd)} ===", flush=True)
        t0 = time.time()
        result = subprocess.run([PY, os.path.join(SCRIPT_DIR, cmd[0]), *cmd[1:]], cwd=DATA_DIR)
        mins = (time.time() - t0) / 60
        timings.append((name, mins, result.returncode))
        if result.returncode != 0:
            print(f"\n✗ {name} failed (exit {result.returncode}) after {mins:.1f} min.")
            print(f"  Fix the issue, then resume with:  weekly_refresh.py --from {name}")
            summary(timings)
            sys.exit(result.returncode)
        print(f"--- {name} done in {mins:.1f} min ---\n", flush=True)

    summary(timings)
    print("\n✓ refresh complete")


def summary(timings):
    print("\nstep summary:")
    for name, mins, code in timings:
        print(f"  {'ok ' if code == 0 else 'FAIL'} {name:14} {mins:7.1f} min")


if __name__ == "__main__":
    main()
