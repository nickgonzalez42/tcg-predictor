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
from datetime import date, datetime, timedelta, timezone
from math import exp
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE                      # noqa: E402
from games import GAMES                                   # noqa: E402
from market_report import (                               # noqa: E402
    PC_DB, PRED_DB, MIN_BASE, MAX_WEEK_RATIO, YOUNG_DAYS, DIM_LABELS,
    week_window, synth_series, young_series, game_moves, card_attrs,
    find_trends, pick_trends, trend_phrase, live_accuracy,
    bar_chart, line_chart, esc, card_link, money, pct, short_label,
)
from article_editorial import write_set_article, write_game_article, write_recent_article   # noqa: E402

CDN = "https://d18pzythfo0bgy.cloudfront.net"
SITE = "https://cardstock.guide"

# Planner (2026-11-07): "Randomize the release of each article for each game
# but each type for each game should release at least once every two weeks."
# Expected gap about 9 days, about 2.7 articles a night at ~6.5 min of prose
# each; FORCE sits a day inside the two-week promise so a night where more
# than MAX_PER_NIGHT slots fall due can slip one by a day and still keep it.
MIN_GAP_DAYS = 5          # never the same slot twice inside a working week
FORCE_GAP_DAYS = 13       # due: written tonight, longest wait first
DAILY_ODDS = 0.22         # between the two: tonight's chance, seeded by date|game|kind
MAX_PER_NIGHT = 4         # the refresh must still end by 7 AM
SET_MIN_AGE_DAYS = 7      # "let the set at least be a week old"
RECENT_DAYS = 90          # the other focus: cards released inside this window ...
RECENT_MIN_CARDS = 8      # ... outside the set; fewer and the set focus runs instead

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
def catalog(game):
    """pid -> {name, set, rarity, price, release} for priced, named cards.
    Older per-game schemas (One Piece) have no release_date column."""
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


def section(prose, key, header, body_text, figure, cap):
    """<h2>, the paragraph, the picture or chart, its caption."""
    h = prose.get("headers", {}).get(key) or header
    p = prose.get("sections", {}).get(key) or body_text
    c = prose.get("captions", {}).get(key) or cap
    return (f"<h2>{esc(h)}</h2><p>{esc(p)}</p>{figure}{caption(c) if figure else ''}")


def model_corner(prose, key, header, body_text, picks, game, record, links):
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
    out.append(f"<p class='report-note'>Forecasts are model estimates, not financial advice. "
               f"{links}</p></div>")
    return "".join(out)


def hero_and_thumb(kind, game, title, today, stats, cards, spark, focus=None):
    from report_thumbnail import render_svg
    label = f"{GAMES[game]['label'].upper()} · {KIND_LABEL['recent' if focus == 'recent' else kind]}"
    pub = today.strftime("%B %-d, %Y")
    hero = render_svg(kind, title, pub, stats, cards, spark, label=label)
    thumb = {"kind": kind, "label": label, "stats": stats, "spark": spark,
             "cards": [list(c) for c in cards]}
    return hero, json.dumps(thumb)


def window_line(w):
    return f" Price window: {w['start']} to {w['end']}, ungraded market prices."


