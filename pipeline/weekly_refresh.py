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
                 scanned (details + art). NO pricing here. Run the scraper by
                 hand without --new-only for an occasional full detail sweep.
  tcg-pokemon    Same for Pokémon.
  pc-download    Fresh PriceCharting CSVs (still needed: the tcg-id -> pc_id
                 linkage that the graded crawl depends on).
  tcg-nm-prices  TCGplayer Near Mint prices for the WHOLE catalog — the ungraded
                 price source (graded stays PriceCharting). Search API, ~50
                 cards/request, into tcg_nm_history. Runs nightly; --resume makes
                 it restart-safe. Watch it live in refresh_status.py.
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
  pc-link-suggest Best-guess PC links for NEW cards (via each PC page's embedded
                 tcg-id) -> match_review.py queue. Non-fatal; forward-only.

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
from datetime import datetime, timezone

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
    # TCGplayer catalog scrape supplies card catalog, details, and images ONLY
    # (no pricing). One scrape step per game in the registry; each is a single
    # request on quiet nights. --max-sets keeps the scheduled run bounded while
    # a game onboards (a fresh game has its whole catalog pending — e.g. Magic's
    # 436 sets); normal weeks change far fewer sets than the cap. Full backfills
    # run out-of-band via magic_backfill.sh / backfill_new_games.sh.
    *[(f"tcg-{g}", [*cfg["scraper"], "--new-only", "--no-history",
                    *(["--max-sets", "8"] if cfg["scraper"][0] == "tcg_scraper.py" else [])])
      for g, cfg in GAMES.items()],
    # Public-page new-card matcher (weekly — this sits before the daily
    # --from tcg-nm-prices entry point, so it runs Sunday only). Sweeps known
    # PriceCharting console pages and flags pc_ids NOT in our match table into
    # ml_data/{game}_price_review_scraped.csv. SHADOW mode (default _scraped
    # suffix) — it must NOT overwrite the live CSV or build_pricecharting would
    # rebuild from 3-tier console data. pc-link-suggest (last step) reads these
    # candidates, reads each page's embedded tcg-id, and queues links for
    # match_review.py to confirm.
    ("pc-console-sweep", ["scrape_pc_prices.py"]),
    # Weekly full stale sweep (Sunday-only by position): probe EVERY visible
    # card whose NM series stopped advancing — incl. never-search-priced ones —
    # against the sales chart, so no priced card stays lost longer than a week.
    ("nm-rescue-full", ["rescue_stale_nm.py", "--all", "--rpm", "40"]),
    # Weekly re-check of cards deferred for missing TCGplayer market data
    # (table had no market price / chart contradicted it / flat frozen echo).
    # Graduates a card off the watch once the table price is live and the
    # chart moves again; until then the search-crawl price stays on display.
    ("tcg-market-watch", ["tcg_market_watch.py"]),
    # pc-download (paid PriceCharting bulk CSV) retired 2026-07-31 when the
    # subscription ended. Graded now comes from the public product-page crawl
    # (pc-graded-new, on a rotation) and ungraded from TCGplayer.
    # Ungraded/Near Mint prices now come from TCGplayer (graded stays PC). Walks
    # every game's sets via the search API (~50 cards/request), writing each
    # card's NM market price into tcg_nm_history for the unify step. --resume
    # skips sets already done today so a resumed nightly (--from tcg-nm-prices)
    # doesn't re-crawl. rpm 30 is deliberately gentle; raise it once we've seen
    # how TCGplayer handles the sustained nightly load.
    ("tcg-nm-prices", ["scrape_tcg_nm_prices.py", "--game", "all",
                       "--resume", "--delay", "1.0", "--rpm", "30"]),
    # Cross-check the high-value cards' search marketPrice against the detailed
    # endpoint's Near Mint market and flag disagreements into tcg_nm_review for
    # manual page inspection. TCGplayer's search marketPrice drifts to an asking
    # value on illiquid (no-recent-sale) cards — this catches the inflated ones
    # (e.g. vintage) without auto-correcting (the detailed number isn't reliable
    # either). Bounded to >= --min-price; always exits 0.
    # Skip-unchanged memo (2026-08-05): only cards whose search price moved
    # since their last check (or >7d stale) cost a request, so the nightly is
    # a few minutes, not 40.
    ("tcg-nm-verify", ["verify_high_value_nm.py", "--min-price", "250", "--rpm", "60"]),
    # Re-pull the detailed NM history for cards flagged TCGplayer-only (PC's
    # ungraded was wrong): the search crawl skips them, so this keeps their
    # ungraded series fresh + sales-based. Cheap (~a dozen cards); exits 0.
    ("tcg-nm-backfill", ["backfill_tcg_nm_history.py", "--refresh-existing", "--rpm", "40"]),
    # Second pass for cards the search crawl couldn't price tonight: if the
    # product page's sales chart still carries a recent NM series, adopt it
    # (tcg_nm_only) so the card keeps a live price instead of going stale.
    # Narrow window nightly (recent failures only); Sunday sweeps the long tail.
    ("tcg-nm-rescue",  ["rescue_stale_nm.py", "--rpm", "40"]),
    # Multi-printing products pinned to one variant (2026-08-08: search
    # marketPrice can reflect the premium printing — 1st Ed vs Unlimited).
    # --detect probes cards whose price jumped >= 2.5x their own trailing
    # median; --refresh re-pulls pinned cards' series on a weekly rotation.
    ("tcg-printing-pins", ["pin_printings.py", "--detect", "--refresh",
                           "--refresh-days", "7", "--rpm", "40"]),
    # Phase 1 of multi-printing (notes/printing-plan.md): ingest ALL variants'
    # NM series per product (labeled rows; base keeps flowing as printing='').
    # Monthly rotation, value-first, ~1h/night until the first sweep completes.
    ("printing-ingest", ["printing_ingest.py", "--rotate", "--refresh-days", "30",
                         "--limit", "2300", "--rpm", "40"]),
    # Imageless-card audit, NIGHTLY on a rotation (moved off the Sunday full
    # run 2026-08-03, where scanning all ~5k art-less cards took 3+ hours and
    # helped throttle TCGplayer). --limit 100/game/night (300 until 2026-08-05;
    # smaller slices shorten the night, the rotation just cycles over ~2 weeks
    # instead of one). Art a card gained flows into the site+model via this
    # same run's s3-upload/art-sync/ml-embed; cards TCGplayer removed are
    # deleted.
    ("imageless-audit", ["audit_imageless.py", "--refresh-days", "7",
                         "--limit", "100", "--rpm", "60"]),
    ("pc-gundam",     ["scrape_gundam_prices.py"]),
    ("pc-starwars",   ["scrape_starwars_prices.py"]),
    ("pc-match",      ["build_pricecharting.py"]),
    # Cards whose PC match says they're a special print (1st Edition/Shadowless)
    # but whose TCGplayer name doesn't show it get the printing appended to
    # their display name. Idempotent; re-stamps after Sunday's catalog rescrape.
    ("print-tags",    ["stamp_print_tags.py"]),
    # Graded prices' PRIMARY source now (paid bulk CSV retired). Night-aware
    # budget (2026-09-29, user decision: consolidate the graded rotation into
    # the weekly full-refresh night): under the twice-weekly cadence the old
    # nightly 2,400-page budget became 4,800/week against a ~19k due backlog,
    # so outside the value ranking's top slice graded prices never refreshed
    # (that night: digimon 5 pages, starwars 2, onepiece 1). The FULL-REBUILD
    # night (Sunday local = Mon UTC; a manual Monday-evening kick = Tue UTC)
    # runs one consolidated 12k-page rotation (~3h at 3 workers); the
    # Thursday run crawls never-crawled pages only (no --refresh-days), so
    # new cards still get their first graded series within the week. The
    # per-game floor on the --total-limit cut lives in the scraper itself.
    ("pc-graded-new", ["scrape_graded_history.py", "--game", "all", "--resume",
                       "--delay", "1.0"]
                      + (["--refresh-days", "30", "--workers", "3",
                          "--limit", "4000", "--total-limit", "12000"]
                         if datetime.now(timezone.utc).weekday() in (0, 1)
                         else ["--workers", "1", "--total-limit", "1500"])),
    # Price-integrity sweep: TCGplayer NM vs PC sales divergences -> review
    # CSV (no auto-action; the two failure classes need human judgment).
    ("nm-pc-sweep",   ["sweep_nm_vs_pc.py"]),
    ("unify",         ["build_unified_history.py"]),
    ("nm-price",      ["backfill_nm_price.py"]),
    ("ml-export",     ["export_for_ml.py"]),
    ("ml-embed",      ["embed_images.py"]),
    ("art-comps",     ["art_comps.py"]),
    # scorecard+forecast+report are the MODEL BLOCK, offloaded to the AWS trainer
    # (run_daily_refresh hands off after art-comps, runs these via aws_model_run.sh,
    # then continues at s3-upload). scorecard only needs the graded history + price
    # matrices — not the image embeddings above — so it follows art-comps instead
    # of preceding ml-embed, keeping the offloaded block contiguous.
    ("scorecard",     ["forecast_scorecard.py"]),
    ("forecast",      ["forecast_predict.py"]),
    # Fridays only (the script no-ops other days): the weekly market report,
    # written into predictions.db so it ships with the normal data push.
    ("report",        ["market_report.py"]),
    # Art goes to S3 (the canonical store) only AFTER ml-embed has seen the
    # new files; art-sync then flags image_path from the bucket listing, so a
    # card is only site-visible once its art is actually fetchable.
    ("s3-upload",     ["s3_upload_images.py"]),
    ("art-sync",      ["sync_local_images.py"]),
    # Last + non-fatal: for any NEW card the paid API would have matched, read
    # each candidate PC page's embedded tcg-id and queue an exact link into
    # match_review.py. Runs after pc-match so it only suggests cards STILL
    # unlinked; reads the sweep's _scraped review CSVs (flip to '' at cutover).
    # pc_link_suggest.py always exits 0 — a link hiccup must not fail the night.
    ("pc-link-suggest", ["pc_link_suggest.py"]),
]


def main():
    names = [n for n, _ in STEPS]
    ap = argparse.ArgumentParser(description="Weekly data refresh (both games, both sources)")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    ap.add_argument("--from", dest="start", choices=names, help="start at this step (resume)")
    ap.add_argument("--to", dest="stop", choices=names,
                    help="stop AFTER this step (inclusive). With --from, runs a "
                         "contiguous slice — used to split the run around the AWS "
                         "model block: Mac does ...--to art-comps, then s3-upload...")
    ap.add_argument("--only", choices=names, help="run a single step")
    args = ap.parse_args()

    if args.list:
        for n, cmd in STEPS:
            print(f"{n:14} {' '.join(cmd)}")
        return

    acquire_lock()

    lo = names.index(args.start) if args.start else 0
    hi = names.index(args.stop) + 1 if args.stop else len(STEPS)
    todo = [s for s in STEPS if s[0] == args.only] if args.only else STEPS[lo:hi]

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
