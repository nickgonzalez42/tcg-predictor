#!/usr/bin/env python3
"""
Per-game weekly articles (2026-11-06, user request: "two reports per game,
per week. One that analyzes the performance of the most recent set and one
that analyzes the overall performance of the game. ... a bit more like a
traditional article with a mix of images, infographics, and text").

Two articles per game:

  set-report-{game}-{date}    the game's newest set: chase cards, this
                              week's movers, the rarities, its price index
                              since release, the price ladder, the model's
                              outlook for it
  game-report-{game}-{date}   the whole game: the week's drift, which sets
                              moved, the biggest movers, the groups that
                              moved together, the price ladder, the model's
                              record and picks for this game

Every number comes from the same sources the Friday market report reads
(TCGplayer Near Mint daily history for the week window, the game's own card
DB for the catalog, predictions.db for forecasts and the model's record);
the prose around them is written by article_editorial.py (Claude, in the
site's house voice) with templated sentences as the fallback. Rows land in
an `articles` table in predictions.db, so they ship with the nightly push
and the API serves them read-only at /api/articles; article_pages.py
renders the crawler-visible static copies.

Schedule (2026-11-07, user rules; the run's nominal day is the UTC date, like
market_report.py: the Sunday-night refresh IS Monday in UTC). No fixed
weekday any more. Every night the planner looks at each (game, kind) slot's
last article and writes the slot if it is due (never written, or
FORCE_GAP_DAYS since the last one) or if a seeded coin toss (DAILY_ODDS)
says so, never inside MIN_GAP_DAYS of the last one and never more than
MAX_PER_NIGHT a night; see plan(). The promise: every slot at least once
every two weeks, on a random day.

The set article also lines the set up against the game's earlier sets at
the same age since release (2026-11-07, user: "compare how it shapes up
against other sets at similar points in their history and how the forecasts
compare to the historical performance of other sets in the same game. Make
sure it's when the set actually released and not just when we started
collecting prices for them"): every set's price index starts on its first
broadly priced day ON OR AFTER its real release date, and a set whose prices
begin more than PEER_LAG_MAX days after release is left out of the
comparison rather than compared from a late start. One Piece's catalog has
no release dates, so they come from TCGplayer's group list (tcgcsv.com
mirror), see group_release_dates().

Sales volume (2026-11-07, user: "discuss volume as part of the sales. If
the card is going to be discussed in an article, make sure to prioritize
scraping its volume movement the day before"): every article carries Near
Mint copies sold in the last complete week against the week before, for the
set (or pool) and for each card it discusses (sales_volume), counting only
cards whose sales were fetched after that week closed, so the buckets are
final. The run also writes tomorrow's plan and the cards it will discuss to
ml_data/article_watch.json (write_watch); the daytime sales crawl
(backfill_sales_volume.py) fetches those first.

Two more rules from the same request:
  * the set article waits until the set is SET_MIN_AGE_DAYS old (a week of
    prices before it is written up); a younger newest set is skipped and the
    set before it is covered instead, see pick_set()
  * the set slot alternates between two focuses: the newest set itself and
    the game's OTHER recent releases, every priced card released inside
    RECENT_DAYS that is not in that set (promos, starter decks, side
    products, the set before it), slug new-releases-{game}-{date}, column
    focus = 'recent'; a pool under RECENT_MIN_CARDS runs the set focus again

Run:  game_articles.py                 # tonight's plan
      game_articles.py --plan          # print tonight's plan, write nothing
      game_articles.py --all           # every game, both kinds, right now
      game_articles.py --game onepiece --kind set [--focus recent]
      game_articles.py --all --no-prose   # templated text only (no Claude call)
"""

import argparse
import collections
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from math import exp, log
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE                      # noqa: E402
from games import GAMES                                   # noqa: E402
from market_report import (                               # noqa: E402
    PC_DB, PRED_DB, MIN_BASE, MAX_WEEK_RATIO, YOUNG_DAYS, DIM_LABELS,
    week_window, synth_series, young_series, game_moves, card_attrs,
    find_trends, pick_trends, trend_phrase, live_accuracy,
    line_chart, esc, card_link, money, pct, short_label,
)
from article_editorial import write_set_article, write_game_article, write_recent_article   # noqa: E402

CDN = "https://d18pzythfo0bgy.cloudfront.net"
SITE = "https://cardstock.guide"

# Planner (2026-11-07): "Randomize the release of each article for each game
# but each type for each game should release at least once every two weeks."
# FORCE sits a day inside the two-week promise so a night where more slots
# fall due than MAX_PER_NIGHT can slip one by a day and still keep it.
#
# MAX_PER_NIGHT went 4 -> 2 on 2026-11-08 ("that's too often, let's just do
# 1 or 2 each night"). Note what that does to the other two numbers: the 24
# slots (12 games x 2 kinds) need 24/FORCE_GAP_DAYS = 1.85 articles a night
# just to keep the two-week promise, so the cap is now the binding rule and
# the coin toss almost never gets a turn -- measured over 180 nights, 2
# articles on 98% of nights and a slot gap of 12 to 13 days. Quiet nights
# and a randomised gap come back only if FORCE_GAP_DAYS is stretched (18
# days with DAILY_ODDS 0.05 gives 1.5 a night and a 16-day average gap),
# which gives up the two weeks. Lowering the cap to 1 would put every slot
# on a 24-day cycle.
MIN_GAP_DAYS = 5          # never the same slot twice inside a working week
FORCE_GAP_DAYS = 13       # due: written tonight, longest wait first
DAILY_ODDS = 0.22         # between the two: tonight's chance, seeded by date|game|kind
MAX_PER_NIGHT = 2         # user's cadence, 2026-11-08; also ~13 min of prose a night
# Paused games (2026-11-08): the user deleted the two Cyberpunk articles --
# 66 of its 406 priced cards had a week of moves, and its newest set had 1 --
# and asked for the game to sit out a week. The planner skips a game until its
# date, so on that date both of its slots fall due at once (deleting the rows
# left them never-written). An explicit --game / --all run writes it anyway.
PAUSED_GAMES = {"cyberpunk": "2026-11-15"}
SET_MIN_AGE_DAYS = 7      # "let the set at least be a week old"
RECENT_DAYS = 90          # the other focus: cards released inside this window ...
RECENT_MIN_CARDS = 8      # ... outside the set; fewer and the set focus runs instead
# Earlier-set comparison (2026-11-07): peers are the game's earlier sets whose
# prices begin within PEER_LAG_MAX days of their real release date.
PEER_MAX = 5              # lines on the chart; the table may list a few more
PEER_LAG_MAX = 7
PEER_LOOKBACK_DAYS = 548  # eighteen months of earlier sets
AGE_TOL_DAYS = 7          # an index "at day N" may come from up to a week before N
HORIZON_DAYS = {"1m": 30, "6m": 182, "12m": 365}
HORIZON_LABEL = {"1m": "One month", "6m": "Six months", "12m": "One year"}
# Sales volume (2026-11-07): the section runs when this share of the cards
# (and at least 5) have final buckets for the last complete week.
VOLUME_MIN_COVER = 0.25
TOP_SELLERS_N = 6
WATCH_FILE = os.path.join(BASE, "ml_data", "article_watch.json")

LATEST_SET_MIN_CARDS = 20     # the API's own floor for "newest set"
SET_INDEX_MIN_CARDS = 8       # a set-index point needs this many priced cards
CHASE_N = 6
MOVERS_N = 4
PICKS_N = 5
MIN_SET_TRACKED = 6           # a set earns a bar on the game chart with this many
LADDER = [("under $1", 0, 1), ("$1 to $5", 1, 5), ("$5 to $20", 5, 20),
          ("$20 to $50", 20, 50), ("$50 to $100", 50, 100), ("$100 and up", 100, None)]
KIND_LABEL = {"set": "SET REPORT", "game": "GAME REPORT", "recent": "NEW RELEASES"}


def r1(v):
    return round(v, 1) if v is not None else None


