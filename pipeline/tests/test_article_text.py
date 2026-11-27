"""Small text rules the articles share (2026-11-24 review of eight type articles)."""
import os
import sqlite3
import sys
import types
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


class StillLine(unittest.TestCase):
    def test_the_tag_line_goes_wherever_it_sits(self):
        self.assertEqual(ae._style("The total slid 3.5 percent for the week. Still undercard."),
                         "The total slid 3.5 percent for the week.")
        self.assertEqual(ae._style("It fell. Still no promotion. Then it fell again."),
                         "It fell. Then it fell again.")
        self.assertEqual(ae._style("Still undercard."), "")

    def test_a_real_sentence_that_starts_with_still_stays(self):
        for keep in ("Still, the index is up 4 percent on the year.",
                     "It fell. Still up 4.1 percent on the year.",
                     "Still the dearest card in the set by a wide margin.",
                     "The card is still falling."):
            self.assertEqual(ae._style(keep), keep)

    def test_a_title_is_left_alone(self):
        self.assertEqual(ae._style("Down 28.7 Percent. Still Winning.", title=True),
                         "Down 28.7 Percent. Still Winning.")

    def test_the_rule_is_in_the_prompt(self):
        self.assertIn('begins with "Still"', ae.RULES)


class TypeMembers(unittest.TestCase):
    CAT = {1: {"name": "A", "set": "Rising Rampage", "rarity": "Starlight Rare"},
           2: {"name": "B", "set": "Rarity Collection 5", "rarity": "Starlight Rare"},
           3: {"name": "C", "set": "2025 Mega-Pack", "rarity": "Starlight Rare"},
           4: {"name": "D", "set": None, "rarity": "Starlight Rare"},
           5: {"name": "E", "set": "Rising Rampage", "rarity": "Ghost Rare"}}

    def test_starlight_leaves_out_the_mass_reprint_sets(self):
        spec = ga.CARD_TYPES["yugioh"]["starlight"]
        self.assertEqual(sorted(ta.members_of(self.CAT, spec)), [1, 4])
        self.assertTrue(spec["skip_note"])

    def test_a_type_without_skip_sets_keeps_every_set(self):
        self.assertEqual(sorted(ta.members_of(self.CAT, {"rarity": "Starlight Rare"})), [1, 2, 3, 4])
        self.assertEqual(sorted(ta.members_of(self.CAT, ga.CARD_TYPES["yugioh"]["ghost"])), [5])


class SteadyPath(unittest.TestCase):
    FYENDAL = [("2025-11-24", 238.17), ("2026-08-31", 199.87), ("2026-09-28", 190.31),
               ("2026-10-31", 1593.99), ("2026-11-27", 1593.99)]

    def test_a_printing_switch_is_a_step_not_a_move(self):
        self.assertFalse(ta.steady_path(self.FYENDAL))
        self.assertFalse(ta.steady_path(self.FYENDAL, "2025-11-27"))
        self.assertTrue(ta.steady_path(self.FYENDAL, "2026-10-01"))      # nothing after the step

    def test_an_ordinary_path_is_steady(self):
        self.assertTrue(ta.steady_path([("2025-01-01", 10.0), ("2025-02-01", 25.0), ("2025-03-01", 9.0)]))
        self.assertTrue(ta.steady_path([]))
        self.assertTrue(ta.steady_path([("2025-01-01", 0.0), ("2025-02-01", 5.0)]))   # no price, no ratio


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


class Writer(unittest.TestCase):
    """The nightly's call to the writer: its own effort and a limit above the
    slowest call seen, whatever the interactive Claude settings say."""

    def setUp(self):
        self.env = {k: os.environ.pop(k, None) for k in ("TCG_PROSE_TIMEOUT", "TCG_PROSE_EFFORT")}
        self.run, self.find = ae.subprocess.run, ae._find_cli
        ae._find_cli = lambda: "claude-stand-in"

    def tearDown(self):
        ae.subprocess.run, ae._find_cli = self.run, self.find
        for k, v in self.env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def call(self, reply=None, raises=None):
        seen = {}

        def fake(cmd, input=None, **kw):
            seen.update(cmd=cmd, timeout=kw.get("timeout"), prompt=input)
            if raises:
                raise raises
            return types.SimpleNamespace(returncode=0, stdout=reply or "{}", stderr="")
        ae.subprocess.run = fake
        out = ae._write("set", {"game": "Magic"}, [("movers", "the week's movers")], "the set")
        return seen, out

    def test_the_effort_is_named_and_the_limit_is_900_seconds(self):
        seen, _ = self.call()
        cmd = seen["cmd"]
        self.assertEqual(cmd[:2], ["claude-stand-in", "-p"])
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-sonnet-5")
        self.assertEqual(seen["timeout"], 900)

    def test_both_can_be_set_for_one_run(self):
        os.environ["TCG_PROSE_TIMEOUT"], os.environ["TCG_PROSE_EFFORT"] = "1200", "medium"
        seen, _ = self.call()
        self.assertEqual(seen["cmd"][seen["cmd"].index("--effort") + 1], "medium")
        self.assertEqual(seen["timeout"], 1200)

    def test_the_reports_use_the_same_call(self):
        import report_editorial as re_
        self.assertEqual(re_.writer_cmd("x"), ae.writer_cmd("x"))
        self.assertIs(ae.writer_timeout, re_.writer_timeout)

    def test_a_section_a_few_words_over_its_limit_is_kept(self):
        import contextlib
        import io
        import json
        words = lambda n: " ".join(["word"] * (n - 1)) + " end."
        ae.subprocess.run = lambda cmd, input=None, **kw: types.SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps({
                "lede": words(ae.LIMITS["lede"] + ae.OVER),
                "sections": {"a": words(ae.SECTION_LIMIT + 3), "b": words(ae.SECTION_LIMIT + ae.OVER),
                             "c": words(ae.SECTION_LIMIT + ae.OVER + 1)},
                "headers": {"a": words(ae.HEADER_LIMIT + 1)}}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            out = ae._write("set", {"game": "Magic"}, [("a", "x"), ("b", "x"), ("c", "x")], "the set")
        self.assertEqual(sorted(out["sections"]), ["a", "b"])
        self.assertTrue(out["lede"])
        self.assertEqual(out["headers"], {})              # a header gets no extra words
        self.assertIn("section 'c' ran 106 words against 95 (+10)", err.getvalue())
        self.assertNotIn("section 'a'", err.getvalue())

    def test_a_writer_that_runs_out_of_time_leaves_the_templated_text(self):
        import contextlib
        import io
        import subprocess
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _, out = self.call(raises=subprocess.TimeoutExpired("claude", 900))
        self.assertIsNone(out)
        self.assertIn("keeping templated text", err.getvalue())


if __name__ == "__main__":
    unittest.main()
