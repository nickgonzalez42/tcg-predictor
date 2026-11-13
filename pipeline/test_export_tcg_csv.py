#!/usr/bin/env python3
"""
Tests for export_tcg_csv.py that need no browser and no network: the retry
pass after the first pass, the run's patience after a long failure, the run
summary, the dead-session stop, the --non-fatal exit, the bounded failure
capture, and the click, the error-page check and the timeouts in
export_game. Every Playwright object is a fake, the clock too, and the data
dir points at a temp dir, so nothing here can reach the Seller Portal or
write into the real one-piece/ tree.

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
# the run's patience before and after a long failure, and the page default
LONG, SHORT, NAV = x.DOWNLOAD_TIMEOUT_MS, x.LONG_FAILURE_S * 1000, x.NAV_TIMEOUT_MS
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
# expect_download's timeout as Playwright 1.64 words it: an event wait logs
# under a banner, not "Call log:".
DOWNLOAD_TIMEOUT = (
    'Timeout 900000ms exceeded while waiting for event "download"\n'
    + "=" * 27 + " logs " + "=" * 27 + '\nwaiting for event "download"\n' + "=" * 60)


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


class Clock:
    """Stands in for the time module in export_tcg_csv: monotonic() moves
    only when a fake export says it took time."""

    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        return self.now


class Script:
    """export_game stand-in. plan[game] lists what each call does, in order:
    'ok' returns a path after 30 s, 'late' after 10 min, 'fail' raises
    FlowError after 5 s, 'slow' raises the click TimeoutError of 2026-11-12
    after 60 s, 'hang' waits out whatever limit the attempt got and raises
    the download wait's TimeoutError, 'login' raises SystemExit(2) like the
    portal's login page. Calls past the plan succeed. Records each call's
    game and the patience it got."""

    def __init__(self, plan):
        self.plan = {g: list(steps) for g, steps in plan.items()}
        self.calls, self.limits = [], []
        self.clock = Clock()

    def __call__(self, page, game, today, patience_ms=x.DOWNLOAD_TIMEOUT_MS):
        self.calls.append(game)
        self.limits.append(patience_ms)
        steps = self.plan.get(game)
        step = steps.pop(0) if steps else "ok"
        if step == "fail":
            self.clock.now += 5
            raise x.FlowError(f"no product line matched {x.PORTAL_LINES[game]}")
        if step == "slow":
            self.clock.now += 60
            raise PlaywrightTimeout(CLICK_TIMEOUT)
        if step == "hang":
            self.clock.now += patience_ms / 1000
            raise PlaywrightTimeout(DOWNLOAD_TIMEOUT.replace("900000", str(patience_ms)))
        if step == "login":
            raise SystemExit(2)
        self.clock.now += 600 if step == "late" else 30
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
            mock.patch.object(x, "time", script.clock), \
            mock.patch.object(x, "save_debug", return_value=(
                os.path.join(_TMP, "absent.png"), os.path.join(_TMP, "absent.json"))), \
            mock.patch.dict(os.environ, {"TCG_BATCH_DATE": BATCH}), \
            contextlib.redirect_stdout(out):
        yield ctx, out


def run_export(plan, games=GAMES):
    """mode_export over games; plan is a Script or a Script's plan."""
    script = plan if isinstance(plan, Script) else Script(plan)
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


class Patience(unittest.TestCase):
    """The 15-min limits hold until an attempt fails after LONG_FAILURE_S:
    that game gets no retry, and every later attempt gets 5 min limits."""

    def test_fast_failures_and_slow_files_keep_the_long_limits(self):
        script = Script({"pokemon": ["late"], "yugioh": ["fail", "ok"], "magic": ["slow", "ok"]})
        rc, calls, out, _ = run_export(script)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "yugioh", "magic"])
        self.assertEqual(script.limits, [LONG] * 5)
        self.assertNotIn("from here on", out)

    def test_long_failure_gets_no_retry_and_shortens_the_rest(self):
        script = Script({"pokemon": ["fail", "ok"], "yugioh": ["hang"], "magic": ["fail", "ok"]})
        rc, calls, out, _ = run_export(script)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "pokemon", "magic"])
        self.assertEqual(script.limits, [LONG, LONG, SHORT, SHORT, SHORT])
        self.assertIn("[yugioh] failed after a long wait: from here on every wait gets 5 min, "
                      "not 15 min", out)
        self.assertIn("[yugioh] no retry: its attempt failed after a long wait", out)
        self.assertEqual(summary(out), "done: 2 exported, 1 failed (yugioh); "
                                       "succeeded on the retry: pokemon, magic")

    def test_long_failure_on_the_retry_shortens_the_retries_after_it(self):
        script = Script({"pokemon": ["fail", "hang"], "yugioh": ["fail", "ok"]})
        rc, calls, out, _ = run_export(script)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["pokemon", "yugioh", "magic", "pokemon", "yugioh"])
        self.assertEqual(script.limits, [LONG, LONG, LONG, LONG, SHORT])
        self.assertEqual(summary(out), "done: 2 exported, 1 failed (pokemon); "
                                       "succeeded on the retry: yugioh")

    def test_a_portal_that_never_answers_costs_one_long_wait(self):
        # Every game's export hangs. The 15-min limits must not turn that into
        # 15-30 min a game (about 6 h): 15 min once, then 5 min a game, once.
        games = list(x.ALL_GAMES)
        script = Script({g: ["hang", "hang"] for g in games})
        start = script.clock.now
        rc, calls, out, _ = run_export(script, games)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, games)                  # no retries
        self.assertEqual(script.limits, [LONG] + [SHORT] * (len(games) - 1))
        self.assertEqual(script.clock.now - start, (LONG + SHORT * (len(games) - 1)) / 1000)
        self.assertLessEqual(script.clock.now - start, 75 * 60)
        self.assertEqual(out.count("from here on"), 1)
        self.assertEqual(out.count("no retry"), len(games))
        self.assertEqual(summary(out), f"done: 0 exported, {len(games)} failed ({', '.join(games)})")

    def test_failure_log_reads_the_download_wait(self):
        _, _, out, _ = run_export({"magic": ["hang"]})
        failed = [ln for ln in out.splitlines() if "[magic] FAILED" in ln][0]
        self.assertIn('FAILED after 900 s: TimeoutError: Timeout 900000ms exceeded while waiting '
                      'for event "download" [call log ... waiting for event "download"]', failed)
        self.assertNotIn("===", failed)


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
        return mock.Mock(json_value=mock.Mock(return_value=type(self.data)(self.data)))

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

    def test_page_title_of_a_stuck_page_is_empty_not_waited_on(self):
        frame = StuckFrame()
        self.assertEqual(x.page_title(CapturePage(frame)), "")
        self.assertEqual(frame.timeouts, [5_000])


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
    def __init__(self, page, fails=None, lands=None):
        self.page, self.fails, self.lands, self.clicks = page, fails, lands, []

    def click(self, **kw):
        # the page's default timeout at click time says which stretch it is in
        self.clicks.append((kw, self.page.default_timeouts[-1] if self.page.default_timeouts else None))
        if self.fails:
            raise self.fails
        if self.lands:                  # the export answered with this page
            self.page.url = self.lands


