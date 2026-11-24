#!/usr/bin/env python3
"""Move cards priced on their Normal printing off a FOIL PriceCharting page.

Found 2026-11-20: 3,333 cards (magic 2,799, lorcana 509, onepiece 24) whose
main price is the Normal printing were linked to the page of their foil
("Orcish Bowmasters [Foil] #103"), so the graded prices and the older
PriceCharting years shown beside a $30 card were the $60 foil's. Both pages
name the same TCGplayer product, and the matcher had taken the foil one.

The non-foil page is the same name with "Foil" taken out of its bracket
("[Foil] #103" -> "#103", "[Extended Art Foil]" -> "[Extended Art]"), looked
up in the same set: first in the stored PriceCharting catalog, then, with
--consoles, in the set's live listing. A page is used only after it is
fetched and found to carry OUR TCGplayer product id (or no product id at
all, as on Lorcana's non-foil pages; never a different product's).

    repoint_foil_pages.py --verify [--consoles] [--limit N]   # fetch + check, writes the plan
    repoint_foil_pages.py --apply                             # act on the checked rows

Applying, per card: the link moves (pricecharting row + a row in
pc_match_overrides.csv so the nightly matcher keeps it), the old page's
graded history becomes the foil printing's own labeled series (or is dropped
when that printing already has one), and the crawl mark is cleared so the
graded crawl fills the new page's history on its next run.

Run it while the graded crawl is NOT running: both fetch PriceCharting.
"""
import argparse
import collections
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE, PC_DB
from games import db_path
import match_dirty

PLAN_CSV = os.path.join(BASE, "ml_data", "foil_page_repoints.csv")
FIELDS = ["game", "product_id", "old_pc", "old_name", "console", "new_pc", "new_name",
          "status", "checked_at"]
NORMAL = "Normal"
RETRY_WAITS = (30, 60, 120)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plain_name(name):
    """The page name with "Foil" taken out of its bracket; None when the name
    has no foil bracket to strip."""
    def fix(m):
        if not re.search(r"(?i)\bfoil\b", m.group(1)):
            return m.group(0)
        inner = re.sub(r"\s+", " ", re.sub(r"(?i)\bfoil\b", "", m.group(1))).strip()
        return f"[{inner}]" if inner else ""
    # "[ [Extended] Foil]" (a bracket inside the bracket, 37 Magic pages):
    # take the word out first, then close up what is left around the inner one.
    nested = re.sub(r"\[\s*(\[[^\]]*\])\s*foil\s*\]", r"\1", name, flags=re.I)
    out = re.sub(r"\s+", " ", re.sub(r"\[([^\]]*)\]", fix, nested)).strip()
    return out if out != name else None


def norm(name):
    """A page name as PriceCharting might have cased it. The foil and the
    non-foil page of one card are title-cased differently ("Sleight Of Hand
    [Foil] #67" beside "Sleight of Hand #67"), which hid 600 pages from an
    exact lookup (2026-11-24)."""
    return re.sub(r"\s+", " ", name.replace("\u2019", "'")).strip().lower()


def name_only(name):
    """The name without its "#number" (some non-foil pages carry none)."""
    return norm(re.sub(r"\s*#\S+\s*$", "", name))


def find_page(cand, exact, loose_index, listing=None):
    """(pc_id, loose) for the non-foil page named `cand` in one set, or
    (None, False). exact / loose_index are that set's stored pages by name
    and by norm(name); listing is its live page list (name -> pc_id).
    loose = found only by dropping the card number, and only when the set
    has exactly one non-foil page of that name: such a hit is used only if
    the page itself names our product."""
    hit = exact.get(cand) or loose_index.get(norm(cand))
    if hit or listing is None:
        return hit, False
    hit = listing.get(cand) or {norm(n): p for n, p in listing.items()}.get(norm(cand))
    if hit:
        return hit, False
    base = name_only(cand)
    same = [p for n, p in listing.items()
            if name_only(n) == base and not re.search(r"(?i)\bfoil\b", n)]
    return (same[0], True) if len(same) == 1 else (None, False)


def foil_linked(pc):
    return pc.execute(
        "SELECT p.game, p.product_id, p.pc_id, p.pc_name, p.pc_console "
        "FROM pricecharting p JOIN tcg_csv_base b "
        "  ON b.game=p.game AND b.product_id=p.product_id "
        "WHERE b.printing=? AND p.pc_id IS NOT NULL AND lower(p.pc_name) LIKE '%foil%' "
        "ORDER BY p.game, p.pc_console, p.product_id", (NORMAL,)).fetchall()


