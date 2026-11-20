"""The daytime crawls' daily quota and stop time (crawl_day.py)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawl_day import day_start_utc, stop_time, past, quota_left

CST = timezone(timedelta(hours=-6))


class CrawlDay(unittest.TestCase):
    def test_day_starts_at_local_midnight(self):
        now = datetime(2026, 11, 20, 11, 30, tzinfo=CST)
        self.assertEqual(day_start_utc(now), "2026-11-20T06:00:00+00:00")
        # A page stamped last evening (local) is not today's.
        self.assertLess("2026-11-20T03:10:00.5+00:00", day_start_utc(now))
        self.assertGreater("2026-11-20T16:48:00.123456+00:00", day_start_utc(now))

    def test_stop_time(self):
        now = datetime(2026, 11, 20, 17, 59, tzinfo=CST)
        stop = stop_time("18:00", now)
        self.assertFalse(past(stop, now))
        self.assertTrue(past(stop, now + timedelta(minutes=1)))
        self.assertIsNone(stop_time(None))
        self.assertFalse(past(None))

    def test_quota_counts_today_and_overdue(self):
        self.assertEqual(quota_left(6600, 0, 0), 6600)
        self.assertEqual(quota_left(6600, 2500, 100), 4000)
        # Overdue pages are never capped; they just leave no room for more.
        self.assertEqual(quota_left(6600, 1000, 20000), 0)
        self.assertEqual(quota_left(6600, 7000, 0), 0)


if __name__ == "__main__":
    unittest.main()
