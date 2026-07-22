"""
One-command data refresh: TCGplayer + PriceCharting for every game, end to end.
(Named for its original weekly cadence; run_daily_refresh.sh now runs it every
morning via launchd.)

Run with the pipeline venv (which lives in the sibling one-piece/ data dir); from
the pipeline/ directory:

    ../../one-piece/.venv/bin/python weekly_refresh.py            # run everything
    ../../one-piece/.venv/bin/python weekly_refresh.py --list     # show the steps
    ../../one-piece/.venv/bin/python weekly_refresh.py --from unify   # resume after a failure
    ../../one-piece/.venv/bin/python weekly_refresh.py --only nm-price

Steps (in order):
  tcg-onepiece   TCGplayer One Piece: NEW cards only — one aggregation request
                 spots sets whose product count changed; only those sets are
                 scanned (details + art). NO pricing — that comes exclusively
                 from PriceCharting. Run the scraper by hand without --new-only
                 for an occasional full detail sweep.
  tcg-pokemon    Same for Pokémon.
  pc-download    Fresh PriceCharting CSVs for both games (2 requests).
  pc-match       Rebuild the current graded-price snapshot (matches new cards by
                 tcg-id) and APPEND today's snapshot into graded_price_history.
  pc-graded-new  Chart-page crawl for cards with no graded history yet (i.e. new
                 cards only) — serial, 1 req/s, polite.
  unify          Rebuild price_history_unified from the append-only sources.
  nm-price       Refresh the near_mint_price column the catalog sorts/displays by.
  ml-export      Re-export card features for the model.
  scorecard      Grade archived forecasts whose horizon has elapsed against
                 realized prices; refresh accuracy stats + self-error signals
                 that feed the next retrain.
  ml-embed       CLIP-embed images (resumable; only new images are processed).
  forecast       Retrain + regenerate all forecasts into predictions.db.

Every data source is append-only (INSERT OR REPLACE on date-keyed tables), so
old price history is never deleted; derived tables (unified, forecasts) are
rebuilt from those sources each run.
"""

import argparse
import atexit
import os
import subprocess
import sys
import time
from datetime import datetime

from _paths import DATA_DIR
from games import GAMES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # the scripts (this repo)
PY = os.path.join(DATA_DIR, ".venv", "bin", "python")      # the venv (sibling one-piece/)
LOCK = os.path.join(DATA_DIR, ".weekly_refresh.lock")      # one refresh at a time


def acquire_lock():
    """Skip cleanly (exit 0) if another refresh is still running; otherwise take
    the lock for this process. A lock left by a dead process is ignored."""
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read().strip())
        except ValueError:
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)   # raises if that pid is gone
                print(f"another refresh (pid {pid}) is still running — skipping this run")
                sys.exit(0)
            except ProcessLookupError:
                pass              # stale lock from a dead run
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(LOCK) and os.remove(LOCK))

STEPS = [
    # TCGplayer supplies the card catalog, details, and images ONLY — all
    # pricing comes exclusively from PriceCharting (steps below). One scrape
    # step per game in the registry; each is a single request on quiet nights.
    # --max-sets keeps the scheduled run bounded while a game onboards (a
    # fresh game has its whole catalog pending — e.g. Magic's 436 sets);
    # normal weeks change far fewer sets than the cap. Full backfills run
    # out-of-band via magic_backfill.sh / backfill_new_games.sh.
    *[(f"tcg-{g}", [*cfg["scraper"], "--new-only", "--no-history",
                    *(["--max-sets", "8"] if cfg["scraper"][0] == "tcg_scraper.py" else [])])
      for g, cfg in GAMES.items()],
    ("pc-download",   ["download_pricecharting.py"]),
    ("pc-gundam",     ["scrape_gundam_prices.py"]),
    ("pc-starwars",   ["scrape_starwars_prices.py"]),
    ("pc-match",      ["build_pricecharting.py"]),
    # --limit bounds the crawl (~50 min worst case) so a game mid-onboarding
    # can't turn the daily run into a day-long one; the backfill script does
    # the bulk crawling out-of-band.
    ("pc-graded-new", ["scrape_graded_history.py", "--game", "all", "--resume",
                       "--workers", "1", "--delay", "1.0", "--limit", "3000"]),
    ("unify",         ["build_unified_history.py"]),
    ("nm-price",      ["backfill_nm_price.py"]),
    ("ml-export",     ["export_for_ml.py"]),
    ("scorecard",     ["forecast_scorecard.py"]),
    ("ml-embed",      ["embed_images.py"]),
    ("art-comps",     ["art_comps.py"]),
    ("forecast",      ["forecast_predict.py"]),
    # Fridays only (the script no-ops other days): the weekly market report,
    # written into predictions.db so it ships with the normal data push.
    ("report",        ["market_report.py"]),
    # Art goes to S3 (the canonical store) only AFTER ml-embed has seen the
    # new files; art-sync then flags image_path from the bucket listing, so a
    # card is only site-visible once its art is actually fetchable.
    ("s3-upload",     ["s3_upload_images.py"]),
    ("art-sync",      ["sync_local_images.py"]),
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

    acquire_lock()

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
