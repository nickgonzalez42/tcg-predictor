#!/usr/bin/env python3
"""
Card-type articles (2026-11-14, user: "add in 'card type performance' that is
a historical analysis of certain kinds of high value card, by title. In One
Piece for example there are manga and SP cards ... It should look at the
overall history, expected performance, and highlight a few examples").

A card type is a named kind of chase card a game prints across many sets,
told by its title (game_articles.CARD_TYPES). Where the set article follows
one release and the game article one week, this one follows the type over
its whole priced history:

  card-type-{game}-{type}-{date}   the group today, its price index month by
                                   month against the game's other expensive
                                   cards, each release's cards and the
                                   typical path by age, a few example cards
                                   with their full history and the model's
                                   calls, what sold, the model's outlook

Same sources and the same prose contract as game_articles.py, which plans
and stores these (kind 'type', focus = the type key).
"""

import statistics
from datetime import date, timedelta

import game_articles as ga
from game_articles import (
    GAMES, CARD_TYPES, CDN, MOVERS_N, PICKS_N, MIN_BASE, r1, prune, esc, money, pct,
    line_chart, card_link, game_moves, move_stats,
)
from article_editorial import write_type_article

MONTH_TOL = 31            # a monthly reading may be interpolated across two months
STEP_MIN_CARDS = 5        # an index step needs this many cards priced on both months
STEP_MAX_RATIO = 3.0      # one card tripling in a month does not move the group
ANCHOR_MAX_DAYS = 45      # age curve: first price within this long of the release
AGE_MONTHS = (3, 6, 12, 18, 24)
AGE_MIN_CARDS = 4
TOP_N = 6
EXAMPLES_N = 4
COMPARE_FLOORS = ((200, 200), (20, 20), (0, 5))   # (type median at least, other cards at least)
HIGH_LADDER = [("under $250", 0, 250), ("$250 to $500", 250, 500), ("$500 to $1,000", 500, 1000),
               ("$1,000 to $2,500", 1000, 2500), ("$2,500 and up", 2500, None)]
ga.BAND_SHORT.update({"under $250": "under $250", "$250 to $500": "$250-500", "$500 to $1,000": "$500-1k",
                      "$1,000 to $2,500": "$1k-2.5k", "$2,500 and up": "$2.5k+"})


def members_of(cat, spec):
    tag, skip = spec["name_tag"], spec.get("skip_prefix")
    return {pid: c for pid, c in cat.items()
            if tag in c["name"] and not (skip and c["name"].startswith(skip))}


def month_grid(first, today):
    """Month starts from the first reading's month to today's, then today."""
    d = date.fromisoformat(first).replace(day=1)
    out = []
    while d <= today:
        out.append(d.isoformat())
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    if out[-1] != today.isoformat():
        out.append(today.isoformat())
    return out


def chained_index(paths, grid):
    """% vs the first drawable month, per grid date: each step is the median
    of the per-card ratios over the cards priced on both months, so a card
    joins the index the month after it is first priced and never moves it by
    arriving. None before the first step with STEP_MIN_CARDS cards."""
    at = {pid: [ga.price_on(s, d, MONTH_TOL) for d in grid] for pid, s in paths.items()}
    out, level = [None] * len(grid), None
    for i in range(1, len(grid)):
        ratios = [v[i] / v[i - 1] for v in at.values() if v[i] and v[i - 1]]
        ratios = [r for r in ratios if 1 / STEP_MAX_RATIO <= r <= STEP_MAX_RATIO]
        if len(ratios) < STEP_MIN_CARDS:
            if level is not None:
                out[i] = (level - 1) * 100       # a thin month holds the level
            continue
        if level is None:
            level, out[i - 1] = 1.0, 0.0
        level *= statistics.median(ratios)
        out[i] = (level - 1) * 100
    return out


