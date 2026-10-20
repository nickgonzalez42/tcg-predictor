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
  tcg-csv-export Seller Portal pricing CSV per game (Playwright, signed in as
                 the user; export_tcg_csv.py --login once). Non-fatal.
  tcg-csv-import The CSVs -> tcg_nm_history (base + labeled printings) and
                 one-piece/tcg_csv.db (every condition). The ungraded price
                 source since 2026-10-17. Non-fatal.
  tcg-nm-prices  TCGplayer search crawl: fallback for games without a CSV, and
                 on the weekly crawl night (Saturday local) the market-depth +
                 search-vs-CSV pass for every game. --resume makes it
                 restart-safe. Watch it live in refresh_status.py.
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
from datetime import datetime, timedelta, timezone

from _paths import DATA_DIR
from games import GAMES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # the scripts (this repo)
PY = os.path.join(DATA_DIR, ".venv", "bin", "python")      # the venv (sibling one-piece/)
LOCK = os.path.join(DATA_DIR, ".weekly_refresh.lock")      # one refresh at a time

# The weekly CRAWL + full-rebuild night is SATURDAY (local). Since the
# 2026-10-18 per-night start times (Saturday 6:20 PM, which is still
# Saturday in UTC) the UTC-weekday rule can no longer identify it, so
# run_daily_refresh.sh decides from the LOCAL weekday and exports
# TCG_CRAWL_NIGHT=1|0; the UTC rule (Sunday = 6) remains only as the
# fallback for a hand-run resume that didn't set the flag.
_env_crawl = os.environ.get("TCG_CRAWL_NIGHT", "").strip()
CRAWL_NIGHT = (_env_crawl == "1") if _env_crawl in ("0", "1") \
    else datetime.now(timezone.utc).weekday() == 6

# --- the evened week (2026-10-18, user: "schedule the updates to be more
# even" + per-night start times tuned to finish by 06:00) ------------------
# Saturday used to carry every heavy rotation in one ~41 h burst. Now each
# night carries one slice, keyed off the LOCAL weekday (Mon=0 .. Sun=6):
#   graded crawl      EVERY night, 2,500 pages with a 30-day refresh (the
#                     rotation was ~3x behind its target: 120,808 of 154,793
#                     cards overdue on 2026-10-18)
#   depth crawl       one or two games a night (full catalog on Saturday
#                     took 193 min); Magic's 77 min sits on a backfill-free
#                     night
#   detailed backfill Tuesday + Friday (its series only has weekly points)
#   console sweep, full price-table rebuild, full-file push: Saturday
#   3m/6m/12m retrain first Saturday of the month (local_model_run.sh)
LOCAL_WEEKDAY = datetime.now().weekday()
DEPTH_SLICE = {
    0: "magic",
    1: "pokemon",
    2: "yugioh",
    3: "onepiece,lorcana",
    4: "digimon,gundam,starwars",
    5: "fleshandblood,sorcery,riftbound,cyberpunk",
    6: "none",
}
BACKFILL_DAYS = (1, 4)   # Tuesday, Friday


def night_plan(local_weekday):
    """(depth games for the night, run the detailed backfill?)"""
    return DEPTH_SLICE[local_weekday], local_weekday in BACKFILL_DAYS


DEPTH_GAMES, BACKFILL_TONIGHT = night_plan(LOCAL_WEEKDAY)