def load_plan():
    if not os.path.exists(PLAN_CSV):
        return {}
    with open(PLAN_CSV, newline="", encoding="utf-8") as f:
        return {(r["game"], int(r["product_id"])): r for r in csv.DictReader(f)}


def save_plan(plan):
    tmp = PLAN_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(plan.values())
    os.replace(tmp, PLAN_CSV)


def console_listing(console_slug, cache):
    """name -> pc_id for one set's live listing (one crawl per set per run)."""
    if console_slug not in cache:
        from scrape_gundam_prices import crawl_console
        try:
            rows = crawl_console(console_slug) or []
        except Exception as e:                                   # noqa: BLE001
            print(f"  listing {console_slug}: {type(e).__name__}: {e}", flush=True)
            rows = []
        out = {}
        for r in rows:
            name = (r.get("product-name") or r.get("name") or "").strip()
            pid = r.get("id") or r.get("pc_id")
            if name and pid:
                out[name] = int(pid)
        cache[console_slug] = out
        print(f"  listing {console_slug}: {len(out)} page(s)", flush=True)
        time.sleep(6)
    return cache[console_slug]


def slug_overrides(game):
    """console -> slug where the page address is not the name slugified."""
    path = os.path.join(BASE, "ml_data", "pc_console_slugs.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["console"]: r["slug"] for r in csv.DictReader(f) if r.get("game") == game}


def slug_of(console):
    s = console.lower().replace("&", "and")
    s = re.sub(r"[':.,!]", "", s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def verify(limit, consoles, delay):
    from remap_special_prints import pc_catalog
    from pc_printing_backfill import fetch_verified
    pc = sqlite3.connect(f"file:{os.path.normpath(PC_DB)}?mode=ro", uri=True, timeout=120)
    rows = foil_linked(pc)
    pc.close()
    plan = load_plan()
    cats, listings, stats = {}, {}, collections.Counter()
    fetched = 0
    pace = delay
    for game, pid, old_pc, name, console in rows:
        key = (game, pid)
        prev = plan.get(key)
        if prev and prev["status"] in ("ok", "ok-name", "applied", "not-our-card") \
                and int(prev["old_pc"]) == old_pc:
            continue
        if prev and prev["status"] == "no-page" and not consoles:
            continue
        cand = plain_name(name)
        row = dict(game=game, product_id=pid, old_pc=old_pc, old_name=name,
                   console=console or "", new_pc="", new_name=cand or "",
                   status="", checked_at=now_iso())
        if not cand:
            row["status"] = "no-bracket"
            plan[key] = row
            stats["no-bracket"] += 1
            continue
        if game not in cats:
            by_con, by_con_i = {}, {}
            for (con, nm), pid_ in pc_catalog(game).items():
                by_con.setdefault(con, {})[nm] = pid_
                by_con_i.setdefault(con, {})[norm(nm)] = pid_
            cats[game] = (by_con, by_con_i, slug_overrides(game))
        by_con, by_con_i, slugs = cats[game]
        con = (console or "").strip()
        listing = (console_listing(slugs.get(con) or slug_of(con), listings)
                   if consoles and con else None)
        hit, loose = find_page(cand, by_con.get(con, {}), by_con_i.get(con, {}), listing)
        if not hit:
            row["status"] = "no-page"
            plan[key] = row
            stats["no-page"] += 1
            continue
        if limit and fetched >= limit:
            break
        refused = 0
        while True:
            time.sleep(pace)
            try:
                _chart, ids, status, asked = fetch_verified(hit)
            except OSError as e:               # reset, timeout: wait and try again
                print(f"  {type(e).__name__} on {hit}: waiting 20 s", flush=True)
                ids, status, asked = set(), 429, 20
            if status != 429 or refused == len(RETRY_WAITS):
                break
            wait = max(RETRY_WAITS[refused], asked or 0)
            print(f"  429 on {hit}: waiting {wait:.0f} s", flush=True)
            time.sleep(wait)
            refused += 1
            pace = max(pace, 2 * delay)
        fetched += 1
        if status in (429, 403):
            print(f"  refused ({status}) after {fetched} fetches: stopping, rerun later",
                  flush=True)
            break
        row["new_pc"] = hit
        # Lorcana's non-foil pages carry no TCGplayer id at all: there the
        # exact name and number in the same set is the whole check. A page
        # that names a DIFFERENT product is never taken.
        row["status"] = (f"http-{status}" if status != 200 else "ok" if pid in ids
                         else "not-our-card" if ids
                         else "no-page" if loose else "ok-name")
        plan[key] = row
        stats[row["status"]] += 1
        if fetched % 50 == 0:
            save_plan(plan)
            print(f"  {fetched} fetched: {dict(stats)} ({datetime.now():%H:%M})", flush=True)
    save_plan(plan)
    print(f"verify done: {fetched} fetched, {dict(stats)}; plan has "
          f"{dict(collections.Counter(r['status'] for r in plan.values()))}", flush=True)


def old_label(printings):
    """The foil printing the old page belongs to: the card's one printing
    besides Normal, else None (the old history is then dropped)."""
    try:
        others = [p for p in json.loads(printings or "[]") if p != NORMAL]
    except ValueError:
        return None
    return others[0] if len(others) == 1 else None


def repoint(pc, game, pid, old_pc, old_name, new_pc, new_name, label, now):
    """One card, inside the caller's transaction. Returns False when the link
    is no longer the one that was checked."""
    key = (game, pid)
    cur = pc.execute("SELECT pc_id FROM pricecharting WHERE game=? AND product_id=?",
                     key).fetchone()
    if not cur or cur[0] != old_pc:
        return False
    has_label = label and pc.execute(
        "SELECT 1 FROM graded_price_history WHERE game=? AND product_id=? AND printing=? LIMIT 1",
        (*key, label)).fetchone()
    if label and not has_label:
        rows = pc.execute(
            "SELECT grade, date, price FROM graded_price_history "
            "WHERE game=? AND product_id=? AND printing=''", key).fetchall()
    else:
        rows = []
    pc.execute("DELETE FROM graded_price_history WHERE game=? AND product_id=? AND printing=''",
               key)
    if rows:
        pc.executemany(
            "INSERT OR REPLACE INTO graded_price_history (game, product_id, printing, grade, "
            "date, price) VALUES (?,?,?,?,?,?)", [(*key, label, g, d, p) for g, d, p in rows])
        pc.execute("INSERT OR REPLACE INTO pc_printing_matches VALUES (?,?,?,?,?,?,?)",
                   (*key, label, old_pc, old_name, "ok", now))
    pc.execute("DELETE FROM pc_printing_matches WHERE game=? AND product_id=? AND printing=?",
               (*key, NORMAL))
    pc.execute(
        "UPDATE pricecharting SET pc_id=?, pc_name=?, ungraded=NULL, grade7=NULL, grade8=NULL, "
        "grade9=NULL, grade95=NULL, psa10=NULL, bgs10=NULL, cgc10=NULL, sgc10=NULL, "
        "sales_volume=NULL, updated_at=? WHERE game=? AND product_id=?",
        (new_pc, new_name, now, *key))
    pc.execute("DELETE FROM graded_crawled WHERE game=? AND product_id=?", key)
    match_dirty.mark(pc, *key)            # the nightly ships the row whatever its prices do
    return True


def apply():
    from match_review import append_override
    plan = load_plan()
    todo = [r for r in plan.values() if r["status"] in ("ok", "ok-name")]
    if not todo:
        print("nothing checked and waiting")
        return
    printings = {}
    for game in {r["game"] for r in todo}:
        cc = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=30)
        printings[game] = dict(cc.execute("SELECT product_id, printings FROM cards"))
        cc.close()
    pc = sqlite3.connect(PC_DB, timeout=600)
    pc.execute("PRAGMA cache_size=-65536")
    now = now_iso()
    done = collections.Counter()
    for i in range(0, len(todo), 50):              # short transactions beside the crawls
        batch = todo[i:i + 50]
        moved = []
        pc.execute("BEGIN IMMEDIATE")
        for r in batch:
            pid = int(r["product_id"])
            label = old_label(printings[r["game"]].get(pid))
            if repoint(pc, r["game"], pid, int(r["old_pc"]), r["old_name"],
                       int(r["new_pc"]), r["new_name"], label, now):
                moved.append(r)
            else:
                r["status"] = "link-changed"
        pc.execute("COMMIT")
        for r in moved:
            append_override(r["game"], r["product_id"], r["new_pc"],
                            f"non-foil page for a Normal-priced card (was {r['old_pc']} "
                            f"{r['old_name']}); page names our product, {now[:10]}")
            r["status"] = "applied"
            done[r["game"]] += 1
        save_plan(plan)
    pc.close()
    print(f"re-pointed: {dict(done)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--consoles", action="store_true",
                    help="also look in each set's live listing for pages the "
                         "stored catalog lacks")
    ap.add_argument("--limit", type=int, default=None, help="max page fetches")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    if args.verify:
        verify(args.limit, args.consoles, args.delay)
    if args.apply:
        apply()
    if not (args.verify or args.apply):
        plan = load_plan()
        print(dict(collections.Counter((r["game"], r["status"]) for r in plan.values())))


if __name__ == "__main__":
    main()