def prune(o):
    """Drop null values from the facts pack: a null invites the writer to
    remark on what is missing ("no all-games line on the books this week")."""
    if isinstance(o, dict):
        return {k: prune(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [prune(v) for v in o]
    return o


STOP = {"the", "of", "a", "an", "and", "in", "on", "to", "edition", "set", "deck", "booster"}


def initials(name, n=3):
    """Short label for a hero bar: a code token as is (ST-21, OP15, EB05),
    else the initials of the first meaningful words."""
    toks = [t.strip(":,.()") for t in str(name).split()]
    toks = [t for t in toks if t]
    for t in toks[:2]:
        if re.match(r"^[A-Za-z]{1,3}-?\d{1,3}$", t):
            return t.upper()[:4]
    words = [t for t in toks if t.lower() not in STOP] or toks
    if len(words) == 1:
        return words[0][:n].upper()
    return "".join(w[0] for w in words[:n]).upper()


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:80] or "set"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
# Release dates for a game whose catalog schema lacks them (One Piece): the
# TCGplayer group list (publishedOn), mirrored by tcgcsv.com, matched by set
# name (all 46 One Piece sets matched on 2026-11-07). Cached a week on disk;
# a stale cache beats a failed fetch; nothing at all leaves release None and
# the set is dated by its first priced day (set_age_days / released_on).
TCGCSV_CATEGORY = {"onepiece": 68}


def group_release_dates(game):
    cid = TCGCSV_CATEGORY.get(game)
    if not cid:
        return {}
    path = os.path.join(BASE, "ml_data", f"tcgcsv_groups_{game}.json")
    data = None
    try:
        if time.time() - os.path.getmtime(path) < 7 * 86400:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    except (OSError, ValueError):
        data = None
    if data is None:
        try:
            import requests
            r = requests.get(f"https://tcgcsv.com/tcgplayer/{cid}/groups", timeout=30,
                             headers={"User-Agent": "cardstock-pipeline/1.0"})
            r.raise_for_status()
            data = r.json()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:          # network, HTTP or JSON: all the same here
            print(f"{game}: TCGplayer group dates unavailable ({type(e).__name__}); "
                  "sets are dated by their first priced day")
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return {}
    out = {}
    for g in (data or {}).get("results", []):
        d = (g.get("publishedOn") or "")[:10]
        if g.get("name") and d:
            out[g["name"].strip()] = d
    return out


def catalog(game):
    """pid -> {name, set, rarity, price, release} for priced, named cards.
    Older per-game schemas (One Piece) have no release_date column; their
    sets are dated from TCGplayer's group list instead."""
    path = os.path.join(BASE, GAMES[game]["db"])
    if not os.path.exists(path):          # a game without a catalog yet
        return {}
    con = sqlite3.connect(path)
    con.execute("PRAGMA busy_timeout=30000")
    have = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
    rel = "release_date" if "release_date" in have else "NULL"
    # Catalog-visible = art on disk AND a price (CardExtensions.VisibleInCatalog),
    # so the newest set here is the one the catalog opens on.
    art = "AND image_path IS NOT NULL AND image_path != ''" if "image_path" in have else ""
    out = {}
    for pid, name, s, r, nm, rd in con.execute(
            f"SELECT product_id, name, set_name, rarity, near_mint_price, {rel} FROM cards "
            f"WHERE near_mint_price IS NOT NULL AND near_mint_price > 0 AND name IS NOT NULL {art}"):
        out[pid] = {"name": name, "set": (s or "").strip() or None,
                    "rarity": (r or "").strip() or None, "price": float(nm),
                    "release": (rd or "")[:10] or None}
    con.close()
    if rel == "NULL":
        dates = group_release_dates(game)
        if dates:
            for c in out.values():
                c["release"] = dates.get(c["set"])
    return out


def ranked_sets(cat, today):
    """The game's sets newest first, the way the catalog's default view picks
    its newest (CardsController.LatestSet): sets with 20+ priced cards, the
    most common release date per set, newest not-in-the-future date first
    (ties: the bigger set); a game whose schema carries no release dates
    ranks sets by median product id."""
    sizes = collections.Counter(c["set"] for c in cat.values() if c["set"])
    big = {s for s, n in sizes.items() if n >= LATEST_SET_MIN_CARDS}
    if not big:
        return []
    dated = collections.defaultdict(collections.Counter)
    for c in cat.values():
        if c["set"] in big and c["release"]:
            dated[c["set"]][c["release"]] += 1
    if dated:
        iso = today.isoformat()
        keyed = []
        for s, dates in dated.items():
            common = dates.most_common(1)[0][0]
            if common <= iso:
                keyed.append(((common, sizes[s]), s))
        if keyed:
            return [s for _k, s in sorted(keyed, reverse=True)]
    ids = collections.defaultdict(list)
    for pid, c in cat.items():
        if c["set"] in big:
            ids[c["set"]].append(pid)
    return sorted(ids, key=lambda s: -sorted(ids[s])[len(ids[s]) // 2])


def newest_set(cat, today):
    ranked = ranked_sets(cat, today)
    return ranked[0] if ranked else None


def set_release(cat, set_name):
    dates = collections.Counter(c["release"] for c in cat.values()
                                if c["set"] == set_name and c["release"])
    return dates.most_common(1)[0][0] if dates else None


def set_history(pc, game, pids):
    """{date: {pid: price}}: the cards' TCGplayer NM base-printing history."""
    pids = list(pids)
    if not pids:
        return {}
    try:
        rows = pc.execute(
            f"SELECT date, product_id, price FROM tcg_nm_history WHERE game=? AND printing='' "
            f"AND price>0 AND product_id IN ({','.join('?' * len(pids))}) ORDER BY date",
            [game, *pids]).fetchall()
    except sqlite3.OperationalError:
        rows = []
    by_date = collections.defaultdict(dict)
    for d, pid, p in rows:
        by_date[d][pid] = p
    return by_date


def broad_days(by_date, n_members):
    """The dates on which enough of the set was priced to call it on sale."""
    floor = min(SET_INDEX_MIN_CARDS, max(3, n_members // 3))
    return [d for d in sorted(by_date) if len(by_date[d]) >= floor]


def set_age_days(pc, game, cat, set_name, today, by_date=None):
    """Days since the set came out: its most common release date, or, for a
    schema without one (One Piece), its first broadly priced day. None when
    neither is known."""
    rel = set_release(cat, set_name)
    if not rel:
        pids = [pid for pid, c in cat.items() if c["set"] == set_name]
        if by_date is None:
            by_date = set_history(pc, game, pids)
        days = broad_days(by_date, len(pids))
        rel = days[0] if days else None
    return (today - date.fromisoformat(rel)).days if rel else None


def pick_set(pc, game, cat, today):
    """The set the set article covers: the newest one at least SET_MIN_AGE_DAYS
    old (user rule 2026-11-07: a set gets a week of prices before it is written
    up), stepping down the ranking past younger ones. Returns (set_name,
    age_days, [younger sets skipped]) or (None, None, []) ."""
    skipped = []
    for s in ranked_sets(cat, today)[:6]:
        age = set_age_days(pc, game, cat, s, today)
        if age is not None and age >= SET_MIN_AGE_DAYS:
            return s, age, skipped
        skipped.append(s)
        print(f"{game}: {s} is {age if age is not None else 'an unknown number of'} days old, "
              f"under {SET_MIN_AGE_DAYS}; looking at the set before it")
    return None, None, skipped


def set_index_path(by_date, release, n_members):
    """The set's price index from its release: [(age_days, date, pct)] over
    the broadly priced days on or after the release date, each point the
    mean move of the cards priced ($1+) on the first of those days. Also the
    lag in days from the release to that first day (None: no such day). A
    release of None starts at the first broadly priced day."""
    broad = broad_days(by_date, n_members)
    if release:
        broad = [d for d in broad if d >= release]
    if len(broad) < 2:
        return [], None
    t0 = broad[0]
    start = date.fromisoformat(release or t0)
    lag = (date.fromisoformat(t0) - start).days
    base = {pid: p for pid, p in by_date[t0].items() if p >= 1.0}
    path = []
    for d in broad:
        ratios = [by_date[d][pid] / b for pid, b in base.items() if pid in by_date[d]]
        ratios = [x for x in ratios if 1 / MAX_WEEK_RATIO <= x <= MAX_WEEK_RATIO]
        if len(ratios) >= max(3, len(base) // 2):
            path.append(((date.fromisoformat(d) - start).days, d, (statistics.mean(ratios) - 1) * 100))
    return path, lag


def index_at(path, age, tol=AGE_TOL_DAYS):
    """The index at `age` days. An exact point wins; between two points the
    reading is interpolated (the history before August 2026 is weekly, and
    holding the earlier point would print the baseline's 0.0 for a week);
    past the last point it holds for tol days; a hole wider than two weeks
    gives no reading."""
    before = [p for p in path if p[0] <= age]
    after = [p for p in path if p[0] > age]
    if not before or age - before[-1][0] > tol:
        return None
    lo = before[-1]
    if lo[0] == age:
        return lo[2]
    if not after:
        return lo[2]
    hi = after[0]
    if hi[0] - lo[0] > 2 * tol:
        return None
    return lo[2] + (hi[2] - lo[2]) * (age - lo[0]) / (hi[0] - lo[0])


def peer_sets(pc, game, cat, set_name, release, today):
    """The game's earlier sets priced from their release week: [{set,
    release, path}], newest first. The user's rule (2026-11-07): compare at
    the same age since the set ACTUALLY released, never since we started
    collecting its prices, so a set whose first broadly priced day is more
    than PEER_LAG_MAX days after its release date is left out rather than
    compared from a late start."""
    if not release:
        return []
    try:
        first_tracked = pc.execute("SELECT MIN(date) FROM tcg_nm_history WHERE game=? AND printing=''",
                                   (game,)).fetchone()[0]
    except sqlite3.OperationalError:
        return []
    if not first_tracked:
        return []
    earliest = (date.fromisoformat(first_tracked) - timedelta(days=PEER_LAG_MAX)).isoformat()
    lookback = (today - timedelta(days=PEER_LOOKBACK_DAYS)).isoformat()
    out = []
    for s in ranked_sets(cat, today):
        if s == set_name:
            continue
        r = set_release(cat, s)
        if not r or r > release or r < max(earliest, lookback):
            continue                  # same-day siblings count; later sets do not
        pids = [pid for pid, c in cat.items() if c["set"] == s]
        path, lag = set_index_path(set_history(pc, game, pids), r, len(pids))
        if lag is None or lag > PEER_LAG_MAX or len(path) < 3:
            continue
        out.append({"set": s, "release": r, "path": path, "lag": lag})
        if len(out) >= PEER_MAX * 2:
            break
    return out


def set_forecast_medians(pred, game, cat, pids):
    """{horizon: (median implied move %, cards)} over the set's cards worth
    MIN_BASE+ that carry a forecast at that horizon."""
    out = {}
    for horizon in HORIZON_DAYS:
        rows = pred.execute(
            "SELECT product_id, base_price, forecast_price FROM forecasts "
            "WHERE game=? AND target='ungraded' AND horizon=? AND printing='' AND base_price>0",
            (game, horizon)).fetchall()
        moves = [(fc / base - 1) * 100 for pid, base, fc in rows
                 if pid in pids and cat.get(pid, {}).get("price", 0) >= MIN_BASE]
        if moves:
            out[horizon] = (statistics.median(moves), len(moves))
    return out


COMP_N = 3                 # earlier sets compared beside each chase card
COMP_SET_MIN = 80          # a "big" set: this many priced cards (no starter decks or promos)
COMP_SPAN_DAYS = 365       # how far past today's age a comparable is followed
COMP_TOL_DAYS = 18         # a reading "at day N" may sit between two monthly points
                           # (the PriceCharting era before the 2026 cutover is monthly)
COMP_HORIZONS = [("1m", 30), ("6m", 182), ("12m", 365)]   # the model's own three windows


def long_history(pc, game, pids):
    """{date: {pid: price}} over the whole blended history (price_history_unified:
    PriceCharting before the 2026 cutover, TCGplayer since), base printing,
    ungraded. tcg_nm_history alone starts in late July 2026, which is why the
    comparables read this table."""
    pids = list(pids)
    if not pids:
        return {}
    try:
        rows = pc.execute(
            f"SELECT date, product_id, price FROM price_history_unified WHERE game=? AND printing='' "
            f"AND grade='ungraded' AND price>0 AND product_id IN ({','.join('?' * len(pids))}) ORDER BY date",
            [game, *pids]).fetchall()
    except sqlite3.OperationalError:
        rows = []
    by_date = collections.defaultdict(dict)
    for d, pid, p in rows:
        by_date[d][pid] = p
    return by_date


def card_paths(by_date):
    """{pid: [(date, price)]} from a set_history() map, dates ascending."""
    out = collections.defaultdict(list)
    for d in sorted(by_date):
        for pid, p in by_date[d].items():
            out[pid].append((d, p))
    return out


def price_on(series, day, tol=AGE_TOL_DAYS):
    """A card's price on an ISO day from its (date, price) readings: exact,
    else interpolated between the neighbours (the history before August 2026
    is weekly), else held from a reading up to tol days before; None past
    that."""
    before = [r for r in series if r[0] <= day]
    if not before:
        return None
    d0, p0 = before[-1]
    if d0 == day:
        return p0
    gap = (date.fromisoformat(day) - date.fromisoformat(d0)).days
    after = [r for r in series if r[0] > day]
    if after:
        d1, p1 = after[0]
        span = (date.fromisoformat(d1) - date.fromisoformat(d0)).days
        if span <= 2 * tol:
            return p0 + (p1 - p0) * gap / span
    return p0 if gap <= tol else None


def model_calls(pred, game, pids):
    """{pid: {horizon: implied move % from today's price}} for 1m, 6m and 12m."""
    out = collections.defaultdict(dict)
    for h in ("1m", "6m", "12m"):
        try:
            rows = pred.execute(
                "SELECT product_id, base_price, forecast_price, ret FROM forecasts "
                "WHERE game=? AND target='ungraded' AND horizon=? AND printing='' AND base_price>0",
                (game, h)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for pid, base, fc, ret in rows:
            if pid in pids:
                out[pid][h] = r1((ret if ret is not None else fc / base - 1) * 100)
    return out


def chase_comparables(pc, game, cat, set_name, chase, by_date, release, age, today):
    """For each chase card, the card at the same price rank in each of the
    game's last few big sets at the same age since release (2026-11-10,
    user: compare the top cards with similar cards from other sets and say
    what to expect from that). Rank, not price, is the likeness: price
    levels drift across a year (One Piece's number one card at day 8 cost
    $300 in April and $1,500 in November), so "the set's number one card"
    is what a collector compares. Each comparable carries what it did over
    the following months from that age and where it sits now; the chase
    card carries its own path so far. {pid: {...}}.

    The pool is every earlier set of the game with COMP_SET_MIN+ priced
    cards and a release date inside PEER_LOOKBACK_DAYS, newest first; a
    card's price at a given age is read between its weekly readings, so the
    blended history (long_history) reaches back years, and that is what
    gives the older comparables six-month outcomes."""
    if age is None or not release or not chase:
        return {}
    lookback = (today - timedelta(days=PEER_LOOKBACK_DAYS)).isoformat()
    by_set = collections.defaultdict(list)
    for pid, c in cat.items():
        if c["set"] and c["set"] != set_name:
            by_set[c["set"]].append(pid)
    # A set with no release date (Magic has a few catalog-only ones) has no
    # age to match on, so it leaves before the sort.
    earlier = [(r, s) for r, s in ((set_release(cat, s), s) for s in by_set if len(by_set[s]) >= COMP_SET_MIN)
               if r]
    earlier = sorted(((r, s) for r, s in earlier if r <= release and r >= lookback), reverse=True)
    # Every big earlier set's cards ranked by price at day `age` (a card
    # needs a reading there and a month later, and art to show).
    ranked = []                               # [(set, release, [(pid, at, series)] by price desc)]
    for r, s in earlier:
        r0 = date.fromisoformat(r)
        rows = []
        for pid, series in card_paths(long_history(pc, game, by_set[s])).items():
            at = price_on(series, (r0 + timedelta(days=age)).isoformat(), COMP_TOL_DAYS)
            if at and at >= MIN_BASE and price_on(series, (r0 + timedelta(days=age + 30)).isoformat(), COMP_TOL_DAYS):
                rows.append((pid, at, series))
        if len(rows) >= COMP_SET_MIN // 2:
            rows.sort(key=lambda t: -t[1])
            ranked.append((s, r, rows))
        if len(ranked) >= COMP_N + 2:         # a couple spare for cards without art
            break
    if not ranked:
        return {}
    own_paths = card_paths(long_history(pc, game, [c[0] for c in chase]))
    rel = date.fromisoformat(release)
    out = {}
    for k, (pid, _name, _price, _rarity, _w) in enumerate(chase):
        picked = []
        for s, r, rows in ranked:
            if k >= len(rows) or not has_art(game, rows[k][0]):
                continue
            cpid, at, series = rows[k]
            r0 = date.fromisoformat(r)
            nxt = {}
            for h, days in COMP_HORIZONS:
                later = price_on(series, (r0 + timedelta(days=age + days)).isoformat(), COMP_TOL_DAYS)
                if later:
                    nxt[f"next_{h}_pct"] = r1((later / at - 1) * 100)
            last_d, last_p = series[-1]
            path = [((date.fromisoformat(d) - r0).days, r1((pp / at - 1) * 100)) for d, pp in series
                    if 0 <= (date.fromisoformat(d) - r0).days <= age + COMP_SPAN_DAYS]
            picked.append({"pid": cpid, "name": cat[cpid]["name"], "set": s, "released": r,
                           "rarity": cat[cpid]["rarity"], "rank_in_its_set": k + 1,
                           "price_at_same_age_usd": round(at, 2), "now_usd": round(last_p, 2),
                           "age_now_days": (date.fromisoformat(last_d) - r0).days,
                           "now_vs_same_age_pct": r1((last_p / at - 1) * 100), **nxt, "_path": path})
            if len(picked) == COMP_N:
                break
        if not picked:
            continue
        entry = {"rank": k + 1, "cards": picked,
                 "own_path": [((date.fromisoformat(d) - rel).days, pp) for d, pp in own_paths.get(pid, [])
                              if (date.fromisoformat(d) - rel).days >= 0]}
        for h, _days in COMP_HORIZONS:
            vals = [c[f"next_{h}_pct"] for c in picked if f"next_{h}_pct" in c]
            if len(vals) >= 2:
                entry[f"median_next_{h}_pct"] = r1(statistics.median(vals))
        out[pid] = entry
        if os.environ.get("TCG_COMP_DEBUG"):
            print(f"    chase #{k + 1}: " + "; ".join(
                f"{c['set'][:24]} #{k + 1} {c['name'][:22]} ${c['price_at_same_age_usd']:,.0f} "
                f"1m {c.get('next_1m_pct')} 6m {c.get('next_6m_pct')}" for c in picked), flush=True)
    return out


def ladder(prices):
    rows = []
    for label, lo, hi in LADDER:
        n = sum(1 for p in prices if p >= lo and (hi is None or p < hi))
        rows.append((label, n))
    return rows


def has_art(game, pid, _cache={}):
    """Only cards whose art is actually on the CDN get a picture slot."""
    key = (game, pid)
    if key not in _cache:
        try:
            import requests
            _cache[key] = requests.head(f"{CDN}/{game}/{pid}.jpg", timeout=10).status_code == 200
        except Exception:
            _cache[key] = False
    return _cache[key]


# ---------------------------------------------------------------------------
# Sales volume (2026-11-07)
# ---------------------------------------------------------------------------
SALES_WEEKS = 8           # weekly buckets on the top sellers' line chart


def monday_of(d):
    return d - timedelta(days=d.weekday())


def sales_volume(pc, game, cat, pids, today):
    """Near Mint copies sold (all printings) for these cards in the last
    complete Monday-to-Sunday week and the week before, from
    tcg_sales_history. A card counts only if its sales were fetched after
    that week closed (tcg_sales_scan), so the bucket is final rather than
    frozen mid-week at the last visit; that is what the day-before priority
    crawl (write_watch) is for. None when the tables are missing or nothing
    is covered."""
    pids = list(pids)
    if not pids:
        return None
    week_open = monday_of(today)
    last_week = week_open - timedelta(days=7)
    prev_week = week_open - timedelta(days=14)
    try:
        scans = dict(pc.execute(
            f"SELECT product_id, fetched_at FROM tcg_sales_scan WHERE game=? "
            f"AND product_id IN ({','.join('?' * len(pids))})", [game, *pids]).fetchall())
        rows = pc.execute(
            f"SELECT product_id, bucket_date, quantity_sold, transaction_count FROM tcg_sales_history "
            f"WHERE game=? AND condition='Near Mint' AND bucket_date IN (?, ?) "
            f"AND product_id IN ({','.join('?' * len(pids))})",
            [game, prev_week.isoformat(), last_week.isoformat(), *pids]).fetchall()
    except sqlite3.OperationalError:
        return None
    fresh = {pid for pid, f in scans.items() if f and f[:10] >= week_open.isoformat()}
    if not fresh:
        return None
    units = collections.defaultdict(float)
    txn = collections.defaultdict(float)
    for pid, d, q, t in rows:
        if pid in fresh:
            units[(pid, d)] += q or 0.0
            txn[(pid, d)] += t or 0.0
    lw, pw = last_week.isoformat(), prev_week.isoformat()
    per_card = {pid: (units.get((pid, lw), 0.0), units.get((pid, pw), 0.0)) for pid in fresh}
    u_last = sum(v[0] for v in per_card.values())
    u_prev = sum(v[1] for v in per_card.values())
    top = sorted(((pid, cat[pid]["name"], v[0], v[1], cat[pid]["price"]) for pid, v in per_card.items()
                  if pid in cat and v[0] > 0), key=lambda t: -t[2])[:TOP_SELLERS_N]
    enough = len(fresh) >= max(5, int(VOLUME_MIN_COVER * len(pids))) and (u_last + u_prev) > 0
    # The top sellers' last SALES_WEEKS weekly buckets, for the section's line
    # chart (2026-11-08): a week with no bucket sold nothing.
    weeks = [(last_week - timedelta(days=7 * k)).isoformat() for k in range(SALES_WEEKS - 1, -1, -1)]
    lines = []
    if top:
        tp = [t[0] for t in top]
        try:
            hist = pc.execute(
                f"SELECT product_id, bucket_date, quantity_sold FROM tcg_sales_history "
                f"WHERE game=? AND condition='Near Mint' AND bucket_date >= ? AND bucket_date <= ? "
                f"AND product_id IN ({','.join('?' * len(tp))})", [game, weeks[0], weeks[-1], *tp]).fetchall()
        except sqlite3.OperationalError:
            hist = []
        wk = collections.defaultdict(float)
        for pid, d, q in hist:
            wk[(pid, d)] += q or 0.0
        lines = [(name, [wk.get((pid, w), 0.0) for w in weeks]) for pid, name, *_ in top]
        # Weeks before anything sold (the set was not out yet) are not zeros
        # worth drawing (2026-11-10, user: a line flat at 0 for seven weeks
        # and one spike): the chart starts at the first week with a sale,
        # and under three such weeks there is no chart, only the table.
        first = next((i for i in range(len(weeks)) if any(ys[i] > 0 for _n, ys in lines)), None)
        if first is None or len(weeks) - first < 3:
            lines = []
        elif first:
            weeks = weeks[first:]
            lines = [(n, ys[first:]) for n, ys in lines]
    return {"week": lw, "prev_week": pw, "units_last": u_last, "units_prev": u_prev,
            "txn_last": sum(txn.get((pid, lw), 0.0) for pid in fresh),
            "change_pct": r1((u_last / u_prev - 1) * 100) if u_prev >= 20 else None,
            "covered": len(fresh), "of": len(pids), "per_card": per_card, "top": top, "enough": enough,
            "weeks": weeks, "top_lines": lines}


def sold_tag(vc, pid):
    """Tile suffix: the card's Near Mint copies sold in the last complete week."""
    v = vc.get(pid)
    n = v[0] if isinstance(v, tuple) else v
    return f" · {n:,.0f} sold" if n else ""


def sales_facts(vol, scope):
    return {"meaning": (f"Near Mint copies sold on TCGplayer, all printings, in the last complete "
                        f"Monday-to-Sunday week ({vol['week']}) against the week before ({vol['prev_week']}); "
                        f"only the {vol['covered']} of {vol['of']} {scope} whose sales were fetched after "
                        "that week closed are counted, so these numbers are final"),
            "copies_sold_last_week": round(vol["units_last"]), "copies_sold_week_before": round(vol["units_prev"]),
            "change_pct": vol["change_pct"], "separate_sales_last_week": round(vol["txn_last"]),
            "note": ("first week with sales, there is no week before to compare" if vol["units_prev"] == 0 else None),
            "top_sellers": [{"name": n, "copies": round(u), "week_before": round(p), "price_usd": round(pr, 2)}
                            for _pid, n, u, p, pr in vol["top"]]}


def sales_block(prose, vol, game, label, scope):
    """The 'What Actually Sold' section: the top sellers' copies per week as
    lines, the table, the fallback sentence."""
    top = vol["top"]
    series = [(short_label(n, 22), ys, PALETTE[i % len(PALETTE)])
              for i, (n, ys) in enumerate(vol.get("top_lines", [])[:SALES_LINES])]
    chart = line_chart(vol["weeks"], series, title=f"{label}: Near Mint copies sold per week",
                       yfmt=lambda v: f"{v:,.0f}") if series and vol.get("weeks") else ""
    table = [f"<table class='report-table'><thead><tr><th>Card</th><th>Copies, week of {vol['week']}</th>"
             "<th>Week before</th><th>Price</th></tr></thead><tbody>"]
    for pid, n, u, p, pr in top:
        table.append(f"<tr><td>{card_link(game, pid, n)}</td><td>{u:,.0f}</td><td>{p:,.0f}</td>"
                     f"<td>{money(pr)}</td></tr>")
    table.append("</tbody></table>")
    chg = vol["change_pct"]
    txt = (f"Across the {vol['covered']} of {vol['of']} {scope} with final sales data, "
           f"{vol['units_last']:,.0f} Near Mint copies sold in the week of {vol['week']}"
           + (", their first week with sales." if vol["units_prev"] == 0 else
              f", against {vol['units_prev']:,.0f} the week before" + (f" ({chg:+.0f} percent)." if chg is not None else ".")))
    if top:
        txt += f" {top[0][1]} sold the most, {top[0][2]:,.0f} copies."
    return section(prose, "sales", "What Actually Sold", txt, chart + "".join(table),
                   f"Copies sold per week by the top sellers, Near Mint, all printings; the table is the week of {vol['week']}.")


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
def move_stats(moves):
    gains = [m[4] for m in moves if m[4] > 0.5]
    losses = [m[4] for m in moves if m[4] < -0.5]
    return {"tracked": len(moves), "rose": len(gains), "fell": len(losses),
            "avg_gain_pct": r1(statistics.mean(gains)) if gains else 0.0,
            "avg_loss_pct": r1(statistics.mean(losses)) if losses else 0.0,
            "median_move_pct": r1(statistics.median(m[4] for m in moves)) if moves else None}


def index_series(synth, keys, dates, min_n=50):
    """% vs the first date, per date: mean of per-card ratios over `keys`
    (base printings) priced on both days. Same recipe as the market report's
    per-game drift line."""
    base, per = {}, {}
    for key in keys:
        s = synth.get(key)
        if not s:
            continue
        pts = {d: p for d, (p, src) in s.items() if src == "tcg"}
        if pts.get(dates[0], 0) >= MIN_BASE:
            base[key], per[key] = pts[dates[0]], pts
    out = []
    for d in dates:
        ratios = [per[k][d] / b for k, b in base.items() if d in per[k]]
        ratios = [r for r in ratios if 1 / MAX_WEEK_RATIO <= r <= MAX_WEEK_RATIO]
        out.append((statistics.mean(ratios) - 1) * 100 if len(ratios) >= min_n else None)
    return out


# Line charts in place of bars (2026-11-08, user: "remove the bar charts on
# the articles (keep them on the reports) and replace with a line graph").
# Wherever an article ranked groups by their week move as a bar each, it
# now draws each group's price index day by day through the window (the
# same recipe as the game's drift line); the table beside it keeps the
# ranking. Counts (price bands, copies sold) are drawn as one line over
# their ordered categories.
PALETTE = ["#ffcb05", "#7fb0ea", "#c678dd", "#3fd98a", "#ff9e64", "#8b96ad"]
LINES_MAX = 6             # index lines on one chart; more is a tangle
SALES_LINES = 4           # weekly sales swing harder, so fewer lines fit
# Short band labels for the ladder's x axis (six of them share 470px).
BAND_SHORT = {"under $1": "under $1", "$1 to $5": "$1-5", "$5 to $20": "$5-20",
              "$20 to $50": "$20-50", "$50 to $100": "$50-100", "$100 and up": "$100+"}


def group_lines(synth, groups, dates, min_n=2):
    """[(label, keys)] -> [(label, [pct-or-None per date])]: each group's
    price index over the window; groups with no drawable line are dropped."""
    out = []
    if not dates:
        return out
    for label, keys in groups:
        ys = index_series(synth, keys, dates, min_n=min_n)
        if any(v is not None for v in ys):
            out.append((label, ys))
    return out


def lines_chart(dates, lines, title):
    series = [(short_label(label, 22), ys, PALETTE[i % len(PALETTE)])
              for i, (label, ys) in enumerate(lines[:LINES_MAX])]
    return line_chart(dates, series, title=title) if series and dates else ""


def band_line(rows, title):
    """The price ladder as one line over its bands, every band labelled."""
    if not rows or not any(n for _, n in rows):
        return ""
    return line_chart([b for b, _ in rows], [("", [float(n) for _, n in rows], "var(--primary, #3d7dca)")],
                      title=title, xfmt=lambda b: BAND_SHORT.get(b, b), yfmt=lambda v: f"{v:,.0f} cards",
                      xticks="all")


def forecast_bits(pred, game, cat, pids=None, min_pick=10.0):
    """The model's 1-month view of a game or a set: how its calls split
    (up / down / flat) and the climbs it is most confident in, each scaled
    to the card's price TODAY (a forecast is a return from its anchor,
    2026-11-06)."""
    rows = pred.execute(
        "SELECT product_id, base_price, forecast_price, ret FROM forecasts "
        "WHERE game=? AND target='ungraded' AND horizon='1m' AND printing='' "
        "AND base_price>0", (game,)).fetchall()
    up = down = flat = 0
    picks = []
    for pid, base, fcst, ret in rows:
        c = cat.get(pid)
        if not c or (pids is not None and pid not in pids) or c["price"] < MIN_BASE:
            continue
        ratio = fcst / base
        if ret is None:
            ret = ratio - 1
        if ret >= 0.01:
            up += 1
        elif ret <= -0.01:
            down += 1
        else:
            flat += 1
        if c["price"] >= min_pick:
            picks.append((pid, c["name"], c["price"], c["price"] * ratio, (ratio - 1) * 100))
    picks.sort(key=lambda p: -p[4])
    return {"up": up, "down": down, "flat": flat}, picks[:PICKS_N]


def record_for(pred, game):
    rows = [r for r in live_accuracy(pred, "1m", 28, by_game=True) if r[0] == game]
    if not rows:
        return None
    _g, n, mae, bias, hit, dn, da, flat = rows[0]
    return {"checked": n, "typical_miss_pct": r1((exp(mae) - 1) * 100),
            "flat_baseline_miss_pct": r1((exp(flat) - 1) * 100),
            "bias_pct": r1((exp(bias) - 1) * 100), "band80_pct": r1(hit * 100),
            "direction_pct": r1(da * 100) if da is not None else None}


# ---------------------------------------------------------------------------
# HTML pieces
# ---------------------------------------------------------------------------
def card_tile(game, pid, name, meta_html):
    return (f"<a class='report-card' href='/catalog/{game}/{pid}'>"
            f"<img src='{CDN}/{game}/{pid}.jpg' alt='{esc(name)}' loading='lazy'>"
            f"<span class='report-card-name'>{esc(name)}</span>"
            f"<span class='report-card-meta'>{meta_html}</span></a>")


def grid(tiles):
    return f"<div class='report-grid'>{''.join(tiles)}</div>" if tiles else ""


def caption(text):
    return f"<p class='report-caption'><em>{esc(text)}</em></p>" if text else ""


COMPARE_TONES = ("a", "b", "c")      # one colour per comparable, legend swatch + line
# 2026-11-10, user: "Make each card a different color so it's clear. Card A
# should be the most vibrant though." Three muted hues (blue, coral, teal)
# under the chase card's yellow; the swatch CSS repeats them.
COMPARE_COLORS = {"a": "#7aa7e6", "b": "#e08a7a", "c": "#6cc3b8"}


def thin(path, n=6):
    """At most n evenly spaced points of a path, keeping both ends."""
    if len(path) <= n:
        return path
    step = (len(path) - 1) / (n - 1)
    return [path[round(i * step)] for i in range(n)]


def compare_chart(entry, age, price, calls):
    """One full-width, quiet chart per chase card (2026-11-10, user):
    this card's own history from its release to today, then the model's
    1M, 6M and 1Y calls as dollar values, in yellow; each comparable in its own muted colour,
    its value at those same ages since its own release, as a % of its price
    at today's age. A comparable younger than a horizon stops short of it.
    The history is compressed into the left part and the three horizons
    sit at equal distances (the card chart's rule), so a year fits."""
    W, H, PL, PR, PT, PB = 560, 200, 46, 40, 30, 34
    HF = 0.26                                 # share of the plot the history takes
    span = W - PL - PR
    hx = PL + HF * span                       # today's x
    slot = {h: PL + span * (HF + (1 - HF) * (k + 1) / 3) for k, (h, _d) in enumerate(COMP_HORIZONS)}

    def X(day):
        return PL + HF * span * (min(day, age) / age if age else 1.0)
    if not price:
        return ""
    # A card with no blended-history reading yet still gets its chart: its
    # own line starts at today's point.
    own = [(d, pr) for d, pr in (entry.get("own_path") or []) if d <= age]
    own_pct = thin([(d, (pr / price - 1) * 100) for d, pr in own])
    if not own_pct or own_pct[-1][0] < age:
        own_pct.append((age, 0.0))
    fc = [(h, calls.get(h)) for h, _d in COMP_HORIZONS]
    fc = [(h, v) for h, v in fc if v is not None]
    peers = []
    for tone, c in zip(COMPARE_TONES, entry["cards"]):
        hist = thin([(d, v) for d, v in c["_path"] if d <= age])
        if not hist or hist[-1][0] < age:
            hist.append((age, 0.0))
        pts = [(X(d), v) for d, v in hist]
        for h, _d in COMP_HORIZONS:
            v = c.get(f"next_{h}_pct")
            if v is None:
                break
            pts.append((slot[h], v))
        peers.append((tone, pts))
    vals = [v for _d, v in own_pct] + [v for _h, v in fc] + [v for _t, pts in peers for _x, v in pts] + [0.0]
    lo, hi = min(vals), max(vals)
    pad = max(6.0, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad

    def Y(v):
        return H - PB - (H - PT - PB) * (v - lo) / (hi - lo)
    muted = "var(--text-muted, #8b96ad)"
    out = [f"<svg class='report-compare-chart' viewBox='0 0 {W} {H}' role='img' "
           f"xmlns='http://www.w3.org/2000/svg'><title>{esc(entry['cards'][0]['set'])} and others at the same age</title>"]
    # Gridlines: zero and the extremes, labelled at the left (an extreme too
    # close to zero keeps its line but loses its label).
    for v in sorted({round(lo + pad), 0, round(hi - pad)}):
        out.append(f"<line x1='{PL}' y1='{Y(v):.1f}' x2='{W - PR}' y2='{Y(v):.1f}' "
                   f"stroke='var(--border, #2e3a52)' opacity='{1 if v == 0 else 0.5}'/>")
        if v == 0 or abs(Y(v) - Y(0)) >= 14:
            out.append(f"<text class='report-compare-tick' x='{PL - 6}' y='{Y(v) + 3.5:.1f}' text-anchor='end' "
                       f"font-size='10' fill='{muted}'>{f'{v:+d}%' if v else '0%'}</text>")
    # Today's hairline and the x labels.
    out.append(f"<line x1='{hx:.1f}' y1='{PT - 8}' x2='{hx:.1f}' y2='{H - PB}' stroke='{muted}' opacity='0.45'/>")
    out.append(f"<text class='report-compare-tick' x='{PL}' y='{H - PB + 16}' font-size='10' fill='{muted}'>release</text>")
    out.append(f"<text class='report-compare-tick' x='{hx:.1f}' y='{H - PB + 16}' text-anchor='middle' font-size='10' fill='{muted}'>today · day {age}</text>")
    for (h, _d), label in zip(COMP_HORIZONS, ("+1M", "+6M", "+1Y")):
        out.append(f"<text class='report-compare-tick' x='{slot[h]:.1f}' y='{H - PB + 16}' text-anchor='middle' font-size='10' fill='{muted}'>{label}</text>")
    # The comparables, one muted colour each; dots where a horizon value exists.
    for tone, pts in peers:
        col = COMPARE_COLORS[tone]
        out.append(f"<polyline points='{' '.join(f'{x:.1f},{Y(v):.1f}' for x, v in pts)}' fill='none' "
                   f"stroke='{col}' stroke-width='1.6' opacity='0.85'/>")
        for x, v in pts:
            if x > hx + 1:
                out.append(f"<circle cx='{x:.1f}' cy='{Y(v):.1f}' r='2.8' fill='{col}'/>")
    # This card: history solid, the calls lighter, dollar labels on the dots.
    out.append(f"<polyline points='{' '.join(f'{X(d):.1f},{Y(v):.1f}' for d, v in own_pct)}' fill='none' "
               "stroke='#ffcb05' stroke-width='2.2'/>")
    if fc:
        pts = [(hx, 0.0)] + [(slot[h], v) for h, v in fc]
        out.append(f"<polyline points='{' '.join(f'{x:.1f},{Y(v):.1f}' for x, v in pts)}' fill='none' "
                   "stroke='#ffcb05' stroke-width='1.6' opacity='0.55'/>")
    out.append(f"<circle cx='{hx:.1f}' cy='{Y(0):.1f}' r='3.5' fill='#ffcb05'/>")
    dollars = lambda v: f"${v:,.0f}"
    out.append(f"<text class='report-compare-num' x='{hx:.1f}' y='{PT - 12}' text-anchor='middle' font-size='11' "
               f"font-weight='600' fill='var(--text, #e8edf5)'>{dollars(price)}</text>")
    for h, v in fc:
        y = Y(v)
        out.append(f"<circle cx='{slot[h]:.1f}' cy='{y:.1f}' r='3.5' fill='#ffcb05'/>"
                   f"<text class='report-compare-num' x='{slot[h]:.1f}' y='{max(PT - 2, y - 11):.1f}' text-anchor='middle' "
                   f"font-size='11' font-weight='600' fill='var(--text, #e8edf5)'>{dollars(price * (1 + v / 100))}</text>")
    out.append("</svg>")
    return "".join(out)


def compare_graphic(game, chase, like, age, calls):
    """The chase cards beside the earlier cards most like them (2026-11-10):
    one block per card: the card and its comparables across the full row,
    the chart on its own row under them. Returns (html, pids shown)."""
    rows, shown = [], set()
    for pid, name, price, _rarity, _w in chase:
        entry = like.get(pid)
        if not entry or not has_art(game, pid):
            continue
        chart = compare_chart(entry, age, price, calls.get(pid, {}))
        if not chart:
            continue
        peers = "".join(
            f"<a class='report-compare-peer' href='/catalog/{game}/{c['pid']}'>"
            f"<img src='{CDN}/{game}/{c['pid']}.jpg' alt='{esc(c['name'])}' loading='lazy'>"
            f"<span class='report-compare-peer-text'><span class='report-compare-peer-name'>"
            f"<span class='report-compare-swatch-{tone}'></span>{esc(c['name'])}</span>"
            f"<span class='report-compare-peer-meta'>{esc(short_label(c['set'], 28))} · {money(c['price_at_same_age_usd'])} at day {age}"
            f" · {esc(pct(c['now_vs_same_age_pct']))} since</span></span></a>"
            for tone, c in zip(COMPARE_TONES, entry["cards"]))
        rows.append(f"<div class='report-compare-row'>"
                    f"<a class='report-compare-card' href='/catalog/{game}/{pid}'>"
                    f"<img src='{CDN}/{game}/{pid}.jpg' alt='{esc(name)}' loading='lazy'>"
                    f"<span class='report-compare-name'><span class='report-compare-swatch-own'></span>{esc(name)}</span>"
                    f"<span class='report-compare-meta'>{money(price)} · day {age}</span></a>"
                    f"<div class='report-compare-like'><span class='report-compare-label'>Number {entry['rank']} of the last sets at day {age}</span>{peers}</div>"
                    f"{chart}</div>")
        shown.add(pid)
    return (f"<div class='report-compare'>{''.join(rows)}</div>" if rows else ""), shown


def section(prose, key, header, body_text, figure, cap):
    """<h2>, the paragraph, the picture or chart, its caption."""
    h = prose.get("headers", {}).get(key) or header
    p = prose.get("sections", {}).get(key) or body_text
    c = prose.get("captions", {}).get(key) or cap
    return (f"<h2>{esc(h)}</h2><p>{esc(p)}</p>{figure}{caption(c) if figure else ''}")


def model_corner(prose, key, header, body_text, picks, game, record, links, extra=""):
    out = [f"<div class='report-model-corner'><h2>{esc(prose.get('headers', {}).get(key) or header)}</h2>",
           f"<p>{esc(prose.get('sections', {}).get(key) or body_text)}</p>"]
    if picks:
        out.append("<table class='report-table'><thead><tr><th>Card</th><th>Now</th>"
                   "<th>1M forecast</th><th>Implied</th></tr></thead><tbody>")
        for pid, name, now, fc, imp in picks:
            out.append(f"<tr><td>{card_link(game, pid, name)}</td><td>{money(now)}</td>"
                       f"<td>{money(fc)}</td><td>{pct(imp)}</td></tr>")
        out.append("</tbody></table>")
    if record:
        out.append(
            "<table class='report-table'><thead><tr><th>Checked</th><th>Typical miss</th>"
            "<th>Flat baseline</th><th>80% band</th><th>Direction</th></tr></thead><tbody>"
            f"<tr><td>{record['checked']:,}</td><td>{record['typical_miss_pct']}%</td>"
            f"<td>{record['flat_baseline_miss_pct']}%</td><td>{record['band80_pct']:.0f}%</td>"
            f"<td>{record['direction_pct']:.0f}%</td></tr></tbody></table>"
            if record["direction_pct"] is not None else
            "<table class='report-table'><thead><tr><th>Checked</th><th>Typical miss</th>"
            "<th>Flat baseline</th><th>80% band</th></tr></thead><tbody>"
            f"<tr><td>{record['checked']:,}</td><td>{record['typical_miss_pct']}%</td>"
            f"<td>{record['flat_baseline_miss_pct']}%</td><td>{record['band80_pct']:.0f}%</td>"
            "</tr></tbody></table>")
    if extra:
        out.append(extra)
    out.append(f"<p class='report-note'>Forecasts are model estimates, not financial advice. "
               f"{links}</p></div>")
    return "".join(out)


def hero_and_thumb(kind, game, title, today, stats, cards, line, focus=None):
    """`line` is (dates, values): the set's or game's price index through the
    week, drawn as one line where the reports' heroes carry a bar per game
    (2026-11-08); None when the window has no drawable index."""
    from report_thumbnail import render_svg
    label = f"{GAMES[game]['label'].upper()} · {KIND_LABEL['recent' if focus == 'recent' else kind]}"
    pub = today.strftime("%B %-d, %Y")
    if line and not any(v is not None for v in line[1]):
        line = None
    hero = render_svg(kind, title, pub, stats, cards, [], label=label, spark_line=line)
    thumb = {"kind": kind, "label": label, "stats": stats, "spark": [],
             "spark_line": [list(line[0]), list(line[1])] if line else None,
             "cards": [list(c) for c in cards]}
    return hero, json.dumps(thumb)


def window_line(w):
    return f" Price window: {w['start']} to {w['end']}, ungraded market prices."


# Which incarnation of a character a card is (2026-11-07, user: "mix in some
# of the character's lore ... sparingly"): the writer gets a one-line "about"
# per card it may discuss (type, source series, crew or traits, from
# market_report.card_attrs) so the lore it reaches for is the right universe
# and the right version. The lore rule itself is report_editorial.LORE.
ABOUT_DIMS = ("type", "series", "trait", "energy", "stage", "form", "ink", "aspect", "attribute")


def about_lines(attrs, pids):
    out = {}
    for pid in pids:
        a = attrs.get(pid) if attrs else None
        if not a:
            continue
        parts = []
        for dim in ABOUT_DIMS:
            v = a.get(dim)
            for x in (v if isinstance(v, list) else [v])[:3]:
                if x and str(x) not in parts:
                    parts.append(str(x))
        if parts:
            out[pid] = " · ".join(parts)[:90]
    return out


# ---------------------------------------------------------------------------
# SET article
# ---------------------------------------------------------------------------
def collect_set(pc, pred, game, cat, set_name, synth, window, young, today, skipped=(), attrs=None):
    label = GAMES[game]["label"]
    baseline, latest, dates = window
    members = {pid: c for pid, c in cat.items() if c["set"] == set_name}
    names = {pid: c["name"] for pid, c in members.items()}
    moves = game_moves(synth, game, baseline, latest, names, young) if baseline else []
    moves = [m for m in moves if not m[5]]            # base printings only
    by_pid = {m[0]: m for m in moves}
    stats = move_stats(moves)
    by_date = set_history(pc, game, members)
    broad = broad_days(by_date, len(members))
    # No release date at all (One Piece with the group list unreachable):
    # the set is dated by its first broadly priced day.
    release = set_release(cat, set_name) or (broad[0] if broad else None)
    age = (today - date.fromisoformat(release)).days if release else None
    path, _lag = set_index_path(by_date, release, len(members))

    chase = sorted(members.items(), key=lambda kv: -kv[1]["price"])[:CHASE_N * 2]
    chase = [(pid, c["name"], c["price"], c["rarity"],
              by_pid[pid][4] if pid in by_pid else None) for pid, c in chase]
    ups = sorted((m for m in moves if m[4] > 0.5), key=lambda m: -m[4])
    downs = sorted((m for m in moves if m[4] < -0.5), key=lambda m: m[4])

    rar = collections.defaultdict(lambda: {"prices": [], "moves": []})
    for pid, c in members.items():
        r = c["rarity"] or "Unlisted"
        rar[r]["prices"].append(c["price"])
        if pid in by_pid:
            rar[r]["moves"].append(by_pid[pid][4])
    rarities = []
    for r, d in rar.items():
        rarities.append({"rarity": r, "cards_priced": len(d["prices"]),
                         "tracked": len(d["moves"]),
                         "median_price_usd": round(statistics.median(d["prices"]), 2),
                         "median_move_pct": r1(statistics.median(d["moves"])) if d["moves"] else None})
    rarities.sort(key=lambda x: -x["median_price_usd"])

    # Price index since release: the set's own path (presale days before the
    # release date are not on it).
    history = None
    pts = path
    if len(pts) >= 3:
        if len(pts) > 40:          # keep the chart readable: ~40 points, ends kept
            step = len(pts) / 39
            pts = [pts[int(i * step)] for i in range(39)] + [pts[-1]]
        history = {"dates": [p[1] for p in pts], "index": [p[2] for p in pts]}

    # Earlier sets at the same age since release, and what each did over the
    # month (and longer) after that age, for the model comparison.
    peers = peer_sets(pc, game, cat, set_name, release, today) if age is not None else []
    compare = []
    for p in peers:
        at = index_at(p["path"], age)
        if at is None:
            continue
        row = {"set": p["set"], "released": p["release"], "priced_from_day": p["lag"],
               "index_at_age_pct": r1(at),
               "age_now_days": p["path"][-1][0], "index_now_pct": r1(p["path"][-1][2])}
        for h, days in HORIZON_DAYS.items():
            later = index_at(p["path"], age + days)
            if later is not None:
                row[f"next_{h}_pct"] = r1(((100 + later) / (100 + at) - 1) * 100)
        compare.append(row)

    calls, picks = forecast_bits(pred, game, cat, pids=set(members), min_pick=MIN_BASE)
    fc_medians = set_forecast_medians(pred, game, cat, set(members))
    model = model_calls(pred, game, set(members))
    like = chase_comparables(pc, game, cat, set_name, chase[:CHASE_N], by_date, release, age, today)
    sales = sales_volume(pc, game, cat, members, today)
    tracked_old = sum(m[2] for m in moves)
    tracked_new = sum(m[3] for m in moves)
    # Day-by-day lines through the window (2026-11-08): one per rarity with
    # two or more tracked cards, and the whole set for the hero.
    rarity_lines = group_lines(synth, [
        (r["rarity"], [(game, pid) for pid, c in members.items() if (c["rarity"] or "Unlisted") == r["rarity"]])
        for r in rarities if r["tracked"] >= 2], dates)
    index_line = (dates, index_series(synth, [(game, pid) for pid in members], dates, min_n=5)) if dates else None
    return {
        "game": game, "label": label, "set": set_name, "set_slug": slugify(set_name),
        "dates": dates, "rarity_lines": rarity_lines, "index_line": index_line,
        "release": release, "age_days": age, "week": {"start": baseline, "end": latest},
        "cards_priced": len(members), "set_value": round(sum(c["price"] for c in members.values()), 2),
        "tracked_value_change_pct": r1((tracked_new / tracked_old - 1) * 100) if tracked_old else None,
        "stats": stats, "chase": chase, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "rarities": rarities, "history": history, "path": path,
        "peers": peers, "compare": compare, "forecast_median": fc_medians, "sales": sales,
        "model_calls": model, "like": like,
        "ladder": ladder([c["price"] for c in members.values()]),
        "calls": calls, "picks": picks, "record": record_for(pred, game),
        "skipped": list(skipped),
        "about": about_lines(attrs, [c[0] for c in chase] + [m[0] for m in ups[:MOVERS_N * 2] + downs[:MOVERS_N * 2]]
                             + [p[0] for p in picks]),
    }


SET_SECTIONS = [
    ("chase", "the set's most valuable cards (the chase list): each one's price and move this week, then, for each, "
              "the earlier sets' cards most like it (like_earlier_cards) and what those did over the months after the "
              "same age, next to the model's own one-month, six-month and one-year calls on it (model_call_*_pct); say "
              "plainly what that record suggests we can expect of each top card, as a tendency with its numbers and "
              "range, never as advice (the picture shows each card beside its comparables and a chart: the card's own "
              "path and the model's three calls in yellow, each comparable at the same ages in its own colour)"),
    ("movers", "this week's biggest risers and fallers inside the set, and what the model expects of each over the "
               "next month (model_call_1m_pct) and six months (model_call_6m_pct)"),
    ("sales", "how many Near Mint copies of the set's cards actually sold in the last complete week against the week before, and which cards sold most"),
    ("rarity", "what each rarity costs and how it moved this week (the chart draws each rarity's price index day by day)"),
    ("history", "the set's price index since its release day: the average move of its cards from the first day they were broadly priced after release"),
    ("peers", "how this set compares with the game's earlier sets at the same number of days since release (every line on the chart starts on its set's release day), and what those sets did over the month after that age"),
    ("ladder", "how the set's priced cards spread across price bands"),
    ("outlook", "what the model's one-month calls say about the set, how its median call compares with what the earlier sets actually did from this age, and the model's own record on this game"),
]


def build_set_article(d, today, no_prose=False):
    game, label, set_name = d["game"], d["label"], d["set"]
    st = d["stats"]
    ab = d.get("about", {})
    vol = d.get("sales")
    vc = vol["per_card"] if vol else {}
    mc = d.get("model_calls", {})
    like = d.get("like", {})

    def like_facts(entry):
        if not entry:
            return None
        out = {"basis": (f"this card is number {entry['rank']} in its set by price; these are the number "
                         f"{entry['rank']} cards of the game's last big sets, each priced at the same day "
                         f"{d['age_days']} since its own set's release. next_1m/6m/12m_pct is what each did over "
                         f"the following month, six months and year from that age (missing when the card is "
                         f"not old enough yet); "
                         f"now_vs_same_age_pct is where it sits today. Read them as a tendency with a "
                         f"range, never a promise"),
               "cards": [{k: v for k, v in c.items() if not k.startswith("_") and k != "pid"}
                         for c in entry["cards"]]}
        out.update({k: v for k, v in entry.items() if k.startswith("median_")})
        return out
    sections = [s for s in SET_SECTIONS
                if not ((s[0] in ("movers", "rarity") and st["tracked"] == 0)
                        or (s[0] == "history" and not d["history"])
                        or (s[0] == "peers" and not d["compare"])
                        or (s[0] == "sales" and not (d["sales"] and d["sales"]["enough"])))]
    facts = {
        "game": label, "set": set_name, "released": d["release"], "age_days": d["age_days"],
        "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "set_value_usd": d["set_value"],
                   "tracked_value_change_pct": d["tracked_value_change_pct"], **st},
        "chase": [{"name": n, "price_usd": round(p, 2), "rarity": r, "week_pct": r1(w), "about": ab.get(_pid),
                   "copies_sold_last_week": round(vc[_pid][0]) if _pid in vc else None,
                   "model_call_1m_pct": mc.get(_pid, {}).get("1m"), "model_call_6m_pct": mc.get(_pid, {}).get("6m"),
                   "model_call_12m_pct": mc.get(_pid, {}).get("12m"),
                   "like_earlier_cards": like_facts(like.get(_pid))}
                  for _pid, n, p, r, w in d["chase"][:CHASE_N]],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                           "about": ab.get(m[0]),
                           "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None,
                           "model_call_1m_pct": mc.get(m[0], {}).get("1m"), "model_call_6m_pct": mc.get(m[0], {}).get("6m")}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                             "about": ab.get(m[0]),
                             "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None,
                             "model_call_1m_pct": mc.get(m[0], {}).get("1m"), "model_call_6m_pct": mc.get(m[0], {}).get("6m")}
                            for m in d["downs"][:MOVERS_N]]},
        "rarities": d["rarities"],
        "since_release": ({"from": d["history"]["dates"][0], "to": d["history"]["dates"][-1],
                           "index_now_pct": r1(d["history"]["index"][-1]),
                           "peak_pct": r1(max(d["history"]["index"])),
                           "trough_pct": r1(min(d["history"]["index"]))} if d["history"] else None),
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp), "about": ab.get(_pid),
                                  "copies_sold_last_week": round(vc[_pid][0]) if _pid in vc else None}
                                 for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
    }
    if vol and vol["enough"]:
        facts["sales"] = sales_facts(vol, "set cards")
    own_at = index_at(d["path"], d["age_days"]) if d["path"] and d["age_days"] is not None else None
    cmp_rows = d["compare"]
    vs = {}
    if cmp_rows:
        vals = [r["index_at_age_pct"] for r in cmp_rows]
        rank = 1 + sum(1 for v in vals if own_at is not None and v > own_at)
        facts["earlier_sets"] = {
            "basis": (f"each set's price index from its first broadly priced day on or after its real "
                      f"release date (priced_from_day says which day that was); only {label} sets priced "
                      f"within {PEER_LAG_MAX} days of release count, so a set we only started pricing "
                      f"later is not here"),
            "this_set_at_age": {"age_days": d["age_days"], "index_pct": r1(own_at),
                                "priced_from_day": d["path"][0][0] if d["path"] else None},
            "at_same_age": cmp_rows,
            "median_index_pct": r1(statistics.median(vals)),
            "rank_strongest_first": f"{rank} of {len(cmp_rows) + 1}",
        }
        for h, days in HORIZON_DAYS.items():
            nxt = [r[f"next_{h}_pct"] for r in cmp_rows if f"next_{h}_pct" in r]
            if h in d["forecast_median"] and len(nxt) >= 2:
                med, n = d["forecast_median"][h]
                vs[h] = {"model_median_call_pct": r1(med), "model_cards": n,
                         "earlier_sets_median_pct": r1(statistics.median(nxt)),
                         "earlier_sets_range_pct": [r1(min(nxt)), r1(max(nxt))],
                         "earlier_sets": len(nxt),
                         "meaning": f"what those sets did over the {days} days after day {d['age_days']}"}
        if vs:
            facts["model"]["vs_earlier_sets"] = vs
    if st["tracked"] == 0:
        facts["note"] = (f"The set is too new for a week of market history (under {YOUNG_DAYS} days "
                         "of prices), so there are no week moves yet; write about the chase list, "
                         "the ladder and the outlook.")
    if d["skipped"]:
        facts["note_newer"] = (f"{', '.join(d['skipped'])} came out under {SET_MIN_AGE_DAYS} days ago and "
                               f"gets its own article once it has a week of prices; this one is about "
                               f"{set_name}. You may say so in one sentence; do not guess at the newer "
                               "set's prices.")
    prose = {} if no_prose else (write_set_article(prune(facts), sections) or {})

    # ---- fallbacks ------------------------------------------------------
    if st["tracked"]:
        lede = (f"{set_name} had {st['rose']} of {st['tracked']} tracked cards rise this week "
                f"and {st['fell']} fall, a median move of {st['median_move_pct']:+.1f} percent. "
                f"The set's {d['cards_priced']} priced cards add up to {money(d['set_value'])}.")
        title = f"{set_name} this week: {st['rose']} cards up, {st['fell']} down"
    else:
        lede = (f"{set_name} is {d['cards_priced']} priced cards worth {money(d['set_value'])} "
                f"together, too new for a full week of market history yet.")
        title = f"{set_name}: the first look at {d['cards_priced']} priced cards"
    lede = prose.get("lede") or lede
    title = prose.get("headline") or title

    body = [f"<p class='report-lede'>{esc(lede)}{esc(window_line(d['week']))}</p>",
            "<p class='report-note'>“Tracked” cards are those with a market price on "
            "both ends of the week <em>and</em> worth at least $5 at the start; cheaper cards "
            "are left out because a few cents of movement reads as a large, meaningless "
            "percentage.</p>"]
    if d["skipped"]:
        body.append(f"<p class='report-note'>{esc(', '.join(d['skipped']))} is under a week old and "
                    "gets its own article once it has a week of prices.</p>")

    # Chase cards: the comparison blocks, and a plain tile only for a card
    # without comparables (2026-11-10, user: no card row where the chart is).
    compare_html, compared = compare_graphic(game, d["chase"][:CHASE_N], like, d["age_days"], mc)
    tiles = []
    for pid, name, price, rarity, w in d["chase"][:CHASE_N]:
        if not has_art(game, pid) or pid in compared:
            continue
        meta = money(price) + (f" · <span class='{'report-up' if w > 0 else 'report-down'}'>{pct(w)}</span>"
                               if w is not None and abs(w) > 0.5 else "") + sold_tag(vc, pid)
        tiles.append(card_tile(game, pid, name, meta))
    top = d["chase"][0] if d["chase"] else None
    chase_txt = (f"The most valuable card in the set is {top[1]} at {money(top[2])}." if top else
                 "The set has no priced cards yet.")
    top_like = like.get(top[0]) if top else None
    if top_like and top_like.get("median_next_6m_pct") is not None:
        chase_txt += (f" The earlier {label} cards most like it at day {d['age_days']} went a median "
                      f"{top_like['median_next_6m_pct']:+.1f} percent over the following six months.")
    body.append(section(
        prose, "chase", "The Chase List", chase_txt,
        grid(tiles) + compare_html,
        f"The top of {set_name}, by price, each beside the earlier cards most like it; the chart follows this card "
        f"in yellow through the model's one-month, six-month and one-year calls, and each comparable in its own colour at "
        f"those same ages since its own release."))

    # Movers
    if st["tracked"]:
        def call_tag(pid):   # the model's one-month call, on the tile (2026-11-10)
            v = mc.get(pid, {}).get("1m")
            return f" · model {pct(v)} next month" if v is not None else ""
        tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}{call_tag(m[0])}")
                 for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
        tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}{call_tag(m[0])}")
                  for m in d["downs"] if has_art(game, m[0])][:MOVERS_N]
        bt = d["ups"][0] if d["ups"] else None
        wt = d["downs"][0] if d["downs"] else None

        def expects(m):
            v = mc.get(m[0], {}).get("1m")
            return f" The model's one-month call on it is {v:+.1f} percent." if v is not None else ""
        txt = " ".join(filter(None, [
            f"{bt[1]} led the set, up {pct(bt[4])} to {money(bt[3])}.{expects(bt)}" if bt else None,
            f"{wt[1]} fell furthest, {pct(wt[4])} to {money(wt[3])}.{expects(wt)}" if wt else None])) \
            or "Nothing in the set moved more than half a percent this week."
        body.append(section(prose, "movers", "This Week's Movers", txt, grid(tiles),
                            "Risers on the left, fallers on the right."))

        # Rarity
        rows = [(r["rarity"], r["median_move_pct"], f"{r['tracked']} cards · median {money(r['median_price_usd'])}")
                for r in d["rarities"] if r["median_move_pct"] is not None and r["tracked"] >= 2]
        rows.sort(key=lambda r: -r[1])
        chart = lines_chart(d["dates"], d.get("rarity_lines", []),
                            f"{short_label(set_name, 36)}: price index this week, by rarity")
        table = ["<table class='report-table'><thead><tr><th>Rarity</th><th>Priced</th>"
                 "<th>Median price</th><th>Tracked</th><th>Median move</th></tr></thead><tbody>"]
        for r in d["rarities"]:
            table.append(f"<tr><td>{esc(r['rarity'])}</td><td>{r['cards_priced']}</td>"
                         f"<td>{money(r['median_price_usd'])}</td><td>{r['tracked']}</td>"
                         f"<td>{pct(r['median_move_pct']) if r['median_move_pct'] is not None else '–'}</td></tr>")
        table.append("</tbody></table>")
        pricey = d["rarities"][0] if d["rarities"] else None
        body.append(section(
            prose, "rarity", "Rarity Is a Tax Bracket",
            (f"{pricey['rarity']} is the priciest tier at a median {money(pricey['median_price_usd'])}."
             if pricey else "No rarity data for this set."),
            chart + "".join(table), "Each rarity's cards averaged, day by day through the week; the table ranks them."))

    # What actually sold (2026-11-07)
    if vol and vol["enough"]:
        body.append(sales_block(prose, vol, game, label, "set cards"))

    # Since release
    if d["history"]:
        h = d["history"]
        chart = line_chart(h["dates"], [(short_label(set_name, 24), h["index"], "#ffcb05")],
                           title=f"{short_label(set_name, 36)}: price index since {h['dates'][0]}")
        body.append(section(
            prose, "history", "The Road Since Release",
            f"Against its first broadly priced day since release, {h['dates'][0]}, the set's cards sit "
            f"{h['index'][-1]:+.1f} percent on average, with a peak of {max(h['index']):+.1f} percent "
            f"and a low of {min(h['index']):+.1f} percent.",
            chart, "The whole set, averaged, from its release day to now."))

    # Earlier sets at the same age (user, 2026-11-07). Every line starts on
    # its set's real release day; the x axis is days since release.
    if cmp_rows:
        A = d["age_days"]
        X = max(A, min(A + 30, max(r["age_now_days"] for r in cmp_rows)))
        ages = [str(a) for a in range(X + 1)]

        def on_grid(path):
            vals = [None] * (X + 1)
            for a, _dt, v in path:
                if 0 <= a <= X:
                    vals[a] = v
            return vals
        series = [(short_label(set_name, 22), on_grid(d["path"]), "#ffcb05")]
        palette = ["#7fb0ea", "#c678dd", "#3fd98a", "#ff9e64", "#8b96ad"]
        shown = {r["set"] for r in cmp_rows[:PEER_MAX]}
        for i, p in enumerate([p for p in d["peers"] if p["set"] in shown][:PEER_MAX]):
            series.append((short_label(p["set"], 22), on_grid(p["path"]), palette[i % len(palette)]))
        chart = line_chart(ages, series, title=f"{label}: price index by days since release",
                           xfmt=lambda s: f"Day {s}")
        table = [f"<table class='report-table'><thead><tr><th>Set</th><th>Released</th><th>Priced from</th>"
                 f"<th>At day {A}</th><th>Next month</th><th>Now</th></tr></thead><tbody>",
                 f"<tr><td><strong>{esc(set_name)}</strong></td><td>{esc(d['release'] or '')}</td>"
                 f"<td>day {d['path'][0][0] if d['path'] else '–'}</td>"
                 f"<td>{pct(own_at) if own_at is not None else '–'}</td><td>–</td>"
                 f"<td>{pct(own_at) if own_at is not None else '–'} (day {A})</td></tr>"]
        for r in cmp_rows[:PEER_MAX + 3]:
            nxt = r.get("next_1m_pct")
            table.append(f"<tr><td>{esc(r['set'])}</td><td>{esc(r['released'])}</td><td>day {r['priced_from_day']}</td>"
                         f"<td>{pct(r['index_at_age_pct'])}</td><td>{pct(nxt) if nxt is not None else '–'}</td>"
                         f"<td>{pct(r['index_now_pct'])} (day {r['age_now_days']})</td></tr>")
        table.append("</tbody></table>")
        vals = [r["index_at_age_pct"] for r in cmp_rows]
        best = max(cmp_rows, key=lambda r: r["index_at_age_pct"])
        worst = min(cmp_rows, key=lambda r: r["index_at_age_pct"])
        txt = (f"At day {A} since release, {set_name} sits {own_at:+.1f} percent against its release-week prices; "
               if own_at is not None else f"At day {A} since release, ")
        txt += (f"the {len(cmp_rows)} earlier {label} set{'s' if len(cmp_rows) != 1 else ''} we priced from "
                f"release week sat at a median {statistics.median(vals):+.1f} percent at the same age"
                + (f" ({best['set']} {best['index_at_age_pct']:+.1f}, {worst['set']} {worst['index_at_age_pct']:+.1f})."
                   if len(cmp_rows) > 1 else f" ({best['set']} {best['index_at_age_pct']:+.1f})."))
        body.append(section(prose, "peers", "How the Last Few Sets Did by Now", txt, chart + "".join(table),
                            f"Every line starts on its set's release day; {short_label(set_name, 24)} in yellow."))

    # Ladder
    chart = band_line(d["ladder"], f"{short_label(set_name, 36)}: priced cards by price band")
    over5 = sum(n for b, n in d["ladder"][2:])
    body.append(section(
        prose, "ladder", "Where the Money Sits",
        f"{over5} of the set's {d['cards_priced']} priced cards are worth $5 or more.",
        chart, "Every priced card in the set, sorted into bands."))

    # Outlook (model corner) + closer
    c = d["calls"]
    rec = d["record"]
    links = (f"<a href='/sets/{game}/{d['set_slug']}'>Every card in {esc(set_name)} with its price</a>, "
             f"or <a href='/catalog?game={game}&amp;sets={quote(set_name, safe='')}'>browse the set in the catalog</a>; "
             f"the About page explains how the forecasts are made.")
    txt = (f"Of the set's cards with a one-month call, the model has {c['up']} headed up, "
           f"{c['down']} headed down and {c['flat']} flat.")
    extra = ""
    if vs:
        v1 = vs.get("1m")
        if v1:
            txt += (f" Its median one-month call on the set's cards is {v1['model_median_call_pct']:+.1f} percent; "
                    f"from day {d['age_days']}, the {v1['earlier_sets']} earlier sets moved a median "
                    f"{v1['earlier_sets_median_pct']:+.1f} percent over the following month "
                    f"(from {v1['earlier_sets_range_pct'][0]:+.1f} to {v1['earlier_sets_range_pct'][1]:+.1f}).")
        rows = "".join(
            f"<tr><td>{HORIZON_LABEL[h]}</td><td>{pct(v['model_median_call_pct'])} ({v['model_cards']} cards)</td>"
            f"<td>{pct(v['earlier_sets_median_pct'])} ({v['earlier_sets']} sets, "
            f"{pct(v['earlier_sets_range_pct'][0])} to {pct(v['earlier_sets_range_pct'][1])})</td></tr>"
            for h, v in vs.items())
        extra = (f"<table class='report-table'><thead><tr><th>Window</th><th>Model, median call</th>"
                 f"<th>Earlier sets from day {d['age_days']}, median</th></tr></thead><tbody>{rows}</tbody></table>")
    if rec:
        txt += (f" On {label} its one-month forecasts have missed by {rec['typical_miss_pct']} percent "
                f"on average, against {rec['flat_baseline_miss_pct']} percent for assuming no change.")
    body.append(model_corner(prose, "outlook", "What the Model Thinks", txt, d["picks"], game, rec, links,
                             extra=extra))
    closer = prose.get("closer")
    if closer:
        body.append(f"<p>{esc(closer)}</p>")

    # Hero: the chase cards fanned, the set's week as one line.
    stats = [[f"{d['cards_priced']:,}", "priced cards", None],
             [money(d["set_value"]), "set value", None]]
    if st["tracked"]:
        stats = [[f"{st['rose']:,}", "cards rose", "up"], [f"{st['fell']:,}", "cards fell", "down"],
                 [f"{st['median_move_pct']:+.1f}%", "median move", None],
                 [money(d["set_value"]), "set value", None]]
    cards = [[game, pid, name] for pid, name, *_ in d["chase"] if has_art(game, pid)][:3]
    hero, thumb = hero_and_thumb("set", game, title, today, stats, cards, d.get("index_line"))
    return {"slug": f"set-report-{game}-{today.isoformat()}", "kind": "set", "focus": "set",
            "game": game, "set_name": set_name, "title": title, "summary": lede,
            "body_html": "".join(body), "thumb": thumb, "hero": hero}


