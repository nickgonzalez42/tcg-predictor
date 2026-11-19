"""switch_card: a card's main ('') price series moves to its Normal printing."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_before = set(sys.modules)
import switch_base_printing as sb
for _m in set(sys.modules) - _before - {"switch_base_printing"}:
    del sys.modules[_m]          # leave other test modules their own TCG_DATA_DIR


def db():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE tcg_nm_history (game TEXT, product_id INT, printing TEXT DEFAULT '',
            date TEXT, price REAL, PRIMARY KEY (game, product_id, printing, date));
        CREATE TABLE tcg_csv_base (game TEXT, product_id INT, printing TEXT, source TEXT,
            decided_at TEXT, PRIMARY KEY (game, product_id));
        CREATE TABLE printing_variants (game TEXT, product_id INT, variant TEXT, sku_id TEXT,
            latest_price REAL, points INT, is_base INT, checked_at TEXT,
            PRIMARY KEY (game, product_id, variant));
        CREATE TABLE pc_printing_matches (game TEXT, product_id INT, printing TEXT, pc_id INT,
            pc_name TEXT, status TEXT, checked_at TEXT, PRIMARY KEY (game, product_id, printing));
        CREATE TABLE pricecharting (game TEXT, product_id INT, pc_id INT, ungraded REAL,
            grade7 REAL, grade8 REAL, grade9 REAL, grade95 REAL, psa10 REAL, bgs10 REAL,
            cgc10 REAL, sgc10 REAL, sales_volume INT, pc_console TEXT, pc_name TEXT,
            updated_at TEXT, PRIMARY KEY (game, product_id));
        CREATE TABLE graded_price_history (game TEXT, product_id INT, printing TEXT DEFAULT '',
            grade TEXT, date TEXT, price REAL,
            PRIMARY KEY (game, product_id, printing, grade, date));
        CREATE TABLE graded_crawled (game TEXT, product_id INT, crawled_at TEXT,
            PRIMARY KEY (game, product_id));
    """)
    c.executemany("INSERT INTO tcg_nm_history VALUES ('magic',1,?,?,?)", [
        ("", "2026-11-01", 30.0), ("", "2026-11-02", 28.0), ("", "2026-11-03", 28.5),
        ("Normal", "2026-11-02", 12.0), ("Normal", "2026-11-03", 12.5)])
    c.execute("INSERT INTO tcg_csv_base VALUES ('magic',1,'Foil','only','x')")
    c.executemany("INSERT INTO printing_variants VALUES ('magic',1,?,NULL,1,1,?,'x')",
                  [("Foil", 1), ("Normal", 0)])
    return c


def series(c, printing):
    return c.execute("SELECT date, price FROM tcg_nm_history WHERE printing=? ORDER BY date",
                     (printing,)).fetchall()


class SwitchCard(unittest.TestCase):
    def test_normal_becomes_the_main_series(self):
        c = db()
        self.assertEqual(sb.switch_card(c, "magic", 1, "Foil"), 12.5)
        self.assertEqual(series(c, ""), [("2026-11-02", 12.0), ("2026-11-03", 12.5)])
        self.assertEqual(series(c, "Foil"), [("2026-11-01", 30.0), ("2026-11-02", 28.0),
                                             ("2026-11-03", 28.5)])
        self.assertEqual(series(c, "Normal"), [])
        self.assertEqual(c.execute("SELECT printing, source FROM tcg_csv_base").fetchone(),
                         ("Normal", "normal-first"))
        self.assertEqual(c.execute("SELECT variant FROM printing_variants WHERE is_base=1").fetchall(),
                         [("Normal",)])

    def test_no_normal_history(self):
        c = db()
        c.execute("DELETE FROM tcg_nm_history WHERE printing='Normal'")
        self.assertIsNone(sb.switch_card(c, "magic", 1, "Foil"))
        self.assertEqual(len(series(c, "")), 3)                 # untouched
        self.assertIsNone(sb.switch_card(c, "magic", 1, "Foil", need_history=False))
        self.assertEqual(series(c, ""), [])                     # the import writes tonight's row
        self.assertEqual(len(series(c, "Foil")), 3)

    def test_the_price_page_follows_when_the_normal_page_is_known(self):
        c = db()
        c.execute("INSERT INTO pricecharting (game, product_id, pc_id, psa10, pc_name) "
                  "VALUES ('magic',1,900,400.0,'Card [Foil] #1')")
        c.execute("INSERT INTO pc_printing_matches VALUES ('magic',1,'Normal',800,'Card #1','ok','x')")
        c.executemany("INSERT INTO graded_price_history VALUES ('magic',1,?,'psa10','2026-11-01',?)",
                      [("", 400.0), ("Normal", 90.0)])
        c.execute("INSERT INTO graded_crawled VALUES ('magic',1,'x')")
        sb.switch_card(c, "magic", 1, "Foil")
        self.assertEqual(c.execute("SELECT pc_id, psa10 FROM pricecharting").fetchone(), (800, None))
        self.assertEqual(c.execute("SELECT printing, pc_id FROM pc_printing_matches").fetchall(),
                         [("Foil", 900)])
        self.assertEqual(sorted(c.execute("SELECT printing, price FROM graded_price_history")),
                         [("", 90.0), ("Foil", 400.0)])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM graded_crawled").fetchone()[0], 0)

    def test_the_page_stays_when_the_normal_page_is_unknown(self):
        c = db()
        c.execute("INSERT INTO pricecharting (game, product_id, pc_id, psa10) VALUES ('magic',1,900,400.0)")
        sb.switch_card(c, "magic", 1, "Foil")
        self.assertEqual(c.execute("SELECT pc_id, psa10 FROM pricecharting").fetchone(), (900, 400.0))


if __name__ == "__main__":
    unittest.main()
