"""Mock-only tests for pc_printing_backfill's 429 handling.

No network (urlopen faked, sockets refused), no sleeping (time.sleep
recorded), no DB file (main() gets an in-memory stand-in for
pricecharting.db). TCG_DATA_DIR points at an empty scratch folder before the
import, so no path in the module can resolve to the real data.

  .venv/bin/python pipeline/tests/test_pc_printing_backfill.py
"""
import contextlib
import email.message
import email.utils
import io
import json
import os
import socket
import sqlite3
import sys
import time
import unittest
import warnings
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

import tempfile

PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["TCG_DATA_DIR"] = tempfile.mkdtemp(prefix="pc_backfill_test_")
sys.path.insert(0, PIPELINE)


def _no_network(*a, **k):
    raise RuntimeError("network access in a mock-only test")


socket.socket.connect = _no_network
socket.create_connection = _no_network

import pc_printing_backfill as bf  # noqa: E402

assert bf.PC_DB.startswith(os.environ["TCG_DATA_DIR"]), bf.PC_DB
assert "/one-piece/" not in bf.PC_DB

GAME, CONSOLE = "pokemon", "Pokemon Base Set"
N_CARDS = 5


OPENED = []


class KeepOpen(sqlite3.Connection):
    """main() closes its connection at the end; keep it readable for asserts
    (tearDown closes it for real)."""
    def close(self):
        pass


def stand_in_db():
    conn = sqlite3.connect(":memory:", factory=KeepOpen)
    OPENED.append(conn)
    conn.executescript("""
        CREATE TABLE printing_variants (game TEXT, product_id INTEGER, variant TEXT,
                                        latest_price REAL, is_base INTEGER);
        CREATE TABLE pricecharting (game TEXT, product_id INTEGER, pc_name TEXT,
                                    pc_console TEXT);
        CREATE TABLE graded_price_history (game TEXT, product_id INTEGER,
            printing TEXT NOT NULL DEFAULT '', grade TEXT, date TEXT, price REAL,
            PRIMARY KEY (game, product_id, printing, grade, date));
    """)
    for n in range(1, N_CARDS + 1):
        pid = 100 + n
        conn.execute("INSERT INTO printing_variants VALUES (?,?,?,?,0)",
                     (GAME, pid, "Reverse Holofoil", 1000 - n))   # card 1 first
        conn.execute("INSERT INTO pricecharting VALUES (?,?,?,?)",
                     (GAME, pid, f"Card {n} #{n}", CONSOLE))
        conn.execute("INSERT INTO graded_price_history VALUES (?,?,'',?,?,?)",
                     (GAME, pid, "psa10", "2026-10-01", 50.0))
    conn.commit()
    return conn


def catalog(_game):
    return {(CONSOLE, f"Card {n} [Reverse Holo] #{n}"): 9000 + n
            for n in range(1, N_CARDS + 1)}


def page(pc_id):
    """A PC product page embedding chart data and its TCGplayer id."""
    pid = pc_id - 9000 + 100
    chart = {"used": [[1727740800000, 1234], [1730419200000, 1300]],
             "manualonly": [[1730419200000, 9900]]}
    return (f"<html>VGPC.chart_data = {json.dumps(chart)};"
            f'<a href="https://www.tcgplayer.com/product/{pid}">buy</a></html>').encode()


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def refused(url, retry_after=None, code=429):
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "Too Many Requests", hdrs, None)


class Script:
    """urlopen stand-in: per pc_id, a queue of outcomes ('ok', 429,
    ('429', retry_after), 404); a pc_id with an empty queue answers 'ok'."""
    def __init__(self, plan):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.calls = []

    def __call__(self, req, timeout=None, context=None):
        url = req.full_url
        pc_id = int(url.rsplit("/", 1)[1])
        self.calls.append(pc_id)
        q = self.plan.get(pc_id) or []
        what = q.pop(0) if q else "ok"
        if what == "ok":
            return FakeResp(page(pc_id))
        if isinstance(what, tuple):
            raise refused(url, what[1])
        raise refused(url, code=what)


