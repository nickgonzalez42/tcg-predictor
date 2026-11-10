#!/usr/bin/env python3
"""Rearrange the stored set articles into the current section order
(game_articles.SET_ORDER): the set's performance sections first, the card
lists after (user, 2026-11-10). Prose, charts and tables are untouched; only
the order of the <h2> runs changes.

  --apply       rewrite body_html in the local predictions.db
  --sql PATH    also write portable UPDATE statements for prod (quotes
                doubled; never sqlite3 .dump, prod's sqlite cannot parse it)
Without --apply it only reports what would change.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from game_articles import reorder_set_body   # noqa: E402
from report_pages import PRED_DB             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=PRED_DB)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sql", help="write prod UPDATE statements here")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=60)
    rows = conn.execute("SELECT slug, body_html FROM articles WHERE kind='set'").fetchall()
    changed = [(slug, reorder_set_body(body)) for slug, body in rows]
    changed = [(slug, new) for (slug, _), (_, new) in zip(rows, changed) if new != dict(rows)[slug]]
    for slug, _ in changed:
        print(f"reorder {slug}")
    print(f"{len(changed)} of {len(rows)} set article(s) change")
    if args.sql:
        with open(args.sql, "w") as f:
            f.write("BEGIN;\n")
            for slug, new in changed:
                f.write(f"UPDATE articles SET body_html = '{new.replace(chr(39), chr(39) * 2)}' "
                        f"WHERE slug = '{slug}';\n")
            f.write("COMMIT;\n")
        print(f"wrote {args.sql}")
    if args.apply and changed:
        with conn:
            conn.executemany("UPDATE articles SET body_html = ? WHERE slug = ?",
                             [(new, slug) for slug, new in changed])
        print("applied")
    conn.close()


if __name__ == "__main__":
    main()
