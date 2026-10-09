"""
Weekly market report generator — runs inside the daily refresh but only
writes on Fridays (or with --force). Builds a "Weekly Market Report" post
from the last ~7 days of daily price snapshots (graded_price_history,
ungraded tier), plus a model corner from the live forecasts, and stores it
in a `reports` table in predictions.db — so reports ship to the server with
the normal data push and the API serves them read-only at /api/market-reports.

Run:  .venv/bin/python market_report.py            # no-op unless Friday
      .venv/bin/python market_report.py --force    # write this week's report now
"""

import argparse
import collections
import html
import json
import os
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from math import exp, log2
from urllib.parse import quote

from _paths import DATA_DIR as BASE
from games import GAMES
from report_editorial import write_editorial

API_DATA = os.path.normpath(os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards"))
PC_DB = os.path.join(API_DATA, "pricecharting.db")
PRED_DB = os.path.join(API_DATA, "predictions.db")

MIN_BASE = 5.0        # penny floor: % moves under this base price are noise
TOP_N = 3             # per-game gainers/losers
OVERALL_N = 5         # cross-game movers table
# Listing-repair guard: PriceCharting occasionally re-bases a listing (flat at
# one value, an overnight 5-10x step, flat at the new value — e.g. a promo
# whose cheap-variant pollution got corrected). That is a data regime change,
# not a market move, so week-over-week ratios beyond this bound (either
# direction) stay out of the report's stats, tables, and graphs entirely.
MAX_WEEK_RATIO = 5.0


def esc(s):
    return html.escape(str(s), quote=True)


def card_link(game, pid, name, printing=""):
    href = f"/catalog/{game}/{pid}"
    if printing:
        href += f"?printing={quote(printing, safe='')}"
    return f'<a href="{href}">{esc(name)}</a>'


# TCGplayer Near Mint became the ungraded source at the switch; before it had
# broad daily coverage there is only PriceCharting. The weekly window therefore
# runs over TCGplayer's broad-crawl days ONLY, so every week-over-week move is
# TCGplayer-vs-TCGplayer and none straddles the switch seam (a source change that
# would otherwise read as a market move). Until a full 7 days of NM history exist
# the window is simply shorter — 2 days on the first post-switch report.
MIN_TCG_DAILY = 10_000    # a date with >= this many NM points is a real crawl day,
                          # not the ~350-card weekly backfill series


def tcg_broad_dates(pc):
    """TCGplayer NM crawl days with broad coverage, oldest first."""
    try:
        return [r[0] for r in pc.execute(
            "SELECT date FROM tcg_nm_history WHERE printing='' "
            "GROUP BY date HAVING COUNT(*) >= ? "
            "ORDER BY date", (MIN_TCG_DAILY,))]
    except sqlite3.OperationalError:
        return []   # table appears after the first NM crawl


def synth_series(pc, dates):
    """(game, pid) -> {date: (price, source)} over `dates`: the synthesized daily
    ungraded price — TCGplayer Near Mint overriding PriceCharting loose for any
    day TCGplayer priced (mirrors build_unified_history's splice), PriceCharting
    filling only days TCGplayer never covered. The report reads its ungraded
    prices from here, so they match the site's ungraded tier."""
    if not dates:
        return {}
    q = ",".join("?" * len(dates))
    args = tuple(dates)
    out = collections.defaultdict(dict)
    for game, pid, d, p in pc.execute(
            f"SELECT game, product_id, date, price FROM graded_price_history "
            f"WHERE printing='' AND grade='ungraded' AND date IN ({q}) AND price > 0", args):
        out[(game, pid)][d] = (p, "pc")
    try:
        for game, pid, d, p in pc.execute(
                f"SELECT game, product_id, date, price FROM tcg_nm_history "
                f"WHERE printing='' AND date IN ({q}) AND price > 0", args):
            out[(game, pid)][d] = (p, "tcg")   # TCGplayer wins the day
    except sqlite3.OperationalError:
        pass
    # LABELED printings move too (2026-08-10): their series are weekly buckets,
    # so forward-fill the newest point at-or-before each window day (<=10d old)
    # onto that day. Entity key = (game, pid, printing).
    try:
        rows = pc.execute(
            "SELECT game, product_id, printing, date, price FROM tcg_nm_history "
            "WHERE printing != '' AND price > 0 AND date <= ? ORDER BY date",
            (dates[-1],)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    series = collections.defaultdict(list)
    for game, pid, pr, d, p in rows:
        series[(game, pid, pr)].append((d, p))
    for key, pts in series.items():
        for day in dates:
            best = None
            for d, p in pts:
                if d <= day:
                    best = (d, p)
                else:
                    break
            if best and (datetime.strptime(day, "%Y-%m-%d")
                         - datetime.strptime(best[0], "%Y-%m-%d")).days <= 10:
                out[key] = out.get(key, {})
                out[key][day] = (best[1], "tcg")
    return out


def week_window(pc):
    """(baseline, latest, days) over TCGplayer's broad daily-coverage dates — the
    last such day and the one nearest 7 days before it, plus every broad day
    between. TCGplayer-only, so the report's moves match the site and never cross
    the PriceCharting->TCGplayer seam."""
    dates = tcg_broad_dates(pc)
    if len(dates) < 2:
        return None, None, []
    latest = dates[-1]
    target = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    baseline = max((d for d in dates if d <= target), default=dates[0])
    return baseline, latest, [d for d in dates if baseline <= d <= latest]


def visible_cards(game):
    """id -> name for priced, named cards. Art is deliberately NOT required —
    it once was, and that silently dropped whole games (Magic's art backfill
    is still running) from the report's market stats."""
    con = sqlite3.connect(os.path.join(BASE, GAMES[game]["db"]))
    rows = con.execute(
        "SELECT product_id, name FROM cards "
        "WHERE near_mint_price IS NOT NULL AND name IS NOT NULL").fetchall()
    con.close()
    return dict(rows)


def young_series(pc):
    """(game, pid) whose base NM series spans < 2 calendar months — brand-new
    or freshly-repaired cards. Their first weeks are listing prices settling,
    not market movement (2026-08-22: a prerelease set led the report), so the
    movers/trends machinery must not rank them."""
    return {(g, pid) for g, pid, n in pc.execute(
        "SELECT game, product_id, COUNT(DISTINCT substr(date,1,7)) "
        "FROM tcg_nm_history WHERE printing='' GROUP BY game, product_id") if n < 2}


def game_moves(synth, game, baseline, latest, names, young=frozenset()):
    """[(pid, name, old, new, pct)] for visible cards with a TCGplayer NM price on
    BOTH window ends. Same-source only (both 'tcg'): a PriceCharting->TCGplayer
    pair straddles the switch and would read as a market move, so it's dropped.
    Young series (< 2 months of data) are excluded — see young_series()."""
    moves = []
    for key, series in synth.items():
        g, pid = key[0], key[1]
        printing = key[2] if len(key) == 3 else ""
        if g != game or pid not in names or (g, pid) in young:
            continue
        b, l = series.get(baseline), series.get(latest)
        if not b or not l or b[1] != "tcg" or l[1] != "tcg":
            continue
        old, new = b[0], l[0]
        if old >= MIN_BASE and old > 0 and 1 / MAX_WEEK_RATIO <= new / old <= MAX_WEEK_RATIO:
            name = names[pid] + (f" — {printing}" if printing else "")
            moves.append((pid, name, old, new, (new / old - 1) * 100, printing))
    return moves


# --- trend detection ---------------------------------------------------------
# The report leads with the GROUPS moving cards — a set, a rarity or art
# treatment, a character, a stat line — instead of a flat list of individual
# movers. Individual cards appear as examples inside their trend.
MIN_TREND_N = 6           # a trend needs at least this many week-priced cards
TREND_MIN_MEDIAN = 2.0    # ...with at least this median % move
TREND_MIN_BREADTH = 0.65  # ...and this share moving the same direction
TREND_EXAMPLES = 2        # linked example cards woven into each trend sentence
GAME_TRENDS = 5           # groups discussed + charted per game

DIM_LABELS = {"set": "Set", "rarity": "Rarity", "type": "Card type",
              "color": "Color", "cost": "Cost", "character": "Card family",
              "art": "Art treatment", "printing": "Printing",
              "level": "Level", "stat": "Stat line", "trait": "Trait",
              "attribute": "Attribute", "series": "Source series",
              "artist": "Illustrator", "text": "Card text", "ink": "Ink",
              "aspect": "Aspect", "energy": "Energy type", "form": "Form",
              "stage": "Stage"}

# Art/finish treatments recognizable from rarity/subtype/name text. (True
# art-similarity clustering over the CLIP embeddings is a possible upgrade;
# these labels cover the treatments collectors actually chase.)
ART_KEYWORDS = [
    ("manga", "Manga art"), ("special illustration", "Special illustration"),
    ("illustration rare", "Illustration rare"), ("alternate art", "Alternate art"),
    ("alt art", "Alternate art"), ("full art", "Full art"),
    ("textured", "Textured foil"), ("parallel", "Parallel foil"),
    ("secret", "Secret rare"), ("promo", "Promo"),
]


def art_treatment(rarity, subtypes, name):
    hay = f"{rarity or ''} {subtypes or ''} {name or ''}".lower()
    for kw, label in ART_KEYWORDS:
        if kw in hay:
            return label
    return None


def character_key(name, clean_name):
    """Base character/subject name: variants of the same character group
    together ("Monkey.D.Luffy (001)" and "Monkey.D.Luffy (Alternate Art)"
    both -> "Monkey.D.Luffy")."""
    base = (clean_name or name or "").strip()
    base = re.sub(r"\s*\(.*?\)", "", base)
    base = base.split(" - ")[0]
    base = re.sub(r"\s*#\S+$", "", base).strip()
    return base if len(base) >= 3 else None


def _num(v):
    m = re.search(r"-?\d+", str(v or "").replace(",", ""))
    return int(m.group()) if m else None


def _vals(v):
    """Multi-valued attribute -> clean list. Accepts a delimited string
    ("Dragon / Effect", "Hero;Princess") or a JSON array (some extended
    attributes, e.g. Gundam's color, arrive as lists)."""
    if not v:
        return []
    parts = v if isinstance(v, list) else re.split(r"\s*[;,/]\s*", str(v))
    seen, out = set(), []
    for s in parts:
        s = str(s).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _sval(v):
    """Scalar attribute that may arrive as a one-element JSON array."""
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").strip() or None


# Mechanic timing markers that appear on huge swaths of a game's cards —
# a "trend" across them is the market, not a story.
TEXT_STOP = {"once per turn", "on play", "when attacking", "main", "counter",
             "trigger", "your turn", "end of your turn", "activate: main",
             "rule", "on your opponent's attack", "opponent's turn", "dp"}
TEXT_KW_RE = re.compile(r"[\[<＜]([A-Za-z][A-Za-z0-9 +&'’\-]{2,22})[\]>＞]")


def text_keywords(desc):
    """Bracketed mechanic keywords from rules text ([Blocker], <Jamming>,
    [Double Attack]) — the ability keywords collectors actually chase."""
    if not desc:
        return []
    out, seen = [], set()
    for kw in TEXT_KW_RE.findall(str(desc)):
        kw = re.sub(r"\s+", " ", kw).strip()
        low = kw.lower()
        if low in TEXT_STOP or low in seen or re.search(r"\d{3,}", kw):
            continue
        seen.add(low)
        out.append(f"[{kw}]")
        if len(out) == 4:
            break
    return out


def _custom_dims(game, cu):
    """Trend dims mined from TCGplayer's per-game extended attributes: the
    stat lines, levels, tribes/traits, inks/aspects — the axes the model's
    attribute features see — plus Gundam's illustrator and source series."""
    d = {}
    if game == "digimon":
        d["color"] = _vals(cu.get("color"))
        lv = _num(cu.get("levelLv"))
        d["level"] = f"Level {lv}" if lv else None
        d["trait"] = _vals(cu.get("digimonType"))
        dp = _num(cu.get("digimonPowerDp"))
        d["stat"] = f"{dp:,} DP" if dp else None
        c = _num(cu.get("playCost"))
        d["cost"] = f"cost {c}" if c is not None else None
        d["form"] = _sval(cu.get("digimonForm"))
    elif game == "magic":
        d["color"] = _vals(cu.get("color"))
        mv = _num(cu.get("convertedCost"))
        d["cost"] = f"mana value {mv}" if mv is not None else None
        p, t = _num(cu.get("powerNumber")), _num(cu.get("toughnessNumber"))
        if p is not None and t is not None:
            d["stat"] = f"{p}/{t}"
        ft = str(cu.get("fullType") or "")
        if "—" in ft:
            d["trait"] = _vals(ft.split("—", 1)[1].replace(" ", ";"))
    elif game == "yugioh":
        d["attribute"] = _sval(cu.get("attribute"))
        d["trait"] = _vals(cu.get("monsterType"))
        lv = _num(cu.get("level"))
        d["level"] = f"Level {lv}" if lv else None
        atk = _num(cu.get("attack"))
        d["stat"] = f"ATK {atk:,}" if atk is not None else None
    elif game == "lorcana":
        d["ink"] = _sval(cu.get("inkType"))
        c = _num(cu.get("costInk"))
        d["cost"] = f"cost {c}" if c is not None else None
        s, w = _num(cu.get("strength")), _num(cu.get("willpower"))
        if s is not None and w is not None:
            d["stat"] = f"{s}/{w}"
        d["trait"] = _vals(cu.get("classification"))
    elif game == "gundam":
        d["color"] = _vals(cu.get("color"))
        lv = _num(cu.get("level"))
        d["level"] = f"Level {lv}" if lv else None
        c = _num(cu.get("cost"))
        d["cost"] = f"cost {c}" if c is not None else None
        d["trait"] = _vals(cu.get("trait"))
        d["series"] = _sval(cu.get("sourceTitle"))
        d["artist"] = _sval(cu.get("illustrator"))
        ap, hp = _num(cu.get("attackPoints")), _num(cu.get("hitPoints"))
        if ap is not None and hp is not None:
            d["stat"] = f"AP {ap} / HP {hp}"
    elif game == "starwars":
        d["aspect"] = _vals(cu.get("aspect"))
        d["trait"] = _vals(cu.get("traits"))
    elif game == "pokemon":
        d["energy"] = _sval(cu.get("energyType"))
        d["stage"] = _sval(cu.get("stage"))
    return d


def card_attrs(game):
    """pid -> the grouping attributes trend detection slices on. Game DBs
    don't share one schema (color/cost/subtypes exist only where the game has
    them), so the query is built from the columns actually present; the rest
    comes from the custom_attributes JSON. Values may be lists (a card with
    two traits belongs to both groups)."""
    con = sqlite3.connect(os.path.join(BASE, GAMES[game]["db"]))
    have = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
    want = ["name", "clean_name", "set_name", "rarity", "card_type",
            "color", "cost", "subtypes", "power", "description",
            "custom_attributes"]
    sel = ", ".join(c if c in have else "NULL" for c in want)
    out = {}
    for (pid, name, clean, s, r, t, c, cost, subs, pw, desc,
         custom) in con.execute(f"SELECT product_id, {sel} FROM cards"):
        cost = str(cost).strip() if cost not in (None, "") else ""
        a = {
            "set": (s or "").strip() or None,
            "rarity": (r or "").strip() or None,
            "type": (t or "").strip() or None,
            "color": (c or "").strip() or None,
            "cost": f"cost {cost}" if cost else None,
            "character": character_key(name, clean),
            "art": art_treatment(r, subs, name),
            "trait": _vals(subs),
            "text": text_keywords(desc),
        }
        pw = _num(pw)
        if pw:
            a["stat"] = f"{pw:,} power"
        if custom:
            try:
                cu = json.loads(custom)
            except ValueError:
                cu = None
            if isinstance(cu, dict):
                for dim, val in _custom_dims(game, cu).items():
                    if val:
                        a[dim] = val
                if not a["text"]:
                    a["text"] = text_keywords(cu.get("description"))
        out[pid] = a
    con.close()
    return out


def find_trends(game, moves, attrs):
    """Attribute groups that moved TOGETHER this week, ranked. Breadth is the
    qualifier that separates "the set is up" from "one chase card dragged the
    average": at least TREND_MIN_BREADTH of a group's week-priced members must
    move the median's direction."""
    groups = collections.defaultdict(list)
    for m in moves:
        pid, printing = m[0], (m[5] if len(m) > 5 else "")
        a = attrs.get(pid)
        if not a:
            continue
        entity_dims = dict(a)
        if printing:
            entity_dims["printing"] = printing   # printings are a trend dim too
        for dim, val in entity_dims.items():
            # Multi-valued dims (traits, text keywords, dual colors): the
            # card belongs to every group it names.
            for v in (val if isinstance(val, list) else [val]):
                if v and len(str(v)) <= 40:
                    groups[(dim, v)].append(m)
    trends = []
    for (dim, val), members in groups.items():
        n = len(members)
        if n < MIN_TREND_N:
            continue
        # A text keyword shared by half the game is a mechanic, not a story.
        if dim == "text" and n > 40:
            continue
        med = statistics.median(m[4] for m in members)
        if abs(med) < TREND_MIN_MEDIAN:
            continue
        rising = med > 0
        same = sum(1 for m in members if (m[4] > 0.5 if rising else m[4] < -0.5))
        breadth = same / n
        if breadth < TREND_MIN_BREADTH:
            continue
        trends.append({"game": game, "dim": dim, "value": val, "n": n,
                       "same": same, "median": med, "rising": rising,
                       "score": abs(med) * breadth * log2(n), "members": members})
    trends.sort(key=lambda t: -t["score"])
    # A hot set resurfaces as its rarity/character/cost slices — keep only the
    # highest-scoring framing of any largely-overlapping pack of cards.
    kept, seen = [], []
    for t in trends:
        ids = {m[0] for m in t["members"]}
        if any(len(ids & s) / len(ids) > 0.6 for s in seen):
            continue
        kept.append(t)
        seen.append(ids)
    return kept


def pick_trends(trends, k=GAME_TRENDS, max_per_dim=2):
    """Top-k with variety (2026-10-09, user request): cap each dimension at
    two slots so a heavy week surfaces sets AND stat lines AND art, not four
    set rows — the analysis reads differently game to game and week to week."""
    out, per_dim = [], collections.Counter()
    for t in trends:
        if per_dim[t["dim"]] >= max_per_dim:
            continue
        out.append(t)
        per_dim[t["dim"]] += 1
        if len(out) == k:
            break
    return out


def short_label(value, n=20):
    v = str(value)
    return v if len(v) <= n else v[:n - 1] + "…"


# Trend dims that map onto a real catalog filter: the group name links to the
# catalog with that filter applied. sets/rarities are comma-split lists in the
# catalog's URL scheme, so values containing a comma stay unlinked.
CATALOG_FILTER_PARAM = {"set": "sets", "rarity": "rarities", "character": "searchTerm"}


def group_link(t):
    label = f"<strong>{esc(t['value'])}</strong>"
    param = CATALOG_FILTER_PARAM.get(t["dim"])
    if not param or (param != "searchTerm" and "," in t["value"]):
        return label
    return (f'<a href="/catalog?game={t["game"]}&amp;{param}={quote(t["value"], safe="")}">'
            f"{label}</a>")


def trend_phrase(t):
    """One prose sentence for a trend — the group is the subject (linked to
    the catalog filtered to it, where a filter exists), individual cards
    appear only as linked examples inside it."""
    label = DIM_LABELS[t["dim"]].lower()
    # Distinct display names only: a card-family trend's printings often share
    # one name, and "Essence Warden and Essence Warden" reads broken.
    ex, seen = [], set()
    for m in sorted(t["members"], key=lambda m: -m[4] if t["rising"] else m[4]):
        if m[1] not in seen:
            ex.append(m)
            seen.add(m[1])
        if len(ex) == TREND_EXAMPLES:
            break
    links = " and ".join(
        f"{card_link(t['game'], m[0], m[1], m[5] if len(m) > 5 else '')} ({pct(m[4])})"
        for m in ex)
    direction = "up" if t["rising"] else "down"
    # "tracked" not "week-priced" (2026-08-29, user report): the count excludes
    # BOTH cards missing a price on either end of the week AND sub-$5 cards
    # (whose % moves are noise) — one word can't carry that, the legend does.
    return (f"{group_link(t)} ({label}): {t['same']} of "
            f"{t['n']} tracked cards {direction}, median {pct(t['median'])} "
            f"— e.g. {links}.")


def forecast_corner(pred, games_live):
    """Top model 1M forecast gainers across live games (base >= $10)."""
    rows = pred.execute(
        "SELECT game, product_id, printing, base_price, forecast_price FROM forecasts "
        "WHERE target='ungraded' AND horizon='1m' AND base_price>=10 "
        "ORDER BY forecast_price/base_price DESC LIMIT 30").fetchall()
    picks = []
    for game, pid, printing, base, fcst in rows:
        if game in games_live and pid in games_live[game]:
            name = games_live[game][pid] + (f" — {printing}" if printing else "")
            picks.append((game, pid, name, base, fcst, printing))
        if len(picks) == OVERALL_N:
            break
    return picks


# Site-served horizons and their fixed display lengths (the pipeline also
# grades 1w internally as a feedback signal, but 1-week forecasts are not a
# product the site shows, so they stay out of the public report card).
REPORT_HORIZONS = (("1m", "1 month", 28), ("6m", "6 months", 182), ("12m", "12 months", 364))
MIN_GRADED = 30   # a horizon/game needs this many graded calls to be worth reporting

# Direction is only graded when both the call and the outcome moved >= ~1%
# (log-return 0.01): an "up" call against a flat month is neither right nor
# wrong, and unchanged monthly buckets are common.
DECISIVE = "ABS(ret) >= 0.01 AND ABS(realized_ret) >= 0.01"
ACCURACY_SELECT = (
    "SELECT {cols} COUNT(*), AVG(ABS(ret - realized_ret)), AVG(ret - realized_ret), "
    "AVG(CASE WHEN realized_price BETWEEN low AND high THEN 1.0 ELSE 0.0 END), "
    f"SUM(CASE WHEN {DECISIVE} THEN 1 ELSE 0 END), "
    f"AVG(CASE WHEN {DECISIVE} THEN "
    "(CASE WHEN (ret > 0) = (realized_ret > 0) THEN 1.0 ELSE 0.0 END) END) "
    "FROM forecast_archive WHERE realized_ret IS NOT NULL AND ")


def live_accuracy(pred, horizon, days, by_game=False):
    """Graded LIVE forecasts of a horizon whose window has genuinely elapsed
    since issue (scored_at + horizon <= today). Stale-anchored archive rows
    grade instantly against old history — real cohorts take wall-clock time,
    so without this gate the report would claim matured 6-month calls months
    before the first one could exist."""
    where = ("printing='' AND substr(model_version,1,2) != '__' AND horizon = ? "
             "AND date(substr(scored_at,1,10)) <= date('now', ?)")
    args = (horizon, f"-{days} days")
    if by_game:
        return pred.execute(ACCURACY_SELECT.format(cols="game,") + where +
                            " GROUP BY game HAVING COUNT(*) >= " + str(MIN_GRADED),
                            args).fetchall()
    r = pred.execute(ACCURACY_SELECT.format(cols="") + where, args).fetchone()
    return r if r[0] and r[0] >= MIN_GRADED else None


def backtest_accuracy(pred, by_game=False):
    """Graded 1-month calls from the base backtest vintages (model retrained
    as of a past month, later data hidden). length()=12 keeps only the
    '__bt-YYYY-MM' bases — the day-stamped display clones would triple-count."""
    where = "model_version LIKE '__bt-%' AND length(model_version) = 12 AND horizon = '1m'"
    if by_game:
        return pred.execute(ACCURACY_SELECT.format(cols="game,") + where +
                            " GROUP BY game HAVING COUNT(*) >= " + str(MIN_GRADED)).fetchall()
    return pred.execute(ACCURACY_SELECT.format(cols="") + where).fetchone()


# Overall direction accuracy split by the sign of the call — how often the
# "up" calls actually rose, the "down" calls actually fell, and the combined
# rate — over the same decisive moves the headline direction stat uses.
DIRECTION_SPLIT_SELECT = (
    "SELECT "
    f"SUM(CASE WHEN {DECISIVE} AND ret > 0 THEN 1 ELSE 0 END), "
    f"AVG(CASE WHEN {DECISIVE} AND ret > 0 THEN (CASE WHEN realized_ret > 0 THEN 1.0 ELSE 0.0 END) END), "
    f"SUM(CASE WHEN {DECISIVE} AND ret < 0 THEN 1 ELSE 0 END), "
    f"AVG(CASE WHEN {DECISIVE} AND ret < 0 THEN (CASE WHEN realized_ret < 0 THEN 1.0 ELSE 0.0 END) END), "
    f"SUM(CASE WHEN {DECISIVE} THEN 1 ELSE 0 END), "
    f"AVG(CASE WHEN {DECISIVE} THEN (CASE WHEN (ret > 0) = (realized_ret > 0) THEN 1.0 ELSE 0.0 END) END) "
    "FROM forecast_archive WHERE realized_ret IS NOT NULL AND ")


def direction_split(pred, live):
    """(up_n, up_acc, down_n, down_acc, all_n, all_acc): direction accuracy on
    the calls that predicted a rise, on those that predicted a fall, and the
    two combined — aggregated across every game. Live uses the matured
    1-month cohort; otherwise the backtest vintages, matching the rest of the
    report card's population."""
    if live:
        where = ("printing='' AND substr(model_version,1,2) != '__' AND horizon = '1m' "
                 "AND date(substr(scored_at,1,10)) <= date('now', '-28 days')")
    else:
        where = "model_version LIKE '__bt-%' AND length(model_version) = 12 AND horizon = '1m'"
    return pred.execute(DIRECTION_SPLIT_SELECT + where).fetchone()


def weekly_coverage(pred, weeks=8):
    """Band coverage week over week: the nightly dated 1-month cohorts
    (ungraded tier) bucketed by the FRIDAY-STARTING week they came due —
    aligned to this report's own cadence, so the newest row is a complete
    Fri→Thu week at generation time instead of a "(so far)" stub.

    Only the dated stream matures continuously, so it's the only honest
    weekly series: month-bucket rows (graded tiers, early ungraded vintages)
    all land on the 1st and would spike whichever week contains it; the
    day-01 exclusion drops those stragglers.

    Live rows alone are NOT the full story once compaction has run: the
    kept median-error representatives are coverage-biased (the first
    compaction lifted one week from 52.8% to 84.6%). compact_1m banks each
    deleted row's weekly tallies in forecast_coverage_weekly; merging the
    banked sums with the surviving live rows reproduces the full cohort."""
    # SQLite %w: Sunday=0 … Friday=5; (w+2)%7 days back = the week's Friday.
    wk_expr = ("date(substr(realized_at, 1, 10), '-' || "
               "((CAST(strftime('%w', substr(realized_at, 1, 10)) AS INTEGER) + 2) % 7)"
               " || ' days')")
    agg = {}
    for wk, n, hits, errs, dn, dh in pred.execute(
            f"SELECT {wk_expr} wk, COUNT(*), "
            "  SUM(CASE WHEN realized_price BETWEEN low AND high THEN 1 ELSE 0 END), "
            "  SUM(ABS(ret - realized_ret)), "
            f" SUM(CASE WHEN {DECISIVE} THEN 1 ELSE 0 END), "
            f" SUM(CASE WHEN {DECISIVE} AND (ret > 0) = (realized_ret > 0) "
            "      THEN 1 ELSE 0 END) "
            "FROM forecast_archive "
            "WHERE horizon = '1m' AND realized_ret IS NOT NULL AND target = 'ungraded' "
            "  AND printing = '' AND substr(model_version, 1, 2) != '__' "
            "  AND strftime('%d', substr(realized_at, 1, 10)) != '01' "
            "GROUP BY wk"):
        agg[wk] = [n, hits or 0, errs or 0.0, dn or 0, dh or 0]
    try:
        for wk, n, hits, errs, dn, dh in pred.execute(
                "SELECT week, n, band_hits, abs_err_sum, dir_n, dir_hits "
                "FROM forecast_coverage_weekly"):
            a = agg.setdefault(wk, [0, 0, 0.0, 0, 0])
            a[0] += n; a[1] += hits; a[2] += errs; a[3] += dn; a[4] += dh
    except sqlite3.OperationalError:
        pass   # pre-persistence DB: live rows only
    rows = [(wk, n, hits / n, errs / n, dn, (dh / dn) if dn else None)
            for wk, (n, hits, errs, dn, dh) in sorted(agg.items())
            if n >= MIN_GRADED]
    return rows[-weeks:]


# ---- inline SVG bar charts -------------------------------------------------
# Self-contained horizontal bars embedded in the stored report HTML. Colors
# ride the site's CSS variables (with hard fallbacks), so they follow the
# theme without any client-side chart code.
CHART_W = 640


def chart_title(title, w=CHART_W):
    """Small caps title inside the SVG so each chart names itself; the class
    keeps it static under the client's draw-in animation."""
    return (f"<text x='0' y='12' font-size='10' class='report-chart-title' "
            f"fill='var(--text-muted, #8b96ad)'>{esc(title.upper())}</text>")


def bar_chart(rows, unit="%", signed=True, color=None, title=None, decimals=1):
    """[(label, value, annotation)] -> horizontal bar SVG string.

    Each row is TWO lines — label above, bar below, value+annotation
    right-aligned on the label line — so labels, values, and bars never share
    a horizontal band (640 viewBox units can't fit set names + annotations +
    bars side by side without collisions). signed=True colors by direction
    and, when signs are mixed, diverges around a center zero line; a chart
    whose values all share one sign anchors bars to the zero EDGE (left for
    gains, right for losses) so the full width carries resolution. color
    forces one fill (unsigned metrics like error size, where green/red would
    editorialize).
    """
    if not rows:
        return ""
    top = 20 if title else 0
    LABEL_H, BAR_H, GAP, PAD = 15, 12, 13, 8
    row_h = LABEL_H + BAR_H + GAP
    h = top + PAD * 2 + row_h * len(rows) - GAP
    span = max(abs(v) for _, v, _ in rows) or 1.0
    neg = signed and any(v < 0 for _, v, _ in rows)
    pos = not signed or any(v >= 0 for _, v, _ in rows)
    mixed = neg and pos
    plot_w = CHART_W - PAD * 2
    zero_x = PAD + (plot_w / 2 if mixed else (plot_w if neg else 0))
    scale = (plot_w / 2 if mixed else plot_w) / span
    parts = [f"<svg class='report-chart' viewBox='0 0 {CHART_W} {h}' "
             f"role='img' xmlns='http://www.w3.org/2000/svg'>"]
    if title:
        parts.append(chart_title(title))
    y = PAD + top
    if mixed:
        parts.append(f"<line x1='{zero_x}' y1='{y}' x2='{zero_x}' y2='{h - PAD}' "
                     "stroke='var(--border, #2e3a52)'/>")
    for label, v, note in rows:
        ly = y + LABEL_H - 4
        vtxt = (f"{v:+.{decimals}f}{unit}" if signed else f"{v:.{decimals}f}{unit}") \
            + (f" · {note}" if note else "")
        parts.append(f"<text x='{PAD}' y='{ly}' font-size='12' "
                     f"fill='var(--text, #e8ecf4)'>{esc(short_label(label, 58))}</text>")
        parts.append(f"<text x='{CHART_W - PAD}' y='{ly}' text-anchor='end' font-size='11' "
                     f"fill='var(--text-muted, #8b96ad)'>{esc(vtxt)}</text>")
        bw = max(abs(v) * scale, 1.5)
        x = zero_x - bw if v < 0 else zero_x
        fill = color or ("var(--down, #ff7a7a)" if v < 0 else "var(--up, #3fd98a)")
        parts.append(f"<rect x='{x:.1f}' y='{y + LABEL_H}' width='{bw:.1f}' "
                     f"height='{BAR_H}' rx='2' fill='{fill}'/>")
        y += row_h
    parts.append("</svg>")
    return "".join(parts)


# Distinct per-game line colors (the site ships a single dark theme).
GAME_COLORS = ["#3d7dca", "#ffcb05", "#3fd98a", "#ff7a7a",
               "#c678dd", "#ff9e64", "#4dd0e1", "#f06292"]


def game_week_series(synth, game, names, dates):
    """% change vs the window's first day, per day — the MEAN across this game's
    cards with a TCGplayer NM price on both that day and the first (None where too
    few). TCGplayer-only points, so the line never jumps on a source change. Mean,
    not median: most cards don't reprice on any given day, so the median is pinned
    to exactly 0 and hides the market's drift."""
    per_card = {}
    for key, series in synth.items():
        # Base printings only: synth mixes (game, pid) base keys with
        # (game, pid, printing) labeled keys (2026-08-10); labeled series are
        # forward-filled weekly buckets and would double-count card families
        # in the game index. (Unpacking key as a 2-tuple crashed the first
        # printing-aware Friday generation, 2026-08-14.)
        g, pid = key[0], key[1]
        if g != game or len(key) == 3 or pid not in names:
            continue
        per_card[pid] = {d: p for d, (p, src) in series.items() if src == "tcg"}
    base = {pid: s[dates[0]] for pid, s in per_card.items()
            if s.get(dates[0], 0) >= MIN_BASE}
    out = []
    for d in dates:
        ratios = [per_card[pid][d] / b for pid, b in base.items() if d in per_card[pid]]
        ratios = [r for r in ratios if 1 / MAX_WEEK_RATIO <= r <= MAX_WEEK_RATIO]
        out.append((statistics.mean(ratios) - 1) * 100 if len(ratios) >= 50 else None)
    return out


def line_chart(dates, series, title=None):
    """Multi-line SVG: series = [(label, [pct-or-None per date], color)], each
    line tagged at its right edge with the label and closing value."""
    W, H, PADT, PADB, LX = 640, 250, 12, 22, 46
    if title:
        PADT += 20
        H += 20
    # Right gutter sized to the longest end label (mono, ~6.6px/char at 11px);
    # "Star Wars Unlimited -10.0%" must not run past the viewBox.
    RGUT = max(120, int(max((len(s[0]) for s in series), default=0) * 6.6) + 60)
    plot_w = W - LX - RGUT
    vals = [v for _, ys, _ in series for v in ys if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    if hi - lo < 0.5:
        hi, lo = hi + 0.25, lo - 0.25
    ys_scale = (H - PADT - PADB) / (hi - lo)
    Y = lambda v: H - PADB - (v - lo) * ys_scale
    X = lambda i: LX + plot_w * i / max(len(dates) - 1, 1)
    parts = [f"<svg class='report-chart' viewBox='0 0 {W} {H}' role='img' "
             "xmlns='http://www.w3.org/2000/svg'>"]
    if title:
        parts.append(chart_title(title, W))
    # zero line + y extremes + first/last date labels
    parts.append(f"<line x1='{LX}' y1='{Y(0):.1f}' x2='{LX + plot_w}' y2='{Y(0):.1f}' "
                 "stroke='var(--border, #2e3a52)'/>")
    for v in (lo, hi):
        parts.append(f"<text x='{LX - 6}' y='{Y(v) + 4:.1f}' text-anchor='end' font-size='10' "
                     f"fill='var(--text-muted, #8b96ad)'>{v:+.1f}%</text>")
    for i, anchor in ((0, "start"), (len(dates) - 1, "end")):
        parts.append(f"<text x='{X(i):.1f}' y='{H - 6}' text-anchor='{anchor}' font-size='10' "
                     f"fill='var(--text-muted, #8b96ad)'>{dates[i][5:]}</text>")
    # lines, then right-edge labels nudged apart so converging lines stay legible
    labels = []
    for label, ys, color in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if v is not None)
        if not pts:
            continue
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2'/>")
        last = next(v for v in reversed(ys) if v is not None)
        labels.append([Y(last), f"{label} {last:+.1f}%", color])
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 13)
    # The forward pass only pushes DOWN — a cluster near the floor runs off
    # the canvas. Clamp the bottom and push the pile back up.
    if labels:
        labels[-1][0] = min(labels[-1][0], H - 8)
        for i in range(len(labels) - 2, -1, -1):
            labels[i][0] = min(labels[i][0], labels[i + 1][0] - 13)
    for y, text, color in labels:
        parts.append(f"<text x='{LX + plot_w + 8}' y='{y + 4:.1f}' font-size='11' "
                     f"fill='{color}'>{esc(text)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def money(v):
    return f"${v:,.2f}"


def pct(v):
    return f"{v:+.1f}%"


def build_report(force=False, as_of=None):
    # --date pins the report's slug/title when REGENERATING a past report (a
    # Saturday regen of Friday's report must update the Friday slug, not mint
    # a new Saturday one).
    # No --date: the run's nominal day is the UTC date (2026-08-14 site-wide
    # decision): the 22:00 CDT nightly is 03:00 UTC, so the whole run shares
    # one UTC day. The Thursday-night run IS Friday in UTC — its model block
    # (Friday ~10:00 UTC on the trainer) generates the report, which is
    # therefore LIVE on Friday morning instead of Saturday.
    today = (date.fromisoformat(as_of) if as_of
             else datetime.now(timezone.utc).date())
    if today.weekday() != 4 and not force:   # 4 = Friday
        print("Not Friday — skipping (use --force to write anyway).")
        return

    pc = sqlite3.connect(PC_DB)
    pred = sqlite3.connect(PRED_DB)
    baseline, latest, window_dates = week_window(pc)
    if not baseline or baseline == latest:
        print("Not enough TCGplayer NM history for a weekly window yet — skipping.")
        return
    # Daily ungraded prices (TCGplayer NM over PriceCharting) for the window days,
    # built once and shared by the movers tables and the per-game line chart.
    synth = synth_series(pc, window_dates)
    young = young_series(pc)
    print(f"excluding {len(young)} young series (<2 months) from movers/trends")

    games_live = {}
    per_game = {}
    all_moves = []
    for game in GAMES:
        names = visible_cards(game)
        if not names:
            continue
        moves = game_moves(synth, game, baseline, latest, names, young)
        if not moves:
            continue
        games_live[game] = names
        per_game[game] = moves
        all_moves += [(game, *m) for m in moves]

    if not all_moves:
        print("No movement data — skipping.")
        return

    n = len(all_moves)
    gains = [m[5] for m in all_moves if m[5] > 0.5]
    losses = [m[5] for m in all_moves if m[5] < -0.5]
    ups, downs = len(gains), len(losses)
    avg_gain = statistics.mean(gains) if gains else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    breadth = "more gainers than losers" if ups > downs else \
              "more losers than gainers" if downs > ups else "an even split"

    title = f"Weekly Market Report — {today.strftime('%B %-d, %Y')}"
    slug = f"weekly-market-report-{today.isoformat()}"
    summary = (f"The card market showed {breadth} this week: of {n:,} cards priced "
               f"at both ends of the window, {ups:,} rose an average of {avg_gain:.1f}% "
               f"and {downs:,} fell an average of {abs(avg_loss):.1f}%.")

    # Trends are computed per game and each game is discussed exactly ONCE.
    # Individual cards never get their own table — they appear only as linked
    # examples inside their group's sentence.
    trends_by_game = {g: pick_trends(find_trends(g, m, card_attrs(g)))
                      for g, m in per_game.items()}

    # The week-drift line chart sits directly under the lede: one overview
    # visual, then the per-game sections in the chart's own order (steepest
    # weekly move first, so the legend order matches the reading order).
    series = []
    drift = {}
    for i, game in enumerate(sorted(per_game)):
        ys = game_week_series(synth, game, games_live[game], window_dates)
        if any(v is not None for v in ys):
            drift[game] = next(v for v in reversed(ys) if v is not None)
            series.append((GAMES[game]["label"], ys, GAME_COLORS[i % len(GAME_COLORS)]))
    if series:
        series.sort(key=lambda s: -abs(next(v for v in reversed(s[1]) if v is not None)))

    game_order = sorted(per_game, key=lambda g: -abs(drift.get(g, 0.0)))
    picks = forecast_corner(pred, games_live)
    live = [(label, *row) for h, label, days in REPORT_HORIZONS
            for row in [live_accuracy(pred, h, days)] if row]
    wow = [r for r in weekly_coverage(pred)
           if (today - date.fromisoformat(r[0])).days >= 7]

    # Claude-written editorial (2026-10-09): every number above is final, so
    # pack the facts and let the model write the prose around them. Each
    # field falls back to the templated sentence independently; see
    # report_editorial.py for the contract.
    r1 = lambda v: round(v, 1)
    facts = {
        "week": {"start": baseline, "end": latest},
        "totals": {"tracked": n, "rose": ups, "fell": downs,
                   "avg_gain_pct": r1(avg_gain), "avg_loss_pct": r1(avg_loss)},
        "games": {}, "model_corner_picks": [], "model": {},
    }
    for game in game_order:
        moves = per_game[game]
        g_g = [m[4] for m in moves if m[4] > 0.5]
        g_l = [m[4] for m in moves if m[4] < -0.5]
        gf = {"label": GAMES[game]["label"], "tracked": len(moves),
              "rose": len(g_g), "fell": len(g_l),
              "avg_gain_pct": r1(statistics.mean(g_g)) if g_g else 0.0,
              "avg_loss_pct": r1(statistics.mean(g_l)) if g_l else 0.0,
              "week_drift_pct": r1(drift.get(game, 0.0)), "trends": []}
        for t in trends_by_game.get(game, []):
            gf["trends"].append({
                "group": t["value"], "dimension": DIM_LABELS[t["dim"]].lower(),
                "moved": t["same"], "of_tracked": t["n"],
                "median_pct": r1(t["median"]),
                "examples": [{"name": m[1], "pct": r1(m[4])}
                             for m in sorted(t["members"], key=lambda m: -abs(m[4]))[:2]]})
        if not gf["trends"] and moves:
            bt_, wt_ = max(moves, key=lambda m: m[4]), min(moves, key=lambda m: m[4])
            gf["outliers"] = {"best": {"name": bt_[1], "pct": r1(bt_[4])},
                              "worst": {"name": wt_[1], "pct": r1(wt_[4])}}
        facts["games"][game] = gf
    facts["model_corner_picks"] = [
        {"name": name, "game": GAMES[game]["label"], "current_usd": round(base, 2),
         "forecast_1m_usd": round(fcst, 2), "implied_pct": r1((fcst / base - 1) * 100)}
        for game, pid, name, base, fcst, printing in picks]
    if live:
        facts["model"]["live_record"] = [
            {"horizon": label, "graded": n_g,
             "typical_miss_pct": r1((exp(mae) - 1) * 100),
             "bias_pct": r1((exp(bias) - 1) * 100), "band80_pct": r1(hit * 100),
             "direction_pct": r1(da * 100) if da is not None else None}
            for label, n_g, mae, bias, hit, dn, da in live]
    if wow:
        facts["model"]["band_coverage_by_week"] = [
            {"week_of": wk, "graded": n_wk, "band80_pct": r1(hit * 100)}
            for wk, n_wk, hit, _mae, _dn, _da in wow]
    prose = write_editorial(facts) or {}
    if prose.get("lede"):
        summary = prose["lede"]   # index card + meta description too

    body = [f"<p class='report-lede'>{esc(summary)} Price window: {baseline} to {latest}, "
            f"ungraded market prices.</p>",
            # Legend for "tracked" (2026-08-29, user report: '12 of 15' read as
            # arbitrary): the count is smaller than the full set on purpose,
            # and the reader deserves to know both reasons once, up front.
            "<p class='report-note'>“Tracked” cards are those with a market price "
            "on both ends of the week <em>and</em> worth at least $5 at the start "
            "— cheaper cards are left out because a few cents of movement "
            "reads as a large, meaningless percentage. A group like “12 of 15 "
            "tracked” may belong to a bigger set; the rest simply "
            "couldn’t be measured honestly this week.</p>"]
    if prose.get("overview"):
        body.append(f"<p>{esc(prose['overview'])}</p>")
    if series:
        body.append(line_chart(window_dates, series, title="Price drift this week by game"))

    for game in game_order:
        moves = per_game[game]
        ggains = [m[4] for m in moves if m[4] > 0.5]
        glosses = [m[4] for m in moves if m[4] < -0.5]
        gups, gdowns = len(ggains), len(glosses)
        gavg_gain = statistics.mean(ggains) if ggains else 0.0
        gavg_loss = statistics.mean(glosses) if glosses else 0.0
        # Each game is a native <details> dropdown (2026-10-09, user request):
        # collapsed by default, the summary row carries the name + a breadth
        # teaser so the collapsed report still scans. No scripts — works
        # identically on the SPA (sanitizer allows details/summary) and the
        # static crawler pages.
        # The opening paragraph is Claude-written when available (the teaser
        # and trendlines carry the exact counts); templated otherwise.
        game_para = prose.get("games", {}).get(game)
        if not game_para:
            game_para = (f"{gups:,} of {len(moves):,} tracked cards rose this week "
                         f"(up an average of {gavg_gain:.1f}%); {gdowns:,} fell "
                         f"(down an average of {abs(gavg_loss):.1f}%).")
        body.append(
            "<details class='report-game-sec'>"
            f"<summary><strong>{esc(GAMES[game]['label'])}</strong>"
            f"<span class='report-game-teaser'>"
            f"<span class='report-up'>{gups:,} ▲</span> · "
            f"<span class='report-down'>{gdowns:,} ▼</span> "
            f"· {len(moves):,} tracked</span></summary>"
            f"<p>{esc(game_para)}</p>")
        gts = trends_by_game.get(game, [])
        if gts:
            # One line per trend, direction-marked, rising first.
            for t in sorted(gts, key=lambda t: (not t["rising"], -abs(t["median"]))):
                marker = ("<span class='report-up'>▲</span>" if t["rising"]
                          else "<span class='report-down'>▼</span>")
                body.append(f"<p class='report-trendline'>{marker} {trend_phrase(t)}</p>")
            rows = [(t["value"], t["median"], f"{t['same']}/{t['n']} cards")
                    for t in sorted(gts, key=lambda t: -t["median"])]
            body.append(bar_chart(rows, signed=True,
                                  title=f"{GAMES[game]['label']}: group moves this week — median %"))
        else:
            bt = max(moves, key=lambda m: m[4])
            wt = min(moves, key=lambda m: m[4])
            body.append("<p>No group-wide trend stood out this week — movement was "
                        f"scattered. The outliers: {card_link(game, bt[0], bt[1], bt[5] if len(bt) > 5 else '')} "
                        f"({pct(bt[4])}) and {card_link(game, wt[0], wt[1], wt[5] if len(wt) > 5 else '')} ({pct(wt[4])}).</p>")
        body.append("</details>")

    if picks:
        # Highlighted panel (2026-10-09, user request): the model's picks are
        # the report's signature content, so they get the site's "ticket"
        # treatment — tinted surface with the forecast-yellow edge the charts
        # already use for the model line. Older SPA bundles unwrap the div
        # harmlessly.
        body.append("<div class='report-model-corner'>"
                    "<h2>Model corner</h2>"
                    "<p>The cards our 1-month model is most optimistic about right now:</p>"
                    "<table class='report-table'>"
                    "<thead><tr><th>Card</th><th>Current</th><th>1M forecast</th><th>Implied</th></tr></thead><tbody>")
        for game, pid, name, base, fcst, printing in picks:
            body.append(f"<tr><td>{card_link(game, pid, name, printing)} "
                        f"<span class='report-game'>{esc(GAMES[game]['label'])}</span></td>"
                        f"<td>{money(base)}</td><td>{money(fcst)}</td>"
                        f"<td>{pct((fcst / base - 1) * 100)}</td></tr>")
        body.append("</tbody></table>"
                    "<p class='report-note'>Forecasts are model estimates, not financial advice; "
                    "see the About page for how they work and how they're graded.</p></div>")

    # Model report card. Live horizons appear once their first cohort has had
    # wall-clock time to mature; until then the backtest vintages (retrained
    # in the past with later data hidden, then graded) carry the record.
    body.append("<h2>Model report card</h2>"
                "<p>Every forecast the model publishes is archived, and once its "
                "target date passes it is graded against what the price actually "
                "did — misses included. Four numbers summarize the record:</p><ul>"
                "<li><strong>Typical miss</strong> — the average gap between the "
                "predicted and realized price. A 5% typical miss on a $100 card "
                "means the model's calls landed about $5 from reality.</li>"
                "<li><strong>Bias</strong> — the direction of the average error. "
                "Above zero, forecasts ran high (the model was optimistic); below "
                "zero it undershot. Near zero is best.</li>"
                "<li><strong>80% band</strong> — every forecast ships with a "
                "low&ndash;high range the model expects to contain the real price 80% "
                "of the time. This column is how often it actually did: 80% is "
                "perfect calibration, higher means the bands are cautious, lower "
                "means overconfident.</li>"
                "<li><strong>Direction</strong> — when the model called a move of at "
                "least 1% and the price also moved at least 1%, how often it picked "
                "the right side, up or down. 50% would be a coin flip; a call "
                "against a flat month is neither right nor wrong and isn't "
                "counted.</li></ul>")

    def dir_cell(dir_acc):
        return f"{dir_acc * 100:.0f}%" if dir_acc is not None else "—"

    if live:
        body.append("<table class='report-table'>"
                    "<thead><tr><th>Horizon</th><th>Graded</th><th>Typical miss</th>"
                    "<th>Bias</th><th>80% band</th><th>Direction</th></tr></thead><tbody>")
        for label, n_graded, mae, bias, hit, dir_n, dir_acc in live:
            body.append(f"<tr><td>{label}</td><td>{n_graded:,}</td>"
                        f"<td>{(exp(mae) - 1) * 100:.1f}%</td>"
                        f"<td>{pct((exp(bias) - 1) * 100)}</td>"
                        f"<td>{hit * 100:.0f}%</td><td>{dir_cell(dir_acc)}</td></tr>")
        body.append("</tbody></table>")
        if prose.get("model_note"):
            body.append(f"<p>{esc(prose['model_note'])}</p>")

        # Week over week (2026-09-27, user request): the same calibration
        # story as a trend, not a snapshot. Nightly dated cohorts only — see
        # weekly_coverage() — so every row is a true ~28-day forecast.
        # Complete Fri→Thu weeks only — a report generated Friday morning
        # would otherwise lead with a one-day stub of its own week.
        # (wow is computed above, before the facts pack.)
        if len(wow) >= 2:
            def wk_label(wk):
                d = date.fromisoformat(wk)
                return f"{d.strftime('%b')} {d.day}" + (
                    " (so far)" if (today - d).days < 7 else "")
            body.append("<h3>Week over week: band coverage</h3>"
                        "<p>Each row is the nightly 1-month forecasts that came due "
                        "that week, graded against ungraded market prices (graded "
                        "tiers settle on the 1st of the month, so they can't be "
                        "read weekly). Weeks run Friday to Thursday, matching this "
                        "report's cadence, so the newest row is a complete week. "
                        "80% is perfect calibration &mdash; higher means cautious "
                        "bands, lower means overconfident.</p>")
            body.append("<table class='report-table'>"
                        "<thead><tr><th>Week of</th><th>Graded</th><th>80% band</th>"
                        "<th>Typical miss</th><th>Direction</th></tr></thead><tbody>")
            for wk, n_wk, hit, mae, dir_n, dir_acc in wow:
                body.append(f"<tr><td>{wk_label(wk)}</td><td>{n_wk:,}</td>"
                            f"<td>{hit * 100:.1f}%</td>"
                            f"<td>{(exp(mae) - 1) * 100:.1f}%</td>"
                            f"<td>{dir_cell(dir_acc)}</td></tr>")
            body.append("</tbody></table>")
            body.append(bar_chart(
                [(wk_label(wk), hit * 100, f"of {n_wk:,}")
                 for wk, n_wk, hit, _mae, _dn, _da in wow],
                signed=False, color="var(--primary, #3d7dca)",
                title="80% band coverage by week — 80% is perfect calibration"))

        by_game = [r for r in live_accuracy(pred, "1m", 28, by_game=True) if r[0] in GAMES]
    else:
        bt = backtest_accuracy(pred)
        by_game = [r for r in backtest_accuracy(pred, by_game=True) if r[0] in GAMES]
        if bt and bt[0]:
            body.append(f"<p>No live forecast has been out long enough to grade yet — "
                        "the first 1-month cohort matures in August 2026, 6-month in "
                        "January 2027, 12-month in July 2027; live grades take over "
                        "here as they land. Until then, the record below comes from "
                        f"backtests: the model was retrained as of May and June 2026 "
                        "with everything after hidden, and its "
                        f"{bt[0]:,} one-month calls graded against what then happened: "
                        f"typical miss {(exp(bt[1]) - 1) * 100:.1f}%, "
                        f"bias {pct((exp(bt[2]) - 1) * 100)}, "
                        f"80% band hit {bt[3] * 100:.0f}%. On the {bt[4]:,} calls where "
                        "both the model and the market moved at least 1%, it picked "
                        f"the right direction {bt[5] * 100:.0f}% of the time.</p>")

    if by_game:
        by_game.sort(key=lambda r: -r[1])
        tag = "" if live else " (backtest)"
        body.append("<table class='report-table'>"
                    f"<thead><tr><th>Game</th><th>Graded{tag}</th><th>Typical miss</th>"
                    "<th>Bias</th><th>80% band</th><th>Direction</th></tr></thead><tbody>")
        for g, n_graded, mae, bias, hit, dir_n, dir_acc in by_game:
            body.append(f"<tr><td>{esc(GAMES[g]['label'])}</td><td>{n_graded:,}</td>"
                        f"<td>{(exp(mae) - 1) * 100:.1f}%</td>"
                        f"<td>{pct((exp(bias) - 1) * 100)}</td>"
                        f"<td>{hit * 100:.0f}%</td><td>{dir_cell(dir_acc)}</td></tr>")
        body.append("</tbody></table>")
        acc_rows = [(GAMES[g]["label"], (exp(mae) - 1) * 100, f"{hit * 100:.0f}% band")
                    for g, _n, mae, _bias, hit, _dn, _da in sorted(by_game, key=lambda r: r[2])]
        body.append(bar_chart(acc_rows, signed=False, color="var(--primary, #3d7dca)",
                              title=f"Typical 1-month forecast miss by game{tag} — shorter is better"))
        # Direction as its own chart, diverging around the coin-flip line:
        # bars right of zero beat chance, bars left of it did worse.
        dir_rows = [(GAMES[g]["label"], dir_acc * 100 - 50, f"{dir_acc * 100:.0f}% right of {dir_n:,}")
                    for g, _n, _mae, _bias, _hit, dir_n, dir_acc in by_game
                    if dir_acc is not None and dir_n >= MIN_GRADED]
        dir_rows.sort(key=lambda r: -r[1])
        if dir_rows:
            body.append(bar_chart(dir_rows, unit=" pts",
                                  title=f"Direction calls vs a coin flip, by game{tag}"))

    # Overall up/down accuracy as its own chart at the very bottom: is the
    # model better at calling gains than drops? Aggregated across all games,
    # so it stands independent of the per-game breakdown above. 50% = coin flip.
    split = direction_split(pred, bool(live))
    if split and split[4]:   # at least some decisive calls overall
        up_n, up_acc, down_n, down_acc, all_n, all_acc = split
        split_tag = "" if live else " (backtest)"
        split_rows = []
        if up_acc is not None:
            split_rows.append(("Predicted rise", up_acc * 100, f"of {up_n:,} up-calls"))
        if down_acc is not None:
            split_rows.append(("Predicted fall", down_acc * 100, f"of {down_n:,} down-calls"))
        split_rows.append(("Overall", all_acc * 100, f"of {all_n:,} calls"))
        # Two decimals here (2026-08-29, user report): up/down/overall accuracy
        # can legitimately near-tie, and at one decimal a real tie reads as a
        # copy-paste bug (all three showed "53.7%").
        body.append(bar_chart(split_rows, signed=False, color="var(--accent, #c678dd)",
                              decimals=2,
                              title=f"Direction accuracy: up vs down calls{split_tag} — 50% is a coin flip"))

    pred.execute("""CREATE TABLE IF NOT EXISTS reports (
        slug TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        published_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        body_html TEXT NOT NULL)""")
    pred.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?)",
                 (slug, title, today.isoformat(), summary, "".join(body)))
    pred.commit()
    pc.close()
    pred.close()
    print(f"Wrote report: {slug} ({n:,} cards in window {baseline} → {latest})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Weekly market report generator")
    ap.add_argument("--force", action="store_true", help="write even if today isn't Friday")
    ap.add_argument("--date", help="report date YYYY-MM-DD (regenerate a past "
                                   "report under ITS slug instead of today's)")
    args = ap.parse_args()
    # Non-fatal in the nightly: the report is a nice-to-have and runs at ~step 20
    # of the daily refresh, BEFORE the deploy. An error here must not abort the
    # run (weekly_refresh exits on any non-zero step) and cost the night's push.
    try:
        build_report(force=args.force, as_of=args.date)
    except Exception as e:
        import traceback
        print(f"market_report: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
