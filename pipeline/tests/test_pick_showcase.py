#!/usr/bin/env python3
"""
pick_showcase's sales vetting next to the daytime sales crawler (2026-11-13:
the nightly handed 18 lists to a crawler paused for the refresh and waited
74 minutes for nothing). Mocks only: no network, no real database, and every
data path (the crawler's lock, the watch lists) in a temp folder.

Run (from the repo root):
      ../one-piece/.venv/bin/python -m unittest discover -s pipeline/tests -v
"""
import contextlib
import fcntl
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from unittest import mock

# Before any pipeline import: _paths reads TCG_DATA_DIR, and the lock and the
# watch-list paths are built from it.
TMP = tempfile.mkdtemp(prefix="showcase-test-")
os.environ["TCG_DATA_DIR"] = TMP
os.makedirs(os.path.join(TMP, "ml_data"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_sales_volume as bsv  # noqa: E402
import pick_showcase as ps           # noqa: E402

for _p in [ps.LOCK_PATH, ps.WATCH_FILE] + bsv.WATCH_FILES:
    assert os.path.realpath(_p).startswith(os.path.realpath(TMP)), f"{_p} is outside the temp folder"


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


# pid: (ppid, command line)
CAFFEINATE, REFRESH, MODEL, SHELL = 400, 500, 600, 700
NIGHTLY = {   # the nightly's model block runs pick_showcase (parent: MODEL)
    1: (0, "/sbin/launchd"),
    CAFFEINATE: (1, "/usr/bin/caffeinate -i /bin/zsh /x/pipeline/run_daily_refresh.sh"),
    REFRESH: (CAFFEINATE, "/bin/zsh /x/pipeline/run_daily_refresh.sh"),
    MODEL: (REFRESH, "/bin/zsh local_model_run.sh"),
    SHELL: (1, "-zsh"),
}
DAYTIME = {1: (0, "/sbin/launchd"), SHELL: (1, "-zsh")}
CRAWLER = {   # the daytime agent, as pgrep showed it on 2026-11-13
    29061: (1, "/x/Python /x/pipeline/backfill_sales_volume.py --game all --rpm 40 --refresh-days 28"),
    29063: (29061, "/usr/bin/caffeinate -i /x/python /x/pipeline/backfill_sales_volume.py "
                   "--game all --rpm 40 --refresh-days 28"),
}
TAIL_SALES = {31000: (REFRESH, "/x/python backfill_sales_volume.py --game all --rpm 40 "
                               "--refresh-days 28 --total-limit 500 --no-yield")}
MENTIONS = {   # name the script without running it
    32000: (SHELL, "/bin/zsh -c tail sales-crawl.log; python backfill_sales_volume.py --no-yield"),
    32001: (SHELL, "vim pipeline/backfill_sales_volume.py"),
}
HISTORY = {"result": [{"variant": "Normal", "condition": "Near Mint", "buckets": [
    {"bucketStartDate": "2026-11-02", "quantitySold": 4, "transactionCount": 3}]}]}


class Procs:
    """subprocess.run for the two process questions: pgrep for the refresh
    (backfill_sales_volume.refresh_pids) and ps for the process tree."""

    def __init__(self, table):
        self.table, self.calls = dict(table), []

    def __call__(self, argv, **kw):
        self.calls.append(argv[0])
        if argv[:2] == ["pgrep", "-f"] and argv[2] == bsv.REFRESH_PROCS:
            out = "".join(f"{p}\n" for p, (_pp, cmd) in sorted(self.table.items())
                          if "run_daily_refresh.sh" in cmd or "weekly_refresh.py" in cmd)
        elif argv[0] == "ps":
            out = "".join(f"{p:>5} {pp:>5} {cmd}\n" for p, (pp, cmd) in sorted(self.table.items()))
        else:
            raise AssertionError(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, 0 if out else 1, out, "")


class FakeTime:
    """pick_showcase's clock: only sleeping moves it on."""

    def __init__(self, on_sleep):
        self.now, self.on_sleep = 1_000_000.0, on_sleep

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.on_sleep(s)


class EnsureFresh(unittest.TestCase):
    NEED = [("onepiece", 101), ("pokemon", 202), ("magic", 303)]

    def setUp(self):
        self.pc = sqlite3.connect(":memory:")
        self.addCleanup(self.pc.close)
        self.sales = ps.Sales(self.pc)
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.pc.executemany("INSERT INTO tcg_sales_scan VALUES (?,?,?)",
                            [(g, p, stale) for g, p in self.NEED])
        self.fetches, self.free_in_fetch, self.handoffs, self.handed = [], [], [], []
        self.logs, self.waits = [], []
        self.on_fetch = lambda pid: None
        self.crawler_crawls = False    # does the crawler fetch a list handed to it?
        self.holder = None             # the crawler's open lock file
        real_via = ps.Sales.via_crawler

        def via_spy(sales, need):
            self.handoffs.append(list(need))
            return real_via(sales, need)

        for p in (mock.patch.object(ps.tp, "fetch_price_history", side_effect=self.fetch),
                  mock.patch.object(ps.tp, "make_session", return_value=object()),
                  mock.patch.object(ps.tp, "RATE_LIMITER", None),
                  mock.patch.object(ps, "time", FakeTime(self.slept)),
                  mock.patch.object(ps, "log", side_effect=self.logs.append),
                  mock.patch.object(ps.Sales, "via_crawler", via_spy)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.crawler_lets_go)
        self.addCleanup(lambda: os.path.exists(ps.WATCH_FILE) and os.remove(ps.WATCH_FILE))

    # -- the crawler and the clock ---------------------------------------
    def crawler_holds_lock(self):
        self.holder = open(ps.LOCK_PATH, "w")
        fcntl.flock(self.holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def crawler_lets_go(self):
        if self.holder:
            self.holder.close()        # a dying crawler's lock goes with its file
            self.holder = None

    def lock_free(self):
        with open(ps.LOCK_PATH, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False

    def fetch(self, session, pid, delay):
        self.fetches.append(pid)
        self.free_in_fetch.append(self.lock_free())
        self.on_fetch(pid)
        return HISTORY

    def slept(self, s):
        self.waits.append(s)
        if not os.path.exists(ps.WATCH_FILE):
            return
        with open(ps.WATCH_FILE, encoding="utf-8") as f:
            w = json.load(f)
        if not self.handed or self.handed[-1] != w:
            self.handed.append(w)
        if self.crawler_crawls:        # it splices the list and fetches it
            now = datetime.now(timezone.utc).isoformat()
            self.pc.executemany("INSERT OR REPLACE INTO tcg_sales_scan VALUES (?,?,?)",
                                [(c["game"], c["pid"], now) for c in w["cards"]])

    def leftover_list(self):
        """The night before's last hand-off, as written before 2026-11-13."""
        now = datetime.now(timezone.utc)
        with open(ps.WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump({"made": now.isoformat(), "for": now.date().isoformat(),
                       "cards": [{"game": "onepiece", "pid": 999}]}, f)

    def run_pick(self, table, parent=MODEL):
        self.procs = Procs(table)
        with mock.patch.object(subprocess, "run", side_effect=self.procs), \
                mock.patch.object(ps.os, "getppid", return_value=parent):
            self.sales.ensure_fresh(self.NEED)

    # -- the three cases ---------------------------------------------------
    def test_crawler_paused_for_the_refresh_means_a_direct_fetch(self):
        """Crawler alive + refresh live (this run's own parent): no hand-off,
        no wait, every card fetched here."""
        self.crawler_holds_lock()
        self.leftover_list()
        self.run_pick({**NIGHTLY, **CRAWLER})
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.waits, [])
        self.assertEqual(self.fetches, [101, 202, 303])
        self.assertEqual(self.sales.fetched, 3)
        self.assertIn("paused for the refresh", self.logs[0])
        self.assertFalse(os.path.exists(ps.WATCH_FILE))   # the leftover is gone too
        since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        scanned = self.sales.scanned_at(self.NEED)
        self.assertTrue(all(scanned[p] > since for p in self.NEED))

    def test_crawling_crawler_gets_the_hand_off(self):
        """Crawler alive + no refresh (the daytime case): hand off and wait,
        as before; the list carries its wait and goes when the wait ends."""
        self.crawler_holds_lock()
        self.crawler_crawls = True
        self.run_pick({**DAYTIME, **CRAWLER}, parent=SHELL)
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])
        self.assertEqual(self.sales.fetched, 3)
        self.assertIn("crawler finished all 3", "\n".join(self.logs))
        w = self.handed[0]
        self.assertEqual([(c["game"], c["pid"]) for c in w["cards"]], self.NEED)
        made, until = datetime.fromisoformat(w["made"]), datetime.fromisoformat(w["until"])
        self.assertGreaterEqual(until, made + timedelta(seconds=ps.WAIT_MIN_S - 1))
        self.assertFalse(os.path.exists(ps.WATCH_FILE))

    def test_no_crawler_means_a_direct_fetch_under_its_lock(self):
        """Crawler not running: fetch here, holding its lock throughout."""
        self.leftover_list()
        self.run_pick({**NIGHTLY})
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.waits, [])
        self.assertEqual(self.fetches, [101, 202, 303])
        self.assertEqual(self.free_in_fetch, [False] * 3)   # no crawl could start meanwhile
        self.assertTrue(self.lock_free())                    # and let go after
        self.assertEqual(self.procs.calls, [])               # no process questions needed
        self.assertFalse(os.path.exists(ps.WATCH_FILE))

    # -- where the hand-off stays --------------------------------------------
    def test_run_by_hand_during_a_refresh_keeps_the_hand_off(self):
        """A refresh is live but this run is not part of it: that refresh may
        be crawling, so nothing is fetched here (the old hand-off)."""
        self.crawler_holds_lock()
        self.run_pick({**NIGHTLY, **CRAWLER}, parent=SHELL)
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])
        self.assertIn("crawler finished 0 of 3", "\n".join(self.logs))
        self.assertFalse(os.path.exists(ps.WATCH_FILE))

    def test_no_yield_crawl_keeps_the_hand_off(self):
        """The tail's tcg-sales step never pauses: it takes the list."""
        self.crawler_holds_lock()
        self.crawler_crawls = True
        self.run_pick({**NIGHTLY, **TAIL_SALES})
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])

    def test_lock_held_by_no_crawler_keeps_the_hand_off(self):
        """Something else holds the lock (another pick_showcase fetching):
        never a second direct fetch beside it."""
        self.crawler_holds_lock()
        self.run_pick({**NIGHTLY})
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])

    def test_no_process_list_keeps_the_hand_off(self):
        """ps and pgrep cannot run (a fork failure): the old way, a hand-off."""
        self.crawler_holds_lock()
        with mock.patch.object(subprocess, "run", side_effect=OSError("fork failed")):
            self.sales.ensure_fresh(self.NEED)
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])

    # -- the refresh or the crawler going away mid-list -----------------------
    def test_refresh_gone_mid_list_stops_and_hands_the_rest_over(self):
        """The refresh dies under the run (a launchd reload): the crawler may
        resume, so the fetching here stops and the cards left go to it."""
        self.crawler_holds_lock()
        self.crawler_crawls = True

        def refresh_dies(pid):
            if pid == 202:
                del self.procs.table[REFRESH], self.procs.table[CAFFEINATE]
                self.procs.table[MODEL] = (1, self.procs.table[MODEL][1])
        self.on_fetch = refresh_dies
        self.run_pick({**NIGHTLY, **CRAWLER})
        self.assertEqual(self.fetches, [101, 202])
        self.assertEqual(self.handoffs, [[("magic", 303)]])
        self.assertEqual(self.sales.fetched, 3)

    def test_crawler_gone_mid_list_its_lock_is_taken(self):
        """The paused crawler dies mid-list: its lock comes free and is taken,
        so a relaunch cannot start while the rest are fetched here."""
        self.crawler_holds_lock()

        def crawler_dies(pid):
            if pid == 101:
                self.crawler_lets_go()
                for p in CRAWLER:
                    del self.procs.table[p]
        self.on_fetch = crawler_dies
        self.run_pick({**NIGHTLY, **CRAWLER})
        self.assertEqual(self.fetches, [101, 202, 303])
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.free_in_fetch, [False] * 3)
        self.assertTrue(self.lock_free())

    def test_a_shell_naming_the_script_is_no_crawl(self):
        """A shell that mentions the script and --no-yield neither keeps a
        paused crawler from counting as paused nor stands in for one."""
        self.crawler_holds_lock()
        self.run_pick({**NIGHTLY, **CRAWLER, **MENTIONS})
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.fetches, [101, 202, 303])
        with mock.patch.object(subprocess, "run", side_effect=Procs({**NIGHTLY, **MENTIONS})), \
                mock.patch.object(ps.os, "getppid", return_value=MODEL):
            self.assertIsNone(ps.crawler_paused())

    def test_paused_names_the_nearest_refresh_ancestor(self):
        with mock.patch.object(subprocess, "run", side_effect=Procs({**NIGHTLY, **CRAWLER})), \
                mock.patch.object(ps.os, "getppid", return_value=MODEL):
            self.assertEqual(ps.crawler_paused(), REFRESH)


