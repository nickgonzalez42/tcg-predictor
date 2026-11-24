#!/usr/bin/env python3
"""
Per-set SEO pages (2026-10-09): one crawler-and-human-visible HTML page per
card set — "{set} price guide" is the long-tail query this site's data is
uniquely placed to win. Served file-first by Caddy at /sets/{game}/{slug}
(SPA has no such route, so humans get these pages too), regenerated weekly
by the refresh right after report_pages.py and rsynced to prod alongside
them.

Output:
    static-content/sets/index.html            all games
    static-content/sets/{game}.html           every set in one game
    static-content/sets/{game}/{slug}.html    one set: stats + full price list
    static-content/sets/manifest.txt          page paths, one per line — the
                                              API's /sitemap-sets.xml reads it

Run:  python3 set_pages.py [--out DIR]
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from datetime import date
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from _paths import API_CARDS
from games import GAMES

API_DATA = os.path.normpath(API_CARDS)
PRED_DB = os.path.join(API_DATA, "predictions.db")
DEFAULT_OUT = os.path.normpath(os.path.join(
    BASE, "..", "tcg-predictor", "static-content", "sets"))
SITE = "https://cardstock.guide"

# AdSense is OFF until the site is approved (2026-10-29): the loader costs
# about 250 KB and three third-party connections on every page view and
# earns nothing while the application is rejected. Flip to True after
# approval; the client app has the same switch in lib/adsense.ts.
ADSENSE_ENABLED = False
ADS = ('<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-3270328826867583" crossorigin="anonymous"></script>' if ADSENSE_ENABLED else "")
IMG = "https://d18pzythfo0bgy.cloudfront.net"
MAX_ROWS = 150          # price-list rows per set page (by price, desc)
MIN_SET_CARDS = 3       # sets smaller than this aren't worth a page

CSS = """
:root{--bg:#101623;--text:#e6ebf4;--muted:#8b96ad;--border:#2e3a52;--brand:#3d7dca;
--up:#4caf7d;--down:#e05c5c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 48px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;gap:14px;align-items:baseline;border-bottom:1px solid var(--border);
padding-bottom:12px;margin-bottom:20px}header .logo{font-weight:800;color:var(--text)}
h1{font-size:25px;margin:8px 0 4px}h2{font-size:18px;margin:26px 0 8px}
.muted{color:var(--muted);font-size:14px}
table{border-collapse:collapse;width:100%;margin:12px 0}
td,th{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;font-size:14.5px}
th{color:var(--muted);font-weight:600;font-size:12.5px}
td.num,th.num{text-align:right}
.up{color:var(--up)}.down{color:var(--down)}
footer{border-top:1px solid var(--border);margin-top:32px;padding-top:12px;
color:var(--muted);font-size:13.5px}
.crumbs{color:var(--muted);font-size:13.5px;margin:0 0 6px}
ul.plain{list-style:none;padding:0;margin:8px 0}ul.plain li{padding:3px 0}
@media (max-width:540px){td,th{padding:5px 5px;font-size:12.5px}}
"""

NAV = (f'<header><a class="logo" href="{SITE}/">CardStock</a>'
       f'<nav><a href="{SITE}/catalog">Catalog</a> <a href="{SITE}/sets">Sets</a> '
       f'<a href="{SITE}/reports">Reports</a> <a href="{SITE}/articles">Articles</a> '
       f'<a href="{SITE}/guides">Guides</a> <a href="{SITE}/about">About</a></nav></header>')
FOOT = (f'<footer><p>CardStock tracks trading card prices and publishes model forecasts '
        f'with a public track record. Not financial advice. '
        f'<a href="{SITE}/about">How the forecasts work</a> · '
        f'<a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a></p></footer>')


def esc(s):
    return html.escape(str(s), quote=True)


def money(v):
    return f"${v:,.2f}" if v is not None else "—"


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:80] or "set"


# Favicon set (2026-11-02). Google Search reads the icon from the HOME page
# only, and crawlers get THIS prerendered copy, which used to declare no
# icon at all; Google then fell back to /favicon.ico and got the SPA shell.
# Search supports ICO/PNG but not SVG, so the raster files come first.
ICONS = """<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">"""

