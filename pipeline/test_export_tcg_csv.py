#!/usr/bin/env python3
"""
Tests for export_tcg_csv.py that need no browser and no network: the retry
pass after the first pass, the run summary, the dead-session stop, the
--non-fatal exit, the bounded failure capture, and the click and timeouts
in export_game. Every Playwright object is a fake and the data dir points
at a temp dir, so nothing here can reach the Seller Portal or write into
the real one-piece/ tree.

Run (from the repo root):
    ../one-piece/.venv/bin/python pipeline/test_export_tcg_csv.py -v
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

# The data dir is read once, when _paths is imported: point it at a temp dir
# first, and refuse to run if the module still resolved the real one.
_TMP = tempfile.mkdtemp(prefix="export_tcg_csv_test_")
os.environ["TCG_DATA_DIR"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_tcg_csv as x  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

if os.path.realpath(x.BASE) != os.path.realpath(_TMP):
    raise RuntimeError(f"export_tcg_csv resolved its data dir to {x.BASE}, not the test's temp dir")

BATCH = "2026-11-13"
GAMES = ["pokemon", "yugioh", "magic"]
# A click timeout like 2026-11-12/13's, with the call log Playwright 1.64
# writes for a click left waiting on the form's navigation (that night's log
# kept only its first 300 characters).
CLICK_TIMEOUT = (
    "Locator.click: Timeout 60000ms exceeded.\nCall log:\n"
    "  - waiting for locator(\"#csv-export-dialog-categories\").locator(\"xpath=ancestor::*"
    "[@role='dialog' or contains(@class,'dialog') or contains(@class,'modal')][1]\")"
    ".locator(\"input[type=submit][value='Export Filtered CSV']\").first\n"
    "    - locator resolved to <input type=\"submit\" value=\"Export Filtered CSV\"/>\n"
    "  - attempting click action\n"
    "    - performing click action\n"
    "    - click action done\n"
    "    - waiting for scheduled navigations to finish\n")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


# ----------------------------------------------------------------------------
# The run loop (mode_export) with export_game replaced
# ----------------------------------------------------------------------------

class LoopPage:
    def __init__(self):
        self.default_timeouts = []

    def set_default_timeout(self, ms):
        self.default_timeouts.append(ms)


class LoopContext:
    def __init__(self):
        self.pages = [LoopPage()]
        self.closed = False

    def close(self):
        self.closed = True


class Script:
    """export_game stand-in. plan[game] lists what each call does, in order:
    'ok' returns a path, 'fail' raises FlowError, 'slow' raises the click
    TimeoutError of 2026-11-12, 'login' raises SystemExit(2) like the
    portal's login page. Calls past the plan succeed."""

    def __init__(self, plan):
        self.plan = {g: list(steps) for g, steps in plan.items()}
        self.calls = []

    def __call__(self, page, game, today):
        self.calls.append(game)
        steps = self.plan.get(game)
        step = steps.pop(0) if steps else "ok"
        if step == "fail":
            raise x.FlowError(f"no product line matched {x.PORTAL_LINES[game]}")
        if step == "slow":
            raise PlaywrightTimeout(CLICK_TIMEOUT)
        if step == "login":
            raise SystemExit(2)
        return os.path.join(x.INCOMING_DIR, f"{game}_{today}.csv")


@contextlib.contextmanager
def faked_browser(script, signed_in="profile"):
    """launch, the sign-in check, export_game and the failure capture
    replaced, and an empty export dir of its own (a file already there would
    be skipped as exported today); yields (context, captured stdout)."""
    ctx, out = LoopContext(), io.StringIO()
    exports = tempfile.mkdtemp(dir=_TMP)
    with mock.patch.object(x, "EXPORT_DIR", exports), \
            mock.patch.object(x, "INCOMING_DIR", os.path.join(exports, "incoming")), \
            mock.patch.object(x, "launch", return_value=ctx), \
            mock.patch.object(x, "ensure_signed_in", return_value=signed_in), \
            mock.patch.object(x, "export_game", side_effect=script), \
            mock.patch.object(x, "save_debug", return_value=(
                os.path.join(_TMP, "absent.png"), os.path.join(_TMP, "absent.json"))), \
            mock.patch.dict(os.environ, {"TCG_BATCH_DATE": BATCH}), \
            contextlib.redirect_stdout(out):
        yield ctx, out


def run_export(plan, games=GAMES):
    script = Script(plan)
    with faked_browser(script) as (ctx, out):
        rc = x.mode_export(None, games, headless=True)
    return rc, script.calls, out.getvalue(), ctx


def summary(out):
    """The 'done: ...' line without its timestamp."""
    line = [ln for ln in out.splitlines() if "] done: " in ln][-1]
    return line.split("] ", 1)[1]