class CrawlerSide(unittest.TestCase):
    """backfill_sales_volume: the refresh test, and a hand-off past its wait."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript(ps.SALES_SCHEMA)
        self.path = bsv.WATCH_FILES[1]
        self.assertTrue(self.path.endswith("showcase_watch.json"))
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def targets(self, **extra):
        now = datetime.now(timezone.utc)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"made": (now - timedelta(minutes=10)).isoformat(),
                       "for": now.date().isoformat(),
                       "cards": [{"game": "onepiece", "pid": 101}], **extra}, f)
        with contextlib.redirect_stdout(io.StringIO()):
            return bsv.watch_targets(self.conn, {"onepiece"})

    def test_hand_off_past_its_wait_is_skipped(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.assertEqual(self.targets(until=past), [])

    def test_hand_off_inside_its_wait_is_fetched(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(timespec="seconds")
        self.assertEqual(self.targets(until=soon), [("onepiece", 101, 0.0, True)])

    def test_list_without_until_is_fetched_for_its_day(self):
        self.assertEqual(self.targets(), [("onepiece", 101, 0.0, True)])

    def test_crawl_started_during_a_refresh_pauses_before_its_first_fetch(self):
        """A crawl launched while a refresh is live (pick_showcase may be
        fetching then) fetches nothing until that refresh is over."""
        events, live = [], iter([True, True, False])

        def refresh_live():
            events.append("live?")
            return next(live, False)

        def fetch(session, pid, delay):
            events.append(f"fetch {pid}")
            return None                    # no history: only marked scanned (in the temp db)

        base = os.path.join(TMP, "crawl")
        os.makedirs(base, exist_ok=True)
        with mock.patch.object(bsv, "BASE", base), \
                mock.patch.object(bsv, "PC_DB", os.path.join(base, "pc.db")), \
                mock.patch.object(bsv, "market_games", return_value=["onepiece"]), \
                mock.patch.object(bsv, "gather", return_value=[("onepiece", 101, 5.0, True),
                                                               ("onepiece", 102, 4.0, True)]), \
                mock.patch.object(bsv, "refresh_live", side_effect=refresh_live), \
                mock.patch.object(bsv.time, "sleep", side_effect=lambda s: events.append(f"sleep {s}")), \
                mock.patch.object(bsv.tp, "fetch_price_history", side_effect=fetch), \
                mock.patch.object(bsv.tp, "make_session", return_value=object()), \
                mock.patch.object(bsv.tp, "RATE_LIMITER", None), \
                mock.patch.object(sys, "argv", ["backfill_sales_volume.py", "--game", "all"]), \
                contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)   # main() keeps its lock open by design
            bsv.main()
        self.assertEqual(events, ["live?", "live?", "sleep 600", "live?", "fetch 101", "fetch 102"])

    def test_refresh_live_reads_the_refresh_pids(self):
        with mock.patch.object(subprocess, "run", side_effect=Procs(NIGHTLY)):
            self.assertEqual(bsv.refresh_pids(), {CAFFEINATE, REFRESH})
            self.assertTrue(bsv.refresh_live())
        with mock.patch.object(subprocess, "run", side_effect=Procs(DAYTIME)):
            self.assertEqual(bsv.refresh_pids(), set())
            self.assertFalse(bsv.refresh_live())


if __name__ == "__main__":
    unittest.main()