# --- finish-by deadline (2026-10-18, user: "have them done at 6AM") ---------
# A CSV night is ~10.5 h of work, but the ROTATION steps are the variable
# part: tcg-sales has run 110 min and once 1,299; tcg-nm-rescue 0.6 and 25.
# A fixed start time cannot absorb that, so instead each rotation is trimmed
# to what still fits before TCG_REFRESH_DEADLINE (local HH:MM, unset = no
# deadline, which is also the behaviour for any hand-run resume). Every step
# that produces the night's OUTPUT — the CSV import, unify, the price
# column, the ML inputs — is protected and always runs in full; only
# backlog rotations give up their budget, and never below a floor, so each
# still makes progress. The CRAWL night is exempt: it is inherently ~40 h
# and is scheduled as a two-day job instead.
DEADLINE = "" if CRAWL_NIGHT else os.environ.get("TCG_REFRESH_DEADLINE", "").strip()
# Measured wall-clock minutes per step (2026-10 logs), used only to decide
# how much of a rotation fits. Steps absent here are treated as ~2 min.
NOMINAL_MIN = {
    "tcg-csv-export": 5, "tcg-csv-import": 5, "tcg-sku-resolve": 11,
    "tcg-nm-prices": 30, "tcg-nm-verify": 12, "tcg-nm-backfill": 70,
    "tcg-sales": 15, "tcg-nm-rescue": 15, "tcg-printing-pins": 6,
    "printing-ingest": 63, "imageless-audit": 22, "pc-gundam": 2,
    "pc-starwars": 3, "pc-graded-new": 120, "nm-pc-sweep": 4, "unify": 20,
    "nm-price": 2, "art-comps": 6, "s3-upload": 3, "art-sync": 15,
    "pc-link-suggest": 70, "pc-console-discover": 2, "pc-console-sweep": 91,
    "nm-rescue-full": 2, "tcg-market-watch": 1,
}
# Rotation steps whose budget may be cut: step -> (flag, floor). The flag's
# value is scaled by the fraction of its nominal time that still fits.
TRIMMABLE = {
    "tcg-sku-resolve": ("--resolve", 50),
    "tcg-sales": ("--total-limit", 100),
    "printing-ingest": ("--limit", 300),
    "imageless-audit": ("--limit", 20),
    "pc-link-suggest": ("--drain", 200),
    "pc-graded-new": ("--total-limit", 800),
}
# Work that happens BETWEEN art-comps and s3-upload (the model block, run by
# run_daily_refresh.sh) plus the prod push, reserved so the rotations don't
# eat the time the forecasts and the deploy need.
MODEL_BLOCK_MIN = 200
PUSH_MIN = 25


def deadline_at(now):
    """The next occurrence of TCG_REFRESH_DEADLINE, as a datetime."""
    hh, mm = (int(x) for x in DEADLINE.split(":"))
    d = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return d if d > now else d + timedelta(days=1)


