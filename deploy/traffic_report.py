#!/usr/bin/env python3
"""
Who visited cardstock.guide, and what did they read?

Reads Caddy's JSON access log (enabled 2026-10-27) ON THE SERVER — stdlib
only, same as prerender_pages.py — and prints a readable summary.

The site is a single-page app: clicking from a card to the catalog makes no
document request, so counting HTML hits would undercount wildly. The API
call behind each view is the reliable signal, so this maps API paths back to
the page a person was looking at:

    /api/cards/pokemon/84186        -> card page  (resolved to the card's name)
    /api/cards?game=magic&...       -> catalog, Magic
    /api/home/stats, /api/cards/movers -> home
    /api/market-reports/<slug>      -> that week's report

Visitors are counted as distinct (IP, browser) pairs per day, never stored —
the log is the only copy and Caddy rolls it. Bots, the uptime health check
and the server's own warm-up calls are excluded from the people-facing
numbers and reported separately.

Run (from the Mac):  deploy/traffic.sh            # today
                     deploy/traffic.sh 7          # last 7 days
On the box:          python3 /srv/tcg/traffic_report.py --days 7
"""

import argparse
import glob
import gzip
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

LOG_GLOB = "/var/log/caddy/access.log*"
CARDS_DIR = "/srv/tcg/data/cards"
DATA_DIR = "/srv/tcg/data"

# A request from one of these is never a person.
# A request from one of these is never a person. Note the hyphens: the
# uptime check calls itself "Amazon-Route53-Health-Check-Service", and a
# spaced pattern missed it entirely on the first run (2026-10-27) — 2,880
# health checks a day then counted as home-page views.
BOT_RE = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|bingpreview|facebookexternalhit|embedly|quora|"
    r"pinterest|vkshare|w3c_validator|headless|python-requests|curl/|wget|go-http|"
    r"okhttp|axios|libwww|scrapy|semrush|ahrefs|mj12|dotbot|petalbot|bytespider|"
    r"gptbot|claudebot|perplexity|ccbot|amazonbot|route\s*-?53|health-?check|"
    r"uptime|monitor|pingdom|statuscake|datadog|newrelic|lighthouse|playwright|"
    r"puppeteer|phantomjs|selenium",
    re.I)

# A readable name for a non-human caller: the first token that is not the
# "Mozilla/5.0" camouflage every agent wears.
def bot_name(ua):
    generic = ("mozilla", "applewebkit", "khtml", "like", "gecko", "chrome", "safari",
               "version", "mobile", "macintosh", "windows", "x11", "linux", "intel",
               "mac", "wow64", "win64", "android", "iphone", "ipad", "compatible")
    toks = re.split(r"[ ;()]+", ua.strip())
    # A product token (name/version) names the agent better than the OS does.
    for tok in toks:
        if "/" in tok and not tok.split("/")[0].lower() in generic:
            return tok.split("/")[0][:30]
    for tok in toks:
        t = tok.split("/")[0]
        if t and t.lower() not in generic:
            return t[:30]
    return (ua.strip() or "(none)")[:30]
GAME_LABEL = {
    "pokemon": "Pokémon", "magic": "Magic", "yugioh": "Yu-Gi-Oh!", "onepiece": "One Piece",
    "lorcana": "Lorcana", "digimon": "Digimon", "gundam": "Gundam",
    "starwars": "Star Wars Unlimited", "fleshandblood": "Flesh and Blood",
    "sorcery": "Sorcery", "riftbound": "Riftbound", "cyberpunk": "Cyberpunk",
}
CARD_RE = re.compile(r"^/api/cards/([a-z]+)/(\d+)(?:/|$)")
# ONLY the list endpoint itself. A loose /api/cards prefix also swallowed
# /api/cards/movers and /api/cards/filters, so the home page's own feeds got
# counted as catalog visits (2026-10-27).
LIST_RE = re.compile(r"^/api/cards$")
# Supporting calls that ride along with a page someone is already on; counting
# them would double-count the view that triggered them.
IGNORE = ("/api/cards/filters", "/api/account/", "/api/watchlist", "/api/portfolio")
REPORT_RE = re.compile(r"^/api/market-reports/(.+)$")
HOME_API = ("/api/home/stats", "/api/home/scorecard", "/api/cards/movers")


def card_names(wanted):
    """{(game, id): name} for the cards that actually appeared, read from the
    per-game catalog DBs. Missing DB or row just leaves the id showing."""
    out = {}
    by_game = defaultdict(list)
    for game, pid in wanted:
        by_game[game].append(pid)
    for game, pids in by_game.items():
        db = os.path.join(DATA_DIR, f"{game}_cards.db")
        if not os.path.exists(db):
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            for chunk in (pids[i:i + 400] for i in range(0, len(pids), 400)):
                q = f"SELECT product_id, name FROM cards WHERE product_id IN ({','.join('?' * len(chunk))})"
                for pid, name in conn.execute(q, chunk):
                    out[(game, pid)] = name
            conn.close()
        except sqlite3.Error:
            pass
    return out