# First-party page-view beacon, the same ping the app sends from
# client/src/lib/pageview.ts (2026-11-06): these static pages are what a
# shared link lands on (Caddy serves them file-first), and without the ping
# every click from a Reddit post or a search result was invisible to
# deploy/traffic.sh. Caddy answers /px itself with 204; the path, the outside
# referrer and any utm tags ride along, nothing else.
PX = ('<script>(function(){try{var q=new URLSearchParams({p:location.pathname});'
      'var r=document.referrer;if(r&&r.indexOf(location.host)<0)q.set("r",r);'
      'var u=location.search.match(/utm_[a-z]+=[^&]+/g);if(u)q.set("u",u.join("&"));'
      'fetch("/px?"+q.toString(),{method:"GET",keepalive:true,cache:"no-store"})'
      '.catch(function(){})}catch(e){}})()</script>')


def crumb_ld(trail):
    """trail = [(name, path)], the page itself last."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n,
                                 "item": f"{SITE}{p}"} for i, (n, p) in enumerate(trail)]}


def list_ld(name, items):
    """items = [(name, path)]: the cards or sets a page ranks, in order."""
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name,
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n,
                                 "url": f"{SITE}{p}"} for i, (n, p) in enumerate(items)]}


def crumbs(trail):
    """The visible trail: every step but the last is a link."""
    return ('<p class="crumbs">' + " › ".join(
        f'<a href="{SITE}{p}">{esc(n)}</a>' for n, p in trail[:-1])
        + f" › {esc(trail[-1][0])}</p>")


def page(path, title, desc, body, og_image=None, ld=None):
    t, d = esc(title), esc(desc)
    # Every page carries a share picture and its place in the site as
    # structured data (2026-11-24).
    og_image = og_image or f"{SITE}/og-image.png?v=3"
    og_img = (f'<meta property="og:image" content="{esc(og_image)}">'
              f'<meta name="twitter:card" content="summary_large_image">')
    og_img += "".join(
        '<script type="application/ld+json">'
        + json.dumps(it, ensure_ascii=False).replace("</", "<\\/") + "</script>"
        for it in (ld or []))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t} | CardStock</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{SITE}{path}">
{ICONS}
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">
<meta property="og:url" content="{SITE}{path}"><meta property="og:site_name" content="CardStock">
{og_img}
{ADS}
<style>{CSS}</style></head>
<body><div class="wrap">{NAV}{body}{FOOT}</div>{PX}</body></html>"""


