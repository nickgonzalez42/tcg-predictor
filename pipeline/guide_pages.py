#!/usr/bin/env python3
"""The guides at /guides (2026-11-24).

Two kinds of guide, one template:
  - written guides: a file each in pipeline/guides_src/ (a comment block of
    title / description / h1 / date, then the body), and
  - guides built from our own numbers (DATA_GUIDES below), re-counted on
    every run so the page is never older than last night's prices.

Every page wears the app's own nav (static_nav.py), ends on the front-page
block, and carries its structured data. The guides used to be six hand-made
HTML files with a one-line text header, shipped only by a deploy.

Output: {out}/{slug}.html and {out}/index.html. The nightly renders and
ships them with the report, article and set pages (local_model_run.sh); a
deploy renders the written ones (--written-only: no database is read) so a
new guide does not wait for the night.

    guide_pages.py [--out DIR] [--written-only]
"""
import argparse
import html
import json
import os
import re
import sqlite3
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from static_nav import nav_html, nav_css, cta_html, promo_html, CTA_CSS, NAV_JS

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "guides_src")
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "tcg-backups", "guide_pages")
SITE = "https://cardstock.guide"
ORG = {"@type": "Organization", "name": "CardStock", "url": f"{SITE}/"}

# Listed on /guides in this order; a slug not named here goes last.
ORDER = ["faq", "psa-10-vs-raw", "card-prices-after-release", "where-card-prices-come-from",
         "how-to-read-price-charts", "how-grading-affects-value", "tcg-glossary"]

CSS = """
:root{--bg:#101623;--panel:#161e2e;--text:#e6ebf4;--text-muted:#8b96ad;
--border:#2e3a52;--brand:#3d7dca;--report-up:#4caf7d;--report-down:#e05c5c;
--accent:#c678dd}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 48px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
header.site{display:flex;gap:16px;align-items:baseline;border-bottom:1px solid var(--border);
padding-bottom:12px;margin-bottom:20px}header.site .logo{font-weight:800;font-size:19px;color:var(--text)}
header.site nav{display:flex;gap:14px;font-size:14px}
h1{font-size:26px;line-height:1.25;margin:8px 0 4px}h2{font-size:19px;margin:28px 0 10px}
.published{color:var(--text-muted);font-size:13.5px;margin:0 0 18px}
.report-lede{color:var(--text-muted);font-size:17px}
.report-note{color:var(--text-muted);font-size:13.5px;border-left:3px solid var(--border);
padding-left:10px}
.report-up{color:var(--report-up)}.report-down{color:var(--report-down)}
.crumbs{color:var(--text-muted);font-size:13.5px;margin:0 0 6px}
table{border-collapse:collapse;width:100%;margin:12px 0}
td,th{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;font-size:14.5px}
th{color:var(--text-muted);font-weight:600;font-size:12.5px}
td.num,th.num{text-align:right}
footer.site{border-top:1px solid var(--border);margin-top:36px;padding-top:14px;
color:var(--text-muted);font-size:13.5px}
@media (max-width:540px){td,th{padding:5px 5px;font-size:12.5px}}
"""

# The same first-party page-view ping the other static pages send.
PX = ('<script>(function(){try{var q=new URLSearchParams({p:location.pathname});'
      'var r=document.referrer;if(r&&r.indexOf(location.host)<0)q.set("r",r);'
      'var u=location.search.match(/utm_[a-z]+=[^&]+/g);if(u)q.set("u",u.join("&"));'
      'fetch("/px?"+q.toString(),{method:"GET",keepalive:true,cache:"no-store"})'
      '.catch(function(){})}catch(e){}})()</script>')


def esc(s):
    return html.escape(str(s), quote=True)


