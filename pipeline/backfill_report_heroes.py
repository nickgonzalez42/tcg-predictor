#!/usr/bin/env python3
"""
Fill every report's `hero` column with its interactive picture
(2026-10-30). New reports get one when they are written (market_report.py);
this gives the archive the same one without regenerating a word of it.

The hero lived at the head of body_html for a few hours; it is a column of
its own now, so the reports index can show it on each card without
fetching bodies. This also strips that leading copy out of any body that
still carries it. Nothing a report actually says is touched, and re-running
is safe: the hero is rebuilt from the report's own stored `thumb` facts.

    backfill_report_heroes.py            # dry run
    backfill_report_heroes.py --apply
"""

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from report_thumbnail import render_svg

PRED_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                       "predictions.db")
LEADING_HERO = re.compile(r'^\s*<svg class="report-hero-svg".*?</svg>', re.S)


def long_date(iso):
    try:
        return datetime.date.fromisoformat(iso[:10]).strftime("%B %-d, %Y")
    except ValueError:
        return iso


def main():
    ap = argparse.ArgumentParser(description="Backfill report hero graphics")
    ap.add_argument("--db", default=PRED_DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    for col in ("thumb", "hero"):
        try:
            conn.execute(f"ALTER TABLE reports ADD COLUMN {col} TEXT")
            print(f"added the {col} column")
        except sqlite3.OperationalError:
            pass
    rows = conn.execute("SELECT slug, title, published_at, body_html, thumb FROM reports "
                        "ORDER BY published_at DESC").fetchall()
    updates = []
    for slug, title, published_at, body, thumb in rows:
        if not thumb:
            print(f"  {slug:36s} no facts to draw from, skipped")
            continue
        t = json.loads(thumb)
        svg = render_svg(t.get("kind", "market"), title, long_date(published_at),
                         stats=[tuple(x) for x in t.get("stats", [])],
                         cards=[tuple(x) for x in t.get("cards", [])],
                         spark=[tuple(x) for x in t.get("spark", [])])
        stripped = LEADING_HERO.sub("", body or "", count=1)
        moved = len(stripped) != len(body or "")
        updates.append((svg, stripped, slug))
        print(f"  {slug:36s} hero set ({len(t.get('cards', []))} cards, "
              f"{len(t.get('spark', []))} bars)"
              + ("; removed the copy from the body" if moved else ""))
    print(f"\n{len(updates)} report(s) to update")
    if not args.apply:
        print("dry run: pass --apply to write")
        return
    conn.executemany("UPDATE reports SET hero = ?, body_html = ? WHERE slug = ?", updates)
    conn.commit()
    conn.close()
    print(f"wrote {len(updates)} -> {os.path.normpath(args.db)}")


if __name__ == "__main__":
    main()