# ---------------------------------------------------------------------------
# NEW RELEASES article: the set slot's other focus (2026-11-07, user: "the set
# analysis should switch back and forth between the set and any cards
# released recently that aren't in the set")
# ---------------------------------------------------------------------------
def released_on(pc, game, cat):
    """pid -> release date: the catalog's release_date, or, for a schema
    without one (One Piece), the first day of the card's TCGplayer price
    series."""
    if any(c["release"] for c in cat.values()):
        return {pid: c["release"] for pid, c in cat.items() if c["release"]}
    try:
        return dict(pc.execute(
            "SELECT product_id, MIN(date) FROM tcg_nm_history "
            "WHERE game=? AND printing='' AND price>0 GROUP BY product_id", (game,)).fetchall())
    except sqlite3.OperationalError:
        return {}


def recent_pool(released, cat, anchor, today):
    """Priced cards released inside RECENT_DAYS, at least SET_MIN_AGE_DAYS
    ago, that are not in the anchor set."""
    lo = (today - timedelta(days=RECENT_DAYS)).isoformat()
    hi = (today - timedelta(days=SET_MIN_AGE_DAYS)).isoformat()
    return {pid: c for pid, c in cat.items()
            if c["set"] != anchor and lo <= released.get(pid, "") <= hi}