def back(grid, index, today, months):
    """The index's change over the last `months`, or None when it does not
    reach back that far."""
    cut = (today - timedelta(days=round(30.44 * months))).isoformat()
    past = [(d, v) for d, v in zip(grid, index) if v is not None and d <= cut]
    now = next((v for v in reversed(index) if v is not None), None)
    if not past or now is None:
        return None
    return ((100 + now) / (100 + past[-1][1]) - 1) * 100


def index_facts(grid, index, today):
    pts = [(d, v) for d, v in zip(grid, index) if v is not None]
    if len(pts) < 4:
        return None
    hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
    return {"from": pts[0][0][:7], "to": pts[-1][0], "index_now_pct": r1(pts[-1][1]),
            "past_year_pct": r1(back(grid, index, today, 12)),
            "past_six_months_pct": r1(back(grid, index, today, 6)),
            "past_three_months_pct": r1(back(grid, index, today, 3)),
            "peak": {"month": hi[0][:7], "pct": r1(hi[1])}, "trough": {"month": lo[0][:7], "pct": r1(lo[1])}}


def hero_line(grid, index, today):
    """The index over the past year, against its level a year ago (the whole
    history when it is shorter than that)."""
    pts = [(g, v) for g, v in zip(grid, index) if v is not None]
    cut = (today - timedelta(days=365)).isoformat()
    past = [p for p in pts if p[0] <= cut]
    if past:
        base = past[-1]
        pts = [(g, ((100 + v) / (100 + base[1]) - 1) * 100) for g, v in pts if g >= base[0]]
    return pts


def month_label(iso):
    return date.fromisoformat(iso).strftime("%b %Y")


