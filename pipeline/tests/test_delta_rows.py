"""The nightly delta ships rows above a rowid mark, so the crawls must not
re-write rows that have not changed, and one oversized table must not hold
the others back."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import backfill_tcg_nm_history as nm
import delta_extras as dx
import scrape_graded_history as gh


def response(newest_qty):
    return {"result": [{"variant": "Normal", "condition": "Near Mint", "buckets": [
        {"bucketStartDate": "2026-11-02", "quantitySold": "4", "transactionCount": "3",
         "lowSalePrice": "1.5", "highSalePrice": "2", "marketPrice": "1.8"},
        {"bucketStartDate": "2026-11-09", "quantitySold": str(newest_qty), "transactionCount": "1",
         "lowSalePrice": None, "highSalePrice": "2", "marketPrice": "1.9"}]}]}


class Sales(unittest.TestCase):
    def test_only_the_changed_bucket_gets_a_new_rowid(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(nm.SALES_SCHEMA if hasattr(nm, "SALES_SCHEMA") else """
            CREATE TABLE tcg_sales_history (game TEXT NOT NULL, product_id INTEGER NOT NULL,
              variant TEXT NOT NULL, condition TEXT NOT NULL, bucket_date TEXT NOT NULL,
              quantity_sold REAL, transaction_count REAL, low_sale REAL, high_sale REAL,
              market_price REAL, PRIMARY KEY (game, product_id, variant, condition, bucket_date));
            CREATE TABLE tcg_sales_scan (game TEXT NOT NULL, product_id INTEGER NOT NULL,
              fetched_at TEXT, PRIMARY KEY (game, product_id));""")
        self.assertEqual(nm.record_sales(conn, "magic", 7, response(1)), 2)
        before = dict(conn.execute("SELECT bucket_date, rowid FROM tcg_sales_history"))
        nm.record_sales(conn, "magic", 7, response(1))          # nothing changed
        self.assertEqual(dict(conn.execute("SELECT bucket_date, rowid FROM tcg_sales_history")),
                         before)
        nm.record_sales(conn, "magic", 7, response(2))          # the newest week moved
        after = dict(conn.execute("SELECT bucket_date, rowid FROM tcg_sales_history"))
        self.assertEqual(after["2026-11-02"], before["2026-11-02"])
        self.assertGreater(after["2026-11-09"], before["2026-11-09"])
        self.assertEqual(conn.execute("SELECT quantity_sold FROM tcg_sales_history "
                                      "WHERE bucket_date='2026-11-09'").fetchone()[0], 2.0)


class Graded(unittest.TestCase):
    def test_unwritten_keeps_new_and_changed_points(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE graded_price_history (game TEXT, product_id INTEGER, "
                     "printing TEXT NOT NULL DEFAULT '', grade TEXT, date TEXT, price REAL)")
        conn.executemany("INSERT INTO graded_price_history VALUES ('magic',7,?,?,?,?)", [
            ("", "psa10", "2026-10-01", 50.0), ("", "psa10", "2026-11-01", 55.0),
            ("Foil", "psa10", "2026-12-01", 99.0)])          # another printing's series
        rows = [("magic", 7, "psa10", "2026-10-01", 50.0),    # same
                ("magic", 7, "psa10", "2026-11-01", 56.0),    # revised
                ("magic", 7, "psa10", "2026-12-01", 99.0),    # new for the page's series
                ("magic", 7, "grade9", "2026-10-01", 50.0)]   # new grade
        self.assertEqual(gh.unwritten(conn, "magic", 7, rows), rows[1:])


class Stage(unittest.TestCase):
    def test_an_oversized_table_waits_alone(self):
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "pc.db")
        conn = sqlite3.connect(src)
        for t in dx.TABLES:
            conn.execute(f"CREATE TABLE {t} (k INTEGER)")
            conn.executemany(f"INSERT INTO {t} VALUES (?)", [(i,) for i in range(5)])
        conn.executemany("INSERT INTO tcg_sales_history VALUES (?)", [(i,) for i in range(40)])
        conn.commit()
        conn.close()
        old = (dx.PC_DB, dx.EXTRAS_DB, dx.STATE, dx.MAX_ROWS)
        dx.PC_DB, dx.EXTRAS_DB, dx.STATE = src, os.path.join(tmp, "x.db"), os.path.join(tmp, "s.json")
        dx.MAX_ROWS = 20
        try:
            dx.write_state({t: 2 for t in dx.TABLES}, "test")
            self.assertEqual(dx.stage(), 0)
            x = sqlite3.connect(dx.EXTRAS_DB)
            staged = dict(x.execute("SELECT tbl, n FROM meta"))
            x.close()
            self.assertEqual(staged, {"tcg_nm_history": 3, "graded_price_history": 3,
                                      "tcg_sales_scan": 3})
            dx.mark()
            marks = dx.read_state()
            self.assertEqual(marks["tcg_nm_history"], 5)
            self.assertEqual(marks["tcg_sales_history"], 2)      # still owed
        finally:
            dx.PC_DB, dx.EXTRAS_DB, dx.STATE, dx.MAX_ROWS = old


if __name__ == "__main__":
    unittest.main()