class RetryPass(unittest.TestCase):
    def test_all_exported_no_retry(self):
        rc, calls, out, ctx = run_export({})
        self.assertEqual(rc, 0)
        self.assertEqual(calls, GAMES)
        self.assertNotIn("retrying", out)
        self.assertEqual(summary(out), "done: 3 exported, 0 failed")
        self.assertTrue(ctx.closed)

    def test_fail_once_then_succeed(self):
        rc, calls, out, _ = run_export({"yugioh": ["slow", "ok"]})
        self.assertEqual(rc, 0)
        # the retry comes after the whole first pass, not straight away
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "yugioh"])
        self.assertIn("[yugioh] retrying after the first pass", out)
        self.assertEqual(summary(out),
                         "done: 3 exported, 0 failed; succeeded on the retry: yugioh")

    def test_fail_twice_stays_failed(self):
        rc, calls, out, _ = run_export({"magic": ["slow", "fail"]})
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "magic"])
        self.assertEqual(out.count("[magic] FAILED"), 2)
        self.assertEqual(summary(out), "done: 2 exported, 1 failed (magic)")

    def test_each_failed_game_retried_once_in_order(self):
        rc, calls, out, _ = run_export({"pokemon": ["fail", "fail"], "magic": ["slow", "ok"]})
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "pokemon", "magic"])
        self.assertEqual(summary(out), "done: 2 exported, 1 failed (pokemon); "
                                       "succeeded on the retry: magic")

    def test_login_failure_stops_without_retry(self):
        rc, calls, out, _ = run_export({"pokemon": ["fail"], "yugioh": ["login"]})
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh"])
        self.assertIn("[yugioh] session expired mid-run — stopping", out)
        self.assertNotIn("retrying", out)
        self.assertEqual(summary(out), "done: 0 exported, 3 failed (pokemon, yugioh, magic)")

    def test_login_failure_during_retry_ends_it(self):
        rc, calls, out, _ = run_export({"pokemon": ["fail", "login"], "yugioh": ["fail"]})
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "pokemon"])
        self.assertIn("[pokemon] session expired mid-run — stopping", out)
        self.assertNotIn("[yugioh] retrying", out)
        self.assertEqual(summary(out), "done: 1 exported, 2 failed (pokemon, yugioh)")

    def test_failure_log_keeps_the_call_log_tail(self):
        _, _, out, _ = run_export({"magic": ["slow", "ok"]})
        failed = [ln for ln in out.splitlines() if "[magic] FAILED" in ln][0]
        self.assertIn("TimeoutError: Locator.click: Timeout 60000ms exceeded.", failed)
        self.assertIn("waiting for scheduled navigations to finish", failed)

    def test_not_signed_in_up_front(self):
        script = Script({})
        with faked_browser(script, signed_in=None) as (ctx, out):
            rc = x.mode_export(None, GAMES, headless=True)
        self.assertEqual(rc, 2)
        self.assertEqual(script.calls, [])
        self.assertIn("NOT SIGNED IN", out.getvalue())
        self.assertTrue(ctx.closed)

    def test_non_fatal_exit(self):
        plan = {g: ["fail", "fail"] for g in x.ALL_GAMES}
        for argv, want in ((["--game", "all", "--non-fatal"], 0), (["--game", "all"], 1)):
            script = Script(plan)
            with faked_browser(script) as (_, out), \
                    mock.patch("playwright.sync_api.sync_playwright",
                               return_value=contextlib.nullcontext(object())), \
                    mock.patch.object(sys, "argv", ["export_tcg_csv.py", *argv]):
                rc = x.main()
            self.assertEqual(rc, want, argv)
            self.assertEqual(len(script.calls), 2 * len(x.ALL_GAMES))
            self.assertEqual(("downgraded to 0" in out.getvalue()), want == 0)


# ----------------------------------------------------------------------------
# Failure capture
# ----------------------------------------------------------------------------

class StuckFrame:
    """A frame whose page never answers: every read times out."""

    def __init__(self):
        self.timeouts = []

    def wait_for_function(self, script, arg=None, timeout=None):
        self.timeouts.append(timeout)
        raise PlaywrightTimeout(f"Frame.wait_for_function: Timeout {timeout:.0f}ms exceeded.")

    def evaluate(self, script, arg=None):
        raise AssertionError("an unbounded evaluate() in the failure capture")


class AnsweringFrame:
    def __init__(self, data):
        self.data = data
        self.timeouts = []

    def wait_for_function(self, script, arg=None, timeout=None):
        self.timeouts.append(timeout)
        return mock.Mock(json_value=mock.Mock(return_value=dict(self.data)))

    def evaluate(self, script, arg=None):
        raise AssertionError("an unbounded evaluate() in the failure capture")


class CapturePage:
    def __init__(self, main, others=()):
        self.main_frame = main
        self.frames = [main, *others]
        self.shot = None

    def screenshot(self, **kw):
        self.shot = kw
        raise PlaywrightTimeout("Page.screenshot: Timeout 15000ms exceeded.\nCall log:\n"
                                "  - taking page screenshot\n  - waiting for fonts to load...\n"
                                "  - fonts loaded\n")


