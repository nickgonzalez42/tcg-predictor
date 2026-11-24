"""Small text rules the articles share (2026-11-24 review of eight type articles)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import game_articles as ga
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


if __name__ == "__main__":
    unittest.main()