def collect_recent(pc, pred, game, cat, anchor, pool, released, synth, window, young, today, attrs=None):
    label = GAMES[game]["label"]
    baseline, latest, dates = window
    names = {pid: c["name"] for pid, c in pool.items()}
    moves = game_moves(synth, game, baseline, latest, names, young) if baseline else []
    moves = [m for m in moves if not m[5]]            # base printings only
    by_pid = {m[0]: m for m in moves}
    stats = move_stats(moves)

    chase = sorted(pool.items(), key=lambda kv: -kv[1]["price"])[:CHASE_N * 2]
    chase = [(pid, c["name"], c["price"], c["set"] or "Other",
              by_pid[pid][4] if pid in by_pid else None) for pid, c in chase]
    ups = sorted((m for m in moves if m[4] > 0.5), key=lambda m: -m[4])
    downs = sorted((m for m in moves if m[4] < -0.5), key=lambda m: m[4])

    prod = collections.defaultdict(lambda: {"prices": [], "moves": [], "dates": collections.Counter(), "keys": []})
    for pid, c in pool.items():
        p = prod[c["set"] or "Other"]
        p["prices"].append(c["price"])
        p["dates"][released[pid]] += 1
        p["keys"].append((game, pid))
        if pid in by_pid:
            p["moves"].append(by_pid[pid][4])
    products = []
    for name, p in prod.items():
        products.append({"product": name, "cards_priced": len(p["prices"]),
                         "released": p["dates"].most_common(1)[0][0],
                         "value_usd": round(sum(p["prices"]), 2),
                         "median_price_usd": round(statistics.median(p["prices"]), 2),
                         "tracked": len(p["moves"]),
                         "median_move_pct": r1(statistics.median(p["moves"])) if p["moves"] else None})
    products.sort(key=lambda x: (-x["cards_priced"], x["product"]))
    # Day-by-day lines through the window (2026-11-08): one per release with
    # two or more tracked cards, and the whole pool for the hero.
    product_lines = group_lines(synth, [(p["product"], prod[p["product"]]["keys"])
                                        for p in products if p["tracked"] >= 2], dates)
    index_line = (dates, index_series(synth, [(game, pid) for pid in pool], dates, min_n=5)) if dates else None

    calls, picks = forecast_bits(pred, game, cat, pids=set(pool), min_pick=MIN_BASE)
    sales = sales_volume(pc, game, cat, pool, today)
    tracked_old = sum(m[2] for m in moves)
    tracked_new = sum(m[3] for m in moves)
    return {
        "game": game, "label": label, "anchor": anchor, "week": {"start": baseline, "end": latest},
        "dates": dates, "product_lines": product_lines, "index_line": index_line,
        "cards_priced": len(pool), "pool_value": round(sum(c["price"] for c in pool.values()), 2),
        "tracked_value_change_pct": r1((tracked_new / tracked_old - 1) * 100) if tracked_old else None,
        "stats": stats, "chase": chase, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "products": products, "ladder": ladder([c["price"] for c in pool.values()]), "sales": sales,
        "calls": calls, "picks": picks, "record": record_for(pred, game),
        "about": about_lines(attrs, [c[0] for c in chase] + [m[0] for m in ups[:MOVERS_N * 2] + downs[:MOVERS_N * 2]]
                             + [p[0] for p in picks]),
    }


