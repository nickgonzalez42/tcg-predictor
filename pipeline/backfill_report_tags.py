#!/usr/bin/env python3
"""
Fill the `tags` column for reports written before it existed (2026-10-29,
user request: a tag line on the article cards at /reports).

New reports are tagged by market_report.py with the game their headline is
about. For the archive the same answer is recoverable from the stored body:
every card the report names is a link carrying its game key, so the first
linked card that also appears in the report's own lede identifies the game
the headline opened on. Reports whose lede names no card fall back to the
game the body discusses first (the sections are ordered by how far the game
moved that week), and model reports span every game, so they stay untagged.

    backfill_report_tags.py           # dry run: print the table
    backfill_report_tags.py --apply   # write the tags
"""

import argparse
import html as html_mod
import os
import re
import sqlite3
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES

PRED_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                       "predictions.db")

CARD_LINK = re.compile(r"<a href=[\"']/catalog/([a-z]+)/\d+[\"']>([^<]{2,80})</a>")
LABELS = {g["label"]: key for key, g in GAMES.items()}


def _fold(text):
    """Lowercase, accent-stripped: ledes write "Pokemon" for Pokémon."""
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def tag_for(slug, summary, body):
    """The game this report's headline is about, as a display label."""
    if slug.startswith("model-report"):
        return ""           # the model report covers every game
    lede = html_mod.unescape(summary or "").lower()
    for game, name in CARD_LINK.findall(body or ""):
        plain = html_mod.unescape(name).strip()
        # Printing suffixes ("Dominus Purge (Alternate Art)") are not in the
        # lede, so match on the name before the parenthesis.
        stem = plain.split(" (")[0].strip().lower()
        if len(stem) >= 4 and stem in lede and game in GAMES:
            return GAMES[game]["label"]
    # No linked card from the lede: take the game the lede names outright
    # ("Gundam was the week's weakest market"), accent and case insensitive
    # so "Pokemon" finds Pokémon.
    flat = _fold(lede)
    named = [(flat.find(_fold(label)), label) for label in LABELS
             if _fold(label) in flat]
    if named:
        return min(named)[1]
    # Nothing else is tagged. A week whose lede names no card and no game was
    # a breadth week ("more cards fell than rose"), and hanging the
    # furthest-moving game on it would claim a story the report never told.
    return ""


def main():
    ap = argparse.ArgumentParser(description="Backfill report tags")
    ap.add_argument("--db", default=PRED_DB)
    ap.add_argument("--apply", action="store_true", help="write the tags")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        print("added the tags column")
    except sqlite3.OperationalError:
        pass
    rows = conn.execute("SELECT slug, published_at, title, summary, body_html, tags "
                        "FROM reports ORDER BY published_at DESC").fetchall()
    updates = []
    for slug, published, title, summary, body, tags in rows:
        tag = tag_for(slug, summary, body)
        print(f"{published}  {tag or '(none)':<20}{title[:58]}")
        if tag != (tags or ""):
            updates.append((tag, slug))
    print(f"\n{len(updates)} of {len(rows)} report(s) change")
    if not args.apply:
        print("dry run: pass --apply to write")
        return
    conn.executemany("UPDATE reports SET tags = ? WHERE slug = ?", updates)
    conn.commit()
    conn.close()
    print(f"wrote {len(updates)} tag(s) -> {os.path.normpath(args.db)}")


if __name__ == "__main__":
    main()