def ld(obj):
    return ('<script type="application/ld+json">'
            + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def read_source(path):
    """({title, description, h1, date, faq}, body html) from a guides_src file."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = re.match(r"\s*<!--(.*?)-->\s*", raw, re.S)
    if not m:
        raise ValueError(f"{path}: no header comment")
    meta = {}
    for line in m.group(1).strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    for need in ("title", "description"):
        if not meta.get(need):
            raise ValueError(f"{path}: no {need}")
    return meta, raw[m.end():].strip()


def faq_pairs(body):
    """[(question, answer text)] from <h2>question</h2><p>answer</p> pairs."""
    return [(text_of(q), text_of(a)) for q, a in
            re.findall(r"<h2>(.*?)</h2>\s*<p>(.*?)</p>", body, re.S)]


def page(slug, meta, body, others):
    """others: [(slug, short name)] for the "more guides" line."""
    url = f"{SITE}/guides/{slug}"
    title, desc = meta["title"], meta["description"]
    h1 = meta.get("h1") or title
    day = meta.get("date") or date.today().isoformat()
    blocks = []
    if meta.get("faq"):
        blocks.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faq_pairs(body)]})
    else:
        blocks.append({"@context": "https://schema.org", "@type": "Article", "headline": title,
                       "description": desc, "datePublished": meta.get("published") or day,
                       "dateModified": day, "author": ORG, "publisher": ORG, "url": url,
                       "mainEntityOfPage": url, "isAccessibleForFree": True})
    blocks.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "CardStock", "item": f"{SITE}/"},
        {"@type": "ListItem", "position": 2, "name": "Guides", "item": f"{SITE}/guides"},
        {"@type": "ListItem", "position": 3, "name": h1, "item": url}]})
    more = " · ".join(f'<a href="{SITE}/guides/{s}">{esc(n)}</a>' for s, n in others if s != slug)
    updated = (f'<p class="published">Updated {date.fromisoformat(day):%B} '
               f'{date.fromisoformat(day).day}, {date.fromisoformat(day).year}</p>'
               if meta.get("dated") else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)} | CardStock Guides</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">
<meta property="og:type" content="article">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="CardStock">
<meta property="og:image" content="{SITE}/og-image.png?v=3">
<meta name="twitter:card" content="summary_large_image">
{chr(10).join(ld(b) for b in blocks)}
<style>{CSS}</style>
<style>{nav_css()}{CTA_CSS}</style>
</head>
<body>
<div class="wrap">
{nav_html()}
<article>
<p class="crumbs"><a href="{SITE}/">CardStock</a> › <a href="{SITE}/guides">Guides</a> › {esc(h1)}</p>
<h1>{esc(h1)}</h1>
{updated}
{body}

</article>
{cta_html()}{promo_html()}
<footer class="site">
  <p>More guides: {more}</p>
  <p>CardStock tracks trading card prices and publishes model forecasts with a public
  track record. Not financial advice. <a href="{SITE}/about">How the forecasts work</a> ·
  <a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a></p>
</footer>
</div>
{PX}
<script>{NAV_JS}</script>
</body>
</html>
"""


def index_page(items):
    """items: [(slug, h1, description)] in listing order."""
    title = "Trading Card Price Guides: How Card Prices Work"
    desc = ("Plain-English guides to trading card prices: what your card is worth, where prices "
            "come from, what grading does to value, and how prices move after a set releases.")
    rows = "\n".join(f'<h2><a href="{SITE}/guides/{s}">{esc(h)}</a></h2>\n<p>{esc(d)}</p>'
                     for s, h, d in items)
    blocks = [
        {"@context": "https://schema.org", "@type": "ItemList", "name": "CardStock guides",
         "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": h,
                              "url": f"{SITE}/guides/{s}"} for i, (s, h, _d) in enumerate(items)]},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "CardStock", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Guides", "item": f"{SITE}/guides"}]}]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)} | CardStock</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{SITE}/guides">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{SITE}/guides">
<meta property="og:site_name" content="CardStock">
<meta property="og:image" content="{SITE}/og-image.png?v=3">
<meta name="twitter:card" content="summary_large_image">
{chr(10).join(ld(b) for b in blocks)}
<style>{CSS}</style>
<style>{nav_css()}{CTA_CSS}</style>
</head>
<body>
<div class="wrap">
{nav_html()}
<article>
<h1>Guides</h1>

<p class="report-lede">Short, plain-English explainers on how the card market works and how
to read the numbers on this site.</p>
{rows}

</article>
{cta_html()}{promo_html()}
<footer class="site">
  <p>CardStock tracks trading card prices and publishes model forecasts with a public
  track record. Not financial advice. <a href="{SITE}/about">How the forecasts work</a> ·
  <a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a></p>