def collect_type(pc, pred, game, cat, tkey, synth, window, young, today, attrs=None):
    spec = CARD_TYPES[game][tkey]
    label = GAMES[game]["label"]
    members = members_of(cat, spec)
    if len(members) < TOP_N:
        return None
    baseline, latest, _dates = window
    names = {pid: c["name"] for pid, c in members.items()}
    moves = game_moves(synth, game, baseline, latest, names, young) if baseline else []
    moves = [m for m in moves if not m[5]]
    by_pid = {m[0]: m for m in moves}

    paths = ga.blended_paths(pc, game, members)
    first = min((s[0][0] for s in paths.values() if s), default=None)
    grid = month_grid(first, today) if first else []
    index = chained_index(paths, grid) if grid else []

    prices = sorted(c["price"] for c in members.values())
    median_price = statistics.median(prices)
    floor = next(f for m, f in COMPARE_FLOORS if median_price >= m)
    others = {pid: c for pid, c in cat.items() if pid not in members and c["price"] >= floor}
    other_index = chained_index(ga.blended_paths(pc, game, others), grid) if grid and len(others) >= 10 else []
    # The charts start where the group's index does, not at one card's first price.
    k = next((i for i, v in enumerate(index) if v is not None), 0)
    grid, index, other_index = grid[k:], index[k:], other_index[k:]

    # Each card over the last year and since we first priced it.
    year_ago = (today - timedelta(days=365)).isoformat()
    year, since = {}, {}
    for pid, c in members.items():
        s = paths.get(pid) or []
        was = ga.price_on(s, year_ago, MONTH_TOL) if s else None
        if was:
            year[pid] = (c["price"] / was - 1) * 100
        if s and s[0][1] > 0 and (today - date.fromisoformat(s[0][0])).days >= 60:
            since[pid] = (c["price"] / s[0][1] - 1) * 100

    releases = {}
    for pid, c in members.items():
        releases.setdefault((c["release"] or "", c["set"] or "Unlisted"), []).append(pid)
    waves = []
    for (rel, s), pids in sorted(releases.items()):
        ys = [year[p] for p in pids if p in year]
        ss = [since[p] for p in pids if p in since]
        waves.append({"set": s, "released": rel or None, "cards": len(pids),
                      "median_price_usd": round(statistics.median(members[p]["price"] for p in pids), 2),
                      "median_past_year_pct": r1(statistics.median(ys)) if ys else None,
                      "median_since_first_priced_pct": r1(statistics.median(ss)) if ss else None})

    # The typical path by age: each card against its first price after release.
    by_age = {m: [] for m in AGE_MONTHS}
    for pid, c in members.items():
        s, rel = paths.get(pid) or [], c["release"]
        if not s or not rel:
            continue
        start = next(((d, p) for d, p in s if d >= rel), None)
        if not start or (date.fromisoformat(start[0]) - date.fromisoformat(rel)).days > ANCHOR_MAX_DAYS:
            continue
        for m in AGE_MONTHS:
            day = date.fromisoformat(rel) + timedelta(days=round(30.44 * m))
            p = ga.price_on(s, day.isoformat(), MONTH_TOL) if day <= today else None
            if p:
                by_age[m].append((p / start[1] - 1) * 100)
    curve = []
    for m in AGE_MONTHS:
        v = sorted(by_age[m])
        if len(v) >= AGE_MIN_CARDS:
            q = statistics.quantiles(v, n=4)
            curve.append({"months_since_release": m, "cards": len(v), "median_pct": r1(q[1]),
                          "lower_quarter_pct": r1(q[0]), "upper_quarter_pct": r1(q[2])})

    model = ga.model_calls(pred, game, set(members))
    calls, picks = ga.forecast_bits(pred, game, cat, pids=set(members), min_pick=MIN_BASE)
    sales = ga.sales_volume(pc, game, cat, members, today)
    vc = sales["per_card"] if sales else {}

    # A few cards that tell the story, one role each.
    top = sorted(members, key=lambda p: -members[p]["price"])
    roles = [("the dearest", top[0])]
    if year:
        roles.append(("the past year's biggest climber", max(year, key=year.get)))
        roles.append(("the past year's biggest faller", min(year, key=year.get)))
    sold = sorted((p for p in vc if p in members and vc[p][0] > 0), key=lambda p: -vc[p][0])
    if sold:
        roles.append(("the best seller last week", sold[0]))
    newest = max((p for p in members if members[p]["release"]), key=lambda p: members[p]["release"], default=None)
    if newest:
        roles.append(("the newest", newest))
    examples, seen = [], set()
    for role, pid in roles:
        if pid in seen or not ga.has_art(game, pid):
            continue
        seen.add(pid)
        examples.append({"role": role, "pid": pid})
        if len(examples) == EXAMPLES_N:
            break

    discussed = top[:TOP_N] + [e["pid"] for e in examples] + [p[0] for p in picks]
    return {
        "game": game, "label": label, "type": tkey, "spec": spec, "members": members,
        "week": {"start": baseline, "end": latest}, "stats": move_stats(moves), "by_pid": by_pid,
        "paths": paths, "grid": grid, "index": index, "other_index": other_index,
        "others": len(others), "other_floor": floor,
        "year": year, "since": since, "waves": waves, "curve": curve, "top": top[:TOP_N],
        "examples": examples, "model_calls": model, "calls": calls, "picks": picks,
        "medians": ga.set_forecast_medians(pred, game, cat, set(members)),
        "record": ga.record_for(pred, game), "sales": sales,
        "ladder": [(b, sum(1 for p in prices if p >= lo and (hi is None or p < hi)))
                   for b, lo, hi in (HIGH_LADDER if median_price >= 100 else ga.LADDER)],
        "about": ga.about_lines(attrs, discussed), "discussed": discussed,
    }


