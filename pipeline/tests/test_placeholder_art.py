"""
TCGplayer's "Image Coming Soon" picture never becomes card art: the check in
placeholder_art.py, every downloader and upload step that uses it, and the
one-off purge (which must touch exactly the verified objects).

Run from pipeline/:  ../../one-piece/.venv/bin/python -m unittest discover -s tests
Nothing here reaches the network, AWS or the real data dir: HTTP and the aws
CLI are mocked, TCG_DATA_DIR points at a temp dir, and every path is temp.
"""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_DATA = tempfile.TemporaryDirectory(prefix="placeholder-test-")
os.environ["TCG_DATA_DIR"] = _DATA.name        # read once, by _paths, on first import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

import placeholder_art
import purge_placeholder_art as purge
import recheck_prerelease_art
import s3_upload_images
import tcg_onepiece_scraper
import tcg_pokemon_scraper
import tcg_scraper


def tearDownModule():
    _DATA.cleanup()


def jpeg(mode, size, colour=None):
    buf = io.BytesIO()
    if colour is None:
        colour = 205 if mode == "L" else (180, 40, 40)
    Image.new(mode, size, colour).save(buf, "JPEG", quality=80)
    return buf.getvalue()


UNSEEN = jpeg("L", (1000, 573))       # a placeholder encoding no md5 list knows
UNSEEN_SMALL = jpeg("L", (400, 229))  # the 400px rendition
CARD = jpeg("RGB", (716, 1000))
BW_CARD = jpeg("L", (700, 1000))      # a grayscale scan, but portrait
WIDE = jpeg("RGB", (1000, 573))       # wide, but colour
LANDSCAPE_CARD = jpeg("RGB", (1000, 716))
SMALL_CARD = jpeg("RGB", (300, 438), (30, 90, 160))   # real art of a placeholder's size
LISTED = b"\xff\xd8 bytes whose md5 is on the list"
LISTED_MD5S = placeholder_art.PLACEHOLDER_MD5S | {hashlib.md5(LISTED).hexdigest()}


class FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)


def read(path):
    with open(path, "rb") as f:
        return f.read()


class PlaceholderCheck(unittest.TestCase):
    def test_a_listed_md5_matches_whatever_the_bytes_look_like(self):
        with mock.patch.object(placeholder_art, "PLACEHOLDER_MD5S", LISTED_MD5S):
            self.assertTrue(placeholder_art.is_placeholder(LISTED))
        self.assertFalse(placeholder_art.is_placeholder(LISTED))

    def test_an_unseen_encoding_is_caught_by_its_shape(self):
        self.assertTrue(placeholder_art.is_placeholder(UNSEEN))
        self.assertTrue(placeholder_art.is_placeholder(UNSEEN_SMALL))

    def test_card_art_is_never_the_placeholder(self):
        for body in (CARD, BW_CARD, WIDE, LANDSCAPE_CARD, b"", b"not an image"):
            self.assertFalse(placeholder_art.is_placeholder(body))

    def test_files(self):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/1.jpg", UNSEEN)
            write(f"{d}/2.jpg", CARD)
            self.assertTrue(placeholder_art.is_placeholder_file(f"{d}/1.jpg"))
            self.assertFalse(placeholder_art.is_placeholder_file(f"{d}/2.jpg"))
            self.assertFalse(placeholder_art.is_placeholder_file(f"{d}/missing.jpg"))