class Download:
    suggested_filename = "TCGplayer__Pricing_Custom_Export_20261113_120000.csv"

    def save_as(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("TCGplayer Id\n")


class DownloadWait:
    def __init__(self, timeout):
        self.timeout, self.ended_by = timeout, "still open"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Playwright's drops the wait when the block raises, else waits on it
        self.ended_by = exc
        return False

    @property
    def value(self):
        return Download()


class FlowPage:
    def __init__(self, title=""):
        self.url = x.PRICING_URL
        self.main_frame = AnsweringFrame([title])
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
        self.download_waits.append(DownloadWait(timeout))
        return self.download_waits[-1]


class ExportGame(unittest.TestCase):
    def run_game(self, go, *patience):
        page = go.page
        opener = Button(page)
        with mock.patch.object(x, "INCOMING_DIR", tempfile.mkdtemp(dir=_TMP)), \
                mock.patch.object(x, "goto"), \
                mock.patch.object(x, "set_dialog_product_line", return_value="Magic"), \
                mock.patch.object(x, "first_visible", side_effect=[opener, go]), \
                mock.patch.object(x, "validate_csv", return_value=(812_000, "Magic")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            return x.export_game(page, "magic", BATCH, *patience), out

    def test_click_waits_on_the_download_not_the_60s_default(self):
        page = FlowPage()
        go = Button(page)
        final, out = self.run_game(go)
        self.assertEqual(os.path.basename(final), f"magic_{BATCH}.csv")
        self.assertTrue(os.path.exists(final))
        (kw, default_at_click), = go.clicks
        # No no_wait_after: the click waits for the server's answer itself
        # and checks that it hit the button (and not an overlay).
        self.assertEqual(kw, {"timeout": LONG})
        self.assertEqual(default_at_click, LONG)
        self.assertEqual([w.timeout for w in page.download_waits], [LONG])
        self.assertIsNone(page.download_waits[0].ended_by)
        self.assertEqual(page.default_timeouts, [LONG, NAV])
        self.assertIn("s after the request)", out.getvalue())

    def test_short_limits_after_a_long_failure(self):
        page = FlowPage()
        go = Button(page)
        self.run_game(go, SHORT)
        (kw, default_at_click), = go.clicks
        self.assertEqual(kw, {"timeout": SHORT})
        self.assertEqual(default_at_click, SHORT)
        self.assertEqual([w.timeout for w in page.download_waits], [SHORT])

    def test_error_page_fails_at_once(self):
        # 2026-11-13 review: a 504 page held main's attempt for the whole
        # download timeout.
        page = FlowPage(title="504 Gateway Time-out")
        go = Button(page, lands=x.PRICING_URL + "/downloadexportcsv")
        with self.assertRaises(x.FlowError) as cm:
            self.run_game(go)
        self.assertIn(f"the export answered with a page, not a file: {x.PRICING_URL}"
                      "/downloadexportcsv ('504 Gateway Time-out')", str(cm.exception))
        self.assertIsInstance(page.download_waits[0].ended_by, x.FlowError)  # dropped, not waited out
        self.assertEqual(page.default_timeouts[-1], NAV)

    def test_login_page_after_the_click_is_a_dead_session(self):
        page = FlowPage()
        go = Button(page, lands="https://store.tcgplayer.com/oauth/login?returnUrl=%2Fadmin%2Fpricing")
        with self.assertRaises(SystemExit) as cm:
            self.run_game(go)
        self.assertEqual(cm.exception.code, 2)
        self.assertIsInstance(page.download_waits[0].ended_by, SystemExit)

    def test_default_timeout_restored_when_the_click_fails(self):
        page = FlowPage()
        go = Button(page, fails=PlaywrightTimeout(CLICK_TIMEOUT))
        with self.assertRaises(PlaywrightTimeout):
            self.run_game(go)
        self.assertEqual(page.default_timeouts[-1], NAV)


if __name__ == "__main__":
    unittest.main()
