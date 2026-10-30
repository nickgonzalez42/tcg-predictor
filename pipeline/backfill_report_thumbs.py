#!/usr/bin/env python3
"""
Give already-published reports something to draw a thumbnail from
(2026-10-30). New reports store their own facts when they are written
(market_report.py); the archive predates that, so this reconstructs what it
honestly can from each stored body: the cards the report singled out, one per
game, which is what the picture's fan shows.

Stats and the per-game bars are left empty rather than guessed: those numbers
belong to that week and are not recoverable from the body's prose. Those
reports get a headline, a chip and a fan of their own cards.

    backfill_report_thumbs.py            # dry run
    backfill_report_thumbs.py --apply
"""

import argparse
import datetime
import json
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

PRED_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                       "predictions.db")
CARD_LINK = re.compile(r"/catalog/([a-z]+)/(\d+)")


def cards_from(body):
    """Up to six cards the report links, one per game, in the order it
    discusses them."""
    out, seen = [], set()
    for game, pid in CARD_LINK.findall(body or ""):
        if game in seen:
            continue
        seen.add(game)
        out.append([game, int(pid)])
        if len(out) == 6:
            break
    return out


TEASER_UP = re.compile(r"report-up'>([\d,]+) \u25b2")
TEASER_DOWN = re.compile(r"report-down'>([\d,]+) \u25bc")


def market_stats(body):
    """That week's breadth, summed from the per-game lines the report itself
    prints. Reports older than the collapsible game sections carry no such
    counts and simply get no stats."""
    ups = [int(n.replace(",", "")) for n in TEASER_UP.findall(body or "")]
    downs = [int(n.replace(",", "")) for n in TEASER_DOWN.findall(body or "")]
    if not ups or not downs:
        return []
    return [[f"{sum(downs):,}", "cards fell", "down"],
            [f"{sum(ups):,}", "cards rose", "up"],
            [f"{len(ups)}", "games tracked", None]]


def model_stats(conn, published_at, today):
    """The forecast record, for a model report's picture.

    Read from forecast_accuracy, which is the same table the report itself
    was generated from — so this is only honest for the newest report. An
    older one gets no stats rather than today's record under its date.
    """
    if (today - published_at).days > 2:
        return []
    try:
        row = conn.execute(
            "SELECT SUM(n), SUM(n * band_hit_rate) / SUM(n), SUM(n * ret_mae) / SUM(n) "
            "FROM forecast_accuracy WHERE horizon='1m' AND target='ungraded' AND n > 0"
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or not row[0]:
        return []
    n, hit, mae = row
    return [[f"{int(n):,}", "forecasts checked", None],
            [f"{hit * 100:.0f}%", "inside the range", "up"],
            [f"±{(math.exp(mae) - 1) * 100:.1f}%", "typical miss", None]]


def main():
    ap = argparse.ArgumentParser(description="Backfill report thumbnail inputs")
    ap.add_argument("--db", default=PRED_DB)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="rewrite reports that already carry thumbnail inputs")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    try:
        conn.execute("ALTER TABLE reports ADD COLUMN thumb TEXT")
        print("added the thumb column")
    except sqlite3.OperationalError:
        pass
    rows = conn.execute("SELECT slug, title, published_at, body_html, thumb FROM reports "
                        "ORDER BY published_at DESC").fetchall()
    # A model report's body is tables and charts, no cards. It borrows the
    # fan from the market report of the same Friday: those are the cards that
    # week's forecasts were about.
    market_cards = {p[:10]: cards_from(b) for s, _t, p, b, _th in rows
                    if not s.startswith("model-report")}
    today = datetime.date.today()
    updates = []
    for slug, title, published_at, body, thumb in rows:
        if thumb and not args.force:
            print(f"  {slug:36s} already has one")
            continue
        kind = "model" if slug.startswith("model-report") else "market"
        cards = cards_from(body) or market_cards.get(published_at[:10], [])
        stats = (model_stats(conn, datetime.date.fromisoformat(published_at[:10]), today)
                 if kind == "model" else market_stats(body))
        updates.append((json.dumps({"kind": kind, "stats": stats, "spark": [], "cards": cards}), slug))
        print(f"  {slug:36s} {len(cards)} card(s), {len(stats)} stat(s)")
    print(f"\n{len(updates)} report(s) to fill")
    if not args.apply:
        print("dry run: pass --apply to write")
        return
    conn.executemany("UPDATE reports SET thumb = ? WHERE slug = ?", updates)
    conn.commit()
    conn.close()
    print(f"wrote {len(updates)} -> {os.path.normpath(args.db)}")


if __name__ == "__main__":
    main()