class DownloadImage(unittest.TestCase):
    """Both copies of download_image (tcg_scraper and audit_imageless use the
    Pokémon one)."""
    SCRAPERS = (tcg_pokemon_scraper, tcg_onepiece_scraper)

    def run_download(self, scraper, response, on_disk=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "123.jpg")
            if on_disk is not None:
                write(path, on_disk)
            with mock.patch.object(scraper, "request_with_retries",
                                   return_value=response) as http:
                got = scraper.download_image(None, 123, 0, d)
            return got, (read(path) if os.path.exists(path) else None), http, path

    def test_placeholder_body_is_not_written(self):
        for scraper in self.SCRAPERS:
            with self.subTest(scraper.__name__):
                got, saved, http, _ = self.run_download(scraper, FakeResponse(200, UNSEEN))
                self.assertIsNone(got)
                self.assertIsNone(saved)
                with mock.patch.object(placeholder_art, "PLACEHOLDER_MD5S", LISTED_MD5S):
                    got, saved, _, _ = self.run_download(scraper, FakeResponse(200, LISTED))
                self.assertIsNone(got)
                self.assertIsNone(saved)

    def test_real_art_is_written(self):
        for scraper in self.SCRAPERS:
            with self.subTest(scraper.__name__):
                got, saved, _, path = self.run_download(scraper, FakeResponse(200, CARD))
                self.assertEqual(got, path)
                self.assertEqual(saved, CARD)

    def test_placeholder_on_disk_is_downloaded_again(self):
        for scraper in self.SCRAPERS:
            with self.subTest(scraper.__name__):
                got, saved, http, path = self.run_download(
                    scraper, FakeResponse(200, CARD), on_disk=UNSEEN)
                http.assert_called_once()
                self.assertEqual(got, path)
                self.assertEqual(saved, CARD)
                # TCGplayer still has no photo: the old file goes, nothing replaces it.
                got, saved, http, _ = self.run_download(
                    scraper, FakeResponse(200, UNSEEN_SMALL), on_disk=UNSEEN)
                http.assert_called_once()
                self.assertIsNone(got)
                self.assertIsNone(saved)

    def test_art_on_disk_is_kept_without_a_request(self):
        for scraper in self.SCRAPERS:
            with self.subTest(scraper.__name__):
                got, saved, http, path = self.run_download(
                    scraper, FakeResponse(200, UNSEEN), on_disk=CARD)
                http.assert_not_called()
                self.assertEqual(got, path)
                self.assertEqual(saved, CARD)

    def test_no_cdn_image(self):
        for scraper in self.SCRAPERS:
            with self.subTest(scraper.__name__):
                got, saved, _, _ = self.run_download(scraper, FakeResponse(403))
                self.assertIsNone(got)
                self.assertIsNone(saved)


PRODUCT = {"productId": 123, "productName": "Test Card", "setName": "Test Set",
           "setUrlName": "test-set", "productUrlName": "test-card", "rarityName": "Common",
           "customAttributes": {"number": "1/100"}}


class CallersRefetchAStagedPlaceholder(unittest.TestCase):
    """Each scraper skips download_image when a file is already staged; a
    staged placeholder must not count as one."""

    def dedicated_main_downloads(self, scraper, staged):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/img/123.jpg", staged)
            argv = ["scraper", "--db", f"{d}/cards.db", "--image-dir", f"{d}/img",
                    "--no-history", "--delay", "0", "--rpm", "6000"]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(scraper, "RATE_LIMITER", None), \
                    mock.patch.object(scraper, "make_session", return_value="session"), \
                    mock.patch.object(scraper, "iter_all_products", return_value=[dict(PRODUCT)]), \
                    mock.patch.object(scraper, "download_image", return_value=None) as dl, \
                    contextlib.redirect_stdout(io.StringIO()):
                scraper.main()
            return dl

    def test_dedicated_scrapers(self):
        for scraper in (tcg_pokemon_scraper, tcg_onepiece_scraper):
            with self.subTest(scraper.__name__):
                dl = self.dedicated_main_downloads(scraper, UNSEEN)
                self.assertEqual(dl.call_count, 1)
                self.assertEqual(dl.call_args.args[1], 123)
                self.dedicated_main_downloads(scraper, CARD).assert_not_called()

    def generic_downloads(self, staged):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/img/123.jpg", staged)
            conn = sqlite3.connect(":memory:")
            tcg_scraper.init_db(conn)
            with mock.patch.object(tcg_scraper, "fetch_set_list", return_value=[("Test Set", 1)]), \
                    mock.patch.object(tcg_scraper, "_iter_query_pages",
                                      side_effect=lambda *a: iter([dict(PRODUCT)])), \
                    mock.patch.object(tcg_scraper.tp, "download_image", return_value=None) as dl, \
                    contextlib.redirect_stdout(io.StringIO()):
                tcg_scraper.process_sets("session", "test-line", 0, conn, f"{d}/img",
                                         new_only=False)
            conn.close()
            return dl

    def test_generic_scraper(self):
        dl = self.generic_downloads(UNSEEN)
        self.assertEqual(dl.call_count, 1)
        self.assertEqual(dl.call_args.args[1], 123)
        self.generic_downloads(CARD).assert_not_called()


class UploadAndRecheck(unittest.TestCase):
    def test_upload_drops_a_staged_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/1.jpg", UNSEEN)
            write(f"{d}/2.jpg", CARD)
            ok = subprocess.CompletedProcess([], 0)
            with mock.patch.object(s3_upload_images, "image_dir", return_value=d), \
                    mock.patch.object(s3_upload_images.subprocess, "run", return_value=ok) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(s3_upload_images.upload("onepiece"))
            self.assertEqual(sorted(os.listdir(d)), ["2.jpg"])
            run.assert_called_once()

    def test_recheck_never_takes_the_placeholder_as_new_art(self):
        with mock.patch.object(recheck_prerelease_art.tp, "request_with_retries",
                               return_value=FakeResponse(200, UNSEEN)):
            self.assertIsNone(recheck_prerelease_art.fetch_fresh(None, 1, 0))
        with mock.patch.object(recheck_prerelease_art.tp, "request_with_retries",
                               return_value=FakeResponse(200, CARD)):
            self.assertEqual(recheck_prerelease_art.fetch_fresh(None, 1, 0), CARD)


