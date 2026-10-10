#!/usr/bin/env python3
"""Best-guess PriceCharting links for NEW cards — the self-sourced replacement
for the tcg-id column the paid CSV used to hand us.

Every PriceCharting product page embeds a "Buy on TCGplayer" link carrying that
product's TCGplayer id = our product_id. So for a PC product we don't yet link
(an unmatched pc_id from the sweep), we fetch its page, read the embedded
tcg-id, and if it belongs to one of OUR catalog cards that has no PC match yet,
we queue an EXACT link suggestion for match_review.py to confirm by eye.

Forward-only + bounded: the first run (--baseline) marks every currently-
unmatched pc_id as "seen" WITHOUT fetching (the ~63k standing backlog is the
status quo, not a cutover regression — don't crawl it). After that each run only
fetches pc_ids that are NEW since last run, capped by --limit so a set-release
spike can't balloon the nightly; the rest carry over to the next run.

  .venv/bin/python pc_link_suggest.py --baseline     # first run: seed, no fetch
  .venv/bin/python pc_link_suggest.py                # normal: check new pc_ids
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, db_path
from scrape_gundam_prices import fetch          # polite 1 req/s + transient retry
from scrape_pc_prices import bulk_games         # the games losing the paid API

ML_DATA = os.environ.get("MATCH_REVIEW_STATE", os.path.join(BASE, "ml_data"))
SEEN_CSV = os.path.join(ML_DATA, "pc_link_seen.csv")            # game,pc_id (triaged)
SUGGEST_CSV = os.path.join(ML_DATA, "pc_link_suggestions.csv")  # pending suggestions
DRAINED_CSV = os.path.join(ML_DATA, "pc_link_drained.csv")      # backlog pages fetched once
TCG_ID_RE = re.compile(r"tcgplayer\.com/product/(\d+)")
TITLE_RE = re.compile(r"<title>(.*?)(?:\s*Prices)?\s*[|<]", re.S)
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
DEFAULT_LIMIT = 500                              # max page fetches per game per run

# --drain works back through the --baseline backlog (2026-10-09, missing-
# grades check: the 2026-08 cutover marked ~99k unmatched PC pages "seen"
# without ever reading their tcg-ids, so whole sets lack graded data).
# Small games first: every game but magic drains in weeks, magic is the
# long tail. Each backlog page is fetched ONCE ever (DRAINED_CSV) — pages
# with no embedded tcg-id (PC hasn't linked them) can't auto-match and
# stay available for manual overrides only.
DRAIN_ORDER = ["digimon", "onepiece", "lorcana", "pokemon", "yugioh", "magic"]

# Special-print PC pages ([1st Edition]/[Shadowless]) embed the SAME tcg id
# as the regular page — never auto-confirm those (see run_game).
SPECIAL = ("1st edition", "shadowless")


def page_tcg_ids(pc_id):
    """(set_of_tcg_ids | None, status). ids None => page didn't load as 200."""
    status, html = fetch(f"https://www.pricecharting.com/game/{pc_id}")
    if status != 200:
        return None, status
    return {int(x) for x in TCG_ID_RE.findall(urllib.parse.unquote(html))}, status