TYPE_SECTIONS = [
    ("group", "what this card type is (what_it_is) and the group today: how many priced cards across how many sets "
              "since which release, what they cost (median, cheapest, dearest, the whole group added up) and the "
              "dearest of them (the picture shows the six dearest, then the cards counted by price band)"),
    ("history", "the group's price index month by month over its whole priced history, against the game's other "
                "expensive cards (index.comparison says which): where it stands against a year ago, six months ago "
                "and three months ago, its peak and its trough, and how many of the cards are above or below their "
                "price of a year ago (the chart draws both indexes)"),
    ("releases", "how each release's cards of this type have done (the table: set, release date, cards, median "
                 "price, median change over the past year) and the typical path of one of these cards by months "
                 "since its set released (age_curve: the median card and the middle half around it, each against "
                 "its own first price after release); say plainly what that path has looked like, as a tendency "
                 "with its range and the number of cards behind each point, never a promise"),
    ("examples", "a few cards that tell the story, each with the role it carries (the dearest, the past year's "
                 "biggest climber and faller, the best seller, the newest): its price, its change over the past "
                 "year and since we first priced it, copies sold, and the model's one-month, six-month and "
                 "one-year calls on it (each has its own chart: the card's whole history, then the model's three "
                 "calls as dotted yellow)"),
    ("sales", "how many Near Mint copies of these cards actually sold in the last complete week against the week before, and which sold most"),
    ("outlook", "what to expect: how the model's one-month calls on the group split, its median one-month, "
                "six-month and one-year calls beside what the group actually did over the past year and what the "
                "age path shows, and the model's own record on this game; expectations are tendencies, never advice"),
]