RECENT_SECTIONS = [
    ("chase", "the most valuable of these newer cards, with each one's price, the release it came from and its move this week"),
    ("movers", "this week's biggest risers and fallers among them"),
    ("sales", "how many Near Mint copies of these cards actually sold in the last complete week against the week before, and which sold most"),
    ("products", "the releases they come from (promos, decks, side products, the set before the newest): how many priced cards each has, what they cost and how each moved this week (the chart draws each release's price index day by day)"),
    ("ladder", "how these cards spread across price bands"),
    ("outlook", "what the model's one-month calls say about them, and the model's own record on this game"),
]


def build_recent_article(d, today, no_prose=False):
    game, label, anchor = d["game"], d["label"], d["anchor"]
    st = d["stats"]
    ab = d.get("about", {})
    vol = d.get("sales")
    vc = vol["per_card"] if vol else {}
    sections = [s for s in RECENT_SECTIONS
                if not ((s[0] == "movers" and st["tracked"] == 0)
                        or (s[0] == "sales" and not (d["sales"] and d["sales"]["enough"])))]
    n_prod = len(d["products"])
    facts = {
        "game": label, "newest_set": anchor,
        "scope": (f"every priced {label} card released in the last {RECENT_DAYS} days that is NOT in "
                  f"{anchor}, the newest set (which has its own article): promos, starter decks, side "
                  f"products and the set before it; cards under {SET_MIN_AGE_DAYS} days old are left out"),
        "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "releases": n_prod, "value_usd": d["pool_value"],
                   "tracked_value_change_pct": d["tracked_value_change_pct"], **st},
        "chase": [{"name": n, "price_usd": round(p, 2), "release": s, "week_pct": r1(w), "about": ab.get(_pid),
                   "copies_sold_last_week": round(vc[_pid][0]) if _pid in vc else None}
                  for _pid, n, p, s, w in d["chase"][:CHASE_N]],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                           "about": ab.get(m[0]),
                           "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                             "about": ab.get(m[0]),
                             "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None}
                            for m in d["downs"][:MOVERS_N]]},
        "releases": d["products"][:10],
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp), "about": ab.get(_pid),
                                  "copies_sold_last_week": round(vc[_pid][0]) if _pid in vc else None}
                                 for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
    }
    if vol and vol["enough"]:
        facts["sales"] = sales_facts(vol, "cards")
    if st["tracked"] == 0:
        facts["note"] = ("None of these cards has a week of market history yet, so there are no week "
                         "moves; write about the chase list, the releases, the ladder and the outlook.")
    prose = {} if no_prose else (write_recent_article(prune(facts), sections) or {})

    # ---- fallbacks ------------------------------------------------------
    short_anchor = short_label(anchor, 30)
    if st["tracked"]:
        lede = (f"Outside {anchor}, {label}'s other recent releases come to {d['cards_priced']} priced "
                f"cards from {n_prod} products, and {st['rose']} of the {st['tracked']} tracked ones rose "
                f"this week while {st['fell']} fell, a median move of {st['median_move_pct']:+.1f} percent.")
        title = (f"Beyond {short_anchor}: {d['cards_priced']} newer {label} cards, "
                 f"{st['rose']} up and {st['fell']} down")
    else:
        lede = (f"Outside {anchor}, {label} has {d['cards_priced']} priced cards from {n_prod} other recent "
                f"releases, worth {money(d['pool_value'])} together and too new for a week of market "
                "history yet.")
        title = f"Beyond {short_anchor}: {d['cards_priced']} other new {label} cards"
    lede = prose.get("lede") or lede
    title = prose.get("headline") or title

    body = [f"<p class='report-lede'>{esc(lede)}{esc(window_line(d['week']))}</p>",
            f"<p class='report-note'>This article covers every priced {esc(label)} card released in the "
            f"last {RECENT_DAYS} days that is not in {esc(anchor)}, which has "
            f"<a href='/articles?game={game}'>its own article</a>: promos, decks, side products and "
            f"the set before it. Cards under a week old wait for their first week of prices. “Tracked” "
            "cards are those with a market price on both ends of the week <em>and</em> worth at least "
            "$5 at the start.</p>"]

    # Chase cards
    tiles = []
    for pid, name, price, product, w in d["chase"]:
        if len(tiles) == CHASE_N:
            break
        if not has_art(game, pid):
            continue
        meta = (money(price)
                + (f" · <span class='{'report-up' if w > 0 else 'report-down'}'>{pct(w)}</span>"
                   if w is not None and abs(w) > 0.5 else "")
                + f" · {esc(short_label(product, 22))}" + sold_tag(vc, pid))
        tiles.append(card_tile(game, pid, name, meta))
    top = d["chase"][0] if d["chase"] else None
    body.append(section(
        prose, "chase", "The Chase List",
        (f"The most valuable of them is {top[1]} from {top[3]} at {money(top[2])}." if top else
         "No priced cards yet."),
        grid(tiles), f"The priciest {label} cards outside {short_anchor}."))

    # Movers
    if st["tracked"]:
        tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}")
                 for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
        tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}")
                  for m in d["downs"] if has_art(game, m[0])][:MOVERS_N]
        bt = d["ups"][0] if d["ups"] else None
        wt = d["downs"][0] if d["downs"] else None
        txt = " ".join(filter(None, [
            f"{bt[1]} led them, up {pct(bt[4])} to {money(bt[3])}." if bt else None,
            f"{wt[1]} fell furthest, {pct(wt[4])} to {money(wt[3])}." if wt else None])) \
            or "None of them moved more than half a percent this week."
        body.append(section(prose, "movers", "This Week's Movers", txt, grid(tiles),
                            "Risers on the left, fallers on the right."))

    # What actually sold (2026-11-07)
    if vol and vol["enough"]:
        body.append(sales_block(prose, vol, game, label, "cards"))

    # Releases
    rows = [(short_label(p["product"], 28), p["median_move_pct"],
             f"{p['tracked']} cards · median {money(p['median_price_usd'])}")
            for p in d["products"] if p["median_move_pct"] is not None and p["tracked"] >= 2]
    rows.sort(key=lambda r: -r[1])
    chart = lines_chart(d["dates"], d.get("product_lines", []), f"{label}: price index this week, by release")
    table = ["<table class='report-table'><thead><tr><th>Release</th><th>Out since</th><th>Priced</th>"
             "<th>Median price</th><th>Tracked</th><th>Median move</th></tr></thead><tbody>"]
    for p in d["products"][:12]:
        link = (f"<a href='/catalog?game={game}&amp;sets={quote(p['product'], safe='')}'>{esc(p['product'])}</a>"
                if "," not in p["product"] else esc(p["product"]))
        table.append(f"<tr><td>{link}</td><td>{esc(p['released'])}</td><td>{p['cards_priced']}</td>"
                     f"<td>{money(p['median_price_usd'])}</td><td>{p['tracked']}</td>"
                     f"<td>{pct(p['median_move_pct']) if p['median_move_pct'] is not None else '–'}</td></tr>")
    table.append("</tbody></table>")
    biggest = d["products"][0] if d["products"] else None
    pricey = max(d["products"], key=lambda p: p["median_price_usd"]) if d["products"] else None
    txt = " ".join(filter(None, [
        f"{biggest['product']} is the biggest of them with {biggest['cards_priced']} priced cards." if biggest else None,
        (f"{pricey['product']} is the priciest at a median {money(pricey['median_price_usd'])}."
         if pricey and pricey is not biggest else None)])) or "No releases to show."
    body.append(section(prose, "products", "Where They Came From", txt, chart + "".join(table),
                        "Each release's cards averaged, day by day through the week; the table ranks them."))

    # Ladder
    chart = band_line(d["ladder"], f"{label}: newer cards outside {short_label(anchor, 24)} by price band")
    over5 = sum(n for b, n in d["ladder"][2:])
    body.append(section(
        prose, "ladder", "Where the Money Sits",
        f"{over5} of these {d['cards_priced']} priced cards are worth $5 or more.",
        chart, "Every one of them, sorted into bands."))

    # Outlook (model corner) + closer
    c = d["calls"]
    rec = d["record"]
    linkable = [p["product"] for p in d["products"] if "," not in p["product"]][:4]
    links = ((f"<a href='/catalog?game={game}&amp;sets={','.join(quote(p, safe='') for p in linkable)}'>"
              f"Browse these releases in the catalog</a>; " if linkable else "")
             + "the About page explains how the forecasts are made.")
    txt = (f"Of these cards with a one-month call, the model has {c['up']} headed up, "
           f"{c['down']} headed down and {c['flat']} flat.")
    if rec:
        txt += (f" On {label} its one-month forecasts have missed by {rec['typical_miss_pct']} percent "
                f"on average, against {rec['flat_baseline_miss_pct']} percent for assuming no change.")
    body.append(model_corner(prose, "outlook", "What the Model Thinks", txt, d["picks"], game, rec, links))
    closer = prose.get("closer")
    if closer:
        body.append(f"<p>{esc(closer)}</p>")

    # Hero: the chase cards fanned, the pool's week as one line.
    stats = [[f"{d['cards_priced']:,}", "priced cards", None], [f"{n_prod}", "releases", None],
             [money(d["pool_value"]), "together", None], [f"{over5:,}", "worth $5+", None]]
    if st["tracked"]:
        stats = [[f"{st['rose']:,}", "cards rose", "up"], [f"{st['fell']:,}", "cards fell", "down"],
                 [f"{st['median_move_pct']:+.1f}%", "median move", None],
                 [f"{n_prod}", "releases", None]]
    cards = [[game, pid, name] for pid, name, *_ in d["chase"] if has_art(game, pid)][:3]
    hero, thumb = hero_and_thumb("set", game, title, today, stats, cards, d.get("index_line"), focus="recent")
    return {"slug": f"new-releases-{game}-{today.isoformat()}", "kind": "set", "focus": "recent",
            "game": game, "set_name": None, "title": title, "summary": lede,
            "body_html": "".join(body), "thumb": thumb, "hero": hero}


