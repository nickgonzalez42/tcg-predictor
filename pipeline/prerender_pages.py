#!/usr/bin/env python3
"""
Prerender the SPA's pages as static HTML for crawlers (2026-09-26).

Runs ON PROD (stdlib only) after each data push: the box already holds every
database, so ~200k card stubs regenerate in minutes with no upload. Caddy
serves these ONLY to crawler user-agents at the same URLs (dynamic
rendering); humans keep the full SPA. Content mirrors what the app shows —
same prices, ladder, forecasts — so bot and human views stay equivalent.

Output (atomic swap):  <root>/static/prerender/
    index.html                     homepage snapshot (intro + top movers)
    about|privacy|terms|contact.html
    catalog/{game}/{id}.html       every catalog-visible card

Usage:  python3 prerender_pages.py [--root /srv/tcg]
"""

import argparse
import glob
import html
import os
import shutil
import sqlite3
import time

SITE = "https://cardstock.guide"
IMG = "https://d18pzythfo0bgy.cloudfront.net"
ADS = ('<script async src="https://pagead2.googlesyndication.com/pagead/js/'
       'adsbygoogle.js?client=ca-pub-3270328826867583" crossorigin="anonymous"></script>')
CSS = """
:root{--bg:#101623;--text:#e6ebf4;--muted:#8b96ad;--border:#2e3a52;--brand:#3d7dca}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 48px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;gap:14px;align-items:baseline;border-bottom:1px solid var(--border);
padding-bottom:12px;margin-bottom:20px}header .logo{font-weight:800;color:var(--text)}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:24px 0 8px}
.muted{color:var(--muted);font-size:14px}
table{border-collapse:collapse;width:100%;margin:10px 0}
td,th{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;font-size:14.5px}
img.card{max-width:240px;border-radius:8px;float:right;margin:0 0 12px 16px}
footer{border-top:1px solid var(--border);margin-top:32px;padding-top:12px;
color:var(--muted);font-size:13.5px}
"""

NAV = (f'<header><a class="logo" href="{SITE}/">CardStock</a>'
       f'<nav><a href="{SITE}/catalog">Catalog</a> <a href="{SITE}/reports">Reports</a> '
       f'<a href="{SITE}/guides/how-to-read-price-charts">Guides</a> '
       f'<a href="{SITE}/about">About</a></nav></header>')
FOOT = (f'<footer><p>CardStock tracks trading card prices and publishes model forecasts '
        f'with a public track record. Not financial advice. '
        f'<a href="{SITE}/about">How the forecasts work</a> · '
        f'<a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a> · '
        f'<a href="{SITE}/contact">Contact</a></p></footer>')


def page(path, title, desc, body):
    t, d = html.escape(title), html.escape(desc)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t} | CardStock</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{SITE}{path}">
