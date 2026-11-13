#!/usr/bin/env python3
"""
pick_showcase's sales vetting next to the daytime sales crawler (2026-11-13:
the nightly handed 18 lists to a crawler paused for the refresh and waited
74 minutes for nothing). No network, no real database, and every data path
(the crawler's lock, the watch lists) in a temp folder.

The process questions are answered the way macOS answers them: pgrep leaves
out its caller's ancestors unless -a is given, and `exec caffeinate` keeps
the refresh script on its pid with the caffeinate as its child. The first
mocks had it the other way round and passed while the nightly could not see
its own refresh, so one test also runs the nightly's process shape for real,
under a marker name (NightlyProcesses).

Run (from the repo root):
      ../one-piece/.venv/bin/python -m unittest discover -s pipeline/tests -v
"""
import contextlib
import fcntl
import io
import json
import os
import re
import shutil
import signal
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
PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PIPELINE)

import backfill_sales_volume as bsv  # noqa: E402
import pick_showcase as ps           # noqa: E402

for _p in [ps.LOCK_PATH, ps.WATCH_FILE] + bsv.WATCH_FILES:
    assert os.path.realpath(_p).startswith(os.path.realpath(TMP)), f"{_p} is outside the temp folder"


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


# pid: (ppid, command line)
CAFFEINATE, REFRESH, MODEL, SHELL, AGENT = 400, 500, 600, 700, 800
NIGHTLY = {   # as launchd starts it: `exec caffeinate` keeps the script on its
              # pid and forks the caffeinate as its child; the model block runs
              # pick_showcase (parent: MODEL)
    1: (0, "/sbin/launchd"),
    REFRESH: (1, "/bin/zsh /x/pipeline/run_daily_refresh.sh"),
    CAFFEINATE: (REFRESH, "caffeinate -i /bin/zsh /x/pipeline/run_daily_refresh.sh"),
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
HAND_RUN = {   # an agent's shell line that names the refresh and runs the pick by hand
    AGENT: (SHELL, "/bin/zsh -c source /x/snapshot.zsh && eval 'grep -n yield "
                   "pipeline/run_daily_refresh.sh; python pipeline/pick_showcase.py'"),
}
HISTORY = {"result": [{"variant": "Normal", "condition": "Near Mint", "buckets": [
    {"bucketStartDate": "2026-11-02", "quantitySold": 4, "transactionCount": 3}]}]}


class Procs:
    """subprocess.run for the two process questions, answered as on macOS:
    pgrep -f for the refresh (backfill_sales_volume.refresh_pids) leaves out
    the caller's ancestors unless -a is given, and ps lists every process.
    Both outputs are bytes decoded as subprocess.run would (a command line
    may not be UTF-8). `parent` is the caller's parent (os.getppid)."""

    def __init__(self, table, parent):
        self.table, self.parent, self.calls = dict(table), parent, []

    def ancestors(self):
        out, pid = set(), self.parent
        while pid in self.table and pid not in out:
            out.add(pid)
            pid = self.table[pid][0]
        return out

    def __call__(self, argv, **kw):
        self.calls.append(argv[0])
        if argv[0] == "pgrep" and argv[-1] == bsv.REFRESH_PROCS and set(argv[1:-1]) in (
                {"-f"}, {"-a", "-f"}):
            hidden = set() if "-a" in argv else self.ancestors()
            out = "".join(f"{p}\n" for p, (_pp, cmd) in sorted(self.table.items())
                          if p not in hidden and re.search(argv[-1], cmd))
        elif argv[0] == "ps":
            out = "".join(f"{p:>5} {pp:>5} {cmd}\n" for p, (pp, cmd) in sorted(self.table.items()))
        else:
            raise AssertionError(f"unexpected command: {argv}")
        raw = out.encode("utf-8", "surrogateescape")
        if kw.get("text") or kw.get("errors"):
            out = raw.decode("utf-8", kw.get("errors") or "strict")
        else:
            out = raw
        return subprocess.CompletedProcess(argv, 0 if raw else 1, out, "")


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
        self.procs = Procs(table, parent)
        with mock.patch.object(subprocess, "run", side_effect=self.procs), \
                mock.patch.object(ps.os, "getppid", return_value=parent):
            self.sales.ensure_fresh(self.NEED)

    def paused(self, table, parent=MODEL):
        with mock.patch.object(subprocess, "run", side_effect=Procs(table, parent)), \
                mock.patch.object(ps.os, "getppid", return_value=parent):
            return ps.crawler_paused()

    # -- the three cases ---------------------------------------------------
    def test_crawler_paused_for_the_refresh_means_a_direct_fetch(self):
        """Crawler alive + refresh live (the one this run is part of): no
        hand-off, no wait, every card fetched here."""
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

    def test_hand_run_from_a_line_naming_the_refresh_keeps_the_hand_off(self):
        """An ancestor that only names the refresh script (an agent's zsh -c
        line, which pgrep -a lists) is not a refresh this run is part of."""
        self.crawler_holds_lock()
        self.run_pick({**NIGHTLY, **CRAWLER, **HAND_RUN}, parent=AGENT)
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.handoffs, [self.NEED])

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

    def test_a_command_line_that_is_not_utf8_is_read(self):
        """ps passes argv bytes through as they are: one process with a
        non-UTF-8 argument must not raise out of the pick."""
        self.crawler_holds_lock()
        odd = {33000: (SHELL, "/x/python -c pass \udcff\udcfe")}   # the bytes ff fe
        self.run_pick({**NIGHTLY, **CRAWLER, **odd})
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.fetches, [101, 202, 303])

    # -- the refresh or the crawler going away mid-list -----------------------
    def test_refresh_gone_mid_list_stops_and_hands_the_rest_over(self):
        """The refresh dies under the run (a launchd reload): the crawler may
        resume, so the fetching here stops and the cards left go to it."""
        self.crawler_holds_lock()
        self.crawler_crawls = True

        def refresh_dies(pid):
            if pid == 202:             # its caffeinate goes with it
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

    # -- reading the process table ---------------------------------------------
    def test_a_shell_naming_the_script_is_no_crawl(self):
        """A shell that mentions the script and --no-yield neither keeps a
        paused crawler from counting as paused nor stands in for one."""
        self.crawler_holds_lock()
        self.run_pick({**NIGHTLY, **CRAWLER, **MENTIONS})
        self.assertEqual(self.handoffs, [])
        self.assertEqual(self.fetches, [101, 202, 303])
        self.assertIsNone(self.paused({**NIGHTLY, **MENTIONS}))

    def test_paused_names_the_nearest_refresh_ancestor(self):
        resumed = dict(NIGHTLY)          # the watchdog's relaunch
        resumed[REFRESH] = (1, "/bin/zsh /x/pipeline/run_daily_refresh.sh --resume 2026-11-13")
        wrapped = dict(NIGHTLY)          # a caffeinate above the script, should one ever be
        wrapped[CAFFEINATE] = (1, NIGHTLY[CAFFEINATE][1])
        wrapped[REFRESH] = (CAFFEINATE, NIGHTLY[REFRESH][1])
        for name, table in (("nightly", NIGHTLY), ("resumed", resumed), ("wrapped", wrapped)):
            with self.subTest(name):
                self.assertEqual(self.paused({**table, **CRAWLER}), REFRESH)

    def test_runs_reads_what_a_command_line_runs(self):
        refresh, crawl = bsv.REFRESH_SCRIPTS, (ps.CRAWL_SCRIPT,)
        for cmd, scripts, want in [
                ("/bin/zsh /x/pipeline/run_daily_refresh.sh", refresh, True),
                ("/bin/zsh /x/pipeline/run_daily_refresh.sh --resume 2026-11-13", refresh, True),
                ("caffeinate -i /bin/zsh /x/pipeline/run_daily_refresh.sh", refresh, True),
                ("/x/Python -u weekly_refresh.py --tail --resume", refresh, True),
                (HAND_RUN[AGENT][1], refresh, False),
                ("vim pipeline/run_daily_refresh.sh", refresh, False),
                ("/bin/zsh local_model_run.sh", refresh, False),
                ("-zsh", refresh, False),
                ("", refresh, False),
                (CRAWLER[29061][1], crawl, True),
                (CRAWLER[29063][1], crawl, True),
                (TAIL_SALES[31000][1], crawl, True),
                (MENTIONS[32000][1], crawl, False),
                (MENTIONS[32001][1], crawl, False),
                ("/x/python -m backfill_sales_volume.py", crawl, False),
                ("caffeinate -i node clicker.js", crawl, False)]:
            with self.subTest(cmd):
                self.assertIs(ps.runs(cmd, scripts), want)


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
        """The crawler's own test, from outside the refresh (a launchd agent)."""
        with mock.patch.object(subprocess, "run", side_effect=Procs(NIGHTLY, 1)):
            self.assertEqual(bsv.refresh_pids(), {CAFFEINATE, REFRESH})
            self.assertTrue(bsv.refresh_live())
        with mock.patch.object(subprocess, "run", side_effect=Procs(DAYTIME, 1)):
            self.assertEqual(bsv.refresh_pids(), set())
            self.assertFalse(bsv.refresh_live())

    def test_refresh_pids_reads_only_the_pid_column(self):
        """Linux's pgrep -a lists command lines too: their numbers are no pids."""
        out = "500 /bin/zsh /x/pipeline/run_daily_refresh.sh --resume 2026-11-13 40\n"
        with mock.patch.object(subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, out, "")):
            self.assertEqual(bsv.refresh_pids(ancestors=True), {500})


