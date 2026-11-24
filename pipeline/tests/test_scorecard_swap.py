"""The scorecard's tables are replaced in one step, never left empty (2026-11-24)."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import forecast_scorecard as fs

NOW = "2026-11-24T23:00:00+00:00"


def archive(path):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE forecast_archive (
            game TEXT NOT NULL, product_id INTEGER NOT NULL, target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT NOT NULL, base_price REAL, forecast_price REAL, low REAL, high REAL, ret REAL,
            confidence TEXT, model_version TEXT, scored_at TEXT,
            realized_price REAL, realized_ret REAL, realized_at TEXT, graded_at TEXT,
            printing TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (game, product_id, printing, target, horizon, as_of))""")
    rows = [  # game, pid, horizon, as_of, base, low, high, ret, realized_price, realized_ret, realized_at
        ("magic", 1, "1m", "2026-10-06", 20.0, 18.0, 23.0, 0.05, 21.0, 0.049, "2026-11-06"),
        ("magic", 2, "1m", "2026-10-06", 5.0, 4.5, 5.5, 0.00, 6.5, 0.262, "2026-11-06"),
        ("magic", 3, "1m", "2026-10-13", 40.0, 36.0, 44.0, -0.03, 38.0, -0.051, "2026-11-13"),
        ("lorcana", 4, "1m", "2026-10-13", 12.0, 11.0, 13.0, 0.02, 12.1, 0.008, "2026-11-13"),
        ("lorcana", 5, "launch1m", "2026-10-13", 9.0, 7.0, 11.0, 0.10, 8.0, -0.118, "2026-11-13"),
        ("lorcana", 6, "1m", "2026-11-20", 9.0, 7.0, 11.0, 0.10, None, None, None),     # not due yet
    ]
    conn.executemany(
        "INSERT INTO forecast_archive (game, product_id, target, horizon, as_of, base_price, low, high, ret, "
        "model_version, realized_price, realized_ret, realized_at) VALUES (?,?,'ungraded',?,?,?,?,?,?,'v4.5',?,?,?)",
        rows)
    conn.commit()
    return conn


def dump(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()


class Swap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "pred.db")
        self.conn = archive(self.path)

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def run_all(self):
        fs.rebuild_accuracy(self.conn, NOW)
        weeks = fs.write_weekly_accuracy(self.conn)
        early = fs.write_weekly_accuracy(self.conn, "launch1m", "forecast_accuracy_weekly_early", banked=False)
        return weeks, early

    def test_the_tables_are_built(self):
        weeks, early = self.run_all()
        self.conn.commit()
        acc = {(g, t, h): n for g, t, h, n, *_ in dump(self.conn, "forecast_accuracy")}
        self.assertEqual(acc, {("magic", "ungraded", "1m"): 3, ("lorcana", "ungraded", "1m"): 1,
                               ("lorcana", "ungraded", "launch1m"): 1})
        self.assertEqual((weeks, early), (2, 1))
        self.assertEqual([r[:2] for r in dump(self.conn, "forecast_accuracy_weekly")],
                         [("2026-11-06", 2), ("2026-11-13", 2)])

    def test_nothing_is_visible_until_the_commit(self):
        self.run_all()
        self.conn.commit()
        before = dump(self.conn, "forecast_accuracy")
        # the next night: grading has a transaction open, then the rebuild runs
        self.conn.execute("UPDATE forecast_archive SET realized_price=9.5, realized_ret=0.054, "
                          "realized_at='2026-11-20' WHERE product_id=6")
        self.run_all()
        other = sqlite3.connect(self.path)
        self.assertEqual(dump(other, "forecast_accuracy"), before)      # readers still see last night's
        self.conn.commit()
        self.assertEqual({r[:4] for r in dump(other, "forecast_accuracy")} - {r[:4] for r in before},
                         {("lorcana", "ungraded", "1m", 2)})
        other.close()

    def test_a_run_that_dies_while_counting_leaves_last_nights_tables(self):
        self.run_all()
        self.conn.commit()
        before = {t: dump(self.conn, t) for t in ("forecast_accuracy", "forecast_accuracy_weekly",
                                                  "forecast_accuracy_weekly_early")}
        real = fs.weekly_rows
        fs.weekly_rows = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("interrupted"))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.run_all()
        finally:
            fs.weekly_rows = real
        self.conn.close()                                   # the process is gone; nothing was committed
        self.conn = sqlite3.connect(self.path)
        for t, rows in before.items():
            self.assertEqual(dump(self.conn, t), rows)
            self.assertTrue(rows)

    def test_no_open_transaction_is_handled(self):
        self.assertFalse(self.conn.in_transaction)
        self.run_all()
        self.assertTrue(self.conn.in_transaction)           # the caller commits
        self.conn.rollback()
        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='forecast_accuracy'").fetchone())


if __name__ == "__main__":
    unittest.main()