# ---------------------------------------------------------------------------
# SET article
# ---------------------------------------------------------------------------
def collect_set(pc, pred, game, cat, set_name, synth, window, young, today, skipped=()):
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
    # A schema without release dates (One Piece) dates the set by its first
    # broadly priced day, the same day its price index starts.
    release = set_release(cat, set_name) or (broad[0] if broad else None)
    age = (today - date.fromisoformat(release)).days if release else None

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

    # Price index since the set's first broadly priced day.
    history = None
    if len(broad) >= 3:
        first = broad[0]
        base = {pid: p for pid, p in by_date[first].items() if p >= 1.0}
        pts = []
        for d in broad:
            ratios = [by_date[d][pid] / b for pid, b in base.items() if pid in by_date[d]]
            ratios = [x for x in ratios if 1 / MAX_WEEK_RATIO <= x <= MAX_WEEK_RATIO]
            if len(ratios) >= max(3, len(base) // 2):
                pts.append((d, (statistics.mean(ratios) - 1) * 100))
        if len(pts) >= 3:
            if len(pts) > 40:          # keep the chart readable: ~40 points, ends kept
                step = len(pts) / 39
                pts = [pts[int(i * step)] for i in range(39)] + [pts[-1]]
            history = {"dates": [p[0] for p in pts], "index": [p[1] for p in pts]}

    calls, picks = forecast_bits(pred, game, cat, pids=set(members), min_pick=MIN_BASE)
    tracked_old = sum(m[2] for m in moves)
    tracked_new = sum(m[3] for m in moves)
    return {
        "game": game, "label": label, "set": set_name, "set_slug": slugify(set_name),
        "release": release, "age_days": age, "week": {"start": baseline, "end": latest},
        "cards_priced": len(members), "set_value": round(sum(c["price"] for c in members.values()), 2),
        "tracked_value_change_pct": r1((tracked_new / tracked_old - 1) * 100) if tracked_old else None,
        "stats": stats, "chase": chase, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "rarities": rarities, "history": history,
        "ladder": ladder([c["price"] for c in members.values()]),
        "calls": calls, "picks": picks, "record": record_for(pred, game),
        "skipped": list(skipped),
    }


SET_SECTIONS = [
    ("chase", "the set's most valuable cards (the chase list) with each one's price and its move this week"),
    ("movers", "this week's biggest risers and fallers inside the set"),
    ("rarity", "what each rarity costs and how it moved this week"),
    ("history", "the set's price index since its first week of prices: the average move of its cards from that first day"),
    ("ladder", "how the set's priced cards spread across price bands"),
    ("outlook", "what the model's one-month calls say about the set, and the model's own record on this game"),
]


def build_set_article(d, today, no_prose=False):
    game, label, set_name = d["game"], d["label"], d["set"]
    st = d["stats"]
    sections = [s for s in SET_SECTIONS
                if not ((s[0] in ("movers", "rarity") and st["tracked"] == 0)
                        or (s[0] == "history" and not d["history"]))]
    facts = {
        "game": label, "set": set_name, "released": d["release"], "age_days": d["age_days"],
        "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "set_value_usd": d["set_value"],
                   "tracked_value_change_pct": d["tracked_value_change_pct"], **st},
        "chase": [{"name": n, "price_usd": round(p, 2), "rarity": r, "week_pct": r1(w)}
                  for _pid, n, p, r, w in d["chase"][:CHASE_N]],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                            for m in d["downs"][:MOVERS_N]]},
        "rarities": d["rarities"],
        "since_release": ({"from": d["history"]["dates"][0], "to": d["history"]["dates"][-1],
                           "index_now_pct": r1(d["history"]["index"][-1]),
                           "peak_pct": r1(max(d["history"]["index"])),
                           "trough_pct": r1(min(d["history"]["index"]))} if d["history"] else None),
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp)} for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
    }
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

    # Chase cards
    tiles = []
    for pid, name, price, rarity, w in d["chase"]:
        if len(tiles) == CHASE_N:
            break
        if not has_art(game, pid):
            continue
        meta = money(price) + (f" · <span class='{'report-up' if w > 0 else 'report-down'}'>{pct(w)}</span>"
                               if w is not None and abs(w) > 0.5 else "")
        tiles.append(card_tile(game, pid, name, meta))
    top = d["chase"][0] if d["chase"] else None
    body.append(section(
        prose, "chase", "The Chase List",
        (f"The most valuable card in the set is {top[1]} at {money(top[2])}." if top else
         "The set has no priced cards yet."),
        grid(tiles), f"The top of {set_name}, by price."))

    # Movers
    if st["tracked"]:
        tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}")
                 for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
        tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}")
                  for m in d["downs"] if has_art(game, m[0])][:MOVERS_N]
        bt = d["ups"][0] if d["ups"] else None
        wt = d["downs"][0] if d["downs"] else None
        txt = " ".join(filter(None, [
            f"{bt[1]} led the set, up {pct(bt[4])} to {money(bt[3])}." if bt else None,
            f"{wt[1]} fell furthest, {pct(wt[4])} to {money(wt[3])}." if wt else None])) \
            or "Nothing in the set moved more than half a percent this week."
        body.append(section(prose, "movers", "This Week's Movers", txt, grid(tiles),
                            "Risers on the left, fallers on the right."))

        # Rarity
        rows = [(r["rarity"], r["median_move_pct"], f"{r['tracked']} cards · median {money(r['median_price_usd'])}")
                for r in d["rarities"] if r["median_move_pct"] is not None and r["tracked"] >= 2]
        rows.sort(key=lambda r: -r[1])
        chart = bar_chart(rows, title=f"{short_label(set_name, 36)}: median move by rarity") if rows else ""
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
            chart + "".join(table), "Median move this week, by rarity."))

    # Since release
    if d["history"]:
        h = d["history"]
        chart = line_chart(h["dates"], [(short_label(set_name, 24), h["index"], "#ffcb05")],
                           title=f"{short_label(set_name, 36)}: price index since {h['dates'][0]}")
        body.append(section(
            prose, "history", "The Road Since Release",
            f"Against its first broadly priced day, {h['dates'][0]}, the set's cards sit "
            f"{h['index'][-1]:+.1f} percent on average, with a peak of {max(h['index']):+.1f} percent "
            f"and a low of {min(h['index']):+.1f} percent.",
            chart, "The whole set, averaged, from its first priced day to now."))

    # Ladder
    chart = bar_chart([(b, n, None) for b, n in d["ladder"]], unit=" cards", signed=False,
                      color="var(--primary, #3d7dca)", decimals=0,
                      title=f"{short_label(set_name, 36)}: priced cards by price band")
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
    if rec:
        txt += (f" On {label} its one-month forecasts have missed by {rec['typical_miss_pct']} percent "
                f"on average, against {rec['flat_baseline_miss_pct']} percent for assuming no change.")
    body.append(model_corner(prose, "outlook", "What the Model Thinks", txt, d["picks"], game, rec, links))
    closer = prose.get("closer")
    if closer:
        body.append(f"<p>{esc(closer)}</p>")

    # Hero: the chase cards fanned, the rarities as the bar strip.
    stats = [[f"{d['cards_priced']:,}", "priced cards", None],
             [money(d["set_value"]), "set value", None]]
    if st["tracked"]:
        stats = [[f"{st['rose']:,}", "cards rose", "up"], [f"{st['fell']:,}", "cards fell", "down"],
                 [f"{st['median_move_pct']:+.1f}%", "median move", None],
                 [money(d["set_value"]), "set value", None]]
    spark = [[r["rarity"], r["median_move_pct"]] for r in d["rarities"]
             if r["median_move_pct"] is not None and r["tracked"] >= 2][:8]
    cards = [[game, pid, name] for pid, name, *_ in d["chase"] if has_art(game, pid)][:3]
    hero, thumb = hero_and_thumb("set", game, title, today, stats, cards, spark)
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