def apply_deadline(todo, idx, now):
    """Trim the budget of todo[idx] to what fits before the deadline.
    Returns the (possibly rewritten) command."""
    name, cmd = todo[idx]
    if name not in TRIMMABLE:
        return cmd
    flag, floor = TRIMMABLE[name]
    if flag not in cmd:
        return cmd
    end = deadline_at(now)
    remaining = (end - now).total_seconds() / 60.0
    # Everything after this step that we must still pay for.
    rest = sum(NOMINAL_MIN.get(n, 2) for n, _ in todo[idx + 1:])
    if any(n == "art-comps" for n, _ in todo[idx:]):
        rest += MODEL_BLOCK_MIN + PUSH_MIN
    elif todo[-1][0] == "pc-link-suggest":
        # The Phase C slice (--from s3-upload): push_data.sh follows it.
        # First night (2026-10-20) the drain was trimmed with "0 min
        # reserved" and the push itself ran past the deadline.
        rest += PUSH_MIN
    available = remaining - rest
    nominal = NOMINAL_MIN.get(name, 2)
    cmd = list(cmd)
    if name == "pc-link-suggest":
        # This one takes a CLOCK, not just a count: the Monday after a
        # console sweep it faces thousands of new pages before its drain
        # (2026-10-20: 108 min, an hour past the deadline). Always hand it
        # the minutes the night can spare, floor 5 so it still triages.
        cmd += ["--max-minutes", str(max(int(available), 5))]
        print(f"    [deadline {DEADLINE}] {name}: {int(remaining)} min left, "
              f"{int(rest)} min reserved -> --max-minutes {max(int(available), 5)}", flush=True)
    if available >= nominal:
        return cmd
    i = cmd.index(flag)
    want = int(cmd[i + 1])
    scaled = max(floor, int(want * max(available, 0) / nominal))
    cmd[i + 1] = str(scaled)
    print(f"    [deadline {DEADLINE}] {name}: {int(remaining)} min left, "
          f"{int(rest)} min reserved -> {flag} {want} trimmed to {scaled}", flush=True)
    return cmd


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
    # New-console discovery (2026-10-10): PriceCharting's category pages list
    # every set it tracks; consoles we don't know yet (every set released
    # after the paid CSV ended) are recorded for the sweep below, so a new
    # set's cards flow into link-suggest and the graded crawl without a hand
    # edit. Gundam/Star Wars (no category page) probe slugs for unlinked sets.
    ("pc-console-discover", ["pc_console_discover.py"]),
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
    # Near Mint prices come from the SELLER PORTAL CSV export (2026-10-17):
    # export_tcg_csv.py drives a signed-in Chrome through "Export Filtered
    # CSV" for every game (~5 min), import_tcg_csv.py turns the files into
    # tcg_nm_history base + labeled-printing rows (+ per-condition history in
    # one-piece/tcg_csv.db). Both are non-fatal: a dead session (run
    # export_tcg_csv.py --login) or a refused file leaves that game to the
    # crawl step below, so no night loses its prices.
    ("tcg-csv-export", ["export_tcg_csv.py", "--game", "all", "--non-fatal"]),
    ("tcg-csv-import", ["import_tcg_csv.py", "--non-fatal"]),
    # Catalog cards the CSV couldn't match by name (products TCGplayer
    # renamed since the catalog crawl): fetch their SKU ids from the
    # detailed endpoint so TOMORROW's import matches them by SKU. ~300
    # cards/night at 40 rpm ≈ 8 min; the backlog (~12k) drains in weeks.
    ("tcg-sku-resolve", ["import_tcg_csv.py", "--resolve", "300", "--rpm", "40",
                         "--non-fatal"]),
    # The TCGplayer search crawl (~50 cards/request) is now the FALLBACK +
    # the depth pass: any game whose CSV didn't land tonight is priced by it
    # as before, and tonight's DEPTH_SLICE games are walked for market depth
    # and the search price (tcg_depth_history.market_price, the
    # search-vs-CSV cross-check) without writing price rows. Every other
    # CSV-priced game is skipped. --resume skips sets already done today.
    ("tcg-nm-prices", ["scrape_tcg_nm_prices.py", "--game", "all",
                       "--resume", "--delay", "1.0", "--rpm", "30",
                       "--csv-games", "depth-only", "--depth-games", DEPTH_GAMES]),
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
    # ungraded series fresh + sales-based. ~70 min for the ~2.5k-card cohort;
    # the series it fetches only has WEEKLY points, so Tuesday + Friday is
    # as fresh as nightly was (BACKFILL_DAYS). Exits 0.
    *([("tcg-nm-backfill", ["backfill_tcg_nm_history.py", "--refresh-existing", "--rpm", "40"])]
      if BACKFILL_TONIGHT else []),
    # Sales volume rotation (2026-10-14, user request): walk the catalog
    # value-first recording each card's "items sold" buckets from the same
    # detailed endpoint tcg-nm-backfill uses; a card's first touch lands ~a
    # year of history, so the rotation IS the backfill.
    # --no-yield is load-bearing (2026-10-16): as a refresh step this script's
    # yield-to-refresh guard would find its own parent and sleep forever.
    #
    # PARKED 2026-10-18 (user decision): 4,000/night cost ~110 min a night,
    # ~11 h a week, for data NOTHING currently reads — no code outside this
    # script's own writers touches tcg_sales_history, and the liquidity /
    # ladder features built on it (TCG_FC_FEAT_LIQ, TCG_FC_FEAT_LADDER) lost
    # their A/B on 2026-10-10 and are set in no runner. Meanwhile the graded
    # crawl is ~3x behind its 30-day target (120,808 of 154,793 cards
    # overdue), and this is the only nightly budget big enough to fund it.
    # Dropped to a token slice so the path stays exercised and the backfill
    # still inches forward. TO RESTORE: put --total-limit back to 4000 (and
    # NOMINAL_MIN["tcg-sales"] back to 110) once a feature reads this data.
    ("tcg-sales", ["backfill_sales_volume.py", "--game", "all", "--rpm", "40",
                   "--refresh-days", "30", "--total-limit", "500", "--no-yield"]),
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
    # Workers 3 -> 2 on the full night (2026-10-13): Cloudflare rate-blocked
    # the crawl for ~2 days after the 10-11 one-off extras (sealed boxes,
    # console discovery/sweep, link-drain backlog) stacked onto the 3-worker
    # rotation. ~2 req/s sustained instead of ~3 costs the Sunday run an hour
    # but keeps the steady state well inside what months of crawling showed
    # PC tolerates. The circuit breaker caps the damage if a block recurs.
    # Graded is a NIGHTLY rotation since 2026-10-18: 2,500 pages a night
    # (~2 h at the 2-worker rate Cloudflare has tolerated) on every night
    # of the week, ~17.5k/week against the old 12k Saturday burst. Workers
    # stay at 2 — 3 got the crawl blocked for two days on 2026-10-13. The
    # per-game floor on --total-limit lives in the scraper; the finish-by
    # deadline may trim this one (TRIMMABLE) on a night that runs long.
    ("pc-graded-new", ["scrape_graded_history.py", "--game", "all", "--resume",
                       "--delay", "1.0", "--refresh-days", "30", "--workers", "2",
                       "--limit", "4000", "--total-limit", "2500"]),
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
    # --drain: work back through the link backlog the 2026-08 cutover
    # grandfathered (~99k unmatched PC pages never read for tcg-ids; whole
    # sets were missing graded data). ~1 page/s, so 1500 adds ~25-45 min per
    # run night; small games drain first, magic is the months-long tail.
    # 1,500 on the crawl night; a smaller slice on CSV nights (2026-10-20: a
    # 1,083-page drain ran ~70 min, not the 48 the Saturday rate suggested,
    # and pushed the first scheduled night an hour past 06:00).
    ("pc-link-suggest", ["pc_link_suggest.py", "--drain", "1500" if CRAWL_NIGHT else "600"]),
]