# ---------------------------------------------------------------------------
# GAME article
# ---------------------------------------------------------------------------
def collect_game(pc, pred, game, cat, synth, window, young, market_index, newest, attrs=None, today=None):
    label = GAMES[game]["label"]
    today = today or datetime.now(timezone.utc).date()
    baseline, latest, dates = window
    names = {pid: c["name"] for pid, c in cat.items()}
    moves = game_moves(synth, game, baseline, latest, names, young) if baseline else []
    stats = move_stats(moves)
    series = index_series(synth, [(game, pid) for pid in cat], dates) if dates else []
    drift = next((v for v in reversed(series) if v is not None), None)

    base_moves = [m for m in moves if not m[5]]
    ups = sorted((m for m in base_moves if m[4] > 0.5), key=lambda m: -m[4])
    downs = sorted((m for m in base_moves if m[4] < -0.5), key=lambda m: m[4])

    by_set = collections.defaultdict(list)
    for m in base_moves:
        s = cat[m[0]]["set"]
        if s:
            by_set[s].append(m[4])
    sets = []
    for s, ms in by_set.items():
        if len(ms) >= MIN_SET_TRACKED:
            med = statistics.median(ms)
            sets.append({"set": s, "tracked": len(ms), "rose": sum(1 for x in ms if x > 0.5),
                         "fell": sum(1 for x in ms if x < -0.5), "median_move_pct": r1(med)})
    sets.sort(key=lambda x: -abs(x["median_move_pct"]))
    # Day-by-day lines through the window (2026-11-08): the sets that moved
    # most, and each group that moved together.
    set_lines = group_lines(synth, [(s["set"], [(game, pid) for pid, c in cat.items() if c["set"] == s["set"]])
                                    for s in sets[:LINES_MAX]], dates, min_n=3)

    if attrs is None:
        attrs = card_attrs(game)
    trends = pick_trends(find_trends(game, moves, attrs)) if moves else []
    trend_lines = group_lines(synth, [
        (str(t["value"]), [((game, m[0], m[5]) if len(m) > 5 and m[5] else (game, m[0])) for m in t["members"]])
        for t in trends], dates, min_n=3)
    calls, picks = forecast_bits(pred, game, cat)
    discussed = [m[0] for m in ups[:MOVERS_N * 2] + downs[:MOVERS_N * 2]] + [p[0] for p in picks]
    sales = sales_volume(pc, game, cat, discussed, today)
    return {
        "game": game, "label": label, "week": {"start": baseline, "end": latest},
        "dates": dates, "series": series, "market_index": market_index, "drift": r1(drift),
        "cards_priced": len(cat), "catalog_value": round(sum(c["price"] for c in cat.values()), 2),
        "stats": stats, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "sets": sets, "trends": trends, "ladder": ladder([c["price"] for c in cat.values()]),
        "set_lines": set_lines, "trend_lines": trend_lines,
        "calls": calls, "picks": picks, "record": record_for(pred, game), "newest": newest, "sales": sales,
        "about": about_lines(attrs, [m[0] for m in ups[:MOVERS_N * 2] + downs[:MOVERS_N * 2]] + [p[0] for p in picks]),
    }


