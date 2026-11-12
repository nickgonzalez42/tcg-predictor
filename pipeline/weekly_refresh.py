"""
One-command data refresh: TCGplayer + PriceCharting for every game, end to end.
(Named for its original weekly cadence; run_daily_refresh.sh now runs it every
night via launchd.)

The nightly runs it in two parts (2026-11-12): --core, every step the night's
prices and forecasts are built from, and --tail, the rate-limited rotation
crawls, which start only after the site has the new data. See TAIL below.

Run with the pipeline venv (which lives in the sibling one-piece/ data dir); from
the pipeline/ directory:

    ../../one-piece/.venv/bin/python weekly_refresh.py            # run everything
    ../../one-piece/.venv/bin/python weekly_refresh.py --list     # show the steps
    ../../one-piece/.venv/bin/python weekly_refresh.py --from unify   # resume after a failure
    ../../one-piece/.venv/bin/python weekly_refresh.py --only nm-price
    ../../one-piece/.venv/bin/python weekly_refresh.py --tail     # the rotations only

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
import signal
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
#   6m/12m retrain first Saturday of the month (local_model_run.sh; 3m retired 2026-10-24)
def night_weekday():
    """Local weekday (Mon=0) of the night this run belongs to: the day before
    TCG_BATCH_DATE (the morning the batch goes live). The clock gave the
    wrong slice to anything that started after midnight, a resume above all;
    hand runs without the label still use today."""
    b = os.environ.get("TCG_BATCH_DATE", "").strip()
    try:
        return (datetime.strptime(b, "%Y-%m-%d") - timedelta(days=1)).weekday()
    except ValueError:
        return datetime.now().weekday()


LOCAL_WEEKDAY = night_weekday()
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
#
# 2026-11-10, user: "7AM is a goal not a hard rule. Let's make it so it isn't
# forced to end." The deadline is now a TARGET: the planner still projects
# every rotation against it and says in the log when a step is going to
# carry the night past it, and the ledger still scores the night against it,
# but no budget is trimmed and nothing is stopped early. The old trimming is
# kept behind TCG_REFRESH_DEADLINE_MODE=hard.
DEADLINE = "" if CRAWL_NIGHT else os.environ.get("TCG_REFRESH_DEADLINE", "").strip()
DEADLINE_MODE = os.environ.get("TCG_REFRESH_DEADLINE_MODE", "goal").strip().lower() or "goal"
# Measured wall-clock minutes per step, used only to decide how much of a
# rotation fits before the deadline. These are the 80th percentile of the
# last 8 nights' logs (recomputed 2026-10-27) — NOT the median: the point is
# to reserve enough that a slow-but-normal night still lands on time.
# Steps absent here are treated as ~2 min.
# 2026-10-27: tcg-nm-prices was carrying a nominal of 30 while really taking
# 25-93 min (max 193) — a 60+ min hole in every night's reservation, and the
# main reason the 2026-10-27 run finished at ~08:00 against a 06:00 deadline.
# 2026-11-12: recomputed from the ten nights 11-03..11-12. The rotations had
# drifted far from their old numbers once the daytime agents took the bulk
# of the graded and sales crawls (pc-graded-new was reserved 129 min and
# takes 1 to 3; pc-link-suggest 66 against 5 to 22), while unify and
# art-comps had grown past theirs. tcg-nm-depth carries what tcg-nm-prices
# used to take (the two were one step); tcg-nm-prices is now only the crawl
# for a game whose CSV did not land, and has no measurements yet.
NOMINAL_MIN = {
    "tcg-magic": 10, "tcg-onepiece": 3, "tcg-starwars": 1, "tcg-pokemon": 3,
    "tcg-digimon": 4,
    "tcg-csv-export": 5, "tcg-csv-import": 7, "tcg-sku-resolve": 2,
    "tcg-nm-prices": 10, "tcg-nm-depth": 92, "tcg-nm-verify": 7, "tcg-nm-backfill": 69,
    "tcg-sales": 3, "tcg-nm-rescue": 1, "tcg-printing-pins": 5,
    "printing-ingest": 63, "imageless-audit": 21, "art-recheck": 12, "pc-gundam": 2,
    "pc-starwars": 3, "pc-graded-new": 3, "nm-pc-sweep": 5, "unify": 27,
    "nm-price": 3, "card-trends": 3, "art-comps": 13, "s3-upload": 4, "art-sync": 7,
    "pc-link-suggest": 22, "pc-console-discover": 2, "pc-console-sweep": 94,
    "nm-rescue-full": 1, "tcg-market-watch": 1,
}
# --- the tail (2026-11-12, refresh review) ----------------------------------
# These are rotation crawls: each night they refresh a slice of a backlog,
# at a request rate the source sets, and nothing the same night publishes
# reads what they fetch (checked step by step, below). They used to sit in
# the middle of the night, so two to four hours of rate-limited crawling
# stood between the fresh prices and the site every night, and a stall in
# any of them held the whole night up. The nightly now runs the steps the
# prices and forecasts are built from first (--core), publishes, and only
# then runs these (--tail): what they fetch reaches the site one night
# later, on rotations of 7 to 30 days. A tail step that fails is logged and
# the tail moves on; nothing after it depends on it.
#   pc-console-discover, pc-console-sweep  crawl night only; read by
#                    pc-link-suggest alone
#   tcg-sku-resolve  SKU ids for TOMORROW's CSV import
#   tcg-nm-depth     market depth + the search price for the night's depth
#                    games: no price rows (see tcg-nm-prices for the split)
#   tcg-nm-verify    flags into tcg_nm_review, read by a person
#   tcg-sales        sales-volume trickle (the daytime agent does the bulk)
#   printing-ingest  labelled-printing series, 30-day rotation; the CSV
#                    import carries the printings' nightly prices
#   imageless-audit, art-recheck  art the next night's ml-embed + s3-upload
#                    pick up
#   pc-graded-new    fallback graded crawl (the daytime agent does the bulk)
#   pc-link-suggest  link candidates for match_review.py
# NOT in the tail, because the same night's unify needs them: the CSV steps,
# tcg-nm-prices (the fallback for a game whose CSV did not land),
# tcg-nm-backfill and tcg-printing-pins (the only price source for the
# TCGplayer-only and pinned cards, which the CSV import skips), tcg-nm-rescue.
TAIL = ("pc-console-discover", "pc-console-sweep", "tcg-sku-resolve", "tcg-nm-depth",
        "tcg-nm-verify", "tcg-sales", "printing-ingest", "imageless-audit",
        "art-recheck", "pc-graded-new", "pc-link-suggest")
TAIL_CRAWL_NIGHT_ONLY = ("pc-console-discover", "pc-console-sweep")
# The tail starts no new step at or after this local time on the batch's own
# day: tomorrow night's run must find the machine free.
TAIL_STOP = os.environ.get("TCG_TAIL_STOP", "16:30").strip()
# ...and a step still running at this time is stopped. launchd does not start
# a job whose last run is still alive, so a tail that ran on would cost the
# next night its start (the earliest is Saturday's, 18:20).
TAIL_HARD_STOP = os.environ.get("TCG_TAIL_HARD_STOP", "17:30").strip()
# Exit code when another refresh holds the lock. It used to be 0, which the
# nightly read as "the crawls are done" and went on to the model on stale data.
EXIT_LOCKED = 75
STATE_DIR = os.path.join(DATA_DIR, "ml_data", "refresh_state")
# Rotation steps whose budget may be cut: step -> (flag, floor). The flag's
# value is scaled by the fraction of its nominal time that still fits.
TRIMMABLE = {
    "tcg-sku-resolve": ("--resolve", 50),
    "tcg-sales": ("--total-limit", 100),
    "printing-ingest": ("--limit", 300),
    "imageless-audit": ("--limit", 20),
    "art-recheck": ("--limit", 15),
    "pc-link-suggest": ("--drain", 200),
    "pc-graded-new": ("--total-limit", 800),
}
# Work that happens BETWEEN art-comps and s3-upload (the model block, run by
# run_daily_refresh.sh) plus the prod push, reserved so the rotations don't
# eat the time the forecasts and the deploy need.
# 2026-10-27: measured phase_a -> model in the ledger over five good nights:
# 234, 247, 259, 263, 277 min, and forecast_predict has grown ~8 min a night
# as the catalog grows (185 -> 223 over a week). The old 200 under-reserved
# the model block by 35-77 min EVERY night. Re-measure from the ledger when
# the trend continues; if this ever exceeds ~5 h the model block itself needs
# bounding, not a bigger reservation.
MODEL_BLOCK_MIN = 290
PUSH_MIN = 25


def deadline_at(now):
    """This night's TCG_REFRESH_DEADLINE, as a datetime.

    Normally the next occurrence. But when the deadline passed EARLIER TODAY
    (within 12 h), this is the overnight run finishing late, and the night's
    deadline is the one that just passed — the remaining budget must shrink
    to the floors, not inflate to tomorrow's. 2026-10-27: Phase C started at
    06:35 against a 06:00 deadline, "next occurrence" gave pc-link-suggest
    1,400 minutes and a --max-minutes of 1375, enough to hold the lock into
    the next scheduled start.
    """
    hh, mm = (int(x) for x in DEADLINE.split(":"))
    d = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if d > now:
        return d
    if now - d < timedelta(hours=12):
        return d
    return d + timedelta(days=1)


def apply_deadline(todo, idx, now):
    """Trim the budget of todo[idx] to what fits before the deadline (hard
    mode), or just say how the step projects against the goal (goal mode,
    the default since 2026-11-10). Returns the (possibly rewritten) command."""
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
    if DEADLINE_MODE != "hard":
        # Goal mode: the full budget runs; the log says where the night stands.
        if available < nominal:
            print(f"    [goal {DEADLINE}] {name}: {int(remaining)} min left, {int(rest)} min "
                  f"still to come, about {int(nominal)} min of work here -> running in full, "
                  f"projected about {int(nominal - available)} min past the goal", flush=True)
        return cmd
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
    """Stop (exit EXIT_LOCKED) if another refresh is still running; otherwise take
    the lock for this process. A lock left by a dead process is ignored."""
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read().strip())
        except ValueError:
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)   # raises if that pid is gone
                # After a reboot the old pid can belong to anything: only a
                # process that IS this script holds the lock (2026-11-12).
                who = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                     capture_output=True, text=True).stdout
                if "weekly_refresh.py" in who:
                    print(f"another refresh (pid {pid}) is still running — skipping this run")
                    sys.exit(EXIT_LOCKED)
            except (ProcessLookupError, PermissionError):
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
    #
    # Split in two 2026-11-12. tcg-nm-prices is the fallback alone
    # (--depth-games none: only a game whose CSV did not land is crawled, and
    # priced), so on a normal night it is over in seconds and the night's
    # prices no longer wait up to two hours on a depth walk that writes no
    # price. tcg-nm-depth is that walk, run in the tail. Its CSV window is
    # widened to 30 h because it can start half a day after the import: with
    # the default 20 h a late tail would take tonight's CSV for missing and
    # start writing crawl prices over it.
    ("tcg-nm-prices", ["scrape_tcg_nm_prices.py", "--game", "all",
                       "--resume", "--delay", "1.0", "--rpm", "30",
                       "--csv-games", "depth-only", "--depth-games", "none"]),
    ("tcg-nm-depth", ["scrape_tcg_nm_prices.py", "--game", "all",
                      "--resume", "--delay", "1.0", "--rpm", "30",
                      "--csv-games", "depth-only", "--depth-games", DEPTH_GAMES,
                      "--csv-window-hours", "30"]),
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
    # Same 28-day cycle for sales volume; the daytime sales-crawl agent does
    # the bulk (~8,100 a day), this is the fallback trickle.
    ("tcg-sales", ["backfill_sales_volume.py", "--game", "all", "--rpm", "40",
                   "--refresh-days", "28", "--total-limit", "500", "--no-yield"]),
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
    # Pre-release art re-check (2026-10-26): TCGplayer swaps a new set's
    # product images on/after release and we only ever downloaded them once.
    # Nightly rotation over cards whose art was fetched before the card had
    # settled; a changed image is replaced in S3 + invalidated on CloudFront
    # and re-embedded by this same run's ml-embed. Non-fatal, exits 0.
    ("art-recheck",     ["recheck_prerelease_art.py", "--refresh-days", "3",
                         "--limit", "60", "--rpm", "60"]),
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
    # Flat 28-day cycle on every graded card (2026-10-29, user decision). The
    # daytime graded-crawl agent (10:00, pipeline/com.tcg-predictor.graded-
    # crawl.plist) carries ~5,600 a day, so this step is the fallback for
    # days the Mac was shut: it finds little or nothing due on a normal night.
    ("pc-graded-new", ["scrape_graded_history.py", "--game", "all", "--resume",
                       "--delay", "1.0", "--refresh-days", "28", "--workers", "2",
                       "--limit", "4000", "--total-limit", "2500"]),
    # Price-integrity sweep: TCGplayer NM vs PC sales divergences -> review
    # CSV (no auto-action; the two failure classes need human judgment).
    ("nm-pc-sweep",   ["sweep_nm_vs_pc.py"]),
    ("unify",         ["build_unified_history.py"]),
    ("nm-price",      ["backfill_nm_price.py"]),
    # Past-change table for the catalog's history sorts (2026-10-26): one
    # pass over the night's unified history into predictions.db, which the
    # model block pushes. Replaces a 28 s per-request recompute on prod.
    ("card-trends",   ["build_card_trends.py"]),
    ("ml-export",     ["export_for_ml.py"]),
    ("ml-embed",      ["embed_images.py"]),
    ("ml-embed-text", ["embed_text.py"]),
    # Scanner / search-by-photo index (2026-10-24): new cards' embeddings
    # join card-index.bin (PCA basis persisted, so it's an append); the
    # data push ships Data/clip/ to prod.
    ("clip-index",    ["export_clip_onnx.py", "--index-only"]),
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


def steps_file():
    """Where this batch's finished steps are noted, or None for a hand run
    (no TCG_BATCH_DATE): those neither note nor skip anything."""
    batch = os.environ.get("TCG_BATCH_DATE", "").strip()
    return os.path.join(STATE_DIR, f"{batch}.steps") if batch else None


def steps_done():
    path = steps_file()
    try:
        return {l.strip() for l in open(path) if l.strip()} if path else set()
    except OSError:
        return set()


def note_step(name):
    path = steps_file()
    if not path:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "a") as f:
            f.write(name + "\n")
    except OSError as e:
        print(f"    (step note not written: {e})", flush=True)


def tail_stop_at(clock=None):
    """`clock` (default TAIL_STOP, the moment after which the tail starts
    nothing new) on the batch's own day. None for a hand run."""
    batch = os.environ.get("TCG_BATCH_DATE", "").strip()
    try:
        hh, mm = (int(x) for x in (clock or TAIL_STOP).split(":"))
        return datetime.strptime(batch, "%Y-%m-%d").replace(hour=hh, minute=mm)
    except ValueError:
        return None


