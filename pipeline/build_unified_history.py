"""
Build one monthly price-history table per card, every condition tier —
priced EXCLUSIVELY from PriceCharting.

TCGplayer supplies the card catalog, details, and images only; every price in
the app (ungraded headline, graded ladders, history charts, model training)
comes from PriceCharting's crawled monthly history:

  ungraded          the raw/loose price
  grade7..sgc10     graded tiers, passthrough

Output: pricecharting.db `price_history_unified` (game, product_id, grade, date, price, source).

Run after the graded-history crawl completes:  .venv/bin/python build_unified_history.py
"""

import collections
import csv
import os
import sqlite3

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
CORRECTIONS_CSV = os.path.join(BASE, "ml_data", "price_corrections.csv")

from games import priced_games


def load_corrections(game):
    """(product_id, grade) -> [(from_date, to_date, price|None)] corrections.

    Applied at build time so the raw crawl stays as-scraped (auditable) while
    the serving/model layer gets the fix — and a recrawl can't resurrect the
    bad points. Two forms of source-side damage PC never repairs:
      price set   -> REPLACE the range (a real card mispriced for months,
                     e.g. Maleficent D23 raw at $2.50 while graded held $1k+)
      price empty -> DROP the range (history from before the card existed —
                     there is no true value to substitute)
    grade '*' applies to every tier."""
    if not os.path.exists(CORRECTIONS_CSV):
        return {}
    out = collections.defaultdict(list)
    with open(CORRECTIONS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["game"] == game:
                out[(int(r["product_id"]), r["grade"])].append(
                    (r["from_date"], r["to_date"],
                     float(r["price"]) if r["price"].strip() else None))
    return out
GAMES = priced_games()


def pc_history(game):
    """grade -> product_id -> {YYYY-MM: (real_date, price)} from the crawled history.

    Ordered by date so when a month holds both a chart point and later daily
    snapshot points, the most recent value wins — and the bucket carries that
    point's REAL date, so "as of" displays don't undersell freshness (a July
    bucket updated by yesterday's snapshot is dated yesterday, not July 1)."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT grade, product_id, date, price FROM graded_price_history WHERE game=? "
        "ORDER BY date", (game,)).fetchall()
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for grade, pid, d, p in rows:
        out[grade][pid][d[:7]] = (d[:10], p)
    return out


def suspects(game):
    """Cards whose PriceCharting match failed the sanity gate — excluded from
    the unified history so stale mismatched points can't resurface."""
    try:
        return {r[0] for r in sqlite3.connect(PC_DB, timeout=30).execute(
            "SELECT product_id FROM pc_match_suspects WHERE game=?", (game,))}
    except sqlite3.OperationalError:
        return set()   # table appears after the first gated pc-match run


def build_game(game):
    rows = []
    counts = collections.Counter()
    skip = suspects(game)
    corrections = load_corrections(game)
    n_corrected = 0
    for grade, by_pid in pc_history(game).items():
        for pid, months in by_pid.items():
            if pid in skip:
                continue
            fixes = corrections.get((pid, grade), []) + corrections.get((pid, "*"), [])
            for (d, p) in months.values():
                dropped = False
                for lo, hi, price in fixes:
                    if lo <= d <= hi:
                        if price is None:
                            dropped = True
                        else:
                            p = price
                        n_corrected += 1
                        break
                if dropped:
                    continue
                rows.append((game, pid, grade, d, p, "pricecharting"))
                counts[grade] += 1
    if n_corrected:
        print(f"[{game}] {n_corrected} point(s) replaced by price_corrections.csv")
    print(f"[{game}] rows per tier: " +
          ", ".join(f"{g}={n}" for g, n in sorted(counts.items())))
    return rows


def main():
    all_rows = []
    for game in GAMES:
        all_rows += build_game(game)

    conn = sqlite3.connect(PC_DB, timeout=60)
    conn.executescript(
        """
        DROP TABLE IF EXISTS price_history_unified;
        CREATE TABLE price_history_unified (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL    NOT NULL,
            source     TEXT    NOT NULL,
            PRIMARY KEY (game, product_id, grade, date)
        );
        """
    )
    conn.executemany("INSERT OR REPLACE INTO price_history_unified VALUES (?,?,?,?,?,?)", all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(PC_DB)} (price_history_unified)")


if __name__ == "__main__":
    main()