GAME_SECTIONS = [
    ("week", "how the whole game's week went: its drift against the first day of the window (and the all-games line beside it), how many cards rose and fell, the average moves"),
    ("sets", "which sets moved, ranked by median move (the chart draws each set's price index day by day)"),
    ("movers", "the game's biggest individual risers and fallers of the week"),
    ("trends", "the groups of cards that moved together this week: a set, a rarity, a character, a keyword"),
    ("ladder", "how the game's priced cards spread across price bands"),
    ("model", "how the model has been doing on this game and the climbs it is most confident in"),
]


def build_game_article(d, today, no_prose=False):
    game, label, st = d["game"], d["label"], d["stats"]
    ab = d.get("about", {})
    vol = d.get("sales")
    vc = vol["per_card"] if vol else {}
    sections = [s for s in GAME_SECTIONS if not (s[0] == "trends" and not d["trends"])]
    mk = next((v for v in reversed(d["market_index"]) if v is not None), None) if d["market_index"] else None
    facts = {
        "game": label, "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "catalog_value_usd": d["catalog_value"],
                   "week_drift_pct": d["drift"], "all_games_drift_pct": r1(mk), **st},
        "sets": d["sets"][:8],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                           "about": ab.get(m[0]),
                           "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4]),
                             "about": ab.get(m[0]),
                             "copies_sold_last_week": round(vc[m[0]][0]) if m[0] in vc else None}
                            for m in d["downs"][:MOVERS_N]]},
        "trends": [{"group": t["value"], "dimension": DIM_LABELS[t["dim"]].lower(),
                    "moved": t["same"], "of_tracked": t["n"], "median_pct": r1(t["median"]),
                    "examples": [{"name": m[1], "pct": r1(m[4])}
                                 for m in sorted(t["members"], key=lambda m: -abs(m[4]))[:2]]}
                   for t in d["trends"]],
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp), "about": ab.get(_pid),
                                  "copies_sold_last_week": round(vc[_pid][0]) if _pid in vc else None}
                                 for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
        "newest_set": d["newest"],
    }
    if vol and vol["covered"]:
        facts["sales"] = sales_facts(vol, "movers and picks named here")
        facts["sales"]["meaning"] += "; the game article has them for the cards it discusses only, not the whole game"
    prose = {} if no_prose else (write_game_article(prune(facts), sections) or {})

    lede = (f"{label} had {st['rose']:,} of {st['tracked']:,} tracked cards rise this week and "
            f"{st['fell']:,} fall, climbers averaging {st['avg_gain_pct']:+.1f} percent and fallers "
            f"{st['avg_loss_pct']:.1f} percent, for a drift of {d['drift'] if d['drift'] is not None else 0:+.1f} percent.")
    title = f"{label} this week: {st['rose']:,} cards up, {st['fell']:,} down"
    lede = prose.get("lede") or lede
    title = prose.get("headline") or title

    body = [f"<p class='report-lede'>{esc(lede)}{esc(window_line(d['week']))}</p>",
            "<p class='report-note'>“Tracked” cards are those with a market price on "
            "both ends of the week <em>and</em> worth at least $5 at the start; cheaper cards "
            "are left out because a few cents of movement reads as a large, meaningless "
            "percentage.</p>"]

    # The week
    chart = ""
    if d["dates"] and any(v is not None for v in d["series"]):
        series = [(label, d["series"], "#ffcb05")]
        if d["market_index"] and any(v is not None for v in d["market_index"]):
            series.append(("All games", d["market_index"], "#8b96ad"))
        chart = line_chart(d["dates"], series, title=f"{label}: price drift this week")
    body.append(section(
        prose, "week", "How the Week Went",
        f"Across {st['tracked']:,} tracked cards, {st['rose']:,} rose and {st['fell']:,} fell.",
        chart, f"{label} against every game we track, day by day."))

    # Sets
    rows = [(s["set"], s["median_move_pct"], f"{s['tracked']} cards") for s in d["sets"][:8]]
    rows.sort(key=lambda r: -r[1])
    chart = lines_chart(d["dates"], d.get("set_lines", []), f"{label}: price index this week, by set")
    table = ["<table class='report-table'><thead><tr><th>Set</th><th>Tracked</th><th>Up</th>"
             "<th>Down</th><th>Median move</th></tr></thead><tbody>"]
    for s in d["sets"][:8]:
        link = (f"<a href='/catalog?game={game}&amp;sets={quote(s['set'], safe='')}'>{esc(s['set'])}</a>"
                if "," not in s["set"] else esc(s["set"]))
        table.append(f"<tr><td>{link}</td><td>{s['tracked']}</td><td>{s['rose']}</td>"
                     f"<td>{s['fell']}</td><td>{pct(s['median_move_pct'])}</td></tr>")
    table.append("</tbody></table>")
    lead = d["sets"][0] if d["sets"] else None
    body.append(section(
        prose, "sets", "Which Sets Moved",
        (f"{lead['set']} moved most, a median {pct(lead['median_move_pct'])} across {lead['tracked']} tracked cards."
         if lead else "No set had enough tracked cards to call this week."),
        chart + ("".join(table) if d["sets"] else ""),
        "The sets that moved most, each one's cards averaged day by day; the table ranks them by median move."))

    # Movers
    tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}")
             for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
    tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}{sold_tag(vc, m[0])}")
              for m in d["downs"] if has_art(game, m[0])][:MOVERS_N]
    bt = d["ups"][0] if d["ups"] else None
    wt = d["downs"][0] if d["downs"] else None
    txt = " ".join(filter(None, [
        f"{bt[1]} led the game, up {pct(bt[4])} to {money(bt[3])}." if bt else None,
        f"{wt[1]} fell furthest, {pct(wt[4])} to {money(wt[3])}." if wt else None])) \
        or "Nothing moved more than half a percent this week."
    body.append(section(prose, "movers", "The Week's Movers", txt, grid(tiles),
                        "Risers on the left, fallers on the right."))

    # Trends
    if d["trends"]:
        lines = []
        for t in sorted(d["trends"], key=lambda t: (not t["rising"], -abs(t["median"]))):
            marker = ("<span class='report-up'>▲</span>" if t["rising"]
                      else "<span class='report-down'>▼</span>")
            lines.append(f"<p class='report-trendline'>{marker} {trend_phrase(t)}</p>")
        chart = lines_chart(d["dates"], d.get("trend_lines", []), f"{label}: price index this week, by group")
        t0 = d["trends"][0]
        body.append(section(
            prose, "trends", "What Moved Together",
            f"{t0['value']} ({DIM_LABELS[t0['dim']].lower()}) moved as a group: {t0['same']} of "
            f"{t0['n']} tracked cards, median {pct(t0['median'])}.",
            "".join(lines) + chart, "Groups that moved as one, each group's cards averaged day by day."))

    # Ladder
    chart = band_line(d["ladder"], f"{label}: priced cards by price band")
    over5 = sum(n for b, n in d["ladder"][2:])
    body.append(section(
        prose, "ladder", "Where the Money Sits",
        f"{over5:,} of {label}'s {d['cards_priced']:,} priced cards are worth $5 or more; "
        f"the whole priced catalog adds up to {money(d['catalog_value'])}.",
        chart, f"Every priced {label} card, sorted into bands."))

    # Model
    c, rec = d["calls"], d["record"]
    txt = (f"Of {label}'s cards with a one-month call, the model has {c['up']:,} headed up, "
           f"{c['down']:,} headed down and {c['flat']:,} flat.")
    if rec:
        txt += (f" Its one-month forecasts on {label} have missed by {rec['typical_miss_pct']} percent "
                f"on average, against {rec['flat_baseline_miss_pct']} percent for assuming no change"
                + (f", and called the direction right {rec['direction_pct']:.0f} percent of the time."
                   if rec["direction_pct"] is not None else "."))
    links = (f"How the forecasts have been doing across every game is in the latest "
             f"<a href='/reports'>model report</a>; the About page explains how they are made.")
    if d["newest"]:
        links = (f"The newest set, {esc(d['newest'])}, has <a href='/articles?game={game}'>its own report</a>. "
                 + links)
    body.append(model_corner(prose, "model", "The Model's Corner", txt, d["picks"], game, rec, links))
    closer = prose.get("closer")
    if closer:
        body.append(f"<p>{esc(closer)}</p>")

    stats = [[f"{st['rose']:,}", "cards rose", "up"], [f"{st['fell']:,}", "cards fell", "down"],
             [f"{d['drift']:+.1f}%" if d["drift"] is not None else "0.0%", "week drift", None],
             [f"{st['tracked']:,}", "tracked", None]]
    cards = [[game, m[0], m[1]] for m in (d["ups"][:2] + d["downs"][:2]) if has_art(game, m[0])][:3]
    hero, thumb = hero_and_thumb("game", game, title, today, stats, cards,
                                 (d["dates"], d["series"]) if d["dates"] else None)
    return {"slug": f"game-report-{game}-{today.isoformat()}", "kind": "game", "game": game,
            "set_name": None, "title": title, "summary": lede,
            "body_html": "".join(body), "thumb": thumb, "hero": hero}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def ensure_table(pred):
    pred.execute("""CREATE TABLE IF NOT EXISTS articles (
        slug TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        game TEXT NOT NULL,
        set_name TEXT,
        title TEXT NOT NULL,
        published_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        body_html TEXT NOT NULL,
        thumb TEXT,
        hero TEXT,
        focus TEXT)""")
    # focus (2026-11-07): a set article is about the newest set ('set') or the
    # game's other recent releases ('recent'); rows from before are NULL = set.
    if "focus" not in {r[1] for r in pred.execute("PRAGMA table_info(articles)")}:
        pred.execute("ALTER TABLE articles ADD COLUMN focus TEXT")
    pred.execute("CREATE INDEX IF NOT EXISTS articles_game_date ON articles (game, published_at)")


