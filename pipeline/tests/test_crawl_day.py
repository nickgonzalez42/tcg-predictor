"""The crawls' stop time (crawl_day.py)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


if __name__ == "__main__":
    unittest.main()