def load_seen():
    seen = {}
    if os.path.exists(SEEN_CSV):
        with open(SEEN_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.setdefault(r["game"], set()).add(int(r["pc_id"]))
    return seen


def append_seen(rows):
    new = not os.path.exists(SEEN_CSV)
    os.makedirs(ML_DATA, exist_ok=True)
    with open(SEEN_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["game", "pc_id"])
        w.writerows(rows)


def load_suggestions():
    """set of (game, product_id, pc_id) already queued — for dedupe."""
    have = set()
    if os.path.exists(SUGGEST_CSV):
        with open(SUGGEST_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                have.add((r["game"], int(r["product_id"]), int(r["pc_id"])))
    return have


SUGGEST_COLS = ["game", "product_id", "pc_id", "pc_name", "source", "created", "console"]


def append_suggestions(rows):
    """Append suggestion rows ([game, product_id, pc_id, pc_name, source,
    created, console?]). The console column (2026-10-10) carries a discovered
    console's slug so build_pricecharting can store it; a file written before
    the column existed is rewritten once with the new header."""
    os.makedirs(ML_DATA, exist_ok=True)
    legacy = None
    if os.path.exists(SUGGEST_CSV):
        with open(SUGGEST_CSV, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            if rd.fieldnames and "console" not in rd.fieldnames:
                legacy = list(rd)
    if legacy is None and os.path.exists(SUGGEST_CSV):
        with open(SUGGEST_CSV, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        return
    with open(SUGGEST_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUGGEST_COLS)
        w.writeheader()
        for r in legacy or []:
            w.writerow({c: r.get(c, "") for c in SUGGEST_COLS})
        for r in rows:
            w.writerow(dict(zip(SUGGEST_COLS, r)))


def catalog_pids(game):
    conn = sqlite3.connect(db_path(game), timeout=30)
    ids = {pid for (pid,) in conn.execute("SELECT product_id FROM cards")}
    conn.close()
    return ids


def linked_pids(game):
    conn = sqlite3.connect(PC_DB, timeout=30)
    ids = {pid for (pid,) in conn.execute(
        "SELECT product_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL", (game,))}
    conn.close()
    return ids


def candidates(game, suffix):
    """Unmatched (pc_id, pc_name) the sweep saw — PC products not in our map."""
    path = os.path.join(ML_DATA, f"{game}_price_review{suffix}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [(int(r["pc_id"]), r.get("pc_name", ""), r.get("console", ""))
                for r in csv.DictReader(f) if r.get("pc_id")]


def page_ids_and_title(pc_id):
    """(set_of_tcg_ids | None, page title, status) — title stands in for
    pc_name on backlog pages, which have no sweep-CSV row anymore."""
    status, html = fetch(f"https://www.pricecharting.com/game/{pc_id}")
    if status != 200:
        return None, "", status
    ids = {int(x) for x in TCG_ID_RE.findall(urllib.parse.unquote(html))}
    m = TITLE_RE.search(html)
    return ids, (m.group(1).strip() if m else ""), status


def load_drained():
    d = {}
    if os.path.exists(DRAINED_CSV):
        with open(DRAINED_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d.setdefault(r["game"], set()).add(int(r["pc_id"]))
    return d


def append_drained(rows):
    new = not os.path.exists(DRAINED_CSV)
    os.makedirs(ML_DATA, exist_ok=True)
    with open(DRAINED_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["game", "pc_id", "status", "drained_at"])
        w.writerows(rows)


def linked_pc_ids(game):
    conn = sqlite3.connect(PC_DB, timeout=30)
    ids = set()
    for (x,) in conn.execute(
            "SELECT pc_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL", (game,)):
        try:
            ids.add(int(x))
        except (TypeError, ValueError):
            pass
    conn.close()
    return ids


def run_drain(budget, seen):
    """Fetch up to `budget` baselined-but-still-unmatched pages, small games
    first, and queue link suggestions exactly like run_game (exact embedded
    tcg-ids auto-confirm at the next pc-match; special prints go to review)."""
    drained = load_drained()
    have = load_suggestions()
    total = 0
    for game in DRAIN_ORDER:
        if budget <= 0:
            break
        pool = seen.get(game, set()) - drained.get(game, set()) - linked_pc_ids(game)
        if not pool:
            continue
        unlinked = catalog_pids(game) - linked_pids(game)
        card_names = dict(sqlite3.connect(db_path(game)).execute(
            "SELECT product_id, name FROM cards"))
        batch = sorted(pool)[:budget]
        budget -= len(batch)
        new_drained, new_sugg = [], []
        for pc_id in batch:
            ids, title, status = page_ids_and_title(pc_id)
            if ids is None and status not in (404,):
                continue                          # transient — retry on a later drain
            new_drained.append([game, pc_id, status, now()])
            for tcg in (ids or set()):
                if tcg in unlinked and (game, tcg, pc_id) not in have:
                    pl, cl = title.lower(), (card_names.get(tcg) or "").lower()
                    src = ("special-print page — needs review"
                           if any(t in pl and t not in cl for t in SPECIAL)
                           else "page-embedded tcg-id")
                    new_sugg.append([game, tcg, pc_id, title, src, now()])
                    have.add((game, tcg, pc_id))
        if new_drained:
            append_drained(new_drained)
        if new_sugg:
            append_suggestions(new_sugg)
        print(f"[drain:{game}] fetched {len(new_drained)} of {len(pool)} backlog "
              f"page(s) -> {len(new_sugg)} link suggestion(s)", flush=True)
        total += len(new_sugg)
    return total


def run_game(game, suffix, baseline, limit, seen):
    game_seen = seen.get(game, set())
    fresh = [(pc_id, name, con) for pc_id, name, con in candidates(game, suffix)
             if pc_id not in game_seen]
    # de-dup within this run's candidate list (a pc_id can list on two consoles)
    seen_this = set()
    fresh = [(p, n, c) for p, n, c in fresh if not (p in seen_this or seen_this.add(p))]

    if baseline:
        append_seen([[game, p] for p, _, _ in fresh])
        print(f"[{game}] baseline: {len(fresh)} unmatched pc_ids marked seen (no fetch)")
        return 0

    capped = fresh[:limit]
    if len(fresh) > limit:
        print(f"[{game}] {len(fresh)} new pc_ids; capping at {limit} this run "
              f"({len(fresh) - limit} carry over to next run)")

    unlinked = catalog_pids(game) - linked_pids(game)
    have = load_suggestions()
    # Special-print PC pages ([1st Edition]/[Shadowless]) embed the SAME tcg id
    # as the regular page (TCGplayer bundles printings), so an exact-id match
    # is NOT proof of the right page. Those suggestions get a distinct source
    # string so take_exact_suggestions won't auto-confirm them — a human
    # decides in match review. (Bug class found 2026-08-08: 99 regular-print
    # pokemon cards auto-linked to 1st Edition pages.)
    card_names = dict(sqlite3.connect(db_path(game)).execute(
        "SELECT product_id, name FROM cards"))
    new_seen, new_sugg = [], []
    for pc_id, pc_name, console in capped:
        ids, status = page_tcg_ids(pc_id)
        if ids is None and status not in (404,):
            continue                              # transient — retry next run, stay unseen
        new_seen.append([game, pc_id])            # 200 or 404 => triaged
        for tcg in (ids or set()):
            if tcg in unlinked and (game, tcg, pc_id) not in have:
                pl, cl = pc_name.lower(), (card_names.get(tcg) or "").lower()
                src = ("special-print page — needs review"
                       if any(t in pl and t not in cl for t in SPECIAL)
                       else "page-embedded tcg-id")
                new_sugg.append([game, tcg, pc_id, pc_name, src, now(), console or ""])
                have.add((game, tcg, pc_id))

    if new_seen:
        append_seen(new_seen)
    if new_sugg:
        append_suggestions(new_sugg)
    print(f"[{game}] checked {len(capped)} new pc_ids -> {len(new_sugg)} link suggestion(s)")
    return len(new_sugg)


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", help="comma-separated (default: all priced games)")
    ap.add_argument("--suffix", default="_scraped",
                    help="review-CSV suffix to read ('_scraped' shadow default, '' at cutover)")
    ap.add_argument("--baseline", action="store_true",
                    help="seed all current unmatched pc_ids as seen WITHOUT fetching (first run)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="max page fetches per game per run")
    ap.add_argument("--drain", type=int, default=0,
                    help="also fetch up to N baselined-but-unmatched backlog "
                         "pages (small games first; each page fetched once ever)")
    args = ap.parse_args()

    games = args.games.split(",") if args.games else bulk_games()
    seen = load_seen()
    total = 0
    for game in games:
        total += run_game(game, args.suffix, args.baseline, args.limit, seen)
    if args.drain and not args.baseline:
        total += run_drain(args.drain, seen)
    if not args.baseline:
        print(f"\n{total} new suggestion(s) -> {SUGGEST_CSV}")


if __name__ == "__main__":
    # Non-fatal: this builds a review queue, it must never fail the nightly and
    # block the model. Per-pc_id fetch errors are already handled in run_game;
    # this guards catastrophic ones (unreadable file/db).
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"[pc-link-suggest] non-fatal error: {e}", file=sys.stderr)
    sys.exit(0)
