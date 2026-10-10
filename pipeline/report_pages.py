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
.report-game-sec{border:1px solid var(--border);border-radius:10px;padding:0 15px;margin:10px 0}
.report-game-sec summary{display:flex;align-items:center;gap:10px;min-height:38px;
cursor:pointer;list-style:none;font-size:16.5px}
.report-game-sec summary::-webkit-details-marker{display:none}
.report-game-sec summary::before{content:'▸';color:var(--text-muted);transition:transform .15s}
.report-game-sec[open] summary::before{transform:rotate(90deg)}
.report-game-teaser{margin-left:auto;color:var(--text-muted);font-size:12px;white-space:nowrap}
.report-game-sec > :last-child{margin-bottom:15px}
.report-model-corner{background:#1c2438;border:1px solid var(--border);
border-left:4px solid #ffcb05;border-radius:10px;padding:2px 18px 6px;margin:40px 0}
.report-model-corner h2{margin:14px 0 10px}
.newsletter{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;
justify-content:space-between;background:#141c2e;border:1px solid var(--border);
border-radius:10px;padding:14px 18px;margin:36px 0 0;position:relative}
.newsletter-copy{display:flex;flex-direction:column;gap:2px}
.newsletter-copy span{color:var(--text-muted);font-size:13px}
.newsletter-row{display:flex;gap:10px}
.newsletter-row input[type=email]{height:38px;padding:0 14px;min-width:210px;
background:#0b101c;border:1px solid var(--border);border-radius:10px;
color:var(--text);font:inherit;font-size:14px}
.newsletter-row button{height:38px;padding:0 18px;background:#ffcb05;
border:1px solid #e0b000;border-radius:10px;color:#101623;font:inherit;
font-size:14px;font-weight:700;cursor:pointer}
.newsletter-done{color:var(--report-up)}
.card-peek{position:fixed;z-index:1000;width:240px;pointer-events:none;
opacity:0;transition:opacity .12s ease}
.card-peek--on{opacity:1}
.card-peek img{display:block;width:100%;border-radius:12px;
border:1px solid var(--border);background:var(--panel);
box-shadow:0 12px 30px rgba(0,0,0,.5)}
svg{max-width:100%;height:auto}
@media (max-width:480px){.report-table{font-size:12px}
.report-table th,.report-table td{padding-right:5px}}
footer.site{border-top:1px solid var(--border);margin-top:36px;padding-top:14px;
color:var(--text-muted);font-size:13.5px}
"""


# Chart draw-in (2026-10-09): the static pages are what humans get on any
# direct load or refresh (Caddy serves /reports/* file-first), so they carry
# the same animation the SPA has — bars grow out of the zero line, lines
# trace, value labels fade in — as a dependency-free inline script.
# IntersectionObserver instead of scroll math: a chart inside a closed
# <details> has no boxes so it simply never intersects until opened, and
# page-length changes from toggling dropdowns can't leave positions stale.
JS = """
(function () {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var ease = function (t) { return 1 - Math.pow(1 - t, 3); };
  function tween(dur, step) {
    var t0 = null;
    requestAnimationFrame(function frame(now) {
      if (t0 === null) t0 = now;
      var p = Math.min((now - t0) / dur, 1);
      step(ease(p));
      if (p < 1) requestAnimationFrame(frame);
    });
  }
  var charts = [].slice.call(document.querySelectorAll('svg.report-chart'));
  charts.forEach(function (svg) {
    // Hide the marks up front (runs pre-paint, so nothing flashes). Bars are
    // anchored to the zero line when the chart has one; original geometry is
    // stashed in data- attributes. Static labels (text-anchor="end") and the
    // caption stay visible; value/series labels fade in with the marks.
    var zero = svg.querySelector('line');
    var zx = zero ? zero.getAttribute('x1') : null;
    [].forEach.call(svg.querySelectorAll('rect'), function (r) {
      r.dataset.x = r.getAttribute('x');
      r.dataset.w = r.getAttribute('width');
      r.setAttribute('x', zx === null ? r.dataset.x : zx);
      r.setAttribute('width', 0);
    });
    [].forEach.call(svg.querySelectorAll('polyline'), function (l) {
      var len = 0;
      try { len = l.getTotalLength(); } catch (e) {}
      if (!len) return;
      l.dataset.len = len;
      l.style.strokeDasharray = len;
      l.style.strokeDashoffset = len;
    });
    [].forEach.call(svg.querySelectorAll('text'), function (t) {
      if (t.getAttribute('text-anchor') === 'end' ||
          (t.getAttribute('class') || '').indexOf('report-chart-title') >= 0) return;
      t.style.opacity = 0;
      t.dataset.fade = 1;
    });
  });
  function reveal(svg) {
    [].forEach.call(svg.querySelectorAll('rect'), function (r) {
      var x = parseFloat(r.dataset.x), w = parseFloat(r.dataset.w);
      var zx = x, zero = svg.querySelector('line');
      if (zero) zx = parseFloat(zero.getAttribute('x1'));
      tween(600, function (p) {
        r.setAttribute('x', zx + (x - zx) * p);
        r.setAttribute('width', w * p);
      });
    });
    [].forEach.call(svg.querySelectorAll('polyline'), function (l) {
      var len = parseFloat(l.dataset.len || 0);
      if (!len) return;
      tween(900, function (p) { l.style.strokeDashoffset = len * (1 - p); });
    });
    setTimeout(function () {
      [].forEach.call(svg.querySelectorAll('text'), function (t) {
        if (!t.dataset.fade) return;
        tween(350, function (p) { t.style.opacity = p; });
      });
    }, 350);
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      io.unobserve(e.target);
      reveal(e.target);
    });
  }, { rootMargin: '0px 0px -12% 0px' });
  charts.forEach(function (svg) { io.observe(svg); });
})();

