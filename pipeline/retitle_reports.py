#!/usr/bin/env python3
"""
Give the already-published reports descriptive titles (2026-10-29, user
request: "give the articles a descriptive title").

Every report written before today was titled "Weekly Market Report: <date>",
which told a reader (and a search engine) nothing about the week. New reports
headline themselves from the facts pack (market_report.py + the "headline"
field in report_editorial.py); this backfills the archive in one pass.

Each report's own lede is the source material, so no number can drift: one
headless `claude -p` call turns the stored ledes into headlines and anything
that fails validation keeps the row's existing title. The date moves to the
page byline, so dropping it from the title loses nothing.

    retitle_reports.py              # dry run: print the before/after table
    retitle_reports.py --apply      # write the new titles
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from report_editorial import LIMITS, _clean, _find_cli, _headline

PRED_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                       "predictions.db")

PROMPT = """You are the editor of CardStock, a trading-card price site. Each \
JSON object below is one already-published weekly report: its slug, the date \
it ran, its current placeholder title and its opening paragraph (the lede).

Write a descriptive headline for each, drawn ONLY from its own lede.

Rules for every headline:
- Max {cap} words, sentence case, plain collector language.
- Say what actually happened that week: name the card, group or game that \
drove it and what it did. A reader should be able to tell two reports apart.
- Use only facts and numbers present in that report's lede, rounded as given. \
Invent nothing.
- No date, no label prefix ("Weekly Market Report:", "Model Report:"), no \
quotation marks, no final period, no em dashes or en dashes.
- A slug starting with "model-report" is about how the price forecasts have \
performed: headline the record, and never use the words "graded", \
"calibration" or "cohort".

Return ONLY a JSON object mapping each slug to its headline string, no code \
fences and no commentary.

REPORTS:
{reports}
"""


def rows(conn):
    return conn.execute("SELECT slug, title, published_at, summary FROM reports "
                        "ORDER BY published_at DESC").fetchall()


def fallback(summary):
    """First sentence of the lede, trimmed to headline length: descriptive
    without a model in the loop."""
    first = re.split(r"(?<=[.!?])\s", (summary or "").strip())[0]
    first = first.rstrip(".").strip()
    return first if 12 <= len(first) <= 95 else None


def ask(cli, items, cap):
    prompt = PROMPT.format(cap=cap, reports=json.dumps(items, indent=1))
    out = subprocess.run([cli, "-p", "--model", "claude-sonnet-5",
                          "--output-format", "text"],
                         input=prompt, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        print(f"claude CLI exit {out.returncode}: {out.stderr.strip()[:200]}",
              file=sys.stderr)
        return {}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", out.stdout.strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        print("no JSON in reply", file=sys.stderr)
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        print("bad JSON in reply", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def main():
    ap = argparse.ArgumentParser(description="Backfill descriptive report titles")
    ap.add_argument("--db", default=PRED_DB)
    ap.add_argument("--apply", action="store_true", help="write the new titles")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    reports = rows(conn)
    if not reports:
        raise SystemExit("no reports in that database")
    cap = LIMITS["headline"]
    items = [{"slug": s, "ran_on": d, "current_title": t, "lede": sm}
             for s, t, d, sm in reports]
    cli = _find_cli()
    written = ask(cli, items, cap) if cli else {}
    if not cli:
        print("claude CLI not found: using the lede's first sentence", file=sys.stderr)

    updates, kept = [], []
    for slug, title, published, summary in reports:
        new = _headline(_clean(written.get(slug), cap)) or fallback(summary)
        if new and new != title:
            updates.append((new, slug))
            print(f"{published}  {title}\n            -> {new}")
        else:
            kept.append(slug)
            print(f"{published}  {title}\n            -> (unchanged)")
    print(f"\n{len(updates)} retitled, {len(kept)} unchanged")
    if not args.apply:
        print("dry run: pass --apply to write")
        return
    conn.executemany("UPDATE reports SET title = ? WHERE slug = ?", updates)
    conn.commit()
    conn.close()
    print(f"wrote {len(updates)} title(s) -> {os.path.normpath(args.db)}")


if __name__ == "__main__":
    main()