<meta name='impact-site-verification' value='7bff3025-2f3e-4682-9142-9dbdfc5ec089'>
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">
<meta property="og:url" content="{SITE}{path}"><meta property="og:site_name" content="CardStock">
{ADS}
<style>{CSS}</style></head>
<body><div class="wrap">{NAV}{body}{FOOT}</div></body></html>"""


def money(v):
    return f"${v:,.2f}" if v is not None else "—"


GRADE_COLS = [("ungraded", "Ungraded"), ("grade7", "Grade 7"), ("grade8", "Grade 8"),
              ("grade9", "Grade 9"), ("grade95", "Grade 9.5"), ("psa10", "PSA 10")]


def card_body(game, r, ladder, fc):
    pid, name, set_name, rarity, number, desc, nm = r
    e = html.escape
    rows = []
    if nm is not None:
        rows.append(f"<tr><td>Near Mint market price</td><td>{money(nm)}</td></tr>")
    for col, label in GRADE_COLS[1:]:
        v = ladder.get(col) if ladder else None
        if v:
            rows.append(f"<tr><td>{label}</td><td>{money(v)}</td></tr>")
    fc_rows = "".join(
        f"<tr><td>{h.upper()} forecast</td><td>{money(f_price)}</td>"
        f"<td class='muted'>range {money(lo)} – {money(hi)}</td></tr>"
        for h, f_price, lo, hi in fc)
    img = f'<img class="card" src="{IMG}/{game}/{pid}.jpg" alt="{e(name)} card image">'
    d = f"<p>{e(desc)}</p>" if desc else ""
    return (f'{img}<h1>{e(name)}</h1>'
            f'<p class="muted">{e(set_name or "")} · {e(rarity or "")}'
            + (f' · #{e(number)}' if number else '') + f' · {e(game)}</p>'
            f'<table>{"".join(rows)}</table>'
            + (f'<h2>Model price forecasts</h2><table>{fc_rows}</table>' if fc_rows else '')
            + d +
            f'<p><a href="{SITE}/catalog/{game}/{pid}">Interactive price chart, full history '
            f'and past-forecast track record →</a></p>')


PROSE = {
    "about": None,   # filled below; About mirrors the SPA page's current copy
}


def write(root_new, rel, content):
    p = os.path.join(root_new, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/srv/tcg")
    args = ap.parse_args()
    t0 = time.time()
    data = os.path.join(args.root, "data")
    cards_dir = os.path.join(data, "cards")
    out_final = os.path.join(args.root, "static", "prerender")
    out_new = out_final + "-new"
    if os.path.exists(out_new):
        shutil.rmtree(out_new)

    pc = sqlite3.connect(os.path.join(cards_dir, "pricecharting.db"))
    ladders = {}
    for row in pc.execute("SELECT game, product_id, ungraded, grade7, grade8, grade9, "
                          "grade95, psa10 FROM pricecharting"):
        ladders[(row[0], row[1])] = dict(zip([c for c, _ in GRADE_COLS], row[2:]))
    pc.close()

    pred = sqlite3.connect(os.path.join(cards_dir, "predictions.db"))
    fcs = {}
    try:
        for g, pid, h, fp, lo, hi in pred.execute(
                "SELECT game, product_id, horizon, forecast_price, low, high FROM forecasts "
                "WHERE target='ungraded' AND printing='' AND horizon IN ('1m','6m','12m')"):
            fcs.setdefault((g, pid), []).append((h, fp, lo, hi))
        top = pred.execute(
            "SELECT game, product_id, base_price, forecast_price FROM forecasts "
            "WHERE target='ungraded' AND horizon='1m' AND printing='' AND base_price >= 10 "
            "ORDER BY forecast_price/base_price DESC LIMIT 20").fetchall()
    except sqlite3.Error:
        top = []
    pred.close()

    n_cards = 0
    top_names = {}
    for db in sorted(glob.glob(os.path.join(data, "*_cards.db"))):
        game = os.path.basename(db).replace("_cards.db", "")
        conn = sqlite3.connect(db)
        for r in conn.execute(
                "SELECT product_id, name, set_name, rarity, card_number, description, "
                "near_mint_price FROM cards "
                "WHERE near_mint_price IS NOT NULL AND image_path IS NOT NULL AND name IS NOT NULL"):
            pid, name = r[0], r[1]
            fc = sorted(fcs.get((game, pid), []), key=lambda x: {"1m": 0, "6m": 1, "12m": 2}[x[0]])
            body = card_body(game, r, ladders.get((game, pid)), fc)
            desc = (f"{name} ({r[2]}) price: {money(r[6])} Near Mint, graded prices and "
                    f"AI price forecasts with a public accuracy record.")
            write(out_new, f"catalog/{game}/{pid}.html",
                  page(f"/catalog/{game}/{pid}", f"{name} price & forecast", desc, body))
            top_names[(game, pid)] = name
            n_cards += 1
        conn.close()

    movers = "".join(
        f"<tr><td><a href='{SITE}/catalog/{g}/{pid}'>{html.escape(top_names.get((g, pid), str(pid)))}</a></td>"
        f"<td>{g}</td><td>{money(b)}</td><td>{money(f)}</td></tr>"
        for g, pid, b, f in top if (g, pid) in top_names)
    write(out_new, "index.html", page("/", "CardStock: Trading Card Price Predictions",
        "AI price forecasts for Pokémon, One Piece, Magic, Yu-Gi-Oh!, Lorcana, Digimon, Gundam "
        "and Star Wars cards, with graded price history and a public accuracy record.",
        f"<h1>Trading card prices, with receipts</h1>"
        f"<p>CardStock tracks {n_cards:,} cards across eight games, publishes model price "
        f"forecasts for one month to a year ahead, and grades every forecast in public once "
        f"its date arrives. Weekly <a href='{SITE}/reports'>market reports</a> round up what "
        f"moved; the <a href='{SITE}/about'>About page</a> explains the model in plain "
        f"language.</p><h2>Model's top 1-month calls right now</h2>"
        f"<table><tr><th>Card</th><th>Game</th><th>Price</th><th>1M forecast</th></tr>"
        f"{movers}</table>"))

    write(out_new, "about.html", page("/about", "About", "What CardStock is and how its trading card price predictions work.", PROSE["about"]))
    write(out_new, "privacy.html", page("/privacy", "Privacy Policy", "How CardStock handles your data, cookies, and advertising.", PROSE["privacy"]))
    write(out_new, "terms.html", page("/terms", "Terms of Service", "The terms for using CardStock.", PROSE["terms"]))
    write(out_new, "contact.html", page("/contact", "Contact", "Get in touch with CardStock.", PROSE["contact"]))

    old = out_final + "-old"
    if os.path.exists(old):
        shutil.rmtree(old)
    if os.path.exists(out_final):
        os.rename(out_final, old)
    os.rename(out_new, out_final)
    if os.path.exists(old):
        shutil.rmtree(old)
    print(f"prerendered {n_cards:,} card pages + home/about/privacy/terms/contact "
          f"in {time.time() - t0:.0f}s -> {out_final}")


# ---- prose (mirrors the SPA pages; update together when those change) ------
PROSE["about"] = """
<h1>How the forecasts work</h1>
<p class="muted">Updated August 2026 · model version forecast-deep-v4.4 · retrained on every data refresh</p>
<p>CardStock treats trading cards like a market you can actually study. Every card gets a price
forecast for 1 month, 6 months, and 1 year ahead, for the raw card and each graded tier, next to
real graded price history. Weekly market reports round up the biggest movers and a public
scorecard of how the model's own predictions have performed. None of it is financial advice.</p>
<h2>Where the numbers come from</h2>
<p>What each card <em>is</em> comes from TCGplayer's catalog. What each card <em>costs</em> comes
from two places: raw prices are TCGplayer's sales-backed market price, collected on every refresh;
graded prices come from PriceCharting's per-grade sales history. Prices also police each other:
a market price is only trusted while real sales stand behind it — a frozen price that disagrees
with fresh sales elsewhere gets flagged, reviewed, and switched to the sales-backed source. When
neither source has defensible data, the card shows no price at all.</p>
<h2>What the model weighs</h2>
<p>Price momentum and volatility, the card's set and its price relative to set-mates, game-wide
and hobby-wide trends, the card's printed characteristics and artwork, and the model's own
trailing forecast errors per card and per set. Under the hood: gradient-boosted decision trees
per game, tier, and horizon, trained on historical outcomes with held-out validation.</p>
<h2>How we keep it honest</h2>
<p>Every published forecast is saved, and once its date arrives it is graded against what the
price actually did — misses stay on display on each card's chart. The weekly report publishes
accuracy, bias, and how often prices landed inside the model's ranges, whether flattering or not.</p>
<p>New cards carry no forecast until they have at least two months of clean price history — we
tested launch-window prediction and the honest answer was: not well enough to publish.</p>
"""
PROSE["privacy"] = """
<h1>Privacy Policy</h1>
<p class="muted">Last updated: July 14, 2026</p>
<h2>What we collect</h2>
<p>If you create an account, we store your email address and the cards you track (portfolio and
watchlist entries, including quantities, grades, and any purchase prices or notes you enter).
That's it. We don't collect names, addresses, or payment details.</p>
<h2>How it's used</h2>
<p>Your tracked cards exist solely to power your portfolio and watchlist. We don't sell or share
your personal data with third parties.</p>
<h2>Cookies</h2>
<p>We use a session cookie to keep you signed in. If advertising is enabled, Google AdSense and
its partners may set cookies to serve ads, including personalized ads. Third-party vendors,
including Google, use cookies to serve ads based on your prior visits to this and other websites.
You can opt out of personalized advertising at
<a href="https://www.google.com/settings/ads" rel="noreferrer">Google Ads Settings</a>.</p>
<h2>Price data</h2>
<p>Card prices and market data shown on this site are aggregated from public sources and are
informational only. Nothing here is financial advice.</p>
<h2>Contact</h2>
<p>Questions about this policy? Use our <a href="https://cardstock.guide/contact">contact
page</a> or email <a href="mailto:hello@cardstock.guide">hello@cardstock.guide</a>.</p>
"""
PROSE["terms"] = """
<h1>Terms of Service</h1>
<p class="muted">Last updated: July 15, 2026</p>
<p>By using CardStock (the "Service," at cardstock.guide) you agree to these terms. If you don't
agree, please don't use the Service.</p>
<h2>What CardStock is</h2>
<p>CardStock is an informational tool for tracking trading-card prices and viewing
machine-generated price forecasts, provided for research and entertainment.</p>
<h2>Not financial advice</h2>
<p>Nothing on CardStock is financial, investment, or trading advice. Price forecasts are model
estimates that are frequently wrong, and past performance does not predict future results. Any
decision to buy, sell, or hold a card is yours alone. Do your own research.</p>
<h2>Accounts</h2>
<p>You're responsible for activity under your account and for keeping your login secure. Provide
a valid email. We may suspend or remove accounts that abuse the Service or violate these terms.</p>
<h2>Your content</h2>
<p>You keep ownership of comments and other content you post, and grant us a non-exclusive
license to display it on the Service. Don't post content that is illegal, hateful, harassing,
deceptive, infringing, spam, or otherwise objectionable. We may remove content or hide it via
automated moderation at our discretion.</p>
<h2>Acceptable use</h2>
<p>Don't scrape, bulk-download, overload, reverse-engineer, or attempt to disrupt the Service,
and don't use it to break the law or infringe others' rights.</p>
<h2>Data and accuracy</h2>
<p>Prices, histories, and forecasts are aggregated from third-party and public sources and are
provided "as is." They may be delayed, incomplete, or inaccurate. Card names, images, and
trademarks belong to their respective owners; CardStock is not affiliated with or endorsed by
any card publisher.</p>
<h2>Intellectual property</h2>
<p>The site design, original text, and the forecasting models are ours. You may use the Service
for personal, non-commercial purposes.</p>
<h2>Disclaimers</h2>
<p>The Service is provided "as is" and "as available," without warranties of any kind, express
or implied, including merchantability, fitness for a particular purpose, and non-infringement.</p>
<h2>Limitation of liability</h2>
<p>To the maximum extent permitted by law, CardStock and its operators are not liable for any
indirect, incidental, or consequential damages, or for any losses arising from your use of the
Service or reliance on its data or forecasts.</p>
<h2>Changes</h2>
<p>We may update these terms; continued use after changes means you accept them. Material
changes are reflected in the "last updated" date above.</p>
<h2>Contact</h2>
<p>Questions about these terms? Reach us via the
<a href="https://cardstock.guide/contact">contact page</a>.</p>
"""
PROSE["contact"] = """
<h1>Contact</h1>
<p>Questions, feedback, a data correction, or a bug? The
<a href="https://cardstock.guide/contact">live contact page</a> has a message form that goes
straight to the team, or email
<a href="mailto:hello@cardstock.guide">hello@cardstock.guide</a>. Add your email if you'd like
a reply.</p>
"""

if __name__ == "__main__":
    main()