// Hover card preview: hovering a card link floats that card's art beside
// the cursor (pointer devices only; shown once the image loads). Card links
// are /catalog/{game}/{id}; art lives on the public image CDN (the same
// one the API's own responses point at).
(function () {
  if (!matchMedia('(hover: hover)').matches) return;
  var peek = document.createElement('div');
  peek.className = 'card-peek';
  var img = document.createElement('img');
  img.alt = '';
  peek.appendChild(img);
  document.body.appendChild(peek);
  var current = '';
  function place(e) {
    var w = peek.offsetWidth || 244, h = peek.offsetHeight || 344;
    var x = e.clientX + 16, y = e.clientY + 16;
    if (x + w > innerWidth - 8) x = e.clientX - w - 16;
    if (y + h > innerHeight - 8) y = Math.max(8, innerHeight - h - 8);
    peek.style.left = x + 'px';
    peek.style.top = y + 'px';
  }
  function linkOf(t) {
    return t && t.closest ? t.closest('a[href*="/catalog/"]') : null;
  }
  document.addEventListener('mouseover', function (e) {
    var a = linkOf(e.target);
    if (!a) return;
    var m = a.getAttribute('href').match(/\\/catalog\\/([a-z]+)\\/(\\d+)/);
    if (!m) return;
    var src = 'https://d18pzythfo0bgy.cloudfront.net/' + m[1] + '/' + m[2] + '.jpg';
    current = src;
    img.onload = function () {
      if (current === src) peek.classList.add('card-peek--on');
    };
    if (img.getAttribute('src') !== src) {
      peek.classList.remove('card-peek--on');
      img.src = src;
    } else if (img.complete && img.naturalWidth) {
      peek.classList.add('card-peek--on');
    }
    place(e);
  });
  document.addEventListener('mouseout', function (e) {
    var from = linkOf(e.target);
    if (from && linkOf(e.relatedTarget) !== from) {
      current = '';
      peek.classList.remove('card-peek--on');
    }
  });
  document.addEventListener('mousemove', function (e) {
    if (peek.classList.contains('card-peek--on')) place(e);
  });
})();

// Weekly-report email signup -> the site's API (same origin).
(function () {
  var form = document.getElementById('nl');
  if (!form) return;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = form.email.value.trim();
    if (!email) return;
    form.querySelector('button').disabled = true;
    fetch('/api/newsletter/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, source: 'static-' + location.pathname,
                             website: form.website.value }),
    }).then(function (r) {
      form.outerHTML = r.ok
        ? "<p class='newsletter-done'>You're on the list — the next report lands Friday morning.</p>"
        : "<p>Something went wrong — try again later.</p>";
    }).catch(function () {
      form.querySelector('button').disabled = false;
    });
  });
})();
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
<form class="newsletter" id="nl">
  <div class="newsletter-copy"><strong>Get the weekly report by email</strong>
  <span>One email every Friday — the market story, the movers and the model's
  record. Nothing else.</span></div>
  <div class="newsletter-row">
    <input type="email" name="email" required placeholder="you@example.com"
           aria-label="Email address">
    <input type="text" name="website" tabindex="-1" autocomplete="off"
           style="position:absolute;left:-9999px" aria-hidden="true">
    <button type="submit">Subscribe</button>
  </div>
</form>
<footer class="site">
  <p>CardStock tracks trading card prices and publishes model forecasts with a
  public track record. Not financial advice.
  <a href="{SITE}/about">How the forecasts work</a> ·
  <a href="{SITE}/reports">All reports</a> ·
  <a href="{SITE}/privacy">Privacy</a> · <a href="{SITE}/terms">Terms</a></p>
</footer>
</div>
<script>{JS}</script>
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
