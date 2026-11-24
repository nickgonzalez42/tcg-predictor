"""The crawls' stop time (crawl_day.py)."""
import os
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crawl_day
from crawl_day import stop_time, past

CST = timezone(timedelta(hours=-6))


class CrawlDay(unittest.TestCase):
    def test_stop_time(self):
        now = datetime(2026, 11, 20, 17, 59, tzinfo=CST)
        stop = stop_time("18:00", now)
        self.assertFalse(past(stop, now))
        self.assertTrue(past(stop, now + timedelta(minutes=1)))
        self.assertIsNone(stop_time(None))
        self.assertFalse(past(None))


class RefreshLive(unittest.TestCase):
    """The crawls stand down for a refresh that holds its lock, and for
    nothing else."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.lock = os.path.join(self.dir, ".daily_refresh.lock")
        self.old = crawl_day.REFRESH_LOCKS
        crawl_day.REFRESH_LOCKS = ((self.lock, "run_daily_refresh"),)
        self.addCleanup(setattr, crawl_day, "REFRESH_LOCKS", self.old)

    def live(self, commands, parents=None):
        """refresh_live() with `ps` answering from {pid: command} / {pid: ppid}."""
        parents = parents or {}
        def ps(argv, **_kw):
            pid = int(argv[-1])
            out = (commands.get(pid, "") if "command=" in argv
                   else str(parents.get(pid, 1)))
            return subprocess.CompletedProcess(argv, 0, stdout=out + "\n")
        with mock.patch.object(subprocess, "run", side_effect=ps), \
                mock.patch.object(os, "kill", side_effect=lambda pid, _s: None
                                  if pid in commands else (_ for _ in ()).throw(ProcessLookupError())):
            return crawl_day.refresh_live()

    def test_no_lock_or_an_empty_one_is_free(self):
        self.assertFalse(self.live({}))
        open(self.lock, "w").close()
        self.assertFalse(self.live({}))

    def test_a_live_runner_holding_the_lock(self):
        with open(self.lock, "w") as f:
            f.write("4242\n")
        self.assertTrue(self.live({4242: "/bin/zsh pipeline/run_daily_refresh.sh"}))

    def test_a_dead_run_or_a_reused_pid_is_free(self):
        with open(self.lock, "w") as f:
            f.write("4242\n")
        self.assertFalse(self.live({}))                                   # pid gone
        self.assertFalse(self.live({4242: "/usr/bin/python3 something_else.py"}))

    def test_a_command_that_only_names_the_script_does_not_count(self):
        # grep, an editor, a dry run: on the process list, holding no lock
        self.assertFalse(self.live({777: "grep -n LOCK pipeline/run_daily_refresh.sh"}))

    def test_a_crawl_started_by_the_refresh_does_not_wait_for_it(self):
        with open(self.lock, "w") as f:
            f.write("4242\n")
        me = os.getpid()
        self.assertFalse(self.live({4242: "zsh run_daily_refresh.sh"},
                                   parents={me: 900, 900: 4242, 4242: 1}))


if __name__ == "__main__":
    unittest.main()