class FakeAws:
    """The aws CLI calls purge_placeholder_art makes, against a dict bucket.
    Any other call fails the test (no recursive delete, no sync, ever)."""

    def __init__(self, objects, account="522029196375"):
        self.objects = dict(objects)
        self.account = account
        self.calls, self.deleted, self.invalidations = [], [], []

    def __call__(self, *args):
        self.calls.append(args)
        done = lambda out="", code=0: subprocess.CompletedProcess(args, code, out, "")
        if args[:2] == ("sts", "get-caller-identity"):
            return done(self.account + "\n")
        if args[:2] == ("s3api", "list-objects-v2"):
            prefix = args[args.index("--prefix") + 1]
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            start = int(args[args.index("--starting-token") + 1]) if "--starting-token" in args else 0
            page = keys[start:start + 1]                    # one per page: pagination is exercised
            items = [[k, f'"{hashlib.md5(self.objects[k]).hexdigest()}"', len(self.objects[k]),
                      "2026-07-17T02:40:09+00:00"] for k in page]
            more = str(start + 1) if start + 1 < len(keys) else None
            return done(json.dumps({"Items": items or None, "NextToken": more}))
        if args[:2] == ("s3api", "head-object"):
            key = args[args.index("--key") + 1]
            if key not in self.objects:
                return done(code=254)
            body = self.objects[key]
            return done(json.dumps([f'"{hashlib.md5(body).hexdigest()}"', len(body),
                                    "2026-07-17T02:40:09+00:00"]))
        if args[:2] == ("s3api", "get-object"):
            key = args[args.index("--key") + 1]
            if key not in self.objects:
                return done(code=254)
            write(args[args.index("--key") + 2], self.objects[key])
            return done("{}")
        if args[:2] == ("s3api", "delete-object"):
            key = args[args.index("--key") + 1]
            self.deleted.append(key)
            self.objects.pop(key, None)
            return done()
        if args[:2] == ("cloudfront", "create-invalidation"):
            assert args[args.index("--distribution-id") + 1] == purge.DISTRIBUTION
            self.invalidations.append(json.loads(args[args.index("--invalidation-batch") + 1]))
            return done("{}")
        raise AssertionError(f"unexpected aws call: {args}")