def history_chart(path, price, calls, name):
    """One card's whole priced history by date, in dollars, then the model's
    1M, 6M and 1Y calls as hollow dots on a dotted line at equal distances
    (the set article's comparison chart with dates for ages and no
    comparables)."""
    W, H, PL, PR, PT, PB = 560, 200, 50, 30, 30, 34
    HF = 0.62
    span = W - PL - PR
    hx = PL + HF * span
    slot = {h: PL + span * (HF + (1 - HF) * (k + 1) / 3) for k, (h, _d) in enumerate(ga.COMP_HORIZONS)}
    pts = [(date.fromisoformat(d), p) for d, p in path if p >= price * 0.05]
    if len(pts) < 2:
        return ""
    d0, d1 = pts[0][0], pts[-1][0]
    days = max((d1 - d0).days, 1)
    hist = ga.thin([((d - d0).days / days, p) for d, p in pts], 80)
    hist[-1] = (1.0, price)
    fc = [(h, price * (1 + calls[h] / 100)) for h, _d in ga.COMP_HORIZONS if calls.get(h) is not None]
    vals = [p for _x, p in hist] + [v for _h, v in fc]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.14, hi * 0.04)
    lo, hi = max(0.0, lo - pad), hi + pad
    step = next((s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
                 if (hi - lo) / s <= 6), 20000)
    ticks = [v * step for v in range(int(lo // step) + 1, int(hi // step) + 1)]
    Y = lambda v: H - PB - (H - PT - PB) * (v - lo) / (hi - lo)
    X = lambda f: PL + HF * span * f
    muted = "var(--text-muted, #8b96ad)"
    dollars = lambda v: f"${v:,.0f}" if v >= 20 else f"${v:,.2f}"
    out = [f"<svg class='report-compare-chart' viewBox='0 0 {W} {H}' role='img' "
           f"xmlns='http://www.w3.org/2000/svg'><title>{esc(name)}: price history and the model's calls</title>"]
    for v in ticks:
        out.append(f"<line x1='{PL}' y1='{Y(v):.1f}' x2='{W - PR}' y2='{Y(v):.1f}' "
                   f"stroke='var(--border, #2e3a52)' opacity='0.55'/>"
                   f"<text class='report-compare-tick' x='{PL - 6}' y='{Y(v) + 3.5:.1f}' text-anchor='end' "
                   f"font-size='10' fill='{muted}'>${v:,.0f}</text>")
    out.append(f"<line x1='{hx:.1f}' y1='{PT - 8}' x2='{hx:.1f}' y2='{H - PB}' stroke='{muted}' opacity='0.45'/>")
    out.append(f"<text class='report-compare-tick' x='{PL}' y='{H - PB + 16}' font-size='10' fill='{muted}'>{d0.strftime('%b %Y')}</text>")
    out.append(f"<text class='report-compare-tick' x='{hx:.1f}' y='{H - PB + 16}' text-anchor='middle' font-size='10' fill='{muted}'>today</text>")
    for (h, _d), lab in zip(ga.COMP_HORIZONS, ("+1M", "+6M", "+1Y")):
        out.append(f"<text class='report-compare-tick' x='{slot[h]:.1f}' y='{H - PB + 16}' text-anchor='middle' font-size='10' fill='{muted}'>{lab}</text>")
    out.append(f"<text class='report-compare-tick' x='{W - PR:.1f}' y='{PT - 12}' text-anchor='end' font-size='10' "
               f"fill='{muted}'>{'dotted = model forecast' if fc else 'no model call for this card yet'}</text>")
    out.append(f"<polyline points='{' '.join(f'{X(f):.1f},{Y(p):.1f}' for f, p in hist)}' fill='none' "
               "stroke='#ffcb05' stroke-width='2.2'/>")
    if fc:
        line = [(hx, price)] + [(slot[h], v) for h, v in fc]
        out.append(f"<polyline points='{' '.join(f'{x:.1f},{Y(v):.1f}' for x, v in line)}' fill='none' "
                   "stroke='#ffcb05' stroke-width='2' stroke-dasharray='2 5' stroke-linecap='round'/>")
    out.append(f"<circle cx='{hx:.1f}' cy='{Y(price):.1f}' r='3.5' fill='#ffcb05'/>")
    out.append(f"<text class='report-compare-num' x='{hx - 6:.1f}' y='{max(PT - 2, Y(price) - 9):.1f}' text-anchor='end' "
               f"font-size='11' font-weight='600' fill='var(--text, #e8edf5)'>{dollars(price)}</text>")
    for h, v in fc:
        y = Y(v)
        out.append(f"<circle cx='{slot[h]:.1f}' cy='{y:.1f}' r='3.5' fill='var(--panel, #141c2e)' stroke='#ffcb05' stroke-width='2'/>"
                   f"<text class='report-compare-num' x='{slot[h]:.1f}' y='{max(PT - 2, y - 11):.1f}' text-anchor='middle' "
                   f"font-size='11' font-weight='600' fill='var(--text, #e8edf5)'>{dollars(v)}</text>")
    out.append("</svg>")
    return "".join(out)


def build_type_article(d, today, no_prose=False):
    game, label, spec, members = d["game"], d["label"], d["spec"], d["members"]
    plural = spec["plural"]
    vol = d["sales"]
    vc = vol["per_card"] if vol else {}
    mc, ab, year, since = d["model_calls"], d["about"], d["year"], d["since"]
    prices = sorted(c["price"] for c in members.values())
    n, median_price, total = len(prices), statistics.median(prices), sum(prices)
    rels = sorted(c["release"] for c in members.values() if c["release"])
    idx = index_facts(d["grid"], d["index"], today)
    other = index_facts(d["grid"], d["other_index"], today) if d["other_index"] else None
    compared = f"{label}'s other cards at {money(d['other_floor'])} and up"
    rose = sum(1 for v in year.values() if v > 0.5)
    fell = sum(1 for v in year.values() if v < -0.5)

    def card_facts(pid, **extra):
        c = members[pid]
        return {"name": c["name"], "set": c["set"], "released": c["release"], "price_usd": round(c["price"], 2),
                "past_year_pct": r1(year.get(pid)), "since_first_priced_pct": r1(since.get(pid)),
                "first_priced": (d["paths"].get(pid) or [("",)])[0][0][:7] or None,
                "week_pct": r1(d["by_pid"][pid][4]) if pid in d["by_pid"] else None,
                "copies_sold_last_week": round(vc[pid][0]) if pid in vc else None,
                "model_call_1m_pct": mc.get(pid, {}).get("1m"), "model_call_6m_pct": mc.get(pid, {}).get("6m"),
                "model_call_12m_pct": mc.get(pid, {}).get("12m"), "about": ab.get(pid), **extra}

    sections = [s for s in TYPE_SECTIONS
                if not ((s[0] == "history" and not idx)
                        or (s[0] == "sales" and not (vol and vol["enough"])))]
    facts = {
        "game": label, "card_type": spec["label"], "what_it_is": spec["what"],
        "scope": (f"every priced {label} card whose title carries {spec['name_tag']}"
                  + (f", the {spec['skip_note']} left out" if spec.get("skip_note") else "")),
        "as_of": today.isoformat(),
        "totals": {"cards_priced": n, "sets": len({c["set"] for c in members.values()}),
                   "first_release": rels[0] if rels else None, "newest_release": rels[-1] if rels else None,
                   "median_price_usd": round(median_price, 2), "cheapest_usd": round(prices[0], 2),
                   "dearest_usd": round(prices[-1], 2), "group_value_usd": round(total, 2)},
        "dearest": [card_facts(pid) for pid in d["top"]],
        "ladder": [{"band": b, "cards": k} for b, k in d["ladder"]],
        "index": ({**idx, "basis": ("each month's step is the median move of the cards priced in both months, so a "
                                    "newly released card joins the month after it is first priced; index_now, peak "
                                    "and trough are against the first month; past_year, past_six_months and "
                                    "past_three_months are the change from that long ago to today"),
                   "comparison": ({"group": compared, "cards": d["others"], **other} if other else None)}
                  if idx else None),
        "past_year": ({"cards_with_a_year_of_prices": len(year), "rose": rose, "fell": fell,
                       "median_pct": r1(statistics.median(year.values()))} if year else None),
        "releases": d["waves"],
        "age_curve": ({"basis": (f"each card against its own first price within {ANCHOR_MAX_DAYS} days of its set's "
                                 "release; cards we only began pricing later are left out, so the older sets are "
                                 "thin here; 'cards' says how many stand behind each point"),
                       "points": d["curve"]} if d["curve"] else None),
        "examples": [card_facts(e["pid"], role=e["role"]) for e in d["examples"]],
        "model": {"calls_1m": d["calls"],
                  "median_calls": {h: {"pct": r1(v), "cards": k} for h, (v, k) in d["medians"].items()},
                  "top_climbs_1m": [{"name": nm, "current_usd": round(now, 2), "forecast_usd": round(fc, 2),
                                     "implied_pct": r1(imp)} for _pid, nm, now, fc, imp in d["picks"]],
                  "record_on_this_game": d["record"]},
        "context": {"compared_against": compared} if other else None,
    }
    if vol and vol["enough"]:
        facts["sales"] = ga.sales_facts(vol, plural)
    prose = {} if no_prose else (write_type_article(prune(facts), sections) or {})

    yr = idx["past_year_pct"] if idx else None
    title = (f"{label} {plural}: {pct(yr)} over the past year across {n} cards" if yr is not None
             else f"{label} {plural}: {n} priced, median {money(median_price)}")
    lede = (f"{label} has {n} priced {plural} across {facts['totals']['sets']} sets, at a median "
            f"{money(median_price)} and {money(total)} for the lot."
            + (f" As a group they are {pct(yr)} against a year ago." if yr is not None else ""))
    title = prose.get("headline") or title
    lede = prose.get("lede") or lede

    body = [f"<p class='report-lede'>{esc(lede)} Ungraded market prices as of {today.isoformat()}.</p>",
            f"<p class='report-note'>{esc(facts['scope'][0].upper() + facts['scope'][1:])}. A card type report "
            "follows one kind of card through its whole priced history, where a set report follows one release "
            "and a game report one week.</p>"]

    # The group today
    def change(pid):
        v = year.get(pid)
        if v is None:
            return ""
        return f" · <span class='report-{'up' if v >= 0 else 'down'}'>{pct(v)}</span> in a year"
    tiles = [ga.card_tile(game, pid, members[pid]["name"], f"{money(members[pid]['price'])}{change(pid)}")
             for pid in d["top"] if ga.has_art(game, pid)]
    t0 = members[d["top"][0]]
    body.append(ga.section(
        prose, "group", f"What a {spec['label']} Card Is Worth",
        f"The {n} priced {plural} run from {money(prices[0])} to {money(prices[-1])}, median {money(median_price)}. "
        f"{t0['name']} is the dearest at {money(t0['price'])}.",
        ga.grid(tiles) + ga.band_line(d["ladder"], f"{label} {plural}: cards by price band"),
        f"The six dearest {plural}, then all {n} counted by price band."))

    # History
    if idx:
        series = [(plural, d["index"], "#ffcb05")]
        if other:
            series.append((f"Other {money(d['other_floor'])}+ cards", d["other_index"], "#8b96ad"))
        chart = line_chart(d["grid"], series, title=f"{label} {plural}: price index since {month_label(d['grid'][0])}",
                           xfmt=month_label)
        txt = (f"Since {month_label(idx['from'] + '-01')} the group's index stands at {pct(idx['index_now_pct'])}"
               + (f", and {pct(yr)} against a year ago" if yr is not None else "") + "."
               + (f" Of the {len(year)} cards with a year of prices, {rose} are up and {fell} are down." if year else "")
               + (f" {compared[0].upper() + compared[1:]} are {pct(other['past_year_pct'])} over the same year."
                  if other and other["past_year_pct"] is not None else ""))
        body.append(ga.section(prose, "history", "The Whole History", txt, chart,
                               f"{plural} against {compared}, month by month."))

    # Releases and the age path
    table = ["<table class='report-table'><thead><tr><th>Set</th><th>Released</th><th>Cards</th>"
             "<th>Median price</th><th>Past year</th></tr></thead><tbody>"]
    for w in d["waves"]:
        table.append(f"<tr><td>{esc(w['set'])}</td><td>{esc(w['released'] or '')}</td><td>{w['cards']}</td>"
                     f"<td>{money(w['median_price_usd'])}</td>"
                     f"<td>{pct(w['median_past_year_pct']) if w['median_past_year_pct'] is not None else ''}</td></tr>")
    table.append("</tbody></table>")
    chart = ""
    if len(d["curve"]) >= 2:
        xs = [f"{c['months_since_release']} mo" for c in d["curve"]]
        chart = line_chart(xs, [("Median card", [c["median_pct"] for c in d["curve"]], "#ffcb05"),
                                ("Upper quarter", [c["upper_quarter_pct"] for c in d["curve"]], "#7fb0ea"),
                                ("Lower quarter", [c["lower_quarter_pct"] for c in d["curve"]], "#8b96ad")],
                           title=f"{label} {plural}: price against the first price after release, by age",
                           xfmt=lambda s: s, xticks="all")
    last = d["curve"][-1] if d["curve"] else None
    body.append(ga.section(
        prose, "releases", "Release by Release",
        f"{plural} have come in {len(d['waves'])} releases since {rels[0] if rels else 'the first'}."
        + (f" {last['months_since_release']} months after release, the median card stood {pct(last['median_pct'])} "
           f"against its first price, across the {last['cards']} cards we have followed that long." if last else ""),
        chart + "".join(table),
        "The typical path by months since release, the middle half of cards between the outer lines; "
        "the table lists every release."))

    # Examples
    rows = []
    for e in d["examples"]:
        pid = e["pid"]
        c = members[pid]
        calls = mc.get(pid, {})
        meta = " · ".join(filter(None, [
            money(c["price"]),
            f"{pct(year[pid])} in a year" if pid in year else None,
            f"{vc[pid][0]:,.0f} sold last week" if pid in vc and vc[pid][0] else None]))
        rows.append(f"<div class='report-compare-row'>"
                    f"<a class='report-compare-card' href='/catalog/{game}/{pid}'>"
                    f"<img src='{CDN}/{game}/{pid}.jpg' alt='{esc(c['name'])}' loading='lazy'>"
                    f"<span class='report-compare-name'><span class='report-compare-swatch-own'></span>{esc(c['name'])}</span>"
                    f"<span class='report-compare-meta'>{esc(meta)}</span></a>"
                    f"<div class='report-compare-like'><span class='report-compare-label'>"
                    f"{esc(e['role'][0].upper() + e['role'][1:])} · {esc(c['set'] or '')}"
                    f"{' · released ' + esc(c['release']) if c['release'] else ''}</span></div>"
                    f"{history_chart(d['paths'].get(pid) or [], c['price'], calls, c['name'])}</div>")
    if rows:
        e0 = members[d["examples"][0]["pid"]]
        body.append(ga.section(
            prose, "examples", "A Few Cards That Tell the Story",
            f"{e0['name']} is {d['examples'][0]['role']} at {money(e0['price'])}.",
            f"<div class='report-compare'>{''.join(rows)}</div>",
            "Each card's whole priced history, then the model's one-month, six-month and one-year calls."))

    # Sales
    if vol and vol["enough"]:
        body.append(ga.sales_block(prose, vol, game, f"{label} {plural}", plural))

    # Outlook
    c, rec, med = d["calls"], d["record"], d["medians"]
    txt = (f"Of the {plural} with a one-month call, the model has {c['up']} headed up, "
           f"{c['down']} headed down and {c['flat']} flat.")
    if med:
        txt += " Its median call is " + ", ".join(
            f"{pct(v)} over {ga.HORIZON_LABEL[h].lower()}" for h, (v, _k) in med.items()) + "."
    if rec:
        txt += (f" On {label} its one-month forecasts have missed by {rec['typical_miss_pct']} percent "
                f"on average, against {rec['flat_baseline_miss_pct']} percent for assuming no change.")
    extra = ""
    if med:
        rows = "".join(f"<tr><td>{ga.HORIZON_LABEL[h]}</td><td>{pct(v)} ({k} cards)</td></tr>"
                       for h, (v, k) in med.items())
        extra = ("<table class='report-table'><thead><tr><th>Window</th><th>Model, median call</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table>")
    links = (f"Every card here is in the <a href='/catalog?game={game}&amp;searchTerm={esc(spec['search'])}'>catalog</a> "
             f"with its own chart and forecast; more on {esc(label)} is under "
             f"<a href='/articles?game={game}'>its articles</a>.")
    body.append(ga.model_corner(prose, "outlook", "What to Expect", txt, d["picks"][:PICKS_N], game, rec, links,
                                extra=extra))
    closer = prose.get("closer")
    if closer:
        body.append(f"<p>{esc(closer)}</p>")

    # The picture stays light (2026-11-14, user: "this is too crowded"): the
    # group's name alone as its headline, three short stats in whole
    # dollars, and the index over the past year only, so the line ends on
    # the same number the title states.
    stats = [[f"{n}", "priced cards", None], [f"${median_price:,.0f}", "median price", None]]
    stats.append([f"{yr:+.1f}%", "past year", "up" if yr >= 0 else "down"] if yr is not None
                 else [f"${total:,.0f}", "group value", None])
    cards = [[game, pid, members[pid]["name"]] for pid in d["top"] if ga.has_art(game, pid)][:3]
    drawn = hero_line(d["grid"], d["index"], today)
    hero, thumb = ga.hero_and_thumb("type", game, title, today, stats, cards,
                                    ([g for g, _v in drawn], [v for _g, v in drawn]) if drawn else None,
                                    headline=f"{label} {plural}")
    return {"slug": f"card-type-{game}-{d['type']}-{today.isoformat()}", "kind": "type", "focus": d["type"],
            "game": game, "set_name": plural, "title": title, "summary": lede,
            "body_html": "".join(body), "thumb": thumb, "hero": hero}
