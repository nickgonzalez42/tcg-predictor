"""A stop time for the crawls (2026-11-20).

--stop-at HH:MM ends a crawl run at a local time. The daytime agents do not
use it: they run until the nightly refresh starts and leave then.
"""
from datetime import datetime


def stop_time(hhmm, now=None):
    """Today's HH:MM (local) as an aware datetime; None when no stop is set."""
    if not hhmm:
        return None
    now = now or datetime.now().astimezone()
    h, m = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def past(stop, now=None):
    return stop is not None and (now or datetime.now().astimezone()) >= stop
