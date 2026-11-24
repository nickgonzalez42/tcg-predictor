#!/usr/bin/env python3
"""Tell the search engines which pages are new (IndexNow, 2026-11-24).

Bing, Yandex, Naver and Seznam share one "this URL changed" inbox, and Bing's
index is the one ChatGPT search, Copilot and DuckDuckGo read. Without a ping
a new article waits for a crawler to wander by; with one it is fetched within
minutes. Google does not take part (it has Search Console and the sitemap).

Each night, after the publish, this sends:
  - every article and report that has not been sent before,
  - the pages that list them and the per-game price pages, which change with
    every night's prices.
Card pages are left to the sitemap: there are 39,000 of them and their
change is one number.

The key is public by design: the engines fetch https://cardstock.guide/{KEY}.txt
to see that whoever pings owns the site. Until that file is live (it ships
with a deploy) the step does nothing.

    indexnow_ping.py            # send
    indexnow_ping.py --dry-run  # print what would be sent
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE, PRED_DB
from games import GAMES

HOST = "cardstock.guide"
SITE = f"https://{HOST}"
KEY = "d6c99def3d26fd7fd7061909297a0a4e"      # client/public/{KEY}.txt
ENDPOINT = "https://api.indexnow.org/indexnow"
SENT = os.path.join(BASE, "ml_data", "indexnow_sent.json")
HUBS = ["/", "/articles", "/reports", "/sets"]


def already_sent():
    try:
        with open(SENT, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def urls(pred, sent):
    """(new article and report URLs, the hub URLs)."""
    fresh = []
    for table, base in (("articles", "articles"), ("reports", "reports")):
        try:
            for (slug,) in pred.execute(f"SELECT slug FROM {table} ORDER BY published_at"):
                u = f"{SITE}/{base}/{slug}"
                if u not in sent:
                    fresh.append(u)
        except sqlite3.Error:
            pass
    hubs = [SITE + h for h in HUBS] + [f"{SITE}/sets/{g}" for g in GAMES]
    return fresh, hubs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        pred = sqlite3.connect(f"file:{os.path.normpath(PRED_DB)}?mode=ro", uri=True, timeout=60)
        sent = already_sent()
        fresh, hubs = urls(pred, sent)
        pred.close()
        batch = fresh + hubs
        print(f"indexnow: {len(fresh)} new page(s), {len(hubs)} hub page(s)")
        if args.dry_run:
            for u in batch:
                print("  " + u)
            return
        import requests
        live = requests.get(f"{SITE}/{KEY}.txt", timeout=30,
                            headers={"User-Agent": "indexnow-ping"})
        if live.status_code != 200 or live.text.strip() != KEY:
            print("indexnow: the key file is not live yet (it ships with a deploy); nothing sent")
            return
        r = requests.post(ENDPOINT, timeout=60,
                          headers={"Content-Type": "application/json; charset=utf-8",
                                   "User-Agent": "indexnow-ping"},
                          data=json.dumps({"host": HOST, "key": KEY,
                                           "keyLocation": f"{SITE}/{KEY}.txt",
                                           "urlList": batch}))
        print(f"indexnow: answered {r.status_code}")
        if r.status_code in (200, 202):
            tmp = SENT + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sorted(sent | set(fresh)), f)
            os.replace(tmp, SENT)
    except Exception as e:                                       # noqa: BLE001
        print(f"non-fatal error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