def collect_recent(pc, pred, game, cat, anchor, pool, released, synth, window, young, today):
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

    prod = collections.defaultdict(lambda: {"prices": [], "moves": [], "dates": collections.Counter()})
    for pid, c in pool.items():
        p = prod[c["set"] or "Other"]
        p["prices"].append(c["price"])
        p["dates"][released[pid]] += 1
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

    calls, picks = forecast_bits(pred, game, cat, pids=set(pool), min_pick=MIN_BASE)
    tracked_old = sum(m[2] for m in moves)
    tracked_new = sum(m[3] for m in moves)
    return {
        "game": game, "label": label, "anchor": anchor, "week": {"start": baseline, "end": latest},
        "cards_priced": len(pool), "pool_value": round(sum(c["price"] for c in pool.values()), 2),
        "tracked_value_change_pct": r1((tracked_new / tracked_old - 1) * 100) if tracked_old else None,
        "stats": stats, "chase": chase, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "products": products, "ladder": ladder([c["price"] for c in pool.values()]),
        "calls": calls, "picks": picks, "record": record_for(pred, game),
    }


RECENT_SECTIONS = [
    ("chase", "the most valuable of these newer cards, with each one's price, the release it came from and its move this week"),
    ("movers", "this week's biggest risers and fallers among them"),
    ("products", "the releases they come from (promos, decks, side products, the set before the newest): how many priced cards each has, what they cost and how each moved this week"),
    ("ladder", "how these cards spread across price bands"),
    ("outlook", "what the model's one-month calls say about them, and the model's own record on this game"),
]


