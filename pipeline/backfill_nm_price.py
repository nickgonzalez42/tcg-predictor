"""
Denormalize each card's latest Near Mint (ungraded) price into the cards table
as `near_mint_price`, sourced from price_history_unified (grade='ungraded').

This is what the catalog headline shows and sorts by. Keeping it as a column in
the SAME database lets the API sort by price in SQL (the two card DBs and
pricecharting.db are separate SQLite files and can't be joined in one query).

Run after rebuilding the unified history (e.g. in the weekly refresh):
    .venv/bin/python backfill_nm_price.py
"""

import os
import sqlite3

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")


def latest_ungraded(game):
    """product_id -> most-recent ungraded (Near Mint) price."""
    rows = sqlite3.connect(PC_DB).execute(
        "SELECT product_id, date, price FROM price_history_unified "
        "WHERE game=? AND grade='ungraded'", (game,)).fetchall()
    latest, on = {}, {}
    for pid, d, p in rows:
        if pid not in on or d > on[pid]:
            on[pid], latest[pid] = d, p
    return latest


def backfill(game):
    prices = latest_ungraded(game)
    # Generous busy timeout: a catalog scrape may be writing this DB in parallel.
    con = sqlite3.connect(os.path.join(BASE, f"{game}_cards.db"), timeout=60)
    cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
    if "near_mint_price" not in cols:
        con.execute("ALTER TABLE cards ADD COLUMN near_mint_price REAL")
    # Authoritative sync: cards with no unified series (unmatched or quarantined
    # PriceCharting rows) must NOT keep a stale price from an earlier source.
    con.execute("UPDATE cards SET near_mint_price=NULL")
    con.executemany("UPDATE cards SET near_mint_price=? WHERE product_id=?",
                    [(p, pid) for pid, p in prices.items()])
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    filled = con.execute("SELECT COUNT(*) FROM cards WHERE near_mint_price IS NOT NULL").fetchone()[0]
    con.close()
    print(f"[{game}] near_mint_price set on {filled}/{total} cards")


if __name__ == "__main__":
    from games import priced_games
    for g in priced_games():
        backfill(g)
