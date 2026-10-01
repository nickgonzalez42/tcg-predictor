#!/usr/bin/env python3
"""
Render weekly market reports as standalone, crawler-visible HTML pages.

Why (2026-08-29): AdSense rejected the site for "low value content" — its
reviewer, like any crawler, saw only the SPA's empty HTML shell. The reports
are the site's original editorial, but they lived as JSON behind the API and
rendered client-side. This writes each report as a complete, self-contained
HTML document that Caddy serves directly at /reports/{slug} (file-first, SPA
fallback), so the content exists without JavaScript.

Runs in two places:
  trainer (Fridays, aws_model_run.sh):  report_pages.py --db  -> scp to prod
  anywhere (backfill):                  report_pages.py --api -> static-content/

The page embeds its own compact stylesheet (dark palette mirroring the site);
the report bodies' inline SVGs already carry hard color fallbacks.
"""

import argparse
import html
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

PRED_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                       "predictions.db")
DEFAULT_OUT = os.path.join(BASE, "..", "tcg-predictor", "static-content", "reports")
SITE = "https://cardstock.guide"

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
h1{font-size:26px;line-height:1.25;margin:8px 0 4px}
h2{font-size:19px;margin:40px 0 14px}h3{font-size:16.5px;margin:30px 0 10px}
p{margin:15px 0}ul{margin:15px 0}li{margin:6px 0}
.published{color:var(--text-muted);font-size:13.5px;margin:0 0 18px}
.report-lede{color:var(--text-muted);font-size:17px}
.report-note{color:var(--text-muted);font-size:13.5px;border-left:3px solid var(--border);
padding-left:10px}
.report-trendline{margin:8px 0}
.report-up{color:var(--report-up)}.report-down{color:var(--report-down)}
.report-table{width:100%;border-collapse:collapse;font-size:14.5px;margin:15px 0}
.report-table th{text-align:left;color:var(--text-muted);font-weight:600;font-size:13px}
.report-table th,.report-table td{padding:6px 10px 6px 0;border-bottom:1px solid var(--border)}
.report-chart{display:block;margin:20px 0}
svg{max-width:100%;height:auto}
@media (max-width:480px){.report-table{font-size:12px}
.report-table th,.report-table td{padding-right:5px}}
footer.site{border-top:1px solid var(--border);margin-top:36px;padding-top:14px;
color:var(--text-muted);font-size:13.5px}
"""


def page(slug, title, published_at, summary, body_html):
    t, s = html.escape(title), html.escape(summary)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t} | CardStock</title>
<meta name="description" content="{s}">
<link rel="canonical" href="{SITE}/reports/{slug}">
<meta property="og:type" content="article">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{s}">
<meta property="og:url" content="{SITE}/reports/{slug}">
<meta property="og:site_name" content="CardStock">
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-3270328826867583" crossorigin="anonymous"></script>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="site">
  <a class="logo" href="{SITE}/">CardStock</a>
  <nav><a href="{SITE}/catalog">Catalog</a><a href="{SITE}/reports">Reports</a>
  <a href="{SITE}/about">About</a><a href="{SITE}/guides/how-to-read-price-charts">Guides</a></nav>
</header>
<article>
<h1>{t}</h1>
<p class="published">Published {html.escape(published_at)} · CardStock weekly market report</p>
{body_html}
</article>
<footer class="site">
  <p>CardStock tracks trading card prices and publishes model forecasts with a
  public track record. Not financial advice.
  <a href="{SITE}/about">How the forecasts work</a> ·
  <a href="{SITE}/reports">All reports</a> ·
  <a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a></p>
</footer>
</div>
</body>
</html>
"""


def from_db(path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT slug, title, published_at, summary, body_html FROM reports").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return rows


def from_api():
    import requests
    def get(url):
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.json()
    rows = []
    for item in get(f"{SITE}/api/market-reports"):
        full = get(f"{SITE}/api/market-reports/{item['slug']}")
        rows.append((full["slug"], full["title"], full["publishedAt"],
                     full["summary"], full["bodyHtml"]))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Render standalone report pages")
    ap.add_argument("--db", nargs="?", const=PRED_DB,
                    help="read reports from this predictions.db (default: the live one)")
    ap.add_argument("--api", action="store_true",
                    help="fetch reports from the public site API (backfill)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    rows = from_api() if args.api else from_db(args.db or PRED_DB)
    os.makedirs(args.out, exist_ok=True)
    for slug, title, published_at, summary, body_html in rows:
        with open(os.path.join(args.out, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page(slug, title, published_at[:10], summary, body_html))
    print(f"wrote {len(rows)} report page(s) -> {args.out}")


if __name__ == "__main__":
    main()
