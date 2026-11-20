"""The daytime crawls' day (2026-11-20): a steady daily quota and a stop time.

The graded and sales crawls used to launch six times a day for a fixed batch
and could only start early on a page at least 21 days old, so pages crawled
in one busy week all fell due in one week again. Each crawl now takes its
overdue pages plus the oldest-crawled ones up to a daily quota, with no age
floor, and counts what was already crawled today, so any number of launches
add up to one day's quota. It stops itself at --stop-at, before the nightly
refresh starts (19:05, Saturdays 18:20).
"""
from datetime import datetime, timezone


def day_start_utc(now=None):
    """Local midnight as the UTC ISO string the crawl stamps compare with."""
    now = now or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) \
              .astimezone(timezone.utc).isoformat()


def stop_time(hhmm, now=None):
    """Today's HH:MM (local) as an aware datetime; None when no stop is set."""
    if not hhmm:
        return None
    now = now or datetime.now().astimezone()
    h, m = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def past(stop, now=None):
    return stop is not None and (now or datetime.now().astimezone()) >= stop


def quota_left(quota, done_today, due):
    """How many not-yet-due pages to add: the quota, less what today already
    crawled, less the overdue pages this run takes anyway (those are never
    capped). Never negative."""
    return max(0, quota - done_today - due)