def page_of(path):
    """The page a request represents, or None when it isn't a page view."""
    path = path.split("?")[0] if path.startswith("/api/cards/") else path
    m = CARD_RE.match(path)
    if m:
        return ("card", m.group(1), int(m.group(2)))
    m = REPORT_RE.match(path.split("?")[0])
    if m:
        return ("report", m.group(1), None)
    if any(path.startswith(h) for h in HOME_API):
        return ("home", None, None)
    if any(path.startswith(h) for h in IGNORE):
        return None
    if LIST_RE.match(path):
        return ("catalog", None, None)
    # Document requests: the first load of any route, and every crawler hit.
    if not path.startswith("/api/") and not path.startswith("/card-images/") \
            and "." not in path.rsplit("/", 1)[-1]:
        return ("page", path or "/", None)
    return None


def catalog_game(uri):
    m = re.search(r"[?&]game=([a-z]+)", uri)
    return m.group(1) if m else None


def read_lines(days):
    """Every log line newer than `days` ago, oldest file first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(glob.glob(LOG_GLOB)):
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt", errors="ignore") as f:
                for line in f:
                    if not line.startswith("{"):
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    ts = e.get("ts")
                    if not ts or datetime.fromtimestamp(ts, timezone.utc) < cutoff:
                        continue
                    yield e
        except OSError:
            continue


def main():
    ap = argparse.ArgumentParser(description="Visitor + page report from Caddy's access log")
    ap.add_argument("--days", type=float, default=1, help="how far back to look (default: 1)")
    ap.add_argument("--top", type=int, default=15, help="rows per table")
    args = ap.parse_args()

    people = set()              # (day, ip, ua)
    per_day = defaultdict(set)
    cards, catalogs, pages, reports = Counter(), Counter(), Counter(), Counter()
    referrers, campaigns, bots = Counter(), Counter(), Counter()
    hours = Counter()
    bot_hits = people_hits = 0

    for e in read_lines(args.days):
        req = e.get("request", {})
        ua = (req.get("headers", {}).get("User-Agent") or [""])[0]
        uri = req.get("uri", "")
        path = uri.split("?")[0]
        ip = req.get("remote_ip", "")
        when = datetime.fromtimestamp(e["ts"], timezone.utc)

        if BOT_RE.search(ua) or not ua:
            bot_hits += 1
            if page_of(path):
                bots[bot_name(ua)] += 1
            continue

        kind = page_of(path)
        if kind is None:
            continue
        people_hits += 1
        day = when.strftime("%Y-%m-%d")
        people.add((day, ip, ua))
        per_day[day].add((ip, ua))
        hours[when.strftime("%Y-%m-%d %H:00")] += 1

        what, a, b = kind
        if what == "card":
            # A card page fires several API calls (details, history, forecast,
            # past forecasts). Count the details call only, so "views" means
            # views and not requests.
            if not path.rstrip("/").endswith(str(b)):
                continue
            cards[(a, b)] += 1
        elif what == "catalog":
            catalogs[catalog_game(uri) or "all"] += 1
        elif what == "report":
            reports[a] += 1
        elif what == "home":
            pages["/"] += 1
        else:
            pages[a] += 1

        ref = (req.get("headers", {}).get("Referer") or [""])[0]
        if ref and "cardstock.guide" not in ref:
            referrers[re.sub(r"^https?://(www\.)?", "", ref).split("/")[0]] += 1
        m = re.search(r"[?&]utm_source=([^&]+).*?(?:[?&]utm_campaign=([^&]+))?", uri)
        if m:
            campaigns[f"{m.group(1)}/{m.group(2) or '-'}"] += 1

    names = card_names(list(cards))
    span = f"last {args.days:g} day(s)"
    print(f"\nCardStock traffic — {span}, through {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("=" * 72)
    if not people_hits:
        print(f"\nNo human page views yet. ({bot_hits:,} bot/monitor requests seen.)")
        print("If the log was only just enabled, data starts from that moment.\n")
        return

    print(f"\n  visitors (distinct browsers)   {len({(i, u) for _d, i, u in people}):>8,}")
    print(f"  page views                     {people_hits:>8,}")
    print(f"  bot / monitor requests         {bot_hits:>8,}  (excluded above)")

    if len(per_day) > 1:
        print("\n  By day")
        for day in sorted(per_day):
            print(f"    {day}   {len(per_day[day]):>5,} visitors")

    def table(title, counter, fmt=str, n=args.top):
        if not counter:
            return
        print(f"\n  {title}")
        for key, c in counter.most_common(n):
            print(f"    {c:>6,}  {fmt(key)}")

    table("Top cards", cards, lambda k: f"{names.get(k, f'#{k[1]}')} ({GAME_LABEL.get(k[0], k[0])})")
    table("Catalog by game", catalogs, lambda k: GAME_LABEL.get(k, k))
    table("Other pages", pages)
    table("Weekly reports", reports)
    table("Referrers", referrers)
    table("Campaigns (utm_source/utm_campaign)", campaigns)
    table("Busiest hours (UTC)", hours, n=8)
    table("Bots crawling pages", bots, n=8)
    print()


if __name__ == "__main__":
    main()