def main():
    names = [n for n, _ in STEPS]
    ap = argparse.ArgumentParser(description="Weekly data refresh (both games, both sources)")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    ap.add_argument("--from", dest="start", choices=names, help="start at this step (resume)")
    ap.add_argument("--to", dest="stop", choices=names + ["catalog"],
                    help="stop AFTER this step (inclusive). With --from, runs a "
                         "contiguous slice — used to split the run around the "
                         "model block: ...--to art-comps, then s3-upload... "
                         "'catalog' = the last TCGplayer catalog crawl step (the daily "
                         "night's crawl-only pass; stays right when games are added).")
    ap.add_argument("--only", choices=names, help="run a single step")
    ap.add_argument("--core", action="store_true",
                    help="leave the TAIL steps out of the slice (the nightly's passes "
                         "before the publish)")
    ap.add_argument("--tail", action="store_true",
                    help="run the TAIL steps and nothing else, in order; a failing step "
                         "is logged and skipped, and the exit code is always 0")
    ap.add_argument("--resume", action="store_true",
                    help="skip the steps this batch (TCG_BATCH_DATE) already finished")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the steps this invocation would run, and exit")
    args = ap.parse_args()
    if args.stop == "catalog":
        args.stop = [n for n in names if n in {f"tcg-{g}" for g in GAMES}][-1]
    if args.tail and (args.core or args.start or args.stop or args.only):
        ap.error("--tail runs the whole tail: it takes no --core/--from/--to/--only")

    if args.list:
        for n, cmd in STEPS:
            print(f"{n:14} {'tail' if n in TAIL else 'core'}  {' '.join(cmd)}")
        return

    lo = names.index(args.start) if args.start else 0
    hi = names.index(args.stop) + 1 if args.stop else len(STEPS)
    todo = [s for s in STEPS if s[0] == args.only] if args.only else STEPS[lo:hi]
    if args.tail:
        todo = [s for s in STEPS if s[0] in TAIL
                and (CRAWL_NIGHT or s[0] not in TAIL_CRAWL_NIGHT_ONLY)]
    elif args.core:
        todo = [s for s in todo if s[0] not in TAIL]
    done = steps_done() if args.resume else set()
    if args.dry_run:
        for n, cmd in todo:
            print(f"{'skip (done)' if n in done else 'run':11} {n:18} {' '.join(cmd)}")
        return

    acquire_lock()

    print(f"weekly refresh — {datetime.now():%Y-%m-%d %H:%M} — {len(todo)} step(s)"
          + (" — tail" if args.tail else "")
          + ((f" — finishing by {DEADLINE}" if DEADLINE_MODE == "hard"
              else f" — aiming to finish by {DEADLINE}")
             if DEADLINE and not args.tail else ""), flush=True)
    print(flush=True)

    # A terminated run takes its step down with it: tomorrow night's start
    # ends a tail that is still going, and an orphaned crawler would keep
    # writing under the new night.
    child = [None]

    def _terminated(signum, frame):
        if child[0] is not None and child[0].poll() is None:
            child[0].terminate()
            try:
                child[0].wait(timeout=20)
            except subprocess.TimeoutExpired:
                child[0].kill()
        print(f"\nterminated (signal {signum}) — stopped", flush=True)
        sys.exit(143)
    signal.signal(signal.SIGTERM, _terminated)

    stop_at = tail_stop_at() if args.tail else None
    hard_at = tail_stop_at(TAIL_HARD_STOP) if args.tail else None
    timings = []
    for i, (name, cmd) in enumerate(todo):
        if name in done:
            print(f"--- {name} already done for this batch, skipped ---\n", flush=True)
            continue
        if stop_at and datetime.now() >= stop_at:
            print(f"--- it is past {TAIL_STOP}: {name} and the rest of the tail wait for "
                  f"tomorrow ---", flush=True)
            break
        # The finish-by planner belongs to the passes before the publish; the
        # tail runs after it and has nothing to race.
        if DEADLINE and not args.tail:
            cmd = apply_deadline(todo, i, datetime.now())
        print(f"=== {name}: {' '.join(cmd)} ===", flush=True)
        t0 = time.time()
        child[0] = subprocess.Popen([PY, os.path.join(SCRIPT_DIR, cmd[0]), *cmd[1:]], cwd=DATA_DIR)
        cut = False
        while True:
            try:
                code = child[0].wait(timeout=30 if hard_at else None)
                break
            except subprocess.TimeoutExpired:
                if datetime.now() >= hard_at:
                    cut = True
                    child[0].terminate()
                    try:
                        code = child[0].wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        child[0].kill()
                        code = child[0].wait()
                    break
        child[0] = None
        mins = (time.time() - t0) / 60
        timings.append((name, mins, code))
        if cut:
            print(f"--- it is past {TAIL_HARD_STOP}: {name} stopped after {mins:.1f} min so "
                  f"tonight's run can start; the rest of the tail waits for tomorrow ---",
                  flush=True)
            break
        if code != 0:
            if args.tail:
                print(f"--- {name} FAILED (exit {code}) after {mins:.1f} min — the tail "
                      f"goes on ---\n", flush=True)
                continue
            print(f"\n✗ {name} failed (exit {code}) after {mins:.1f} min.")
            print(f"  Fix the issue, then resume with:  weekly_refresh.py --from {name}")
            summary(timings)
            sys.exit(code)
        note_step(name)
        print(f"--- {name} done in {mins:.1f} min ---\n", flush=True)

    summary(timings)
    print("\n✓ refresh complete" if not args.tail else "\n✓ tail complete")


def summary(timings):
    print("\nstep summary:")
    for name, mins, code in timings:
        print(f"  {'ok ' if code == 0 else 'FAIL'} {name:14} {mins:7.1f} min")


if __name__ == "__main__":
    main()
