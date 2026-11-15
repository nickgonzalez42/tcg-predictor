#!/usr/bin/env python3
"""
Render the per-game articles (game_articles.py -> predictions.db `articles`)
as standalone, crawler-visible HTML pages at /articles/{slug}, the way
report_pages.py does for the weekly reports. Same page template, same
thumbnail twin (JPEG beside the page for link previews).

Each page also carries the reader's next steps (2026-11-13, static_nav.py):
every card the piece is about, in the catalog with our forecasts, after the
opening section; after the piece, those cards again, a free portfolio and
more articles on the game.

  article_pages.py --db [--out DIR]      # every article in predictions.db
"""

import argparse
import os
import sqlite3
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE          # noqa: E402
from games import GAMES                       # noqa: E402
from report_pages import PRED_DB, long_date, page, thumbnail   # noqa: E402
from static_nav import browse_html, next_html  # noqa: E402

DEFAULT_OUT = os.path.join(BASE, "..", "tcg-predictor", "static-content", "articles")
SECTION = {"set": "Set report", "game": "Game report", "type": "Card type report"}
MORE_N = 3          # other articles listed under the piece


def section_of(kind, focus):
    return "New releases" if focus == "recent" else SECTION.get(kind, "Game report")


def catalog_count(game, set_name=None):
    """How many cards the catalog lists for the game, or for one of its sets:
    the API's own rule (CardExtensions.VisibleInCatalog: art on disk and a
    Near Mint price; sets match by name, any case). Read-only; None when the
    game's card DB is not on this machine or cannot answer."""
    cfg = GAMES.get(game)
    path = os.path.join(BASE, cfg["db"]) if cfg else None
    if not path or not os.path.exists(path):
        return None
    sql = ("SELECT COUNT(*) FROM cards WHERE near_mint_price IS NOT NULL "
           "AND image_path IS NOT NULL AND image_path != ''")
    args = ()
    if set_name:
        sql += " AND LOWER(set_name) = LOWER(?)"
        args = (set_name,)
    try:
        conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=30)
        try:
            return conn.execute(sql, args).fetchone()[0] or None
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"  card count for {game} skipped: {e}")
        return None


def browse_for(game, kind, focus, set_name, counts):
    """The cards an article is about, in the catalog: its set for a set
    report, the whole game for a game report or a new-releases piece. A set
    whose name carries a comma cannot ride the catalog's comma-separated
    sets filter (game_articles.py skips those links too), so it gets the
    whole game. `counts` caches catalog_count across articles."""
    label = GAMES.get(game, {}).get("label", game)
    one_set = kind == "set" and focus != "recent" and set_name and "," not in set_name
    key = (game, set_name if one_set else None)
    if key not in counts:
        counts[key] = catalog_count(*key)
    if one_set:
        href = f"/catalog?game={quote(game)}&sets={quote(set_name, safe='')}"
    else:
        href = f"/catalog?game={quote(game)}&sets=all"
    return {"href": href, "set": set_name if one_set else None, "label": label, "count": counts[key],
            "game": game}


def more_for(slug, game, rows):
    """Up to MORE_N other articles on the same game, newest first, with the
    link to all of them; with none yet, the newest from as many other games
    instead. -> (items [(slug, title, meta)], heading, all_href, all_text)."""
    label = GAMES.get(game, {}).get("label", game)
    # the API's order (ArticlesController.List): newest first, then by slug
    others = sorted(sorted((r for r in rows if r["slug"] != slug), key=lambda r: r["slug"]),
                    key=lambda r: r["published_at"], reverse=True)
    same = [r for r in others if r["game"] == game][:MORE_N]
    if same:
        items = [(r["slug"], r["title"],
                  f"{long_date(r['published_at'])} · {section_of(r['kind'], r['focus'])}") for r in same]
        return items, f"More from {label}", f"/articles?game={quote(game)}", f"Every {label} article"
    newest = {}
    for r in others:
        newest.setdefault(r["game"], r)
    items = [(r["slug"], r["title"],
              f"{long_date(r['published_at'])} · {GAMES.get(r['game'], {}).get('label', r['game'])}"
              f" · {section_of(r['kind'], r['focus'])}") for r in list(newest.values())[:MORE_N]]
    return items, "More articles", "/articles", "All articles"


def main():
    ap = argparse.ArgumentParser(description="Render standalone article pages")
    ap.add_argument("--db", nargs="?", const=PRED_DB,
                    help="read articles from this predictions.db (default: the live one)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db or PRED_DB)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        # focus (2026-11-07): 'recent' marks the set slot's new-releases article.
        have = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
        focus_col = "focus" if "focus" in have else "NULL AS focus"
        rows = conn.execute(
            f"SELECT slug, kind, game, set_name, title, published_at, summary, body_html, thumb, hero, "
            f"{focus_col} FROM articles").fetchall()
    except sqlite3.OperationalError:
        rows = []      # no articles yet
    conn.close()
    os.makedirs(args.out, exist_ok=True)
    counts = {}
    for r in rows:
        slug, title, published_at = r["slug"], r["title"], r["published_at"]
        thumb_url = thumbnail(slug, r["thumb"], title, published_at[:10], args.out, base="articles")
        browse = browse_for(r["game"], r["kind"], r["focus"], r["set_name"], counts)
        more, more_title, all_href, all_text = more_for(slug, r["game"], rows)
        with open(os.path.join(args.out, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page(slug, title, published_at[:10], r["summary"], r["body_html"], thumb_url, r["hero"],
                         base="articles", section=section_of(r["kind"], r["focus"]),
                         lead_html=browse_html(browse),
                         end_html=next_html(browse, more, more_title, all_href, all_text),
                         game=r["game"]))
    print(f"wrote {len(rows)} article page(s) -> {args.out}")


if __name__ == "__main__":
    main()