class Capture(unittest.TestCase):
    def setUp(self):
        self.debug = tempfile.mkdtemp(dir=_TMP)

    def capture(self, page):
        out = io.StringIO()
        with mock.patch.object(x, "DEBUG_DIR", self.debug), contextlib.redirect_stdout(out):
            png, js = x.save_debug(page, "fail_magic")
        return png, js, out.getvalue()

    def test_stuck_page_is_skipped_not_waited_on(self):
        page = CapturePage(StuckFrame())
        png, js, out = self.capture(page)
        self.assertEqual(page.shot["timeout"], x.CAPTURE_TIMEOUT_MS)
        self.assertTrue(page.shot["full_page"])
        self.assertEqual(len(page.main_frame.timeouts), 1)
        self.assertLessEqual(page.main_frame.timeouts[0], x.CAPTURE_TIMEOUT_MS)
        self.assertIn("(screenshot skipped: TimeoutError: Page.screenshot: Timeout 15000ms", out)
        self.assertIn("(element dump skipped: TimeoutError", out)
        self.assertFalse(os.path.exists(png))
        self.assertFalse(os.path.exists(js))      # no empty half-written dump

    def test_stuck_frame_dropped_page_dump_kept(self):
        main, frame = AnsweringFrame({"url": "https://store.tcgplayer.com/admin/pricing",
                                      "elements": []}), StuckFrame()
        page = CapturePage(main, [frame])
        _, js, out = self.capture(page)
        self.assertTrue(os.path.exists(js))
        with open(js, encoding="utf-8") as f:
            self.assertNotIn("frame_dumps", f.read())
        self.assertLessEqual(frame.timeouts[0], x.CAPTURE_TIMEOUT_MS)
        self.assertNotIn("element dump skipped", out)


# ----------------------------------------------------------------------------
# export_game: the click and the timeouts around it
# ----------------------------------------------------------------------------

class Loc:
    """Any locator: present, one match, the Seller Portal's product lines."""

    def __init__(self, page):
        self.page = page
        self.first = self.last = self

    def locator(self, *a, **kw):
        return self

    def get_by_role(self, *a, **kw):
        return self

    def count(self):
        return 1

    def all_inner_texts(self):
        return ["Magic", "Pokemon", "YuGiOh"]

    def select_option(self, **kw):
        pass

    def wait_for(self, **kw):
        pass


class Button:
    def __init__(self, page, fails=None):
        self.page, self.fails, self.clicks = page, fails, []

    def click(self, **kw):
        # the page's default timeout at click time says which stretch it is in
        self.clicks.append((kw, self.page.default_timeouts[-1] if self.page.default_timeouts else None))
        if self.fails:
            raise self.fails


class Download:
    suggested_filename = "TCGplayer__Pricing_Custom_Export_20261113_120000.csv"

    def save_as(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("TCGplayer Id\n")


class DownloadWait:
    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def value(self):
        return Download()


class FlowPage:
    url = x.PRICING_URL

    def __init__(self):
        self.default_timeouts = []
        self.download_waits = []

    def set_default_timeout(self, ms):
        self.default_timeouts.append(ms)

    def locator(self, *a, **kw):
        return Loc(self)

    def get_by_role(self, *a, **kw):
        return Loc(self)

    def wait_for_timeout(self, ms):
        pass

    def expect_download(self, timeout=None):
        self.download_waits.append(timeout)
        return DownloadWait(timeout)


class ExportGame(unittest.TestCase):
    def run_game(self, go):
        page = go.page
        opener = Button(page)
        with mock.patch.object(x, "INCOMING_DIR", tempfile.mkdtemp(dir=_TMP)), \
                mock.patch.object(x, "goto"), \
                mock.patch.object(x, "set_dialog_product_line", return_value="Magic"), \
                mock.patch.object(x, "first_visible", side_effect=[opener, go]), \
                mock.patch.object(x, "validate_csv", return_value=(812_000, "Magic")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            return x.export_game(page, "magic", BATCH), out

    def test_click_waits_on_the_download_not_the_60s_default(self):
        page = FlowPage()
        go = Button(page)
        final, out = self.run_game(go)
        self.assertEqual(os.path.basename(final), f"magic_{BATCH}.csv")
        self.assertTrue(os.path.exists(final))
        (kw, default_at_click), = go.clicks
        self.assertEqual(kw, {"timeout": x.DOWNLOAD_TIMEOUT_MS, "no_wait_after": True})
        self.assertEqual(default_at_click, x.DOWNLOAD_TIMEOUT_MS)
        self.assertEqual(page.download_waits, [x.DOWNLOAD_TIMEOUT_MS])
        self.assertEqual(page.default_timeouts, [x.DOWNLOAD_TIMEOUT_MS, x.NAV_TIMEOUT_MS])
        self.assertIn("s after the request)", out.getvalue())

    def test_default_timeout_restored_when_the_click_fails(self):
        page = FlowPage()
        go = Button(page, fails=PlaywrightTimeout(CLICK_TIMEOUT))
        with self.assertRaises(PlaywrightTimeout):
            self.run_game(go)
        self.assertEqual(page.default_timeouts[-1], x.NAV_TIMEOUT_MS)


if __name__ == "__main__":
    unittest.main()
