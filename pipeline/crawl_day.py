"""When the daytime crawls must stand down (2026-11-20).

--stop-at HH:MM ends a crawl run at a local time. The daytime agents do not
use it: they run until the nightly refresh starts and leave then, which
refresh_live() tells them.
"""
import os
import subprocess
from datetime import datetime

from _paths import DATA_DIR

# The refresh's own locks: the nightly runner's (run_daily_refresh.sh writes
# its pid, empties the file on exit) and weekly_refresh.py's (a hand run of
# the crawl steps; removed on exit).
REFRESH_LOCKS = ((os.path.join(DATA_DIR, ".daily_refresh.lock"), "run_daily_refresh"),
                 (os.path.join(DATA_DIR, ".weekly_refresh.lock"), "weekly_refresh.py"))


def _command(pid):
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                          capture_output=True, text=True, errors="replace").stdout


def _parent(pid):
    out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else 0


def lock_holder(lock, needle):
    """The live pid a refresh lock names, or None. An empty file, a lock left
    by a dead run, and a pid that now belongs to something else are all free
    (the same reading as run_daily_refresh.sh's lock_holder)."""
    try:
        with open(lock) as f:
            pid = int(f.readline().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid if needle in _command(pid) else None


def refresh_live():
    """True while a refresh holds one of its locks, unless this process was
    started BY that refresh (the nightly's own crawl steps are not asked to
    wait for themselves).

    It used to be `pgrep -f "run_daily_refresh.sh|weekly_refresh.py"`, which
    is true for ANY process whose command line holds one of those names: a
    dry run, a grep or an editor on the file, another shell reading the log.
    Each one sent both crawls home until the next hourly launch (11-20 16:58,
    11-21 08:58, 11-22 17:09, 11-24 09:56, with no refresh anywhere near)."""
    holders = {h for h in (lock_holder(lock, needle) for lock, needle in REFRESH_LOCKS) if h}
    if not holders:
        return False
    pid, seen = os.getpid(), set()
    while pid > 1 and pid not in seen:          # our own ancestry
        if pid in holders:
            return False
        seen.add(pid)
        pid = _parent(pid)
    return True


def stop_time(hhmm, now=None):
    """Today's HH:MM (local) as an aware datetime; None when no stop is set."""
    if not hhmm:
        return None
    now = now or datetime.now().astimezone()
    h, m = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def past(stop, now=None):
    return stop is not None and (now or datetime.now().astimezone()) >= stop