class Purge(unittest.TestCase):
    """A small world: onepiece/1 is a listed encoding (found by ETag),
    onepiece/3 is real art that happens to have a placeholder's size,
    digimon/7 is an encoding nobody listed, found only because its CLIP row
    equals onepiece/1's, and digimon/9 has such a row but no object."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bucket = FakeAws({
            "onepiece/1.jpg": LISTED, "onepiece/2.jpg": WIDE, "onepiece/3.jpg": SMALL_CARD,
            "digimon/7.jpg": UNSEEN, "digimon/8.jpg": LANDSCAPE_CARD})
        sizes = [len(b) for k, b in self.bucket.objects.items() if k != "onepiece/3.jpg"]
        assert len(SMALL_CARD) not in sizes, "fixture sizes must differ"
        self.ml = f"{t}/ml_data"
        os.makedirs(self.ml)
        eye = np.eye(8, dtype=np.float32)
        np.savez(f"{self.ml}/onepiece_img_emb.npz", product_id=np.array([1, 2, 3]),
                 emb=eye[[0, 1, 2]])
        np.savez(f"{self.ml}/digimon_img_emb.npz", product_id=np.array([7, 8, 9]),
                 emb=eye[[0, 3, 0]] * 2)                # unnormalised on purpose
        for g, rows in (("onepiece", [(1, "Jinbe", 0.1), (2, "Nami", 0.5), (3, "Killer", 0.2)]),
                        ("digimon", [(7, "Growlmon", 3.6), (8, "Agumon", 1.0)])):
            con = sqlite3.connect(f"{t}/{g}.db")
            con.execute("CREATE TABLE cards (product_id INTEGER PRIMARY KEY, name TEXT, "
                        "set_name TEXT, near_mint_price REAL, image_path TEXT)")
            con.executemany("INSERT INTO cards VALUES (?,?,'Set',?,'x.jpg')", rows)
            con.commit()
            con.close()
        write(f"{t}/images_onepiece/1.jpg", LISTED)     # a staged copy of the placeholder
        write(f"{t}/images_digimon/7.jpg", CARD)        # real art landed since: keep it
        self.art_sync = mock.Mock()
        self.patches = [
            mock.patch.object(purge, "aws", self.bucket),
            mock.patch.object(purge, "ML_DATA", self.ml),
            mock.patch.object(purge, "db_path", lambda g: f"{t}/{g}.db"),
            mock.patch.object(purge, "image_dir", lambda g: f"{t}/images_{g}"),
            mock.patch.object(purge, "art_sync", self.art_sync),
            mock.patch.object(purge, "refresh_running", return_value=False),
            mock.patch.object(purge, "PLACEHOLDER_MD5S", LISTED_MD5S),
            mock.patch.object(purge, "PLACEHOLDER_SIZES", frozenset({len(SMALL_CARD)})),
            mock.patch.object(placeholder_art, "PLACEHOLDER_MD5S", LISTED_MD5S),
            mock.patch.dict(os.environ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_main(self, *extra):
        argv = ["purge", "--games", "onepiece,digimon", "--work-dir", f"{self.tmp.name}/work",
                *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            purge.main()
        return out.getvalue()

    def npz_ids(self, game):
        with np.load(f"{self.ml}/{game}_img_emb.npz") as z:
            return z["product_id"].tolist(), z["emb"].shape

    def test_wrong_account_stops_before_any_other_call(self):
        self.bucket.account = "870397520032"
        os.environ["AWS_PROFILE"] = "client"
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertEqual(os.environ["AWS_PROFILE"], "default")
        self.assertEqual([c[:2] for c in self.bucket.calls], [("sts", "get-caller-identity")])

    def test_apply_refuses_while_the_nightly_runs(self):
        with mock.patch.object(purge, "refresh_running", return_value=True):
            with self.assertRaises(SystemExit):
                self.run_main("--apply")
        self.assertEqual(self.bucket.calls, [])

    def test_dry_run_changes_nothing(self):
        out = self.run_main()
        with open(f"{self.tmp.name}/work/plan.json") as f:
            saved = json.load(f)
        plan = saved["plan"]
        self.assertEqual([it["key"] for it in plan["onepiece"]], ["onepiece/1.jpg"])
        self.assertEqual([it["key"] for it in plan["digimon"]], ["digimon/7.jpg"])
        self.assertEqual(plan["onepiece"][0]["found"], ["etag"])
        self.assertEqual(plan["digimon"][0]["found"], ["clip 1.0"])
        self.assertIn("placeholder shape", plan["digimon"][0]["verified"])
        self.assertEqual(plan["onepiece"][0]["staged"], "remove")
        self.assertEqual(plan["digimon"][0]["staged"], "keep (real art staged)")
        self.assertEqual([(r[0], r[2][:9]) for r in saved["rejected"]],
                         [("onepiece/3.jpg", "real art:"), ("digimon/9.jpg", "no object")])
        self.assertIn("dry run", out)
        self.assertEqual(self.bucket.deleted, [])
        self.assertEqual(self.bucket.invalidations, [])
        self.assertEqual(self.npz_ids("onepiece")[0], [1, 2, 3])
        self.assertTrue(os.path.exists(f"{self.tmp.name}/images_onepiece/1.jpg"))
        self.art_sync.assert_not_called()
        for call in self.bucket.calls:
            self.assertIn(call[:2], {("sts", "get-caller-identity"), ("s3api", "list-objects-v2"),
                                     ("s3api", "head-object"), ("s3api", "get-object")})

    def test_apply_touches_exactly_the_verified_objects(self):
        self.run_main("--apply")
        self.assertEqual(self.bucket.deleted, ["onepiece/1.jpg", "digimon/7.jpg"])
        self.assertEqual(len(self.bucket.invalidations), 1)
        self.assertEqual(self.bucket.invalidations[0]["Paths"]["Items"],
                         ["/onepiece/1.jpg", "/digimon/7.jpg"])
        self.assertEqual(self.npz_ids("onepiece"), ([2, 3], (2, 8)))
        self.assertEqual(self.npz_ids("digimon"), ([8, 9], (2, 8)))
        self.assertFalse(os.path.exists(f"{self.tmp.name}/images_onepiece/1.jpg"))
        self.assertTrue(os.path.exists(f"{self.tmp.name}/images_digimon/7.jpg"))
        self.assertEqual([c.args[0] for c in self.art_sync.call_args_list], ["onepiece", "digimon"])
        self.assertEqual(sorted(self.bucket.objects),
                         ["digimon/8.jpg", "onepiece/2.jpg", "onepiece/3.jpg"])
        for call in self.bucket.calls:
            self.assertNotIn("--recursive", call)
            self.assertNotIn("rm", call)
            self.assertNotIn("sync", call)


if __name__ == "__main__":
    unittest.main()