# The nightly's first lines (re-exec under caffeinate) and its model block,
# under a marker name; the python in it reports what pick_showcase sees.
REFRESH_STANDIN = """#!/bin/zsh
SELF="${0:A}"
if [[ -z "${SHOWCASE_PROBE_CAFFEINATED:-}" ]]; then
  export SHOWCASE_PROBE_CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$SELF" "$@"
fi
/bin/zsh "${SELF:h}/model.sh"
exit $?
"""
MODEL_STANDIN = """#!/bin/zsh
"$SHOWCASE_PROBE_PY" "${0:A:h}/probe.py"
exit $?
"""
PROBE = """
import json, os, subprocess, sys
sys.path.insert(0, os.environ["SHOWCASE_PROBE_PIPELINE"])
import backfill_sales_volume as bsv
import pick_showcase as ps

script = os.environ["SHOWCASE_PROBE_SCRIPT"]      # the refresh stand-in's file name
bsv.REFRESH_PROCS = script.replace(".", "[.]")
ps.REFRESH_SCRIPTS = (script,)
real_run = subprocess.run


def run(argv, **kw):          # a paused daytime crawler, in this ps listing only
    r = real_run(argv, **kw)
    if argv[0] == "ps":
        r.stdout += ("999999 1 /x/python /x/pipeline/backfill_sales_volume.py "
                     "--game all --rpm 40 --refresh-days 28\\n")
    return r


subprocess.run = run
refresh = int(real_run(["ps", "-o", "ppid=", "-p", str(os.getppid())],
                       capture_output=True, text=True).stdout)
print(json.dumps({"refresh": refresh, "plain": sorted(bsv.refresh_pids()),
                  "ancestors": sorted(bsv.refresh_pids(ancestors=True)),
                  "paused": ps.crawler_paused()}))
"""


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("caffeinate"),
                     "the nightly's process layout is macOS's")