def build_recent_article(d, today, no_prose=False):
    game, label, anchor = d["game"], d["label"], d["anchor"]
    st = d["stats"]
    sections = [s for s in RECENT_SECTIONS if not (s[0] == "movers" and st["tracked"] == 0)]
    n_prod = len(d["products"])
    facts = {
        "game": label, "newest_set": anchor,
        "scope": (f"every priced {label} card released in the last {RECENT_DAYS} days that is NOT in "
                  f"{anchor}, the newest set (which has its own article): promos, starter decks, side "
                  f"products and the set before it; cards under {SET_MIN_AGE_DAYS} days old are left out"),
        "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "releases": n_prod, "value_usd": d["pool_value"],
                   "tracked_value_change_pct": d["tracked_value_change_pct"], **st},
        "chase": [{"name": n, "price_usd": round(p, 2), "release": s, "week_pct": r1(w)}
                  for _pid, n, p, s, w in d["chase"][:CHASE_N]],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                            for m in d["downs"][:MOVERS_N]]},
        "releases": d["products"][:10],
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp)} for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
    }
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
                + f" · {esc(short_label(product, 22))}")
        tiles.append(card_tile(game, pid, name, meta))
    top = d["chase"][0] if d["chase"] else None
    body.append(section(
        prose, "chase", "The Chase List",
        (f"The most valuable of them is {top[1]} from {top[3]} at {money(top[2])}." if top else
         "No priced cards yet."),
        grid(tiles), f"The priciest {label} cards outside {short_anchor}."))

    # Movers
    if st["tracked"]:
        tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}")
                 for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
        tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}")
                  for m in d["downs"] if has_art(game, m[0])][:MOVERS_N]
        bt = d["ups"][0] if d["ups"] else None
        wt = d["downs"][0] if d["downs"] else None
        txt = " ".join(filter(None, [
            f"{bt[1]} led them, up {pct(bt[4])} to {money(bt[3])}." if bt else None,
            f"{wt[1]} fell furthest, {pct(wt[4])} to {money(wt[3])}." if wt else None])) \
            or "None of them moved more than half a percent this week."
        body.append(section(prose, "movers", "This Week's Movers", txt, grid(tiles),
                            "Risers on the left, fallers on the right."))

    # Releases
    rows = [(short_label(p["product"], 28), p["median_move_pct"],
             f"{p['tracked']} cards · median {money(p['median_price_usd'])}")
            for p in d["products"] if p["median_move_pct"] is not None and p["tracked"] >= 2]
    rows.sort(key=lambda r: -r[1])
    chart = bar_chart(rows, title=f"{label}: median move by release") if rows else ""
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
                        "Each release outside the newest set, by median move this week."))

    # Ladder
    chart = bar_chart([(b, n, None) for b, n in d["ladder"]], unit=" cards", signed=False,
                      color="var(--primary, #3d7dca)", decimals=0,
                      title=f"{label}: newer cards outside {short_label(anchor, 24)} by price band")
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

    # Hero: the chase cards fanned, the releases as the bar strip.
    stats = [[f"{d['cards_priced']:,}", "priced cards", None], [f"{n_prod}", "releases", None],
             [money(d["pool_value"]), "together", None], [f"{over5:,}", "worth $5+", None]]
    if st["tracked"]:
        stats = [[f"{st['rose']:,}", "cards rose", "up"], [f"{st['fell']:,}", "cards fell", "down"],
                 [f"{st['median_move_pct']:+.1f}%", "median move", None],
                 [f"{n_prod}", "releases", None]]
    spark = [[initials(p["product"]), p["median_move_pct"], "", p["product"]] for p in d["products"]
             if p["median_move_pct"] is not None and p["tracked"] >= 2][:8]
    cards = [[game, pid, name] for pid, name, *_ in d["chase"] if has_art(game, pid)][:3]
    hero, thumb = hero_and_thumb("set", game, title, today, stats, cards, spark, focus="recent")
    return {"slug": f"new-releases-{game}-{today.isoformat()}", "kind": "set", "focus": "recent",
            "game": game, "set_name": None, "title": title, "summary": lede,
            "body_html": "".join(body), "thumb": thumb, "hero": hero}


# ---------------------------------------------------------------------------
# GAME article
# ---------------------------------------------------------------------------
def collect_game(pc, pred, game, cat, synth, window, young, market_index, newest):
    label = GAMES[game]["label"]
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

    trends = pick_trends(find_trends(game, moves, card_attrs(game))) if moves else []
    calls, picks = forecast_bits(pred, game, cat)
    return {
        "game": game, "label": label, "week": {"start": baseline, "end": latest},
        "dates": dates, "series": series, "market_index": market_index, "drift": r1(drift),
        "cards_priced": len(cat), "catalog_value": round(sum(c["price"] for c in cat.values()), 2),
        "stats": stats, "ups": ups[:MOVERS_N * 2], "downs": downs[:MOVERS_N * 2],
        "sets": sets, "trends": trends, "ladder": ladder([c["price"] for c in cat.values()]),
        "calls": calls, "picks": picks, "record": record_for(pred, game), "newest": newest,
    }


GAME_SECTIONS = [
    ("week", "how the whole game's week went: its drift against the first day of the window (and the all-games line beside it), how many cards rose and fell, the average moves"),
    ("sets", "which sets moved, ranked by median move"),
    ("movers", "the game's biggest individual risers and fallers of the week"),
    ("trends", "the groups of cards that moved together this week: a set, a rarity, a character, a keyword"),
    ("ladder", "how the game's priced cards spread across price bands"),
    ("model", "how the model has been doing on this game and the climbs it is most confident in"),
]


