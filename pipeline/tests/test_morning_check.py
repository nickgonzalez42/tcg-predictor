"""The morning check reads a night's files; no network, no real data here."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import morning_check as mc

STATE = """night csv 2026-11-26 19:05:01
started 2026-11-26 19:05:01
stage core 2026-11-26 19:05:04
ok core 2026-11-26 21:04:10
stage model 2026-11-26 21:04:16
ok model 2026-11-27 01:22:01
stage publish 2026-11-27 01:22:02
ok publish 2026-11-27 01:50:40
finished ok 2026-11-27 01:50:41
stage tail 2026-11-27 01:58:46
ok tail 2026-11-27 04:03:46
done 2026-11-27 04:21:31
"""
LOG = """--- game_articles 00:35:57 ---
wrote new-releases-magic-2026-11-27: "Magic's New Side Releases Fell 3.5 Percent This Week"
article_editorial: kept 18 prose field(s) (5/5 sections)
wrote card-type-riftbound-overnumbered-2026-11-27: 'Riftbound Overnumbered'
--- game_articles rc=0 in 34 min ---
Wrote reports: weekly-market-report-2026-11-27 (57,276 cards in window 2026-11-20 → 2026-11-27) and model-report-2026-11-27
prerendered 231,877 card pages + home/about/privacy/terms/contact in 298s -> /srv/tcg/static/prerender
graded: 0 card(s) over 32 days (oldest None)
sales: 0 card(s) over 32 days (oldest None)
indexnow: answered 200
  ok  crawl-staleness     0.0 min
"""


class Review(unittest.TestCase):
    def test_a_clean_night_has_nothing_to_look_at(self):
        problems, fine, pages = mc.review("2026-11-27", "2026-11-27", STATE, LOG)
        self.assertEqual(problems, [])
        self.assertIn("finished at 04:21", fine[0])
        self.assertIn("updated at 01:50", fine[0])
        self.assertIn("298 s", fine[1])
        self.assertEqual([p for p, _ in pages],
                         ["/articles/new-releases-magic-2026-11-27",
                          "/articles/card-type-riftbound-overnumbered-2026-11-27",
                          "/reports/weekly-market-report-2026-11-27", "/reports/model-report-2026-11-27"])
        self.assertEqual(pages[0][1], "Magic's New Side Releases Fell 3.5 Percent This Week")

    def test_the_quiet_faults_are_each_named(self):
        log = LOG + "\n".join([
            "article_editorial: CLI failed (TimeoutExpired), keeping templated text",
            "pc_printing_backfill: non-fatal error: URLError: reset",
            "delta_extras: tcg_nm_history not shipped tonight (cap)",
            "article_editorial: section 'movers' ran 170 words against 95 (+10), keeping templated text",
            "article_editorial: kept 19 prose field(s) (2/7 sections)",
            "type_articles: read failed (database is locked); that part is left out",
            "--- forecast_predict rc=1 in 772 min ---",
            "=== MODEL BLOCK FAILED at forecast_predict (rc=1) — predictions.db NOT pushed ===",
            "indexnow: answered 403", "graded: 1,250 card(s) over 32 days (oldest 2026-10-01)"])
        problems, _fine, _pages = mc.review("2026-11-27", "2026-11-27", STATE, log)
        text = "\n".join(problems)
        for want in ("A step failed (2)", "skipped (1)", "held back", "writer failed", "ran too long",
                     "could not read", "only part of its written text (1)", "ping was refused",
                     "graded crawl is behind"):
            self.assertIn(want, text)
        self.assertNotIn("5/5", text)                       # a whole article is not a fault

    def test_many_lines_of_one_kind_are_cut_short(self):
        log = LOG + "\n".join(f"step{i}: non-fatal error: boom" for i in range(7))
        problems, _f, _p = mc.review("2026-11-27", "2026-11-27", STATE, log)
        self.assertIn("skipped (7)", problems[0])
        self.assertIn("(4 more like it)", problems[0])

    def test_a_night_still_running(self):
        early = STATE.split("finished ok")[0]
        problems, _f, _p = mc.review("2026-11-27", "2026-11-27", early, "")
        self.assertIn("NOT been updated yet", problems[0])
        self.assertIn("stage: publish", problems[0])
        late = STATE.split("ok tail")[0]
        problems, _f, _p = mc.review("2026-11-27", "2026-11-27", late, LOG)
        self.assertIn("last steps are still running (stage: tail)", problems[0])

    def test_no_night_at_all(self):
        problems, fine, pages = mc.review("2026-11-26", "2026-11-27", STATE, LOG)
        self.assertEqual(len(problems), 1)
        self.assertIn("No refresh ran last night", problems[0])
        self.assertEqual((fine, pages), ([], []))

    def test_a_site_update_with_no_crawler_pages(self):
        log = "\n".join(ln for ln in LOG.splitlines() if "prerendered" not in ln)
        problems, _f, _p = mc.review("2026-11-27", "2026-11-27", STATE, log)
        self.assertIn("crawler pages were not rebuilt", "\n".join(problems))


class Compose(unittest.TestCase):
    def test_the_subject_carries_the_verdict(self):
        s, body = mc.compose(date(2026, 11, 27), [], ["The night finished at 04:21."], "/x.log")
        self.assertEqual(s, "CardStock morning check, Nov 27: all clear")
        self.assertNotIn("TO LOOK AT", body)
        s, body = mc.compose(date(2026, 11, 7), ["one"], ["fine"], "/x.log")
        self.assertEqual(s, "CardStock morning check, Nov 7: 1 thing to look at")
        self.assertLess(body.index("TO LOOK AT"), body.index("FINE"))
        s, _ = mc.compose(date(2026, 11, 27), ["a", "b"], [], "/x.log")
        self.assertTrue(s.endswith("2 things to look at"))


if __name__ == "__main__":
    unittest.main()
