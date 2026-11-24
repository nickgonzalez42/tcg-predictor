"""Match rows changed by hand ride the night's delta (2026-11-24)."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_unified_history as buh
import match_dirty

NOW = datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)
# What deploy/push_data.sh runs on the server for the match rows.
SERVER_APPLY = "INSERT OR REPLACE INTO pricecharting SELECT * FROM d.match_rows"


def match_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pricecharting (game TEXT, product_id INTEGER, pc_id INTEGER, "
                 "pc_name TEXT, psa10 REAL, updated_at TEXT, PRIMARY KEY (game, product_id))")
    return conn


class Marks(unittest.TestCase):
    def test_mark_then_pending_and_expiry(self):
        conn = sqlite3.connect(":memory:")
        match_dirty.mark(conn, "magic", 10, NOW - timedelta(days=9))
        match_dirty.mark(conn, "magic", 11, NOW - timedelta(days=7))
        match_dirty.mark(conn, "magic", 11, NOW - timedelta(days=2))     # marked again: one row
        match_dirty.mark(conn, "lorcana", "12", NOW)
        self.assertEqual(match_dirty.pending(conn, NOW), {("magic", 11), ("lorcana", 12)})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM match_dirty").fetchone()[0], 2)

    def test_pending_on_a_database_without_the_table(self):
        self.assertEqual(match_dirty.pending(sqlite3.connect(":memory:"), NOW), set())


class Stage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.conn = match_db(os.path.join(self.dir.name, "pc.db"))
        self.conn.executemany("INSERT INTO pricecharting VALUES (?,?,?,?,?,?)", [
            ("magic", 1, 100, "Foil page", 50.0, "old"),      # its graded price changed tonight
            ("magic", 2, 201, "Plain page", None, "today"),   # re-pointed by hand, no prices yet
            ("magic", 3, 300, "Untouched", 9.0, "old"),
        ])
        match_dirty.mark(self.conn, "magic", 2)
        match_dirty.mark(self.conn, "magic", 99)              # its row was deleted since
        self.conn.commit()
        self.delta = os.path.join(self.dir.name, "delta.db")
        self.conn.execute("ATTACH ? AS delta", (self.delta,))

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def staged(self):
        return sorted(r[1] for r in self.conn.execute("SELECT * FROM delta.match_rows"))

    def test_changed_and_marked_rows_are_staged(self):
        by_hand = buh.stage_match_rows(self.conn, {("magic", 1)})
        self.assertEqual(self.staged(), [1, 2])
        self.assertEqual(by_hand, 2)                          # 2 and the vanished 99

    def test_a_row_both_changed_and_marked_is_staged_once(self):
        by_hand = buh.stage_match_rows(self.conn, {("magic", 1), ("magic", 2)})
        self.assertEqual(self.staged(), [1, 2])
        self.assertEqual(by_hand, 1)

    def test_nothing_changed_nothing_marked(self):
        self.conn.execute("DELETE FROM match_dirty")
        self.assertEqual(buh.stage_match_rows(self.conn, set()), 0)
        self.assertEqual(self.staged(), [])

    def test_trouble_with_the_marks_ships_the_changed_rows_alone(self):
        self.conn.execute("DROP TABLE match_dirty")
        self.conn.execute("CREATE TABLE match_dirty (something_else TEXT)")
        self.assertEqual(buh.stage_match_rows(self.conn, {("magic", 1)}), 0)
        self.assertEqual(self.staged(), [1])

    def test_the_server_ends_up_with_the_new_link(self):
        buh.stage_match_rows(self.conn, {("magic", 1)})
        self.conn.commit()
        self.conn.execute("DETACH delta")
        server = match_db(os.path.join(self.dir.name, "server.db"))
        server.executemany("INSERT INTO pricecharting VALUES (?,?,?,?,?,?)", [
            ("magic", 1, 100, "Foil page", 40.0, "old"),
            ("magic", 2, 200, "Foil page", 75.0, "old"),      # still the foil's link and price
            ("magic", 3, 300, "Untouched", 9.0, "old"),
        ])
        server.execute("ATTACH ? AS d", (self.delta,))
        server.execute(SERVER_APPLY)
        rows = {r[1]: r[2:5] for r in server.execute("SELECT * FROM pricecharting")}
        self.assertEqual(rows[2], (201, "Plain page", None))
        self.assertEqual(rows[1], (100, "Foil page", 50.0))
        self.assertEqual(rows[3], (300, "Untouched", 9.0))
        server.close()


if __name__ == "__main__":
    unittest.main()
