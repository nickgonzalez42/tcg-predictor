#!/usr/bin/env python3
"""
Seller Portal CSV export bot — pulls TCGplayer's pricing export for every
tracked game, logged in as the user, so the nightly can price from the CSV
instead of the 3-hour search crawl (plan of 2026-10-17: CSV most nights, the
crawl once a week for depth + cross-check).

How it works
  Playwright drives the INSTALLED Google Chrome (channel="chrome") with its
  own persistent profile under one-piece/ — never the user's real Chrome
  profile, which Chrome locks while open and which carries every other
  login. The user signs in ONCE in that profile (--login opens a visible
  window); the session then persists across runs like any browser left
  signed in. When it eventually expires the export lands on the login page,
  the run exits 2 with a clear message, and run_daily_refresh falls back to
  the search crawl for that night. Nothing here ever sees the password.

Modes
  --login             visible window on the Seller Portal; sign in by hand
                      (2FA included). Saves the signed-in state, restarts
                      headless and proves the restore works before exiting.
  --status            session health check: signed in (and how) + cookie
                      expiry dates. Exit 0 signed in, 2 not.
  --inspect URL       load URL in the bot profile and write a screenshot plus
                      a JSON dump of every interactive element (buttons,
                      links, selects + their options, inputs) to the debug
                      dir — how the export flow's selectors get discovered
                      and repaired without anyone watching the screen.
  --game all|<game>   export each game's pricing CSV into
                      one-piece/tcg_exports/incoming/<game>_<YYYY-MM-DD>.csv
                      (validated: header, product line, row floor).

Exit codes: 0 every requested game exported; 1 some game failed (the rest
are saved — the import step prices what it has, the crawl covers the rest);
2 not signed in (nothing exported; run --login); 3 the browser could not
start (profile locked by a stray window, Chrome missing).

Run:
    .venv/bin/python export_tcg_csv.py --login
    .venv/bin/python export_tcg_csv.py --game lorcana
    .venv/bin/python export_tcg_csv.py --game all
    .venv/bin/python export_tcg_csv.py --inspect https://sellerportal.tcgplayer.com/
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, ALL_GAMES

# The bot's own Chrome profile (cookies/session live here; the user signs in
# once with --login). Under one-piece/ so it is outside the public repo.
PROFILE_DIR = os.path.join(BASE, ".tcg_seller_profile")
# The signed-in cookies, saved by --login and injected by every later run if
# the profile has dropped them (Chrome's session-restore trick was not
# enough on 2026-10-17: signed in during --login, signed out after the
# restart). Plain JSON, mode 600, outside the repo — the same secret the
# profile's cookie store already holds, just not at Chrome's mercy.
STATE_FILE = os.path.join(BASE, ".tcg_seller_state.json")
EXPORT_DIR = os.path.join(BASE, "tcg_exports")
INCOMING_DIR = os.path.join(EXPORT_DIR, "incoming")   # the import step reads here
DEBUG_DIR = os.path.join(EXPORT_DIR, "debug")         # screenshots + dumps on failure

SELLER_PORTAL_URL = "https://store.tcgplayer.com/admin/"
# The Pricing page that carries the Custom Export button. The signed-in
# portal lives on the legacy admin host (sellerportal.tcgplayer.com bounces
# there after login — seen 2026-10-17). Override with TCG_PRICING_URL if
# TCGplayer moves it.
PRICING_URL = os.environ.get("TCG_PRICING_URL",
                             "https://store.tcgplayer.com/admin/pricing")

# Seller Portal product-line labels per game, EXACTLY as the pricing page's
# <select id=CategoryId> lists them (read from the page 2026-10-17; 78
# lines in all, including look-alikes such as "Pokemon Japan" — which is
# why pick_option tries an exact match before any prefix match).
PORTAL_LINES = {
    "pokemon":  ["Pokemon"],
    "onepiece": ["One Piece Card Game"],
    "yugioh":   ["YuGiOh"],
    "magic":    ["Magic"],
    "lorcana":  ["Lorcana TCG"],
    "digimon":  ["Digimon Card Game"],
    "gundam":   ["Gundam Card Game"],
    "starwars": ["Star Wars Unlimited"],
}
# Product lines that share a prefix with ours but are NOT ours (the loose
# matcher refuses any option carrying one of these words).
NOT_OURS = ("japan", "japanese", "korean", "chinese")

# A real export is one row per SKU (condition x printing), i.e. 7-9x the
# game's product count (Lorcana 3.8k products -> 33k rows, Magic 115k ->
# 812k). The floor is 3x the catalog's product count (fallback 1000): a
# partial export — one set, one condition — fails validation instead of
# silently pricing a sliver of the game.
MIN_ROWS_FALLBACK = 1000
# The dialog "Export Filtered CSV" opens (seen 2026-10-17): select2 widgets
# over hidden <select>s — categories (product line, single, DEFAULTS TO
# MAGIC regardless of the page's own dropdown), sets / conditions /
# rarities / printings (multi, default "All …"), languages (default
# English), a price filter, and its own "Export Filtered CSV" submit. Only
# the product line is changed; every other default is the whole game.
DIALOG_CATEGORY = "#csv-export-dialog-categories"
DIALOG_SETS = "#csv-export-dialog-sets"
REQUIRED_COLUMNS = ("TCGplayer Id", "Product Line", "Product Name",
                    "Condition", "TCG Market Price")

NAV_TIMEOUT_MS = 60_000
DOWNLOAD_TIMEOUT_MS = 15 * 60_000   # Magic is ~1M rows; give the server time
LOGIN_WAIT_S = 15 * 60


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ----------------------------------------------------------------------------
# Browser
# ----------------------------------------------------------------------------

def ensure_profile_prefs():
    """Chrome discards SESSION cookies at startup unless the profile restores
    its last session ("continue where you left off"). The portal's sign-in
    is session-scoped, so without this the bot was signed out on every
    restart (2026-10-17: signed in during --login, signed out five minutes
    later). Written before each launch while Chrome is closed; a no-op once
    set. Remember Me on the login form is the second, independent guard."""
    pref = os.path.join(PROFILE_DIR, "Default", "Preferences")
    os.makedirs(os.path.dirname(pref), exist_ok=True)
    try:
        with open(pref, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    session = data.setdefault("session", {})
    profile = data.setdefault("profile", {})
    if session.get("restore_on_startup") == 1 and profile.get("exit_type") == "Normal":
        return
    session["restore_on_startup"] = 1
    profile["exit_type"] = "Normal"          # no "restore pages?" bubble
    with open(pref, "w", encoding="utf-8") as f:
        json.dump(data, f)


def launch(p, headless):
    """Persistent context on the bot profile, driving installed Chrome.
    Falls back to Playwright's bundled Chromium if Chrome isn't available
    (python -m playwright install chromium)."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    ensure_profile_prefs()
    kwargs = dict(
        user_data_dir=PROFILE_DIR, headless=headless, accept_downloads=True,
        viewport={"width": 1440, "height": 900},
        # Chrome's "automation" banner/flag makes some sites behave
        # differently; a plain profile looks like the user's own browser.
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception as e:                       # noqa: BLE001
        msg = str(e)
        if "ProcessSingleton" in msg or "profile" in msg.lower() and "in use" in msg.lower():
            raise SystemExit(f"browser profile is locked (a --login window still open?): {msg[:200]}")
        log(f"installed Chrome unavailable ({type(e).__name__}); trying bundled Chromium")
        return p.chromium.launch_persistent_context(**kwargs)


def is_login_page(page):
    u = page.url.lower()
    return "/oauth/login" in u or "/logon" in u or "/login" in u


def save_state(ctx):
    ctx.storage_state(path=STATE_FILE)
    os.chmod(STATE_FILE, 0o600)
    log(f"saved sign-in state to {STATE_FILE} (mode 600)")


def restore_state(ctx):
    """Inject the saved cookies into a context whose own were lost."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            cookies = json.load(f).get("cookies") or []
    except (OSError, ValueError):
        return False
    if not cookies:
        return False
    ctx.add_cookies(cookies)
    return True


def ensure_signed_in(ctx, page):
    """Load the pricing page; if it bounces to login, restore the saved
    cookies and try once more. Returns 'profile', 'state' or None."""
    goto(page, PRICING_URL)
    if not is_login_page(page):
        return "profile"
    if restore_state(ctx):
        log("profile session gone — restoring the saved sign-in state")
        goto(page, PRICING_URL)
        if not is_login_page(page):
            save_state(ctx)       # refresh the file with any rotated cookies
            return "state"
    return None


def log_cookie_summary(ctx):
    """Names/domains/expiry of the portal's cookies (never values) — shows
    whether the sign-in is session-only or persistent (Remember Me)."""
    for c in ctx.cookies():
        if "tcgplayer" not in (c.get("domain") or ""):
            continue
        exp = c.get("expires", -1) or -1
        when = ("session" if exp < 0 else
                datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%d"))
        log(f"  cookie {c['name'][:30]:<30} {c['domain'][:26]:<26} expires {when}")


def goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    # SPA portals render after load; a short settle beats a brittle selector.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:                            # noqa: BLE001
        pass


def save_debug(page, name):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = os.path.join(DEBUG_DIR, f"{name}_{stamp}.png")
    js = os.path.join(DEBUG_DIR, f"{name}_{stamp}.json")
    try:
        page.screenshot(path=png, full_page=True)
    except Exception as e:                       # noqa: BLE001
        log(f"  (screenshot failed: {e})")
    try:
        with open(js, "w", encoding="utf-8") as f:
            json.dump(dump_elements(page), f, indent=1)
    except Exception as e:                       # noqa: BLE001
        log(f"  (element dump failed: {e})")
    return png, js


def dump_elements(page, limit=500):
    """Every interactive element on the page (and inside same-origin
    frames): tag/role/text/attrs + select options. This is what --inspect
    writes and what a failed export leaves behind for diagnosis."""
    script = """
    (limit) => {
      const sel = 'a, button, select, input, textarea, [role=button], [role=link], ' +
                  '[role=combobox], [role=listbox], [role=option], [role=menuitem], ' +
                  '[role=tab], [role=dialog], [role=checkbox], [role=radio], label';
      const out = [];
      for (const el of document.querySelectorAll(sel)) {
        if (out.length >= limit) break;
        const r = el.getBoundingClientRect();
        const item = {
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || null,
          text: (el.innerText || el.value || '').trim().slice(0, 120),
          id: el.id || null, name: el.getAttribute('name') || null,
          type: el.getAttribute('type') || null,
          aria: el.getAttribute('aria-label') || null,
          placeholder: el.getAttribute('placeholder') || null,
          href: el.getAttribute('href') || null,
          cls: (el.className && String(el.className).slice(0, 120)) || null,
          testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
          visible: !!(r.width && r.height) && getComputedStyle(el).visibility !== 'hidden',
          disabled: el.disabled || el.getAttribute('aria-disabled') === 'true' || false,
        };
        if (el.tagName === 'SELECT') {
          item.options = Array.from(el.options).slice(0, 200).map(o => o.text.trim());
        }
        out.push(item);
      }
      return { url: location.href, title: document.title,
               frames: Array.from(document.querySelectorAll('iframe')).map(f => f.src),
               elements: out };
    }"""
    data = page.evaluate(script, limit)
    for i, fr in enumerate(page.frames[1:], 1):
        try:
            sub = fr.evaluate(script, limit)
            sub["frame_index"] = i
            data.setdefault("frame_dumps", []).append(sub)
        except Exception:                        # noqa: BLE001
            pass
    return data


# ----------------------------------------------------------------------------
# Export flow
# ----------------------------------------------------------------------------

class FlowError(RuntimeError):
    pass


def first_visible(page, locators, what):
    """The first visible element among candidate locators, or FlowError."""
    for loc in locators:
        try:
            n = loc.count()
        except Exception:                        # noqa: BLE001
            continue
        for i in range(min(n, 10)):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:                    # noqa: BLE001
                continue
    raise FlowError(f"could not find {what}")


def pick_option(options, candidates):
    """Index of the option matching a game's product-line label: exact
    (normalized) first, then the shortest option starting with a candidate
    that isn't another region's line ("Pokemon Japan")."""
    nopts = [norm(o) for o in options]
    for c in candidates:
        nc = norm(c)
        if nc in nopts:
            return nopts.index(nc)
    best = None
    for c in candidates:
        nc = norm(c)
        for i, o in enumerate(nopts):
            if o.startswith(nc) and not any(w in o for w in NOT_OURS):
                if best is None or len(o) < len(nopts[best]):
                    best = i
    return best


def choose_product_line(page, scope, game):
    """Select the game's product line in the export dialog. Handles a native
    <select> or a combobox/search input with a dropdown of options."""
    cands = PORTAL_LINES[game]
    # 1) native <select> whose options include our line
    selects = scope.locator("select")
    for i in range(min(selects.count(), 10)):
        sel = selects.nth(i)
        try:
            if not sel.is_visible():
                continue
            opts = sel.locator("option").all_inner_texts()
        except Exception:                        # noqa: BLE001
            continue
        idx = pick_option(opts, cands)
        if idx is not None:
            sel.select_option(index=idx)
            log(f"  product line <select>: {opts[idx]!r}")
            return opts[idx]
    # 2) combobox / searchable dropdown: click it, type, pick the option
    box = first_visible(page, [
        scope.get_by_role("combobox"),
        scope.get_by_placeholder(re.compile(r"product line|search", re.I)),
        scope.get_by_label(re.compile(r"product line", re.I)),
        scope.locator("[class*=select], [class*=dropdown]").filter(
            has_text=re.compile(r"product line", re.I)),
    ], "the product-line selector")
    box.click()
    try:
        box.fill(cands[0])
    except Exception:                            # noqa: BLE001
        page.keyboard.type(cands[0])
    page.wait_for_timeout(800)
    options = page.get_by_role("option")
    texts = options.all_inner_texts() if options.count() else []
    if not texts:
        # non-ARIA dropdowns: any visible list item containing the label
        options = page.locator("li, [class*=option], [class*=item]").filter(
            has_text=re.compile(re.escape(cands[0]), re.I))
        texts = options.all_inner_texts() if options.count() else []
    idx = pick_option(texts, cands)
    if idx is None:
        raise FlowError(f"no product-line option matched {cands} among {texts[:20]}")
    options.nth(idx).click()
    log(f"  product line option: {texts[idx].strip()!r}")
    return texts[idx].strip()


def set_dialog_product_line(page, dialog, game):
    """Pick the game in the export dialog's product-line select2 and confirm
    the widget shows it (select2 hides the real <select>; a change on the
    hidden element normally updates the widget and reloads the dependent
    set list — if it doesn't, the widget is driven by typing instead)."""
    cat = page.locator(DIALOG_CATEGORY)
    opts = cat.locator("option").all_inner_texts()
    idx = pick_option(opts, PORTAL_LINES[game])
    if idx is None:
        raise FlowError(f"dialog has no product line matching {PORTAL_LINES[game]}: {opts[:40]}")
    label = opts[idx].strip()
    shown_loc = dialog.locator(".select2-selection--single").first

    def shown():
        try:
            return shown_loc.inner_text().strip()
        except Exception:                        # noqa: BLE001
            return ""

    cat.select_option(index=idx, force=True)
    page.wait_for_timeout(2500)                  # set list reloads for the line
    if norm(label) != norm(shown()):
        log(f"  widget shows {shown()!r} after select_option — driving the widget instead")
        shown_loc.click()
        page.wait_for_timeout(500)
        page.keyboard.type(label)
        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
    if norm(label) != norm(shown()):
        raise FlowError(f"dialog product line shows {shown()!r}, wanted {label!r}")
    log(f"  dialog product line: {shown()!r}")
    # Sets must be back on "All Set Names" (the reload resets it; verify).
    sets_shown = ""
    try:
        sets_shown = dialog.locator(".select2-selection--multiple").first.inner_text()
    except Exception:                            # noqa: BLE001
        pass
    if "all set" not in sets_shown.lower():
        log(f"  set filter shows {sets_shown.strip()[:60]!r} — resetting to All Set Names")
        page.locator(DIALOG_SETS).select_option(index=0, force=True)
        page.wait_for_timeout(1000)
    return label


def export_game(page, game, today):
    """Pricing page (store.tcgplayer.com/admin/pricing, "Manage Your
    Pricing") -> product line <select name=CategoryId> -> All Set Names ->
    "Export Filtered CSV" -> download. Seen 2026-10-17: the page is a plain
    form (search box, CategoryId + SetNameId selects, submit buttons Export
    From Live / Export From Staged / Export Filtered CSV / Import to Staged).
    If the export button opens an options dialog instead of downloading
    outright, its Export button is pressed too. Returns the saved CSV path;
    a structural change raises FlowError with a screenshot + element dump."""
    goto(page, PRICING_URL)
    if is_login_page(page):
        raise SystemExit(2)
    sel = page.locator("select#CategoryId, select[name=CategoryId]").first
    if not sel.count():
        raise FlowError("no product-line <select id=CategoryId> on the pricing page")
    opts = sel.locator("option").all_inner_texts()
    idx = pick_option(opts, PORTAL_LINES[game])
    if idx is None:
        raise FlowError(f"no product line matched {PORTAL_LINES[game]} among {opts[:40]}")
    sel.select_option(index=idx)
    line = opts[idx].strip()
    log(f"  product line: {line!r}")
    page.wait_for_timeout(1500)            # the set list reloads for the new line
    sets = page.locator("select#SetNameId, select[name=SetNameId]").first
    if sets.count():
        try:
            sets.select_option(index=0)    # "All Set Names" — the whole game
        except Exception as e:             # noqa: BLE001
            log(f"  (could not reset set filter: {e})")
    opener = first_visible(page, [
        page.locator("input[type=submit][value='Export Filtered CSV']"),
        page.get_by_role("button", name=re.compile(r"^export filtered csv$", re.I)),
    ], "the page's 'Export Filtered CSV' button")
    log("  opening the export dialog")
    opener.click()
    cat = page.locator(DIALOG_CATEGORY)
    cat.wait_for(state="attached", timeout=20_000)
    page.wait_for_timeout(1000)
    # The dialog container = nearest dialog/modal ancestor of its category
    # select (scopes the submit button to the dialog, not the page's twin).
    dialog = cat.locator("xpath=ancestor::*[@role='dialog' or contains(@class,'dialog') "
                         "or contains(@class,'modal')][1]")
    if not dialog.count():
        dialog = page
    line = set_dialog_product_line(page, dialog, game)
    go = first_visible(page, [
        dialog.locator("input[type=submit][value='Export Filtered CSV']"),
        dialog.get_by_role("button", name=re.compile(r"^export filtered csv$", re.I)),
        page.locator("input[type=submit][value='Export Filtered CSV']").last,
    ], "the dialog's 'Export Filtered CSV' button")
    log(f"  requesting export of {line!r}")
    with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
        go.click()
    download = dl_info.value
    log(f"  download started: {download.suggested_filename!r}")
    os.makedirs(INCOMING_DIR, exist_ok=True)
    tmp = os.path.join(INCOMING_DIR, f".{game}_{today}.part")
    download.save_as(tmp)
    rows, line_seen = validate_csv(tmp, game)
    final = os.path.join(INCOMING_DIR, f"{game}_{today}.csv")
    os.replace(tmp, final)
    log(f"  saved {final} ({rows:,} rows, product line {line_seen!r}; dialog pick {line!r})")
    return final


def min_rows(game):
    """3x the catalog's product count (see MIN_ROWS_FALLBACK)."""
    import sqlite3
    from games import db_path
    try:
        con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=30)
        n = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        con.close()
        return max(MIN_ROWS_FALLBACK, 3 * int(n))
    except Exception:                            # noqa: BLE001
        return MIN_ROWS_FALLBACK


def validate_csv(path, game):
    """Header, row floor, and that the file really is this game's line.
    Returns (row_count, dominant Product Line value)."""
    floor = min_rows(game)
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.reader(f)
        header = next(r, None) or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise FlowError(f"export missing columns {missing}; header={header[:16]}")
        li = header.index("Product Line")
        counts, rows = {}, 0
        for row in r:
            rows += 1
            if len(row) > li:
                counts[row[li]] = counts.get(row[li], 0) + 1
    if rows < floor:
        raise FlowError(f"export has only {rows:,} rows (floor {floor:,}) — truncated or filtered")
    dominant = max(counts, key=counts.get) if counts else ""
    if pick_option([dominant], PORTAL_LINES[game]) is None:
        raise FlowError(f"export's product line is {dominant!r}, not {PORTAL_LINES[game]} — wrong game picked")
    return rows, dominant


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------

def mode_login(p):
    ctx = launch(p, headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    goto(page, SELLER_PORTAL_URL)
    if not is_login_page(page):
        log(f"already signed in ({page.url}) — nothing to do")
        ctx.close()
        return 0
    # Tick "Remember Me" for the user: it turns the sign-in into a persistent
    # cookie, so the session survives browser restarts regardless of Chrome's
    # session-cookie policy (ensure_profile_prefs is the other guard).
    try:
        box = page.locator("input[name=RememberMe]")
        if not box.count():
            box = page.get_by_label(re.compile(r"remember me", re.I))
        if box.count() and not box.first.is_checked():
            box.first.check()
            log("ticked 'Remember Me' on the form (keeps the session across restarts)")
    except Exception as e:                       # noqa: BLE001
        log(f"(could not tick Remember Me myself: {e}) — please tick it before signing in")
    log("Sign in to the Seller Portal in the window that just opened (2FA is fine).")
    log(f"Waiting up to {LOGIN_WAIT_S // 60} min for the portal to load signed in…")
    t0 = time.time()
    while time.time() - t0 < LOGIN_WAIT_S:
        page.wait_for_timeout(2000)
        try:
            if not is_login_page(page) and "tcgplayer.com" in page.url:
                break
        except Exception:                        # noqa: BLE001
            break   # window closed
    else:
        log("timed out waiting for sign-in")
        ctx.close()
        return 2
    page.wait_for_timeout(3000)
    log(f"signed in: {page.url}")
    goto(page, PRICING_URL)
    if is_login_page(page):
        log("the pricing page still bounced to login — sign-in did not complete?")
        ctx.close()
        return 2
    png, js = save_debug(page, "login_pricing")
    log(f"pricing page after login: {page.url} — screenshot {png}")
    log_cookie_summary(ctx)
    save_state(ctx)
    # Give Chrome a moment to flush cookies to disk, then close cleanly.
    page.wait_for_timeout(5000)
    ctx.close()
    # Self-check: the whole point is surviving a restart, so prove it now
    # (headless, exactly like the nightly) instead of finding out at 8 PM.
    log("verifying the session survives a browser restart…")
    ctx2 = launch(p, headless=True)
    page2 = ctx2.pages[0] if ctx2.pages else ctx2.new_page()
    how = ensure_signed_in(ctx2, page2)
    if how:
        save_debug(page2, "login_verified_pricing")
    ctx2.close()
    if how == "profile":
        log("session persisted natively across a restart — the bot can run unattended now.")
        return 0
    if how == "state":
        log("session restored from the saved state after a restart — the bot can run unattended now.")
        return 0
    log("session did NOT survive a restart even from the saved state — tell Claude; "
        "the sign-in may be bound to something beyond cookies.")
    return 2


def mode_status(p):
    """Session health: signed in (and how), plus the portal cookies' expiry
    dates — the pre-flight the nightly can run before committing to CSVs."""
    ctx = launch(p, headless=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    how = ensure_signed_in(ctx, page)
    log(f"signed in: {how or 'NO'}")
    if how:
        log_cookie_summary(ctx)
    ctx.close()
    return 0 if how else 2


def mode_inspect(p, url, headless):
    ctx = launch(p, headless=headless)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    how = ensure_signed_in(ctx, page)
    log(f"sign-in: {how or 'NONE'}")
    goto(page, url)
    page.wait_for_timeout(2000)
    name = "inspect_" + re.sub(r"[^a-z0-9]+", "_", url.lower())[:60]
    png, js = save_debug(page, name)
    log(f"{url} -> {page.url}")
    log(f"signed in: {not is_login_page(page)}")
    log(f"screenshot: {png}")
    log(f"elements:   {js}")
    ctx.close()
    return 0


def mode_export(p, games, headless, force=False):
    # UTC day, like every other batch label in the pipeline (the 8 PM local
    # start is already the next UTC day; the crawl and the import agree).
    today = datetime.now(timezone.utc).date().isoformat()
    ctx = launch(p, headless=headless)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(NAV_TIMEOUT_MS)
    # One sign-in check up front so a dead session is one line, not 8 failures.
    how = ensure_signed_in(ctx, page)
    if not how:
        log("NOT SIGNED IN — the saved session has expired. "
            "Run: export_tcg_csv.py --login   (the refresh falls back to the crawl tonight)")
        save_debug(page, "not_signed_in")
        ctx.close()
        return 2
    log(f"signed in via {how}")
    ok, failed = [], []
    for i, game in enumerate(games):
        final = os.path.join(INCOMING_DIR, f"{game}_{today}.csv")
        archived = os.path.join(EXPORT_DIR, "archive", f"{game}_{today}.csv.gz")
        if not force and (os.path.exists(final) or os.path.exists(archived)):
            # Idempotent rerun (the refresh may resume this step): today's
            # file is already in, or already imported and archived; --force
            # re-exports it.
            where = "waiting in incoming/" if os.path.exists(final) else "imported + archived"
            log(f"[{game}] already exported today ({where}) — skipping")
            ok.append(final)
            continue
        log(f"[{game}] exporting {PORTAL_LINES[game][0]!r}")
        try:
            ok.append(export_game(page, game, today))
        except SystemExit as e:
            if e.code == 2:
                # Session died mid-run: this game and every one after it are
                # unexported; the crawl fallback covers them tonight.
                log(f"[{game}] session expired mid-run — stopping")
                failed.extend(games[i:])
                break
            raise
        except Exception as e:                   # noqa: BLE001
            png, js = save_debug(page, f"fail_{game}")
            log(f"[{game}] FAILED: {type(e).__name__}: {str(e)[:300]}")
            log(f"[{game}]   see {png}")
            failed.append(game)
            # Reset to a clean page state for the next game.
            try:
                page.keyboard.press("Escape")
            except Exception:                    # noqa: BLE001
                pass
    ctx.close()
    log(f"done: {len(ok)} exported, {len(failed)} failed{' (' + ', '.join(failed) + ')' if failed else ''}")
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login", action="store_true", help="sign in once in the bot's Chrome profile")
    ap.add_argument("--inspect", metavar="URL", help="screenshot + element dump of URL in the bot profile")
    ap.add_argument("--status", action="store_true", help="session health check (exit 0 signed in, 2 not)")
    ap.add_argument("--game", help="all or a game name from games.py")
    ap.add_argument("--headful", action="store_true", help="show the browser (export/inspect modes)")
    ap.add_argument("--force", action="store_true", help="re-export games already exported today")
    ap.add_argument("--non-fatal", action="store_true",
                    help="always exit 0 (refresh step: a failed export falls back to the crawl)")
    a = ap.parse_args()
    if not (a.login or a.inspect or a.game or a.status):
        ap.error("one of --login, --status, --inspect URL, --game is required")
    unknown = [g for g in ALL_GAMES if g not in PORTAL_LINES]
    if unknown:
        ap.error(f"games.py has games without a PORTAL_LINES entry: {unknown}")

    from playwright.sync_api import sync_playwright
    rc = 3
    try:
        with sync_playwright() as p:
            try:
                if a.login:
                    rc = mode_login(p)
                elif a.status:
                    rc = mode_status(p)
                elif a.inspect:
                    rc = mode_inspect(p, a.inspect, headless=not a.headful)
                else:
                    games = ALL_GAMES if a.game == "all" else [a.game]
                    bad = [g for g in games if g not in GAMES]
                    if bad:
                        ap.error(f"unknown game(s) {bad}; known: {ALL_GAMES}")
                    rc = mode_export(p, games, headless=not a.headful, force=a.force)
            except SystemExit as e:
                if isinstance(e.code, str):
                    log(e.code)
                    rc = 3
                else:
                    raise
    except Exception as e:                       # noqa: BLE001
        log(f"FAILED: {type(e).__name__}: {str(e)[:300]}")
        rc = 3
    if rc and a.non_fatal:
        log(f"(exit {rc} downgraded to 0: --non-fatal — the crawl covers whatever is missing)")
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