</footer>
</div>
{PX}
<script>{NAV_JS}</script>
</body>
</html>
"""


# ---- guides built from our own numbers ---------------------------------------
# Each entry: slug -> (meta, builder). The meta is fixed text, so a
# --written-only run can still list the guide on /guides; the builder reads
# the databases and returns the body, or None when there is too little to say.

RAW_FLOOR = 10.0        # a raw price under this is mostly noise against a slab
MIN_CARDS = 40          # a game needs this many cards for a row of its own
BANDS = [(10, 25, "$10 to $25"), (25, 100, "$25 to $100"), (100, 500, "$100 to $500"),
         (500, None, "$500 and up")]


def times(x):
    return f"{x:.1f}x" if x < 10 else f"{x:.0f}x"


def graded_multiples():
    """{game: [(raw, grade9 or None, psa10 or None)]} for cards with a raw
    Near Mint price of RAW_FLOOR or more and at least one of the two grades."""
    from _paths import PC_DB
    from games import GAMES, db_path
    pc = sqlite3.connect(f"file:{os.path.normpath(PC_DB)}?mode=ro", uri=True, timeout=60)
    slabs = {}
    for g, pid, g9, p10 in pc.execute(
            "SELECT game, product_id, grade9, psa10 FROM pricecharting "
            "WHERE pc_id IS NOT NULL AND (grade9 IS NOT NULL OR psa10 IS NOT NULL)"):
        slabs[(g, pid)] = (g9, p10)
    pc.close()
    out = {}
    for game in GAMES:
        try:
            con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=30)
            rows = con.execute("SELECT product_id, near_mint_price FROM cards "
                               "WHERE near_mint_price >= ?", (RAW_FLOOR,)).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        got = [(nm, *slabs[(game, pid)]) for pid, nm in rows if (game, pid) in slabs]
        if got:
            out[game] = got
    return out


def psa10_vs_raw_body():
    from games import GAMES
    data = graded_multiples()
    every = [r for rows in data.values() for r in rows]
    tens = [p10 / nm for nm, _g9, p10 in every if p10]
    nines = [g9 / nm for nm, g9, _p10 in every if g9]
    if len(tens) < 200:
        return None
    under = sum(1 for x in nines if x < 1.0) / len(nines) * 100 if nines else 0
    game_rows = []
    for game, rows in data.items():
        t = [p10 / nm for nm, _g9, p10 in rows if p10]
        n = [g9 / nm for nm, g9, _p10 in rows if g9]
        if len(t) < MIN_CARDS:
            continue
        game_rows.append((len(t), GAMES[game]["label"], statistics.median(t),
                          statistics.median(n) if len(n) >= MIN_CARDS else None))
    game_rows.sort(key=lambda r: -r[2])
    band_rows = []
    for lo, hi, label in BANDS:
        t = [p10 / nm for nm, _g9, p10 in every if p10 and nm >= lo and (hi is None or nm < hi)]
        n = [g9 / nm for nm, g9, _p10 in every if g9 and nm >= lo and (hi is None or nm < hi)]
        if len(t) >= MIN_CARDS:
            band_rows.append((label, len(t), statistics.median(t),
                              statistics.median(n) if len(n) >= MIN_CARDS else None))
    cell = lambda v: times(v) if v is not None else "—"
    games_tbl = "".join(
        f"<tr><td>{esc(label)}</td><td class='num'>{n:,}</td><td class='num'>{cell(g9)}</td>"
        f"<td class='num'>{times(p10)}</td></tr>" for n, label, p10, g9 in game_rows)
    bands_tbl = "".join(
        f"<tr><td>{label}</td><td class='num'>{n:,}</td><td class='num'>{cell(g9)}</td>"
        f"<td class='num'>{times(p10)}</td></tr>" for label, n, p10, g9 in band_rows)
    top = game_rows[0] if game_rows else None
    low = game_rows[-1] if game_rows else None
    spread = (f" It runs from {times(low[2])} in {esc(low[1])} to {times(top[2])} in "
              f"{esc(top[1])}." if top and low and top is not low else "")
    return (
        f'<p class="report-lede">A graded card is not automatically worth more than the raw '
        f'one. Here is what the grades actually sell for against a raw Near Mint copy, '
        f'counted from the {len(every):,} cards we track with a raw price of $10 or more '
        f'and a graded price to set beside it.</p>'
        f"<h2>The short answer</h2>"
        f"<p>On the {len(tens):,} of those cards with a PSA 10 price, a PSA 10 sells for "
        f"about {times(statistics.median(tens))} the raw Near Mint price, taking the card in "
        f"the middle.{spread} A Grade 9 sells for about {times(statistics.median(nines))} "
        f"the raw price, and on {under:.0f}% of cards a Grade 9 sells for less than a raw "
        f"Near Mint copy does.</p>"
        f"<h2>By game</h2>"
        f"<p>Each row is the card in the middle of its game: half the game's cards carry a "
        f"bigger multiple, half a smaller one.</p>"
        f"<table><tr><th>Game</th><th class='num'>Cards counted</th>"
        f"<th class='num'>Grade 9 vs raw</th><th class='num'>PSA 10 vs raw</th></tr>"
        f"{games_tbl}</table>"
        f"<h2>By what the raw card costs</h2>"
        f"<p>The multiple shrinks as the raw card gets more expensive.</p>"
        f"<table><tr><th>Raw Near Mint price</th><th class='num'>Cards counted</th>"
        f"<th class='num'>Grade 9 vs raw</th><th class='num'>PSA 10 vs raw</th></tr>"
        f"{bands_tbl}</table>"
        f"<h2>How to read these numbers</h2>"
        f"<p>Raw prices are TCGplayer's Near Mint market price. Graded prices come from "
        f"PriceCharting's sales history for each grade. A multiple is the graded price "
        f"divided by the raw price on the same card, and the figure shown is the middle "
        f"card, so one $50,000 trophy card cannot drag it. A PSA 10 price on an older card "
        f"can rest on a handful of sales. These are prices only: they say "
        f"nothing about what grading costs, how long it takes, or what grade a given copy "
        f"would come back with. For how grading works, see "
        f'<a href="{SITE}/guides/how-grading-affects-value">How grading affects value</a>; '
        f"every card page lists its own graded prices.</p>")


AGES = (30, 60, 90)


def release_paths(today):
    """{game: [{set, release, at: {age: pct}}]} for each game's recent sets
    priced from their release week, using the set articles' own index
    (game_articles.set_index_path), so a guide and an article never disagree."""
    import game_articles as ga
    pc = sqlite3.connect(f"file:{os.path.normpath(ga.PC_DB)}?mode=ro", uri=True, timeout=60)
    out = {}
    from games import GAMES
    for game in GAMES:
        try:
            cat = ga.catalog(game)
            peers = ga.peer_sets(pc, game, cat, None, today.isoformat(), today)
        except Exception as e:                                   # noqa: BLE001
            print(f"guide_pages: {game}: {type(e).__name__}: {e}", flush=True)
            continue
        rows = []
        for p in peers:
            at = {a: ga.index_at(p["path"], a) for a in AGES}
            if at[AGES[0]] is not None:
                rows.append({"set": p["set"], "release": p["release"], "at": at})
        if rows:
            out[game] = rows
    pc.close()
    return out


def after_release_body():
    from games import GAMES
    today = date.today()
    data = release_paths(today)
    sets = [r for rows in data.values() for r in rows]
    month = [r["at"][30] for r in sets]
    if len(month) < 12:
        return None
    pct = lambda v: (f"<span class='{'report-up' if v > 0 else 'report-down'}'>{v:+.0f}%</span>"
                     if v is not None else "—")
    med = lambda vals: statistics.median(vals) if len(vals) >= 3 else None
    game_rows = []
    for game, rows in data.items():
        if len(rows) < 3:
            continue
        game_rows.append((GAMES[game]["label"], len(rows),
                          *[med([r["at"][a] for r in rows if r["at"][a] is not None])
                            for a in AGES]))
    game_rows.sort(key=lambda r: r[2] if r[2] is not None else 0)
    games_tbl = "".join(
        f"<tr><td>{esc(label)}</td><td class='num'>{n}</td>"
        + "".join(f"<td class='num'>{pct(v)}</td>" for v in vals) + "</tr>"
        for label, n, *vals in game_rows)
    fell = sum(1 for v in month if v < 0)
    ninety = [r["at"][90] for r in sets if r["at"][90] is not None]
    later = (f" Of the {len(ninety)} old enough to have a day-90 price, the one in the "
             f"middle was at {statistics.median(ninety):+.0f}%."
             if len(ninety) >= 12 else "")
    return (
        f'<p class="report-lede">Most sets get cheaper after they come out. Here is how '
        f'much, and how fast, counted from the {len(sets)} releases across {len(data)} games '
        f'that we have priced from their release week.</p>'
        f"<h2>The short answer</h2>"
        f"<p>A month after release, {fell} of {len(sets)} releases were worth less than in "
        f"their first week, and the one in the middle was at "
        f"{statistics.median(month):+.0f}%.{later} Early prices are set by the first few "
        f"copies listed, before most boxes have been opened.</p>"
        f"<h2>By game</h2>"
        f"<p>Each figure is the release in the middle of its game, measured against its "
        f"own first week of prices. A release is a main set or one of the decks, promo "
        f"packs and side sets that come out with it. Games with fewer than three counted "
        f"are left out.</p>"
        f"<table><tr><th>Game</th><th class='num'>Releases</th><th class='num'>After 30 days</th>"
        f"<th class='num'>After 60 days</th><th class='num'>After 90 days</th></tr>"
        f"{games_tbl}</table>"
        f"<h2>How to read these numbers</h2>"
        f"<p>A set's figure is the average move of its cards that were priced at $1 or more "
        f"in its first week, so it follows the cards people actually trade, not the bulk. "
        f"Only releases we priced within a week of their real release date are counted, "
        f"over the last eighteen months. A dash means too few of the game's releases are "
        f"that old yet. Single cards "
        f"can do the opposite of their set: a chase card can climb while the set around it "
        f"falls. Our <a href=\"{SITE}/articles\">set reports</a> follow each new set through "
        f"exactly this stretch, and every <a href=\"{SITE}/sets\">set page</a> lists its "
        f"cards with today's prices.</p>")


DATA_GUIDES = {
    "psa-10-vs-raw": ({
        "title": "PSA 10 vs Raw: How Much More a Graded Card Sells For",
        "description": "What a PSA 10 and a Grade 9 actually sell for against the raw Near Mint "
                       "card, by game and by price, counted from the cards we track. Updated nightly.",
        "h1": "PSA 10 vs raw: how much more a graded card sells for",
        "published": "2026-11-24", "dated": "yes"}, psa10_vs_raw_body),
    "card-prices-after-release": ({
        "title": "How Card Prices Move After a Set Releases",
        "description": "How far a new set's prices fall in its first 30, 60 and 90 days, by game, "
                       "counted from every release we have priced from its first week. Updated nightly.",
        "h1": "How card prices move after a set releases",
        "published": "2026-11-24", "dated": "yes"}, after_release_body),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--written-only", action="store_true",
                    help="render the written guides and the index; read no database")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    guides = {}                                    # slug -> (meta, body or None)
    for fn in sorted(os.listdir(SRC)):
        if fn.endswith(".html"):
            guides[fn[:-5]] = read_source(os.path.join(SRC, fn))
    for slug, (meta, build) in DATA_GUIDES.items():
        body = None
        if not args.written_only:
            try:
                body = build()
            except Exception as e:                               # noqa: BLE001
                print(f"guide_pages: {slug}: {type(e).__name__}: {e}; left as it was", flush=True)
        guides[slug] = (dict(meta, date=date.today().isoformat()), body)

    rank = lambda s: (ORDER.index(s) if s in ORDER else len(ORDER), s)
    # A numbers guide that has never been rendered here (a first, written-only
    # run) is not listed: its page does not exist yet.
    listed = [s for s in sorted(guides, key=rank)
              if guides[s][1] is not None or os.path.exists(os.path.join(args.out, s + ".html"))]
    others = [(s, guides[s][0].get("h1") or guides[s][0]["title"]) for s in listed]
    wrote = 0
    for slug in listed:
        meta, body = guides[slug]
        if body is None:
            continue                                # keep the page already there
        with open(os.path.join(args.out, slug + ".html"), "w", encoding="utf-8") as f:
            f.write(page(slug, meta, body, others))
        wrote += 1
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_page([(s, guides[s][0].get("h1") or guides[s][0]["title"],
                             guides[s][0]["description"]) for s in listed]))
    print(f"wrote {wrote} guide page(s) + the index -> {args.out}")


if __name__ == "__main__":
    main()
