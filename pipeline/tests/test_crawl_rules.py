#!/usr/bin/env python3
"""
The 2026-11-15 rules: which exact PriceCharting suggestions may be linked
unseen (build_pricecharting.vet_exact), and what the graded crawl takes on
(dead pages wait out a rotation, a short due list is topped up with the
pages crawled longest ago). No network, no real database.

Run (from the repo root):
      ../one-piece/.venv/bin/python -m unittest discover -s pipeline/tests -v
"""
import contextlib
import io
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_before = set(sys.modules)
import build_pricecharting as bp       # noqa: E402
import scrape_graded_history as gh     # noqa: E402
# The other test modules point TCG_DATA_DIR at a temp folder before their
# first pipeline import; leave them a clean slate (nothing here reads a path).
for _m in set(sys.modules) - _before:
    if os.path.dirname(getattr(sys.modules[_m], "__file__", None) or "") == sys.path[0]:
        del sys.modules[_m]


def sug(pid, pc_id, pc_name):
    return {"game": "magic", "product_id": str(pid), "pc_id": str(pc_id),
            "pc_name": pc_name, "source": bp.EXACT}


class VetExact(unittest.TestCase):
    def vet(self, rows, cards):
        link, hold, drop = bp.vet_exact("magic", rows, cards)
        return ([r["pc_id"] for r in link], [r["pc_id"] for r, _ in hold],
                [r["pc_id"] for r in drop])

    def test_plain_card_plain_page_links(self):
        rows = [sug(1, 10, "Augment Orphan #231")]
        self.assertEqual(self.vet(rows, {1: ("Augment Orphan", "231", '["Normal","Foil"]', "Normal")}),
                         (["10"], [], []))

    def test_pair_keeps_our_printing(self):
        rows = [sug(1, 10, "Shelob #516"), sug(1, 11, "Shelob [Foil} #516")]
        cards = {1: ("Shelob (Borderless)", "516", '["Normal","Foil"]', "Normal")}
        self.assertEqual(self.vet(rows, cards), (["10"], [], ["11"]))
        cards = {1: ("Shelob (Borderless)", "516", '["Normal","Foil"]', "Foil")}
        self.assertEqual(self.vet(rows, cards), (["11"], [], ["10"]))

    def test_only_the_other_printing_is_held(self):
        rows = [sug(1, 11, "Edgar Markov [Showcase Foil] #328")]
        cards = {1: ("Edgar Markov (Showcase)", "328", '["Normal","Foil"]', "Normal")}
        self.assertEqual(self.vet(rows, cards), ([], ["11"], []))
        cards = {1: ("Edgar Markov (Showcase)", "328", '["Foil"]', "Foil")}   # foil-only product
        self.assertEqual(self.vet(rows, cards), (["11"], [], []))

    def test_number_mismatch_is_held(self):
        rows = [sug(1, 10, "U.S.S. Enterprise-A, Free Spirit #43")]
        cards = {1: ("U.S.S. Enterprise-A, Free Spirit (Showcase)", "550", '["Normal"]', "Normal")}
        self.assertEqual(self.vet(rows, cards), ([], ["10"], []))
        cards = {1: ("Pikachu", "043/198", '["Normal"]', "Normal")}           # zero padding, set total
        self.assertEqual(self.vet([sug(1, 10, "Pikachu #43")], cards), (["10"], [], []))
        cards = {1: ("Inteleon", "SWSH113", '["Normal"]', "Normal")}          # not a plain number: no check
        self.assertEqual(self.vet([sug(1, 10, "Inteleon #113")], cards), (["10"], [], []))

    def test_signed_card_never_takes_the_plain_page(self):
        rows = [sug(1, 10, "Captain Jean-Luc Picard #7")]
        cards = {1: ("Captain Jean-Luc Picard (Borderless) (Signed)", "7", '["Normal"]', "Normal")}
        self.assertEqual(self.vet(rows, cards), ([], ["10"], []))

    def test_unknown_card_links_as_before(self):
        self.assertEqual(self.vet([sug(1, 10, "Anything [Foil] #1")], {}), (["10"], [], []))


class GradedTargets(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        gh.ensure_table(self.conn)
        self.conn.executescript(
            "CREATE TABLE pricecharting (game TEXT, product_id INTEGER, pc_id INTEGER, psa10 REAL);"
            "CREATE TABLE tcg_nm_history (game TEXT, product_id INTEGER, date TEXT, price REAL);")
        now = datetime.now(timezone.utc)
        self.ago = lambda d: (now - timedelta(days=d)).isoformat()
        for pid, days in ((1, 30), (2, 25), (3, 22), (4, 5), (5, 40)):
            self.conn.execute("INSERT INTO pricecharting VALUES ('magic',?,?,?)", (pid, pid * 100, 10.0))
            self.conn.execute("INSERT INTO graded_crawled VALUES ('magic',?,?)", (pid, self.ago(days)))

    def gather(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return gh.gather_targets("magic", self.conn, True, None, 28, **kw)

    def test_due_and_early(self):
        due, early = self.gather(fill_days=21)
        self.assertEqual(sorted(t[1] for t in due), [1, 5])
        self.assertEqual([t[2] for t in sorted(early)], [2, 3])      # oldest first, card 4 too fresh
        self.assertEqual(self.gather()[1], [])

    def test_dead_page_waits_a_rotation(self):
        self.conn.execute("INSERT INTO graded_dead VALUES ('magic',5,500,'HTTP 404',3,?)", (self.ago(2),))
        self.assertEqual([t[1] for t in self.gather()[0]], [1])
        self.conn.execute("UPDATE graded_dead SET last_try=?", (self.ago(29),))
        self.assertEqual(sorted(t[1] for t in self.gather()[0]), [1, 5])
        # relinked to another page: tried at once
        self.conn.execute("UPDATE graded_dead SET last_try=?, pc_id=999", (self.ago(2),))
        self.assertEqual(sorted(t[1] for t in self.gather()[0]), [1, 5])


if __name__ == "__main__":
    unittest.main()