class NightlyProcesses(unittest.TestCase):
    """The nightly's process shape for real: a zsh script that re-execs
    itself under caffeinate, a zsh model block it runs, and a python in that
    asking crawler_paused(). A marker name stands in for the refresh's, so
    nothing a crawler pauses for runs; the paused crawler is a line added to
    the probe's ps output only."""

    def test_the_model_block_finds_its_refresh(self):
        d = tempfile.mkdtemp(prefix="nightly-", dir=TMP)
        script = f"showcaseprobe{os.getpid()}.sh"
        for name, text in ((script, REFRESH_STANDIN), ("model.sh", MODEL_STANDIN),
                           ("probe.py", PROBE)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)

        def stop_leftovers():          # by pid: only processes running from d
            r = subprocess.run(["pgrep", "-f", os.path.basename(d)], capture_output=True, text=True)
            for p in r.stdout.split():
                with contextlib.suppress(OSError, ValueError):
                    os.kill(int(p), signal.SIGTERM)
        self.addCleanup(stop_leftovers)
        env = {k: v for k, v in os.environ.items() if k != "SHOWCASE_PROBE_CAFFEINATED"}
        env.update(SHOWCASE_PROBE_PY=sys.executable, SHOWCASE_PROBE_SCRIPT=script,
                   SHOWCASE_PROBE_PIPELINE=PIPELINE)
        r = subprocess.run(["/bin/zsh", os.path.join(d, script)], env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        seen = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn(seen["refresh"], seen["plain"])   # pgrep hides it from inside...
        self.assertIn(seen["refresh"], seen["ancestors"])  # ... unless -a (still_paused's test)
        self.assertEqual(seen["paused"], seen["refresh"])


if __name__ == "__main__":
    unittest.main()
