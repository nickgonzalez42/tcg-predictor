import os
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import crawl_staleness as cs

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def db():
    pc = sqlite3.connect(":memory:")
    pc.executescript(
        "CREATE TABLE pricecharting (game TEXT, product_id INTEGER, pc_id INTEGER);"
        "CREATE TABLE graded_crawled (game TEXT, product_id INTEGER, crawled_at TEXT);"
        "CREATE TABLE graded_dead (game TEXT, product_id INTEGER);"
        "CREATE TABLE tcg_sales_scan (game TEXT, product_id INTEGER, fetched_at TEXT);")
    return pc


class Counts(unittest.TestCase):
    def test_only_linked_live_cards_past_the_line_count(self):
        pc = db()
        pc.executemany("INSERT INTO pricecharting VALUES ('magic',?,?)",
                       [(1, 10), (2, 20), (3, None), (4, 40), (5, 50)])
        pc.executemany("INSERT INTO graded_crawled VALUES ('magic',?,?)", [
            (1, "2026-10-01T00:00:00+00:00"),     # late
            (2, "2026-11-10T00:00:00+00:00"),     # fresh
            (3, "2026-09-01T00:00:00+00:00"),     # unlinked: not ours to fetch
            (4, "2026-09-01T00:00:00+00:00"),     # dead page
        ])                                        # 5: never fetched, not counted
        pc.execute("INSERT INTO graded_dead VALUES ('magic', 4)")
        pc.executemany("INSERT INTO tcg_sales_scan VALUES ('magic',?,?)", [
            (1, "2026-10-01 08:00:00"), (2, "2026-10-19T08:00:00+00:00"),
            (3, "2026-11-19 08:00:00")])
        found = cs.counts(pc, NOW)
        self.assertEqual(found["graded"], (1, "2026-10-01T00:00:00+00:00"))
        self.assertEqual(found["sales"][0], 1)    # 10-19 is the line itself: not late


class Message(unittest.TestCase):
    def test_quiet_at_or_under_the_line(self):
        self.assertIsNone(cs.message({"graded": (cs.ALERT_OVER, "2026-10-01"),
                                      "sales": (0, None)}))

    def test_names_only_the_late_crawl(self):
        subject, body = cs.message({"graded": (cs.ALERT_OVER + 1, "2026-10-01T00:00"),
                                    "sales": (3, "2026-10-02")})
        self.assertEqual(subject, "cardstock crawl is behind")
        self.assertIn("graded crawl: 201 card(s)", body)
        self.assertNotIn("sales crawl", body)


if __name__ == "__main__":
    unittest.main()
