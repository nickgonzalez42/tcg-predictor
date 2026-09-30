#!/usr/bin/env python3
"""
One-time predictions.db migration for per-printing forecasts (2026-08-10).

Adds `printing` ('' = base) INSIDE the primary keys of `forecasts` and
`forecast_archive`. Existing rows — all base-printing by definition — get ''.
Idempotent. Runs on the TRAINER (authoritative copy); prod inherits via the
normal predictions.db push.

Run:  .venv/bin/python migrate_predictions_printing.py [db_path]
"""

import os
import sqlite3
import sys

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "dotnet", "API", "Data", "cards", "predictions.db")

REBUILDS = {
    "forecasts": (
        """
        CREATE TABLE forecasts (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT, base_price REAL, forecast_price REAL,
            low REAL, high REAL, ret REAL, reason TEXT,
            confidence TEXT,
            model_version TEXT, scored_at TEXT,
            anchor_date TEXT,
            printing TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (game, product_id, printing, target, horizon)
        )
        """,
        "INSERT INTO forecasts SELECT *, '' FROM old_forecasts",
    ),
    "forecast_archive": (
        """
        CREATE TABLE forecast_archive (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT NOT NULL,
            base_price REAL, forecast_price REAL, low REAL, high REAL, ret REAL,
            confidence TEXT, model_version TEXT, scored_at TEXT,
            realized_price REAL, realized_ret REAL, realized_at TEXT, graded_at TEXT,
            printing TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (game, product_id, printing, target, horizon, as_of)
        )
        """,
        "INSERT INTO forecast_archive SELECT *, '' FROM old_forecast_archive",
    ),
}


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    conn = sqlite3.connect(db, timeout=120)
    for table, (create_sql, copy_sql) in REBUILDS.items():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            print(f"{table}: absent — skipped")
            continue
        if "printing" in cols:
            print(f"{table}: already migrated — skipped")
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: rebuilding {n:,} rows with printing in the PK...")
        conn.executescript(
            f"BEGIN; ALTER TABLE {table} RENAME TO old_{table}; {create_sql}; "
            f"{copy_sql}; DROP TABLE old_{table}; COMMIT;")
        n2 = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n2 == n, f"{table}: {n} -> {n2}"
        print(f"{table}: done ({n2:,} rows, printing='')")
    conn.close()


if __name__ == "__main__":
    main()
