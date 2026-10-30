#!/usr/bin/env python3
"""
Put the interactive hero at the head of every stored report body
(2026-10-30). New reports get one when they are written (market_report.py);
this gives the archive the same opening without regenerating a word of it.

It only ever replaces a hero it finds at the very start of the body, or
inserts one where there is none, so nothing a report actually says is
touched. Re-running it is safe: the hero is rebuilt from the report's own
stored `thumb` facts, and the old one is dropped first.

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
        had = len(stripped) != len(body or "")
        new_body = svg + stripped
        if new_body == body:
            print(f"  {slug:36s} unchanged")
            continue
        updates.append((new_body, slug))
        print(f"  {slug:36s} {'replaced' if had else 'added'} "
              f"({len(t.get('cards', []))} cards, {len(t.get('spark', []))} bars)")
    print(f"\n{len(updates)} report(s) to update")
    if not args.apply:
        print("dry run: pass --apply to write")
        return
    conn.executemany("UPDATE reports SET body_html = ? WHERE slug = ?", updates)
    conn.commit()
    conn.close()
    print(f"wrote {len(updates)} -> {os.path.normpath(args.db)}")


if __name__ == "__main__":
    main()
