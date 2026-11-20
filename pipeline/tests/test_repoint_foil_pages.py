"""Moving a Normal-priced card off a foil PriceCharting page."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from repoint_foil_pages import plain_name, old_label, repoint


def db():
    pc = sqlite3.connect(":memory:")
    pc.executescript("""
        CREATE TABLE pricecharting (game, product_id, pc_id, ungraded, grade7, grade8, grade9,
            grade95, psa10, bgs10, cgc10, sgc10, sales_volume, pc_console, pc_name, updated_at,
            PRIMARY KEY (game, product_id));
        CREATE TABLE graded_price_history (game, product_id, printing DEFAULT '', grade, date,
            price, PRIMARY KEY (game, product_id, printing, grade, date));
        CREATE TABLE pc_printing_matches (game, product_id, printing, pc_id, pc_name, status,
            checked_at, PRIMARY KEY (game, product_id, printing));
        CREATE TABLE graded_crawled (game, product_id, crawled_at, PRIMARY KEY (game, product_id));
        INSERT INTO pricecharting (game, product_id, pc_id, psa10, pc_name)
            VALUES ('magic', 7, 100, 60.0, 'Orcish Bowmasters [Foil] #103');
        INSERT INTO graded_price_history VALUES ('magic', 7, '', 'psa10', '2026-10-01', 60.0);
        INSERT INTO graded_crawled VALUES ('magic', 7, '2026-11-01');
    """)
    return pc


class PlainName(unittest.TestCase):
    def test_strips_foil_from_the_bracket(self):
        self.assertEqual(plain_name("Orcish Bowmasters [Foil] #103"), "Orcish Bowmasters #103")
        self.assertEqual(plain_name("Scion of Draco [Extended Art Foil] #323"),
                         "Scion of Draco [Extended Art] #323")
        self.assertEqual(plain_name("Swamp [272 Foil]"), "Swamp [272]")
        self.assertEqual(plain_name("X [Foil]"), "X")

    def test_leaves_other_names_alone(self):
        self.assertIsNone(plain_name("Tinfoil Hat #12"))
        self.assertIsNone(plain_name("Foil"))
        self.assertIsNone(plain_name("Island [Full Art] #258"))

    def test_old_label(self):
        self.assertEqual(old_label('["Normal", "Foil"]'), "Foil")
        self.assertEqual(old_label('["Normal", "Cold Foil"]'), "Cold Foil")
        self.assertIsNone(old_label('["Normal", "Foil", "Etched"]'))
        self.assertIsNone(old_label(None))


class Repoint(unittest.TestCase):
    def test_link_moves_and_old_history_becomes_the_foils(self):
        pc = db()
        self.assertTrue(repoint(pc, "magic", 7, 100, "Orcish Bowmasters [Foil] #103",
                                200, "Orcish Bowmasters #103", "Foil", "now"))
        self.assertEqual(pc.execute("SELECT pc_id, pc_name, psa10 FROM pricecharting").fetchone(),
                         (200, "Orcish Bowmasters #103", None))
        self.assertEqual(pc.execute("SELECT printing FROM graded_price_history").fetchall(),
                         [("Foil",)])
        self.assertEqual(pc.execute("SELECT printing, pc_id FROM pc_printing_matches").fetchall(),
                         [("Foil", 100)])
        self.assertEqual(pc.execute("SELECT COUNT(*) FROM graded_crawled").fetchone()[0], 0)

    def test_existing_foil_series_is_kept(self):
        pc = db()
        pc.execute("INSERT INTO graded_price_history VALUES ('magic', 7, 'Foil', 'psa10', "
                   "'2026-09-01', 55.0)")
        repoint(pc, "magic", 7, 100, "x [Foil]", 200, "x", "Foil", "now")
        self.assertEqual(pc.execute("SELECT printing, date FROM graded_price_history").fetchall(),
                         [("Foil", "2026-09-01")])

    def test_a_link_that_changed_since_the_check_is_left(self):
        pc = db()
        pc.execute("UPDATE pricecharting SET pc_id=300")
        self.assertFalse(repoint(pc, "magic", 7, 100, "x [Foil]", 200, "x", "Foil", "now"))
        self.assertEqual(pc.execute("SELECT pc_id FROM pricecharting").fetchone()[0], 300)


if __name__ == "__main__":
    unittest.main()
