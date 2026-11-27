"""Moving a Normal-priced card off a foil PriceCharting page."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import repoint_foil_pages as rf
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


class Unlink(unittest.TestCase):
    """A card whose only PriceCharting page is the foil's."""

    def test_the_link_comes_off_and_the_history_stays_with_the_foil(self):
        pc = db()
        self.assertTrue(rf.unlink(pc, "magic", 7, 100, "Orcish Bowmasters [Foil] #103", "Foil", "now"))
        self.assertEqual(pc.execute("SELECT pc_id, psa10, pc_name, updated_at FROM pricecharting").fetchone(),
                         (None, None, "Orcish Bowmasters [Foil] #103", "now"))
        self.assertEqual(pc.execute("SELECT printing, price FROM graded_price_history").fetchall(),
                         [("Foil", 60.0)])
        self.assertEqual(pc.execute("SELECT printing, pc_id, status FROM pc_printing_matches").fetchall(),
                         [("Foil", 100, "ok")])
        self.assertEqual(pc.execute("SELECT COUNT(*) FROM graded_crawled").fetchone()[0], 0)
        self.assertEqual(pc.execute("SELECT game, product_id FROM match_dirty").fetchall(), [("magic", 7)])

    def test_no_single_foil_printing_drops_the_history(self):
        pc = db()
        self.assertTrue(rf.unlink(pc, "magic", 7, 100, "x [Foil]", None, "now"))
        self.assertEqual(pc.execute("SELECT COUNT(*) FROM graded_price_history").fetchone()[0], 0)
        self.assertEqual(pc.execute("SELECT COUNT(*) FROM pc_printing_matches").fetchone()[0], 0)

    def test_a_link_that_changed_since_the_check_is_left(self):
        pc = db()
        pc.execute("UPDATE pricecharting SET pc_id=300")
        self.assertFalse(rf.unlink(pc, "magic", 7, 100, "x [Foil]", "Foil", "now"))
        self.assertEqual(pc.execute("SELECT pc_id, psa10 FROM pricecharting").fetchone(), (300, 60.0))
        self.assertEqual(pc.execute("SELECT printing FROM graded_price_history").fetchall(), [("",)])

    def test_the_nightly_exclusion_then_finds_nothing_left_to_do(self):
        # build_pricecharting.apply_confirmed_links acts on an override only
        # while the card is linked (pc_id IS NOT NULL).
        pc = db()
        rf.unlink(pc, "magic", 7, 100, "x [Foil]", "Foil", "now")
        self.assertEqual(pc.execute(
            "SELECT COUNT(*) FROM pricecharting WHERE pc_id IS NOT NULL").fetchone()[0], 0)

    def test_the_whole_run_writes_the_override_and_the_plan(self):
        import csv
        import tempfile
        import types
        d = tempfile.mkdtemp(prefix="foil_unlink_test_")
        plan = os.path.join(d, "plan.csv")
        with open(plan, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rf.FIELDS)
            w.writeheader()
            w.writerow({"game": "magic", "product_id": 7, "old_pc": 100, "old_name": "x [Foil]",
                        "console": "Set", "new_pc": "", "new_name": "", "status": "no-page",
                        "checked_at": "t"})
            w.writerow({"game": "lorcana", "product_id": 9, "old_pc": 5, "old_name": "y [Foil]",
                        "console": "Set", "new_pc": "", "new_name": "", "status": "no-page",
                        "checked_at": "t"})
        pc = db()
        overrides = []

        class Keep:                                   # the run closes its connections
            def __init__(self, c): self.c = c
            def __getattr__(self, k): return getattr(self.c, k)
            def close(self): pass

        cards = sqlite3.connect(":memory:")
        cards.execute("CREATE TABLE cards (product_id, printings)")
        cards.execute("""INSERT INTO cards VALUES (7, '["Normal", "Foil"]')""")
        saved = (rf.PLAN_CSV, rf.sqlite3, sys.modules.get("match_review"))
        rf.PLAN_CSV = plan
        rf.sqlite3 = types.SimpleNamespace(
            connect=lambda path, **kw: Keep(cards if "magic" in path and "pricecharting" not in path else pc))
        sys.modules["match_review"] = types.SimpleNamespace(
            append_override=lambda g, pid, pc_id, note: overrides.append((g, int(pid), pc_id, note)))
        try:
            rf.unlink_foil_only({"magic"})
        finally:
            rf.PLAN_CSV, rf.sqlite3 = saved[0], saved[1]
            if saved[2] is None:
                sys.modules.pop("match_review", None)
            else:
                sys.modules["match_review"] = saved[2]
        self.assertEqual([(g, p, i) for g, p, i, _ in overrides], [("magic", 7, 0)])
        self.assertIn("foil-only", overrides[0][3])
        with open(plan, newline="") as f:
            status = {r["game"]: r["status"] for r in csv.DictReader(f)}
        self.assertEqual(status, {"magic": "unlinked", "lorcana": "no-page"})   # another game is not touched
        self.assertIsNone(pc.execute("SELECT pc_id FROM pricecharting").fetchone()[0])


class FindPage(unittest.TestCase):
    def test_nested_bracket_names(self):
        self.assertEqual(rf.plain_name("Getaway Car [ [Extended] Foil] #438"),
                         "Getaway Car [Extended] #438")
        self.assertIsNone(rf.plain_name("Airfoil #97"))          # a card's own name

    def test_case_and_apostrophes_do_not_hide_a_page(self):
        stored = {"Sleight of Hand #67": 11}
        index = {rf.norm(n): p for n, p in stored.items()}
        self.assertEqual(rf.find_page("Sleight Of Hand #67", stored, index), (11, False))
        self.assertEqual(rf.find_page("Can\u2019T Wake Up #2", {}, {rf.norm("Can't Wake Up #2"): 5}),
                         (5, False))

    def test_numberless_page_only_when_it_is_the_only_one(self):
        live = {"Earl Of Squirrel": 7, "Earl of Squirrel [Foil] #108": 9}
        self.assertEqual(rf.find_page("Earl of Squirrel #108", {}, {}, live), (7, True))
        live["Earl Of Squirrel [Promo]"] = 8                       # another printing: not it
        self.assertEqual(rf.find_page("Earl of Squirrel #108", {}, {}, live), (7, True))
        live["Earl of Squirrel #9"] = 6                            # now two of that name
        self.assertEqual(rf.find_page("Earl of Squirrel #108", {}, {}, live), (None, False))
        self.assertEqual(rf.find_page("Nobody #1", {}, {}, live), (None, False))


if __name__ == "__main__":
    unittest.main()
