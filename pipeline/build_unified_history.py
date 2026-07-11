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
import os
import sqlite3

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

from games import priced_games
GAMES = priced_games()


def pc_history(game):
    """grade -> product_id -> {YYYY-MM: price} from the crawled monthly history."""
    # Ordered by date so when a month holds both a chart point and a later weekly
    # snapshot point, the most recent (month-end) value wins deterministically.
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT grade, product_id, date, price FROM graded_price_history WHERE game=? "
        "ORDER BY date", (game,)).fetchall()
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for grade, pid, d, p in rows:
        out[grade][pid][d[:7]] = p
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
    for grade, by_pid in pc_history(game).items():
        for pid, months in by_pid.items():
            if pid in skip:
                continue
            for m, p in months.items():
                rows.append((game, pid, grade, m + "-01", p, "pricecharting"))
                counts[grade] += 1
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
