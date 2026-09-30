#!/usr/bin/env python3
"""
One-time schema migration for multi-printing support (Phase 1, 2026-08-08).

Adds a `printing` column ('' = base printing) INSIDE the primary key of the
two raw price-history tables, so one card can carry one series per printing:

  tcg_nm_history        PK (game, product_id, date)
                     -> PK (game, product_id, printing, date)
  graded_price_history  PK (game, product_id, grade, date)
                     -> PK (game, product_id, printing, grade, date)

SQLite can't alter a PK, so each table is rebuilt (existing rows keep
printing=''). price_history_unified is NOT migrated here — build_unified
recreates it and detects the missing column itself. Idempotent: tables that
already have the column are skipped.

Run:  .venv/bin/python migrate_printing_schema.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")

REBUILDS = {
    "tcg_nm_history": (
        """
        CREATE TABLE tcg_nm_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL DEFAULT '',  -- '' = base printing
            date       TEXT    NOT NULL,   -- YYYY-MM-DD, the scrape day
            price      REAL    NOT NULL,   -- TCGplayer Near Mint market price
            PRIMARY KEY (game, product_id, printing, date)
        )
        """,
        "INSERT INTO tcg_nm_history (game, product_id, printing, date, price) "
        "SELECT game, product_id, '', date, price FROM old_tcg_nm_history",
        ["CREATE INDEX idx_tcgnm_game_pid ON tcg_nm_history(game, product_id)"],
    ),
    "graded_price_history": (
        """
        CREATE TABLE graded_price_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL DEFAULT '',  -- '' = matched page's printing
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL    NOT NULL,
            PRIMARY KEY (game, product_id, printing, grade, date)
        )
        """,
        "INSERT INTO graded_price_history (game, product_id, printing, grade, date, price) "
        "SELECT game, product_id, '', grade, date, price FROM old_graded_price_history",
        [],
    ),
}


def main():
    conn = sqlite3.connect(PC_DB, timeout=120)
    for table, (create_sql, copy_sql, indexes) in REBUILDS.items():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "printing" in cols:
            print(f"{table}: already migrated — skipped")
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: rebuilding {n:,} rows with printing in the PK...")
        conn.executescript(
            f"""
            BEGIN;
            ALTER TABLE {table} RENAME TO old_{table};
            {create_sql};
            {copy_sql};
            DROP TABLE old_{table};
            COMMIT;
            """)
        for idx in indexes:
            conn.execute(idx)
        conn.commit()
        n2 = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n2 == n, f"{table}: row count changed {n} -> {n2}"
        print(f"{table}: done ({n2:,} rows, printing='')")
    conn.close()


if __name__ == "__main__":
    main()