def build_game_article(d, today, no_prose=False):
    game, label, st = d["game"], d["label"], d["stats"]
    sections = [s for s in GAME_SECTIONS if not (s[0] == "trends" and not d["trends"])]
    mk = next((v for v in reversed(d["market_index"]) if v is not None), None) if d["market_index"] else None
    facts = {
        "game": label, "week": d["week"],
        "totals": {"cards_priced": d["cards_priced"], "catalog_value_usd": d["catalog_value"],
                   "week_drift_pct": d["drift"], "all_games_drift_pct": r1(mk), **st},
        "sets": d["sets"][:8],
        "movers": {"up": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                          for m in d["ups"][:MOVERS_N]],
                   "down": [{"name": m[1], "from_usd": round(m[2], 2), "to_usd": round(m[3], 2), "pct": r1(m[4])}
                            for m in d["downs"][:MOVERS_N]]},
        "trends": [{"group": t["value"], "dimension": DIM_LABELS[t["dim"]].lower(),
                    "moved": t["same"], "of_tracked": t["n"], "median_pct": r1(t["median"]),
                    "examples": [{"name": m[1], "pct": r1(m[4])}
                                 for m in sorted(t["members"], key=lambda m: -abs(m[4]))[:2]]}
                   for t in d["trends"]],
        "ladder": [{"band": b, "cards": n} for b, n in d["ladder"]],
        "model": {"calls_1m": d["calls"],
                  "top_climbs": [{"name": n, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                  "implied_pct": r1(imp)} for _pid, n, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
        "newest_set": d["newest"],
    }
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
    chart = bar_chart(rows, title=f"{label}: median move by set (6+ tracked cards)") if rows else ""
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
        chart + ("".join(table) if d["sets"] else ""), "Sets ranked by how far their median card moved."))

    # Movers
    tiles = [card_tile(game, m[0], m[1], f"<span class='report-up'>{pct(m[4])}</span> · {money(m[3])}")
             for m in d["ups"] if has_art(game, m[0])][:MOVERS_N]
    tiles += [card_tile(game, m[0], m[1], f"<span class='report-down'>{pct(m[4])}</span> · {money(m[3])}")
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
        rows = [(t["value"], t["median"], f"{t['same']}/{t['n']} cards")
                for t in sorted(d["trends"], key=lambda t: -t["median"])]
        chart = bar_chart(rows, title=f"{label}: group moves this week (median %)")
        t0 = d["trends"][0]
        body.append(section(
            prose, "trends", "What Moved Together",
            f"{t0['value']} ({DIM_LABELS[t0['dim']].lower()}) moved as a group: {t0['same']} of "
            f"{t0['n']} tracked cards, median {pct(t0['median'])}.",
            "".join(lines) + chart, "Groups that moved as one, by median move."))

    # Ladder
    chart = bar_chart([(b, n, None) for b, n in d["ladder"]], unit=" cards", signed=False,
                      color="var(--primary, #3d7dca)", decimals=0,
                      title=f"{label}: priced cards by price band")
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
    spark = [[initials(s["set"]), s["median_move_pct"], "", s["set"]] for s in d["sets"][:10]]
    cards = [[game, m[0], m[1]] for m in (d["ups"][:2] + d["downs"][:2]) if has_art(game, m[0])][:3]
    hero, thumb = hero_and_thumb("game", game, title, today, stats, cards, spark)
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
    tonight and did not get one (they ask again tomorrow)."""
    due, drawn = [], []
    for game in games or GAMES:
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
    window = week_window(pc)
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
    anchors = {}
    written = []
    for game, kind in slots:
        cat = cats.get(game) or {}
        if not cat:
            print(f"{game}: no priced cards; skipped")
            continue
        if game not in anchors:
            anchors[game] = pick_set(pc, game, cat, today)
        anchor, _age, skipped = anchors[game]
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
                    d = collect_recent(pc, pred, game, cat, anchor, pool, released, synth, window, young, today)
                    a = build_recent_article(d, today, args.no_prose)
                else:
                    print(f"{game}: {len(pool)} priced cards released in the last {RECENT_DAYS} days "
                          f"outside {anchor}, under {RECENT_MIN_CARDS}; the set focus runs instead")
            if a is None:
                d = collect_set(pc, pred, game, cat, anchor, synth, window, young, today, skipped)
                a = build_set_article(d, today, args.no_prose)
        else:
            d = collect_game(pc, pred, game, cat, synth, window, young, market_index, anchor)
            a = build_game_article(d, today, args.no_prose)
        store(pred, a, today)
        pred.commit()
        written.append(a["slug"])
        print(f"wrote {a['slug']}: {a['title']!r}")
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
