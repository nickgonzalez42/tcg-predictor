#!/usr/bin/env python3
"""What search engines and AI assistants get from the live site (2026-11-24).

Fetches a handful of pages the way crawlers do and prints PASS or FAIL for
each thing the 2026-11-24 search audit fixed. Run it after a deploy:

    ../one-piece/.venv/bin/python deploy/seo_check.py

Read-only: plain GET requests to cardstock.guide, nothing else.
"""
import json
import re
import sys

import requests

SITE = "https://cardstock.guide"
UA = {
    "google": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "person": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "chatgpt": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
               "ChatGPT-User/1.0; +https://openai.com/bot",
    "oai-search": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
                  "OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "claude": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
              "Claude-User/1.0; +Claude-User@anthropic.com)",
    "perplexity": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
                  "Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
    "applebot": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1)",
}
FAILS = []


def get(path, ua="google", follow=True):
    return requests.get(path if path.startswith("http") else SITE + path,
                        headers={"User-Agent": UA[ua]}, timeout=40, allow_redirects=follow)


def one(pattern, text):
    m = re.search(pattern, text, re.I | re.S)
    return m.group(1).strip() if m else None


def title(text):
    return one(r"<title[^>]*>(.*?)</title>", text) or ""


def canonical(text):
    return one(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']', text)


def ld_types(text):
    out = []
    for raw in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S):
        try:
            j = json.loads(raw)
            out += [it.get("@type") for it in (j if isinstance(j, list) else [j])]
        except ValueError:
            out.append("INVALID")
    return out


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(detail)[:110]}]" if detail else ""))
    if not ok:
        FAILS.append(name)


def main():
    # ---- sitemap
    index = get("/sitemap.xml").text
    subs = re.findall(r"<loc>(.*?)</loc>", index)
    dead = [s for s in subs if get(s).status_code != 200]
    check("every sitemap the index lists answers 200", not dead, dead)
    arts = get("/sitemap-articles.xml").text
    check("article sitemap carries lastmod", "<lastmod>" in arts)
    cards = re.findall(r"<loc>(.*?)</loc>", get("/sitemap-onepiece.xml").text)
    check("card sitemap carries lastmod", "<lastmod>" in get("/sitemap-onepiece.xml").text)
    card = cards[len(cards) // 2].replace(SITE, "")

    # ---- a card page, as each crawler
    g = get(card).text
    check("card title names its set", " - " in title(g) and "Price & Forecast" in title(g),
          title(g))
    check("card page carries Product + BreadcrumbList",
          ld_types(g) == ["Product", "BreadcrumbList"], ld_types(g))
    check("card page answers what it is worth", "worth?</h2>" in g)
    check("card page links to its game's set index", f'{SITE}/sets/' in g)
    for ua in ("chatgpt", "oai-search", "claude", "perplexity", "applebot"):
        t = get(card, ua).text
        check(f"{ua} gets the real card page", canonical(t) == SITE + card, title(t))
    shell = get(card, "person").text
    check("the app shell claims no canonical of its own", canonical(shell) is None,
          canonical(shell))

    # ---- hubs
    for path, want in (("/sets", "Set Price Guides"), ("/guides", "Guides"),
                       ("/guides/faq", "FAQ"), ("/sets/onepiece", "One Piece Card Prices")):
        for ua in ("person", "google"):
            r = get(path, ua)
            check(f"{path} is a real page for a {ua}", r.status_code == 200 and want in title(r.text),
                  title(r.text))
    for slug in ("psa-10-vs-raw", "card-prices-after-release"):
        r = get(f"/guides/{slug}", "person")
        check(f"/guides/{slug} is live", r.status_code == 200 and "Updated" in r.text
              and canonical(r.text) == f"{SITE}/guides/{slug}", title(r.text))
    setpage = get("/sets/onepiece", "person").text
    check("set pages wear the app's nav", 'class="navbar"' in setpage and "gamepick" in setpage)
    api = get("/api/cards" + card.replace("/catalog", ""), "person")
    check("the card API names the card's set page",
          api.status_code == 200 and str(api.json().get("setPage") or "").startswith("/sets/"),
          api.json().get("setPage") if api.status_code == 200 else api.status_code)
    home = get("/").text
    check("home title names the site once", title(home).count("CardStock") == 1, title(home))
    check("home carries WebSite + Organization", ld_types(home) == ["WebSite", "Organization"],
          ld_types(home))
    check("FAQ carries FAQPage data", "FAQPage" in ld_types(get("/guides/faq").text))

    # ---- addresses that should not be indexed, or should move
    r = get("/catalog/onepiece/999999999")
    check("a card that does not exist tells crawlers noindex",
          "noindex" in (r.headers.get("X-Robots-Tag") or ""), r.headers.get("X-Robots-Tag"))
    r = get("/og-image.png")
    check("a real file is NOT marked noindex", "noindex" not in (r.headers.get("X-Robots-Tag") or ""))
    r = get(card + "/", follow=False)
    check("a trailing slash redirects", r.status_code == 308 and r.headers.get("Location") == card,
          f"{r.status_code} {r.headers.get('Location')}")
    r = get(card + "/?utm_source=x", follow=False)
    check("...and keeps the query string",
          r.headers.get("Location") == card + "?utm_source=x", r.headers.get("Location"))
    r = get("http://www.cardstock.guide/about", follow=False)
    check("http://www goes to the site in one hop", r.headers.get("Location") == SITE + "/about",
          r.headers.get("Location"))

    # ---- files for crawlers
    for path, want in (("/robots.txt", "Sitemap:"), ("/llms.txt", "# CardStock")):
        r = get(path)
        check(f"{path} is served as text", r.status_code == 200 and want in r.text
              and "text/plain" in r.headers.get("Content-Type", ""),
              r.headers.get("Content-Type"))

    # ---- reports
    rep = re.findall(r"<loc>(.*?)</loc>", get("/sitemap-reports.xml").text)[-1]
    check("report title says what it is", "Report," in title(get(rep).text)
          or "Accuracy," in title(get(rep).text), title(get(rep).text))

    print(f"\n{len(FAILS)} failing" if FAILS else "\nall passing")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