def run(plan, *, budget=None):
    """Run main() against the stand-in; returns (conn, sleeps, urlopen fake,
    stdout, stderr)."""
    conn = stand_in_db()
    fake = Script(plan)
    sleeps = []
    out, err = io.StringIO(), io.StringIO()
    patches = [
        mock.patch.object(bf.sqlite3, "connect", lambda *a, **k: conn),
        mock.patch.object(bf, "pc_catalog", catalog),
        mock.patch.object(bf.urllib.request, "urlopen", fake),
        mock.patch.object(bf.time, "sleep", sleeps.append),
        mock.patch.object(sys, "argv", ["pc_printing_backfill.py", "--games", GAME,
                                        "--limit", "10"]),
    ]
    if budget is not None:
        patches.append(mock.patch.object(bf, "WAIT_BUDGET", budget))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(out))
        st.enter_context(contextlib.redirect_stderr(err))
        # The faked HTTPError carries no response body to close; its GC-time
        # ResourceWarning would land in the captured stderr.
        st.enter_context(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        bf.main()
    return conn, sleeps, fake, out.getvalue(), err.getvalue()


def memos(conn):
    return {pid: st for pid, st in conn.execute(
        "SELECT product_id, status FROM pc_printing_matches")}


class BackoffTests(unittest.TestCase):
    def tearDown(self):
        while OPENED:
            sqlite3.Connection.close(OPENED.pop())

    def test_no_429_paces_at_the_base_delay(self):
        conn, sleeps, fake, out, err = run({})
        self.assertEqual(memos(conn), {100 + n: "ok" for n in range(1, 6)})
        self.assertEqual(sleeps, [1.0] * 5)
        self.assertNotIn("429", out)
        self.assertEqual(err, "")

    def test_429_then_200_waits_retries_and_slows_down(self):
        conn, sleeps, fake, out, err = run({9002: [429]})
        self.assertEqual(memos(conn), {100 + n: "ok" for n in range(1, 6)})
        self.assertEqual(fake.calls, [9001, 9002, 9002, 9003, 9004, 9005])
        # base pace, the refused try, the 30 s wait, the retry and the rest at 2 s
        self.assertEqual(sleeps, [1.0, 1.0, 30, 2.0, 2.0, 2.0, 2.0])
        self.assertIn("429 on pc_id 9002 — waiting 30 s, then 2 s between pages", out)
        self.assertIn("'429': 1", out)
        self.assertEqual(err, "")
        # labeled rows written for every page, base rows untouched
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM graded_price_history WHERE printing=''").fetchone()[0], 5)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM graded_price_history WHERE printing='Reverse Holofoil'"
        ).fetchone()[0], 15)

    def test_retry_after_longer_than_the_step_is_honoured(self):
        conn, sleeps, fake, out, err = run({9001: [("429", "90")]})
        self.assertEqual(sleeps[:3], [1.0, 90, 2.0])
        self.assertEqual(memos(conn)[101], "ok")
        self.assertEqual(err, "")

    def test_retry_after_shorter_than_the_step_waits_the_step(self):
        conn, sleeps, fake, out, err = run({9001: [("429", "5")]})
        self.assertEqual(sleeps[:3], [1.0, 30, 2.0])

    def test_retry_after_as_http_date(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=200)
        conn, sleeps, fake, out, err = run(
            {9001: [("429", email.utils.format_datetime(when, usegmt=True))]})
        self.assertTrue(195 <= sleeps[1] <= 200, sleeps)
        self.assertEqual(memos(conn)[101], "ok")

    def test_escalation_resets_after_a_success(self):
        conn, sleeps, fake, out, err = run({9001: [429, 429], 9003: [429]})
        waits = [s for s in sleeps if s >= 30]
        self.assertEqual(waits, [30, 60, 30])
        self.assertEqual(memos(conn), {100 + n: "ok" for n in range(1, 6)})
        self.assertIn("'429': 3", out)

    def test_repeated_429_stops_the_run(self):
        conn, sleeps, fake, out, err = run({9002: [429, 429, 429, 429]})
        self.assertEqual([s for s in sleeps if s >= 30], [30, 60, 120])
        # card 1 saved; card 2 left unmemoized (tried again next run); 3-5 untouched
        self.assertEqual(memos(conn), {101: "ok"})
        self.assertEqual(fake.calls, [9001, 9002, 9002, 9002, 9002])
        self.assertIn("rate limited (4 in a row, 210 s waited this run) — stopping "
                      "this run early", err)
        self.assertIn("'429': 4", out)

    def test_retry_after_past_the_budget_stops_without_waiting(self):
        conn, sleeps, fake, out, err = run({9001: [("429", "3600")]})
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(memos(conn), {})
        self.assertIn("rate limited (1 in a row, 0 s waited this run)", err)

    def test_wait_budget_is_per_run(self):
        # 100 s budget: 30 + 60 fit, the next 30 (a new page) would pass it
        conn, sleeps, fake, out, err = run({9001: [429, 429], 9002: [429]}, budget=100)
        self.assertEqual([s for s in sleeps if s >= 30], [30, 60])
        self.assertEqual(memos(conn), {101: "ok"})
        self.assertIn("rate limited (1 in a row, 90 s waited this run)", err)

    def test_other_http_errors_keep_their_old_handling(self):
        conn, sleeps, fake, out, err = run({9002: [404]})
        self.assertEqual(memos(conn)[102], "verify-failed")
        self.assertEqual(len(memos(conn)), 5)
        self.assertEqual(sleeps, [1.0] * 5)

    def test_a_single_403_is_left_for_the_next_run(self):
        conn, sleeps, fake, out, err = run({9002: [403]})
        self.assertNotIn(102, memos(conn))
        self.assertEqual(len(memos(conn)), 4)
        self.assertNotIn("stopping", err)

    def test_five_403s_in_a_row_stop_the_run_unsaved(self):
        conn, sleeps, fake, out, err = run({9000 + n: [403] for n in range(1, 6)})
        self.assertEqual(memos(conn), {})
        self.assertIn("5 refusals (403) in a row", err)

    def test_a_success_resets_the_403_streak(self):
        plan = {9001: [403], 9002: [403], 9003: [403], 9004: [403]}
        conn, sleeps, fake, out, err = run(plan)
        self.assertEqual(memos(conn), {105: "ok"})
        self.assertNotIn("stopping", err)

    def test_retry_after_is_logged(self):
        conn, sleeps, fake, out, err = run({9001: [("429", "45")]})
        self.assertIn("Retry-After 45 s", out)


class RetryAfterParsing(unittest.TestCase):
    def msg(self, value):
        m = email.message.Message()
        if value is not None:
            m["Retry-After"] = value
        return m

    def test_values(self):
        self.assertEqual(bf.retry_after(self.msg("120")), 120)
        self.assertEqual(bf.retry_after(self.msg(" 7 ")), 7)
        self.assertIsNone(bf.retry_after(self.msg(None)))
        self.assertIsNone(bf.retry_after(self.msg("soon")))
        self.assertIsNone(bf.retry_after(None))
        past = email.utils.format_datetime(datetime(2020, 1, 1, tzinfo=timezone.utc), usegmt=True)
        self.assertEqual(bf.retry_after(self.msg(past)), 0.0)
        self.assertEqual(bf.retry_after({"Retry-After": "45"}), 45)

    def test_garbage_never_raises(self):
        for v in ("\u00b2", "9" * 5000, "Mon, 01 Jan 99999 00:00:00 GMT", "Fri, 31 Dec 9999 23:59:59 GMT",
                  "-5", "1e3", ""):
            bf.retry_after(self.msg(v))   # any result, never an exception


if __name__ == "__main__":
    unittest.main(verbosity=2)
