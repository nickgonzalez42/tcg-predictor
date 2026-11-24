"""Small text rules the articles share (2026-11-24 review of eight type articles)."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import article_editorial as ae
import game_articles as ga
import type_articles as ta
from market_report import pct


class Percent(unittest.TestCase):
    def test_no_negative_zero(self):
        self.assertEqual(pct(-0.04), "+0.0%")
        self.assertEqual(pct(0.0), "+0.0%")
        self.assertEqual(pct(-0.06), "-0.1%")
        self.assertEqual(pct(12.34), "+12.3%")


class Section(unittest.TestCase):
    def test_written_heading_and_paragraph_together(self):
        prose = {"headers": {"examples": "Three Cards, Three Very Different Years"},
                 "sections": {"examples": "Charizard did this, Charmander that."}}
        out = ga.section(prose, "examples", "A Few Cards That Tell the Story", "stock text", "", "cap")
        self.assertIn("<h2>Three Cards, Three Very Different Years</h2>", out)
        self.assertIn("Charizard did this", out)

    def test_a_dropped_paragraph_takes_its_heading_with_it(self):
        # the overreach guard removed the paragraph; the witty heading must not
        # sit over the one stock sentence
        prose = {"headers": {"examples": "Three Cards, Three Very Different Years"}, "sections": {}}
        out = ga.section(prose, "examples", "A Few Cards That Tell the Story",
                         "Charizard is the dearest at $5,975.50.", "", "cap")
        self.assertIn("<h2>A Few Cards That Tell the Story</h2>", out)
        self.assertNotIn("Three Cards", out)
        self.assertIn("Charizard is the dearest", out)

    def test_model_corner_follows_the_same_rule(self):
        prose = {"headers": {"model": "The Model Shrugs"}, "sections": {}}
        out = ga.model_corner(prose, "model", "What the Model Expects", "stock", [], "magic", None, "")
        self.assertIn("What the Model Expects", out)
        self.assertNotIn("The Model Shrugs", out)


class Style(unittest.TestCase):
    """One style for numbers and words, whatever the writer sent."""

    def test_percent_dollars_and_signs(self):
        self.assertEqual(ae._style("at $81.41 after a 63.5% drop, called at negative 15.9% and 51.6%"),
                         "at $81.41 after a 63.5 percent drop, called at -15.9 percent and 51.6 percent")
        self.assertEqual(ae._style("the dearest, 624.12 dollars, up 35.3 percent"),
                         "the dearest, $624.12, up 35.3 percent")
        self.assertEqual(ae._style("range negative 36.4 to positive 15.9: a partial refund"),
                         "range -36.4 to +15.9: a partial refund")
        self.assertEqual(ae._style("a negative surprise and three percentage points"),
                         "a negative surprise and three percentage points")

    def test_title_case_in_a_headline(self):
        self.assertEqual(ae._style("One Piece SP Cards Are Up 57% over the Past Year", title=True),
                         "One Piece SP Cards Are Up 57 Percent over the Past Year")

    def test_words_we_do_not_use(self):
        self.assertEqual(ae._style("the model's homework, graded later"), "the model's homework, checked later")
        self.assertEqual(ae._style("Graded Later, If at All"), "Checked Later, If at All")
        self.assertEqual(ae._style("over every horizon the cohort fell"), "over every window the group fell")

    def test_names_are_left_alone(self):
        self.assertEqual(ae._style("Ral, Monsoon Mage from Modern Horizons 3, ungraded"),
                         "Ral, Monsoon Mage from Modern Horizons 3, ungraded")


class HeadlineFigure(unittest.TestCase):
    """The title states the number the picture shows, or ours stands."""

    def test_a_percentage(self):
        self.assertTrue(ta.carries("One Piece SP Cards Are Up 57 Percent over the Past Year", 57.0))
        self.assertTrue(ta.carries("Alternate Art Cards Fell 12.6 Percent Over the Past Year", -12.6))
        self.assertFalse(ta.carries("Flesh and Blood Marvel Cards Fell 2.4 Percent as Market Held Flat", -2.1))
        self.assertFalse(ta.carries("Cards Are Up 157 Percent", 57.0))
        self.assertFalse(ta.carries("Cards Are Up 57.3 Percent", 57.0))
        self.assertFalse(ta.carries(None, 57.0))

    def test_a_price(self):
        self.assertTrue(ta.carries("Riftbound's Signature Cards Carry a $672.31 Median Price", 672.31, True))
        self.assertTrue(ta.carries("Signature Cards Sit at a $672 Median", 672.31, True))
        self.assertTrue(ta.carries("Manga Cards Sit at a $1,234.50 Median", 1234.5, True))
        self.assertFalse(ta.carries("Signature Cards Rose 57 Percent", 672.31, True))


class Recent(unittest.TestCase):
    """The writer is shown what the last few articles already used."""

    def setUp(self):
        self.pred = sqlite3.connect(":memory:")
        self.pred.execute("CREATE TABLE articles (slug TEXT, published_at TEXT, body_html TEXT)")
        self.pred.executemany("INSERT INTO articles VALUES (?,?,?)", [
            ("a", "2026-11-20", "<h2>All 43 Manga Cards</h2><p>Dearest first.</p>"
                                "<h2>Three Cards, Three Very Different Years</h2>"
                                "<p>One fell 12.6 percent. Still falling. It &amp; one other card rose.</p>"),
            ("b", "2026-11-21", "<h2>The 150 Most Expensive Secret Rare Cards</h2>"
                                "<h2>The Model Only Trusts Its Own Next Month</h2><p>No seat. Up 3 percent.</p>"),
            ("old", "2026-10-01", "<h2>An Old Header</h2><p>Long gone.</p>"),
        ])

    def tearDown(self):
        ae.set_recent(None)

    def test_headers_and_short_lines_of_the_newest(self):
        used = ga.recent_phrases(self.pred, n=2)
        self.assertEqual(used["headers"], ["The Model Only Trusts Its Own Next Month",
                                           "Three Cards, Three Very Different Years"])
        self.assertEqual(used["refrains"], ["No seat.", "Dearest first.", "Still falling."])   # none with a number

    def test_the_prompt_block(self):
        self.assertEqual(ae._recent_block(), "")
        ae.set_recent(ga.recent_phrases(self.pred, n=2))
        block = ae._recent_block()
        self.assertIn("Three Cards, Three Very Different Years", block)
        self.assertIn("Still falling.", block)
        self.assertNotIn("An Old Header", block)

    def test_no_table_no_block(self):
        self.assertEqual(ga.recent_phrases(sqlite3.connect(":memory:")), {})


if __name__ == "__main__":
    unittest.main()