def write(out, rel, content):
    p = os.path.join(out, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    ap = argparse.ArgumentParser(description="Generate per-set SEO pages")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    pred = sqlite3.connect(PRED_DB)
    fc1m = {}
    try:
        for g, pid, base, f in pred.execute(
                "SELECT game, product_id, base_price, forecast_price FROM forecasts "
                "WHERE target='ungraded' AND horizon='1m' AND printing=''"):
            fc1m[(g, pid)] = (base, f)
    except sqlite3.Error:
        pass
    articles = {}
    try:
        for g, slug, title in pred.execute(
                "SELECT game, slug, title FROM articles ORDER BY published_at DESC, slug"):
            articles.setdefault(g, []).append((slug, title))
    except sqlite3.Error:
        pass
    pred.close()

    manifest = []
    today = date.today().strftime("%B %Y")
    game_rows = []
    for game, cfg in GAMES.items():
        con = sqlite3.connect(os.path.join(BASE, cfg["db"]))
        cards = con.execute(
            "SELECT set_name, product_id, name, rarity, near_mint_price FROM cards "
            "WHERE near_mint_price IS NOT NULL AND name IS NOT NULL "
            "AND set_name IS NOT NULL AND set_name != ''").fetchall()
        con.close()
        by_set = {}
        for s, pid, name, rarity, nm in cards:
            by_set.setdefault(s.strip(), []).append((pid, name, rarity, nm))
        # The game's dearest cards, whatever their set, for its hub page.
        dearest = sorted(((nm, pid, name, s.strip()) for s, pid, name, rarity, nm in cards
                          if nm), reverse=True)[:20]

        label = GAMES[game]["label"]
        slugs, set_rows = {}, []
        for set_name, members in sorted(by_set.items()):
            if len(members) < MIN_SET_CARDS:
                continue
            slug = slugify(set_name)
            while slug in slugs:          # rare collision: distinct names, same slug
                slug += "-2"
            slugs[slug] = set_name
            members.sort(key=lambda m: -(m[3] or 0))
            total = sum(m[3] or 0 for m in members)
            chase = members[0]

            rows = []
            for pid, name, rarity, nm in members[:MAX_ROWS]:
                base_f = fc1m.get((game, pid))
                if base_f and base_f[0]:
                    imp = (base_f[1] / base_f[0] - 1) * 100
                    cls = "up" if imp > 0 else "down"
                    # The call applied to the card's price today, not the
                    # dollar target set at the model's anchor (2026-11-06).
                    fcell = (f"<td class='num'>{money(nm * base_f[1] / base_f[0] if nm else base_f[1])}</td>"
                             f"<td class='num {cls}'>{imp:+.1f}%</td>")
                else:
                    fcell = "<td class='num'>—</td><td class='num'>—</td>"
                rows.append(
                    f"<tr><td><a href='{SITE}/catalog/{game}/{pid}'>{esc(name)}</a></td>"
                    f"<td>{esc(rarity or '')}</td><td class='num'>{money(nm)}</td>{fcell}</tr>")

            more = (f"<p class='muted'>Showing the top {MAX_ROWS} of "
                    f"{len(members):,} priced cards.</p>" if len(members) > MAX_ROWS else "")
            cat_link = (f"{SITE}/catalog?game={game}&sets={quote(set_name, safe='')}"
                        if "," not in set_name else f"{SITE}/catalog?game={game}")
            path = f"/sets/{game}/{slug}"
            trail = [("CardStock", "/"), (f"{label} card prices", f"/sets/{game}"),
                     (set_name, path)]
            # The question the page answers, in a sentence (2026-11-24):
            # "most valuable cards in {set}" is what people search, and a
            # sentence is what a search snippet or an AI assistant quotes.
            top5 = members[:5]
            top_line = "; ".join(
                f"<a href='{SITE}/catalog/{game}/{pid}'>{esc(name)}</a> ({money(nm)})"
                for pid, name, _rar, nm in top5)
            body = (
                crumbs(trail) +
                f"<h1>{esc(set_name)} card prices</h1>"
                f"<p class='muted'>{esc(label)} · {len(members):,} priced cards · "
                f"prices updated {today}</p>"
                f"<p>Every priced card in {esc(set_name)}, with its TCGplayer Near Mint "
                f"market price and our model's 1-month price forecast. The set's priced "
                f"cards total {money(total)}; the most valuable is "
                f"<a href='{SITE}/catalog/{game}/{chase[0]}'>{esc(chase[1])}</a> at "
                f"{money(chase[3])}. Click any card for its full price history, graded "
                f"ladder and forecast track record, or "
                f"<a href='{cat_link}'>browse this set in the interactive catalog</a>.</p>"
                f"<h2>Most valuable cards in {esc(set_name)}</h2>"
                f"<p>The {len(top5)} most valuable cards in {esc(set_name)} right now are "
                f"{top_line}.</p>"
                f"<h2>{esc(set_name)} price list</h2>"
                f"<table><tr><th>Card</th><th>Rarity</th><th class='num'>NM price</th>"
                f"<th class='num'>1M forecast</th><th class='num'>Implied</th></tr>"
                f"{''.join(rows)}</table>{more}"
                f"<p><a href='{SITE}/sets/{game}'>Every {esc(label)} set, with its total "
                f"value →</a></p>")
            desc = (f"{set_name} ({label}) price guide: {len(members):,} cards priced, "
                    f"total value {money(total)}. The most valuable is {chase[1]} at "
                    f"{money(chase[3])}. Near Mint prices and AI 1-month forecasts.")
            write(args.out, f"{game}/{slug}.html",
                  page(path, f"{set_name} Card Prices & Most Valuable Cards ({label})",
                       desc, body, og_image=f"{IMG}/{game}/{chase[0]}.jpg",
                       ld=[crumb_ld(trail),
                           list_ld(f"Most valuable cards in {set_name}",
                                   [(name, f"/catalog/{game}/{pid}")
                                    for pid, name, _rar, _nm in members[:10]])]))
            manifest.append(path)
            set_rows.append((set_name, slug, len(members), total))

        set_rows.sort(key=lambda r: -r[3])
        idx_rows = "".join(
            f"<tr><td><a href='{SITE}/sets/{game}/{slug}'>{esc(name)}</a></td>"
            f"<td class='num'>{n:,}</td><td class='num'>{money(total)}</td></tr>"
            for name, slug, n, total in set_rows)
        # The game's own page (2026-11-24): what card pages and set pages
        # link up to, so it answers the game-level questions too: what the
        # dearest cards are and what has been written about the game lately.
        slug_of = {name: slug for slug, name in slugs.items()}
        dear_rows = "".join(
            f"<tr><td><a href='{SITE}/catalog/{game}/{pid}'>{esc(name)}</a></td>"
            + (f"<td><a href='{SITE}/sets/{game}/{slug_of[sn]}'>{esc(sn)}</a></td>"
               if sn in slug_of else f"<td>{esc(sn)}</td>")
            + f"<td class='num'>{money(nm)}</td></tr>"
            for nm, pid, name, sn in dearest)
        art_rows = "".join(
            f"<li><a href='{SITE}/articles/{esc(slug)}'>{esc(title)}</a></li>"
            for slug, title in articles.get(game, [])[:6])
        n_priced = sum(n for _n, _s, n, _t in set_rows)
        hub_trail = [("CardStock", "/"), ("Set price guides", "/sets"),
                     (f"{label} card prices", f"/sets/{game}")]
        write(args.out, f"{game}.html", page(
            f"/sets/{game}", f"{label} Card Prices: Price Guides for Every Set",
            f"{label} card prices by set: {len(set_rows)} sets and {n_priced:,} priced cards, "
            f"with the most valuable {label} cards, Near Mint prices and AI forecasts.",
            crumbs(hub_trail) +
            f"<h1>{esc(label)} card prices by set</h1>"
            f"<p>Every {esc(label)} set we track, with the number of priced cards and "
            f"the set's total Near Mint value: {len(set_rows)} sets and {n_priced:,} priced "
            f"cards. Prices updated {today}.</p>"
            + (f"<h2>Most valuable {esc(label)} cards right now</h2>"
               f"<table><tr><th>Card</th><th>Set</th><th class='num'>NM price</th></tr>"
               f"{dear_rows}</table>" if dear_rows else "")
            + f"<h2>{esc(label)} sets by total value</h2>"
            f"<table><tr><th>Set</th><th class='num'>Priced cards</th>"
            f"<th class='num'>Total value</th></tr>{idx_rows}</table>"
            + (f"<h2>Latest {esc(label)} articles</h2><ul class='plain'>{art_rows}</ul>"
               if art_rows else "")
            + f"<p><a href='{SITE}/catalog?game={game}'>Browse {esc(label)} cards in the "
              f"interactive catalog →</a></p>",
            ld=[crumb_ld(hub_trail),
                list_ld(f"Most valuable {label} cards",
                        [(name, f"/catalog/{game}/{pid}") for _nm, pid, name, _sn in dearest[:10]])]))
        manifest.append(f"/sets/{game}")
        game_rows.append((label, game, len(set_rows)))

    write(args.out, "index.html", page(
        "/sets", "Trading Card Set Price Guides: Every Set, Every Game",
        "Set-by-set price guides for " + ", ".join(label for label, _g, _n in game_rows[:8])
        + " and more: every priced card with its market price and forecast.",
        "<h1>Set price guides</h1>"
        "<p>Pick a game to browse set-by-set price guides — every priced card with "
        "its market price and our model's 1-month forecast.</p>"
        "<table><tr><th>Game</th><th class='num'>Sets</th></tr>" + "".join(
            f"<tr><td><a href='{SITE}/sets/{g}'>{esc(label)}</a></td>"
            f"<td class='num'>{n}</td></tr>" for label, g, n in game_rows) + "</table>"))
    manifest.append("/sets")

    write(args.out, "manifest.txt", "\n".join(manifest) + "\n")
    print(f"wrote {len(manifest):,} set page(s) -> {args.out}")


if __name__ == "__main__":
    main()
