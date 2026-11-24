"""The guide generator (guide_pages.py)."""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import guide_pages as gp


def blocks(page_html):
    return [json.loads(m) for m in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', page_html, re.S)]


class Sources(unittest.TestCase):
    def test_every_written_guide_has_its_header(self):
        names = [f for f in os.listdir(gp.SRC) if f.endswith(".html")]
        self.assertGreaterEqual(len(names), 5)
        for fn in names:
            meta, body = gp.read_source(os.path.join(gp.SRC, fn))
            self.assertTrue(meta["title"] and meta["description"], fn)
            self.assertNotIn("<h1", body, fn)            # the template writes the h1
            self.assertNotIn("adsbygoogle", body, fn)

    def test_a_file_without_a_header_is_refused(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.html")
        with open(p, "w") as f:
            f.write("<p>no header</p>")
        with self.assertRaises(ValueError):
            gp.read_source(p)


class Page(unittest.TestCase):
    OTHERS = [("faq", "Questions"), ("tcg-glossary", "Glossary")]

    def test_faq_pairs_become_faq_data(self):
        body = '<h2>Is it free?</h2>\n<p>Yes. <a href="/x">More</a>.</p><h2>Who?</h2><p>Us.</p>'
        self.assertEqual(gp.faq_pairs(body), [("Is it free?", "Yes. More."), ("Who?", "Us.")])
        out = gp.page("faq", {"title": "FAQ", "description": "d", "faq": "yes"}, body, self.OTHERS)
        faq = blocks(out)[0]
        self.assertEqual(faq["@type"], "FAQPage")
        self.assertEqual(faq["mainEntity"][0]["acceptedAnswer"]["text"], "Yes. More.")

    def test_an_article_page(self):
        out = gp.page("tcg-glossary", {"title": "Glossary", "description": "d", "h1": "Words",
                                       "date": "2026-09-30"}, "<p>b</p>", self.OTHERS)
        self.assertEqual([b["@type"] for b in blocks(out)], ["Article", "BreadcrumbList"])
        self.assertIn("<title>Glossary | CardStock Guides</title>", out)
        self.assertIn("<h1>Words</h1>", out)
        self.assertIn('href="https://cardstock.guide/guides/faq"', out)       # more guides
        self.assertNotIn('guides/tcg-glossary">Glossary</a>', out)            # not itself
        self.assertNotIn("Updated", out)                                      # a written guide

    def test_multiples_read_like_a_person_wrote_them(self):
        self.assertEqual(gp.times(2.64), "2.6x")
        self.assertEqual(gp.times(15.2), "15x")


class WrittenOnly(unittest.TestCase):
    def test_reads_no_database_and_lists_only_pages_that_exist(self):
        out = tempfile.mkdtemp()
        old = sys.argv
        sys.argv = ["guide_pages.py", "--out", out, "--written-only"]
        try:
            gp.main()
        finally:
            sys.argv = old
        made = sorted(os.listdir(out))
        self.assertIn("faq.html", made)
        self.assertIn("index.html", made)
        self.assertNotIn("psa-10-vs-raw.html", made)
        index = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertNotIn("psa-10-vs-raw", index)
        # once the nightly has rendered a numbers guide, a later written-only
        # run keeps it and lists it
        with open(os.path.join(out, "psa-10-vs-raw.html"), "w") as f:
            f.write("rendered by the nightly")
        sys.argv = ["guide_pages.py", "--out", out, "--written-only"]
        try:
            gp.main()
        finally:
            sys.argv = old
        self.assertEqual(open(os.path.join(out, "psa-10-vs-raw.html")).read(),
                         "rendered by the nightly")
        self.assertIn("psa-10-vs-raw", open(os.path.join(out, "index.html"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