def main():
    names = [n for n, _ in STEPS]
    ap = argparse.ArgumentParser(description="Weekly data refresh (both games, both sources)")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    ap.add_argument("--from", dest="start", choices=names, help="start at this step (resume)")
    ap.add_argument("--to", dest="stop", choices=names + ["catalog"],
                    help="stop AFTER this step (inclusive). With --from, runs a "
                         "contiguous slice — used to split the run around the AWS "
                         "model block: Mac does ...--to art-comps, then s3-upload... "
                         "'catalog' = the last TCGplayer catalog crawl step (the daily "
                         "night's crawl-only pass; stays right when games are added).")
    ap.add_argument("--only", choices=names, help="run a single step")
    args = ap.parse_args()
    if args.stop == "catalog":
        args.stop = [n for n in names if n in {f"tcg-{g}" for g in GAMES}][-1]

    if args.list:
        for n, cmd in STEPS:
            print(f"{n:14} {' '.join(cmd)}")
        return

    acquire_lock()

    lo = names.index(args.start) if args.start else 0
    hi = names.index(args.stop) + 1 if args.stop else len(STEPS)
    todo = [s for s in STEPS if s[0] == args.only] if args.only else STEPS[lo:hi]

    print(f"weekly refresh — {datetime.now():%Y-%m-%d %H:%M} — {len(todo)} step(s)"
          + (f" — finishing by {DEADLINE}" if DEADLINE else ""), flush=True)
    print(flush=True)
    timings = []
    for i, (name, cmd) in enumerate(todo):
        if DEADLINE:
            cmd = apply_deadline(todo, i, datetime.now())
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
