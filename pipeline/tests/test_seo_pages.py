"""What a search engine reads off the generated pages (2026-11-24)."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import prerender_pages as pp
import report_pages as rp
import set_pages as sp

LAW = (415805, "Trafalgar Law", "ST-02: Starter Deck 2", "Super Rare", "ST02-009", None, 16.68)


def blocks(page_html):
    return [json.loads(m) for m in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', page_html, re.S)]


class CardPage(unittest.TestCase):
    def test_title_names_the_number_and_the_set(self):
        self.assertEqual(pp.card_title("Trafalgar Law", "ST02-009", "ST-02: Starter Deck 2"),
                         "Trafalgar Law ST02-009 Price & Forecast - ST-02: Starter Deck 2")
        # no number (Sorcery), and a name that already carries it
        self.assertEqual(pp.card_title("Miracle Workers", None, "Beta"),
                         "Miracle Workers Price & Forecast - Beta")
        self.assertEqual(pp.card_title("Resource (RP-030)", "RP-030", "Promo"),
                         "Resource (RP-030) Price & Forecast - Promo")

    def test_worth_answer_uses_the_page_numbers(self):
        text = pp.worth_block("Trafalgar Law", 16.68, {"psa10": 120.0},
                              [("1m", 17.21, 14.0, 19.0), ("12m", 16.94, 14.0, 29.0)],
                              "November 24, 2026")
        self.assertIn("What is Trafalgar Law worth?", text)
        self.assertIn("about $16.68 as of November 24, 2026", text)
        self.assertIn("A PSA 10 goes for about $120.00.", text)
        self.assertIn("$17.21 a month from now (+3.2%)", text)
        self.assertIn("$16.94 in a year (+1.6%)", text)
        self.assertEqual(pp.worth_block("X", None, None, [], "today"), "")

    def test_product_block_spans_the_prices_shown(self):
        ld = pp.card_ld("onepiece", "One Piece", LAW, {"grade9": 40.0, "psa10": 120.0}, "d")
        self.assertEqual(ld["@type"], "Product")
        self.assertEqual(ld["name"], "Trafalgar Law (ST02-009)")
        self.assertEqual(ld["offers"], {"@type": "AggregateOffer", "priceCurrency": "USD",
                                        "lowPrice": "16.68", "highPrice": "120.00"})

    def test_page_carries_valid_blocks_and_one_site_name(self):
        out = pp.page("/x", 'A </script> "card"', "d", "<p>b</p>",
                      ld=[{"@type": "Thing", "name": "</script><b>"}])
        self.assertEqual(blocks(out)[0]["name"], "</script><b>")
        self.assertEqual(out.count("| CardStock</title>"), 1)
        home = pp.page("/", "CardStock: prices", "d", "b", bare_title=True)
        self.assertIn("<title>CardStock: prices</title>", home)
        self.assertIn('property="og:image"', home)          # every page has a share picture

    def test_set_slug_matches_the_set_page_generator(self):
        for name in ("ST-02: Starter Deck 2 (Super Pre-Release)", "Royal Blood", "Évolutions"):
            self.assertEqual(pp.slugify(name), sp.slugify(name))


class ReportPage(unittest.TestCase):
    def test_report_title_says_what_it_is_and_the_headline_stays(self):
        out = rp.page("weekly-market-report-2026-11-20", "Decliners outnumbered gainers",
                      "2026-11-20", "summary", "<p>body</p>")
        self.assertIn("<title>Trading Card Market Report, Nov 20, 2026: "
                      "Decliners outnumbered gainers | CardStock</title>", out)
        self.assertIn('<meta property="og:title" content="Decliners outnumbered gainers">', out)
        types = [b["@type"] for b in blocks(out)]
        self.assertEqual(types, ["NewsArticle", "BreadcrumbList"])
        self.assertTrue(blocks(out)[0]["image"])

    def test_article_title_is_its_headline(self):
        out = rp.page("set-report-onepiece-2026-11-10", "Heroines Is Up", "2026-11-10",
                      "summary", "<p>body</p>", base="articles", section="Set report")
        self.assertIn("<title>Heroines Is Up | CardStock</title>", out)


if __name__ == "__main__":
    unittest.main()
