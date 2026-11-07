#!/usr/bin/env python3
"""
Render the per-game articles (game_articles.py -> predictions.db `articles`)
as standalone, crawler-visible HTML pages at /articles/{slug}, the way
report_pages.py does for the weekly reports. Same page template, same
thumbnail twin (JPEG beside the page for link previews).

  article_pages.py --db [--out DIR]      # every article in predictions.db
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE          # noqa: E402
from report_pages import PRED_DB, page, thumbnail   # noqa: E402

DEFAULT_OUT = os.path.join(BASE, "..", "tcg-predictor", "static-content", "articles")
SECTION = {"set": "Set report", "game": "Game report"}


def main():
    ap = argparse.ArgumentParser(description="Render standalone article pages")
    ap.add_argument("--db", nargs="?", const=PRED_DB,
                    help="read articles from this predictions.db (default: the live one)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db or PRED_DB)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        rows = conn.execute(
            "SELECT slug, kind, title, published_at, summary, body_html, thumb, hero "
            "FROM articles").fetchall()
    except sqlite3.OperationalError:
        rows = []      # no articles yet
    conn.close()
    os.makedirs(args.out, exist_ok=True)
    for slug, kind, title, published_at, summary, body_html, thumb, hero in rows:
        thumb_url = thumbnail(slug, thumb, title, published_at[:10], args.out, base="articles")
        with open(os.path.join(args.out, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page(slug, title, published_at[:10], summary, body_html, thumb_url, hero,
                         base="articles", section=SECTION.get(kind, "Game report")))
    print(f"wrote {len(rows)} article page(s) -> {args.out}")


if __name__ == "__main__":
    main()