def store(pred, a, today):
    pred.execute(
        "INSERT OR REPLACE INTO articles (slug, kind, game, set_name, title, published_at, "
        "summary, body_html, thumb, hero, focus) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (a["slug"], a["kind"], a["game"], a["set_name"], a["title"], today.isoformat(),
         a["summary"], a["body_html"], a["thumb"], a["hero"], a.get("focus")))


# ---------------------------------------------------------------------------
# Tomorrow's cards, for the daytime sales crawl (2026-11-07)
# ---------------------------------------------------------------------------
def write_watch(today, last, written, pc, pred, cats, synth, window, young, attrs_by_game):
    """Plan tomorrow's slots as they will stand after tonight's writes and
    list every card those articles would discuss (chase list, movers, model
    picks), so backfill_sales_volume.py can fetch their sales first during
    the day and the articles find final, fresh buckets. Tomorrow's movers
    are approximated from tonight's window; the chase list and the picks
    are the same cards either way."""
    tomorrow = today + timedelta(days=1)
    last2 = dict(last)
    for g, k, f in written:
        last2[(g, k)] = (today.isoformat(), f or "set")
    slots, _waiting = plan(tomorrow, last2)
    cards, seen = [], set()
    for game, kind in slots:
        cat = cats.get(game) or {}
        if not cat:
            continue
        try:
            anchor, _age, skipped = pick_set(pc, game, cat, tomorrow)
            attrs = attrs_by_game.get(game)
            if kind == "set":
                if not anchor:
                    continue
                prev = last2.get((game, "set"))
                d = None
                if prev and prev[1] == "set":
                    released = released_on(pc, game, cat)
                    pool = recent_pool(released, cat, anchor, tomorrow)
                    if len(pool) >= RECENT_MIN_CARDS:
                        d = collect_recent(pc, pred, game, cat, anchor, pool, released, synth, window, young,
                                           tomorrow, attrs=attrs)
                if d is None:
                    d = collect_set(pc, pred, game, cat, anchor, synth, window, young, tomorrow, skipped, attrs=attrs)
                pids = [c[0] for c in d["chase"]] + [m[0] for m in d["ups"] + d["downs"]] + [p[0] for p in d["picks"]]
            else:
                d = collect_game(pc, pred, game, cat, synth, window, young, [], anchor, attrs=attrs, today=tomorrow)
                pids = [m[0] for m in d["ups"] + d["downs"]] + [p[0] for p in d["picks"]]
        except Exception as e:          # the watch list is a courtesy, never fatal
            print(f"watch: {game}/{kind} skipped ({type(e).__name__}: {e})")
            continue
        for pid in pids:
            if (game, pid) not in seen:
                seen.add((game, pid))
                cards.append({"game": game, "pid": pid, "slot": f"{game}/{kind}"})
    os.makedirs(os.path.dirname(WATCH_FILE), exist_ok=True)
    with open(WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump({"for": tomorrow.isoformat(), "made": datetime.now(timezone.utc).isoformat(),
                   "slots": [f"{g}/{k}" for g, k in slots], "cards": cards}, f)
    print(f"watch list for {tomorrow}: {len(cards)} card(s) across {len(slots)} slot(s) -> {WATCH_FILE}")


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------
def last_articles(pred):
    """{(game, kind): (published_at, focus)} for each slot's newest article;
    a NULL focus (rows from before 2026-11-07) is the set focus."""
    rows = pred.execute("SELECT game, kind, published_at, focus FROM articles "
                        "ORDER BY published_at, slug").fetchall()
    return {(g, k): (p[:10], f or "set") for g, k, p, f in rows}


def plan(today, last, games=None):
    """Tonight's (game, kind) slots. Due slots (never written, or
    FORCE_GAP_DAYS since the last one) go first, the longest wait first; then
    the coin tosses, each seeded by date|game|kind so a re-run the same day
    plans the same night; cut to MAX_PER_NIGHT. Nothing inside MIN_GAP_DAYS
    of its last article, which includes a slot already written today.
    Returns (picks, waiting): waiting counts the slots that wanted a turn
    tonight and did not get one (they ask again tomorrow). A game in
    PAUSED_GAMES sits out entirely until its date."""
    due, drawn = [], []
    for game in games or GAMES:
        paused = PAUSED_GAMES.get(game)
        if paused and today < date.fromisoformat(paused):
            continue
        for kind in ("set", "game"):
            prev = last.get((game, kind))
            gap = (today - date.fromisoformat(prev[0])).days if prev else None
            if gap is not None and gap < MIN_GAP_DAYS:
                continue
            if gap is None or gap >= FORCE_GAP_DAYS:
                due.append((gap if gap is not None else 10 ** 6, game, kind))
            elif random.Random(f"{today}|{game}|{kind}").random() < DAILY_ODDS:
                drawn.append((gap, game, kind))
    rnd = random.Random(f"{today}|order")
    rnd.shuffle(due)
    due.sort(key=lambda t: -t[0])          # stable: equal waits keep the shuffled order
    rnd.shuffle(drawn)
    picks = due + drawn
    return [(g, k) for _gap, g, k in picks[:MAX_PER_NIGHT]], max(0, len(picks) - MAX_PER_NIGHT)


def main():
    ap = argparse.ArgumentParser(description="Per-game articles")
    ap.add_argument("--game", action="append", help="game key (repeatable): write it now, whatever the plan says")
    ap.add_argument("--kind", choices=["set", "game"], help="only this kind")
    ap.add_argument("--focus", choices=["set", "recent"],
                    help="set articles: this focus (default: the other one from the game's last set article)")
    ap.add_argument("--all", action="store_true", help="every game, both kinds, right now")
    ap.add_argument("--plan", action="store_true", help="print tonight's plan and exit; nothing is written")
    ap.add_argument("--date", help="publish date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--no-prose", action="store_true", help="templated text, no Claude call")
    ap.add_argument("--pred-db", default=PRED_DB, help=argparse.SUPPRESS)
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    kinds = [args.kind] if args.kind else ["set", "game"]
    pred = sqlite3.connect(args.pred_db)
    pred.execute("PRAGMA busy_timeout=30000")
    ensure_table(pred)
    last = last_articles(pred)
    waiting = 0
    if args.game:
        slots = [(g, k) for g in args.game if g in GAMES for k in kinds]
    elif args.all:
        slots = [(g, k) for g in GAMES for k in kinds]
    else:
        slots, waiting = plan(today, last)
        slots = [(g, k) for g, k in slots if k in kinds]
    print(f"game_articles plan for {today}: " + (", ".join(f"{g}/{k}" for g, k in slots) or "nothing due")
          + (f"; {waiting} more slot(s) wait for another night" if waiting else ""))
    for g, k in slots:
        prev = last.get((g, k))
        print(f"  {g}/{k}: last {prev[0]} ({prev[1]})" if prev else f"  {g}/{k}: never written")
    if args.plan or not slots:
        pred.close()
        return

    pc = sqlite3.connect(PC_DB)
    pc.execute("PRAGMA busy_timeout=30000")
    window = week_window(pc, end=today.isoformat() if args.date else None)
    baseline, latest, dates = window
    synth = synth_series(pc, dates) if dates else {}
    young = young_series(pc) if dates else frozenset()
    if not baseline:
        print("game_articles: no weekly window yet; articles will carry no week moves.")

    # Every game's catalog, so the all-games drift line the game articles are
    # drawn against really is all games, not just tonight's.
    cats = {g: catalog(g) for g in GAMES}
    market_index = index_series(synth, [(g, pid) for g, cat in cats.items() for pid in cat], dates) \
        if dates else []
    anchors, attrs_by_game = {}, {}
    written, written_slots = [], []
    for game, kind in slots:
        cat = cats.get(game) or {}
        if not cat:
            print(f"{game}: no priced cards; skipped")
            continue
        if game not in anchors:
            anchors[game] = pick_set(pc, game, cat, today)
            attrs_by_game[game] = card_attrs(game)
        anchor, _age, skipped = anchors[game]
        attrs = attrs_by_game[game]
        if kind == "set":
            if not anchor:
                print(f"{game}: no set with {LATEST_SET_MIN_CARDS}+ priced cards and "
                      f"{SET_MIN_AGE_DAYS}+ days of age; set article skipped")
                continue
            prev = last.get((game, "set"))
            focus = args.focus or ("recent" if prev and prev[1] == "set" else "set")
            a = None
            if focus == "recent":
                released = released_on(pc, game, cat)
                pool = recent_pool(released, cat, anchor, today)
                if len(pool) >= RECENT_MIN_CARDS:
                    d = collect_recent(pc, pred, game, cat, anchor, pool, released, synth, window, young, today,
                                       attrs=attrs)
                    a = build_recent_article(d, today, args.no_prose)
                else:
                    print(f"{game}: {len(pool)} priced cards released in the last {RECENT_DAYS} days "
                          f"outside {anchor}, under {RECENT_MIN_CARDS}; the set focus runs instead")
            if a is None:
                d = collect_set(pc, pred, game, cat, anchor, synth, window, young, today, skipped, attrs=attrs)
                a = build_set_article(d, today, args.no_prose)
        else:
            d = collect_game(pc, pred, game, cat, synth, window, young, market_index, anchor, attrs=attrs,
                             today=today)
            a = build_game_article(d, today, args.no_prose)
        store(pred, a, today)
        pred.commit()
        written.append(a["slug"])
        written_slots.append((game, kind, a.get("focus")))
        print(f"wrote {a['slug']}: {a['title']!r}")
    if not (args.game or args.all):
        write_watch(today, last, written_slots, pc, pred, cats, synth, window, young, attrs_by_game)
    pc.close()
    pred.close()
    print(f"game_articles: {len(written)} article(s) written for {today}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # never let an article take the nightly down
        print(f"game_articles: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
