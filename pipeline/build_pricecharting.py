"""
Import PriceCharting graded prices and match them to our cards.

PriceCharting rows carry a `tcg-id` = the TCGplayer product id, which is exactly
our cards' `product_id`, so matching is an EXACT join (no fuzzy name logic).

CSV prices are dollar strings ("$373.53"); we store them as REAL USD to match
our existing market_price. Output: a `pricecharting` table keyed (game, product_id)
in predictions.db-adjacent `pricecharting.db`, read-only app data separate from
the scraper card DBs.

Run:  .venv/bin/python build_pricecharting.py
"""

import csv
import os
import sqlite3
from datetime import datetime, timezone

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

# game -> (our card DB, PriceCharting CSV)
SOURCES = {
    "pokemon": ("pokemon_cards.db", "pricecharting_pokemon.csv"),
    "onepiece": ("onepiece_cards.db", "pricecharting_onepiece.csv"),
}

# CSV column -> our column (graded tiers)
PRICE_COLS = {
    "loose-price": "ungraded",
    "cib-price": "grade7",
    "new-price": "grade8",
    "graded-price": "grade9",
    "box-only-price": "grade95",
    "manual-only-price": "psa10",
    "bgs-10-price": "bgs10",
    "condition-17-price": "cgc10",
    "condition-18-price": "sgc10",
}


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def to_int(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def import_game(game, card_db, csv_name, now):
    ours = set(r[0] for r in sqlite3.connect(os.path.join(BASE, card_db)).execute("SELECT product_id FROM cards"))

    rows, seen = [], set()
    total_pc = matched = 0
    with open(os.path.join(BASE, csv_name), encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f):
            tid = to_int(r.get("tcg-id", ""))
            if tid is None:
                continue
            total_pc += 1
            if tid not in ours or tid in seen:
                continue
            seen.add(tid)
            matched += 1
            rec = {our: money(r.get(col)) for col, our in PRICE_COLS.items()}
            rows.append((
                game, tid, to_int(r.get("id", "")),
                rec["ungraded"], rec["grade7"], rec["grade8"], rec["grade9"], rec["grade95"],
                rec["psa10"], rec["bgs10"], rec["cgc10"], rec["sgc10"],
                to_int(r.get("sales-volume", "")),
                r.get("console-name"), r.get("product-name"), now,
            ))
    print(f"[{game}] our cards: {len(ours)} | PC rows w/ tcg-id: {total_pc} | matched: {matched} "
          f"({matched/len(ours)*100:.1f}%)")
    return rows


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_rows = []
    for game, (card_db, csv_name) in SOURCES.items():
        all_rows += import_game(game, card_db, csv_name, now)

    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.executescript(
        """
        DROP TABLE IF EXISTS pricecharting;
        CREATE TABLE pricecharting (
            game         TEXT    NOT NULL,
            product_id   INTEGER NOT NULL,
            pc_id        INTEGER,
            ungraded     REAL,
            grade7       REAL,
            grade8       REAL,
            grade9       REAL,
            grade95      REAL,
            psa10        REAL,
            bgs10        REAL,
            cgc10        REAL,
            sgc10        REAL,
            sales_volume INTEGER,
            pc_console   TEXT,
            pc_name      TEXT,
            updated_at   TEXT,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO pricecharting VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", all_rows)
    append_snapshot_history(conn, all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(OUT_DB)}")


# Tiers in the order they sit in the snapshot rows (indices 3..11).
HISTORY_TIERS = ["ungraded", "grade7", "grade8", "grade9", "grade95",
                 "psa10", "bgs10", "cgc10", "sgc10"]


def append_snapshot_history(conn, snapshot_rows):
    """Append today's snapshot prices into graded_price_history (append-only).

    The chart-page crawl only backfills a card's history once; these weekly
    snapshot points keep every matched card's graded series growing — including
    bgs10/cgc10/sgc10, which chart pages don't carry. Keyed by date, so re-runs
    on the same day are idempotent and old history is never touched.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS graded_price_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL    NOT NULL,
            PRIMARY KEY (game, product_id, grade, date)
        );
        """
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    points = [
        (row[0], row[1], tier, today, row[3 + i])
        for row in snapshot_rows
        for i, tier in enumerate(HISTORY_TIERS)
        if row[3 + i] is not None
    ]
    conn.executemany("INSERT OR REPLACE INTO graded_price_history VALUES (?,?,?,?,?)", points)
    print(f"appended {len(points)} snapshot points ({today}) to graded_price_history")


if __name__ == "__main__":
    main()
