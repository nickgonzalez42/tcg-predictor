#!/usr/bin/env python3
"""Make the Normal printing a card's main price (2026-11-19, user decision).

A card's main price is its '' series in tcg_nm_history. The CSV import
decides which printing that is ONCE and keeps it; a card first imported on a
night when only its foil had a market price (a pre-release, mostly) stayed
on the foil for good, so the site led with $28 for a card anyone can buy
for $12. 1,476 cards across six games on the day this was written.

For every card whose main printing is not Normal although a Normal printing
exists and carries a recent price:
  - the '' rows become that old printing's own labeled series,
  - the Normal labeled series becomes the '' series,
  - tcg_csv_base, printing_variants.is_base and the catalog row follow,
  - the PriceCharting page follows when pc_printing_matches already knows
    the Normal printing's page; the old page stays as the old printing's.
Nothing is spliced: the main chart simply starts on the Normal printing's
first price.

Rows are deleted and re-inserted, never updated in place, so the nightly
delta (delta_extras.py ships rows above a rowid mark) carries the new ones.
The deletions reach the server with the next full push.

import_tcg_csv.py calls switch_card() itself from now on, the night a
provisional foil's Normal printing first gets a price.

    switch_base_printing.py            # dry run: the cards, before and after
    switch_base_printing.py --apply
    switch_base_printing.py --games magic,pokemon
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import PC_DB
from games import db_path, priced_games

NORMAL = "Normal"
FRESH_DAYS = 14      # the Normal printing must have a price this recent
SOURCE = "normal-first"
# A holo rare's Holofoil printing IS the card collectors mean (the Normal one
# is a theme-deck or reprint oddity), so those stay as they are.
KEEP = {"Holofoil", "1st Edition Holofoil", "Unlimited Holofoil"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def excluded(game):
    """Cards whose main series another step owns (pins, the detailed-endpoint
    refresh) or that are blocked: left alone."""
    from scrape_tcg_nm_prices import TCG_NM_ONLY_CSV, NM_BLOCKLIST_CSV, PINS_CSV
    out = set()
    for path in (TCG_NM_ONLY_CSV, NM_BLOCKLIST_CSV, PINS_CSV):
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game") == game and (r.get("product_id") or "").isdigit():
                    out.add(int(r["product_id"]))
    return out


def candidates(pc, game):
    """[(product_id, name, set, old printing, old price, normal price, normal
    points, first normal date, old points)] for one game."""
    cc = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=30)
    cols = {r[1] for r in cc.execute("PRAGMA table_info(cards)")}
    if "base_printing" not in cols:
        cc.close()
        return []
    # tcg_csv_base is what the import writes the '' series from, so it says
    # which printing the series really is; the catalog stamp is the fallback
    # (printing_ingest re-stamps it from price closeness alone).
    sticky = dict(pc.execute(
        "SELECT product_id, printing FROM tcg_csv_base WHERE game=?", (game,)))
    rows = [(pid, name, set_name, sticky.get(pid) or base, printings)
            for pid, name, set_name, base, printings in cc.execute(
                "SELECT product_id, name, set_name, base_printing, printings FROM cards "
                "WHERE printings LIKE ?", (f'%"{NORMAL}"%',))
            if (sticky.get(pid) or base) not in (None, NORMAL)]
    cc.close()
    skip = excluded(game)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    out = []
    for pid, name, set_name, old, printings in rows:
        if pid in skip or old in KEEP:
            continue
        try:
            if NORMAL not in json.loads(printings):
                continue
        except ValueError:
            continue
        norm = pc.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM tcg_nm_history "
            "WHERE game=? AND product_id=? AND printing=?", (game, pid, NORMAL)).fetchone()
        if not norm[0] or norm[2] < cutoff:
            continue                      # no live Normal price to lead with
        base = pc.execute(
            "SELECT COUNT(*) FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=''",
            (game, pid)).fetchone()[0]
        last = lambda pr: (pc.execute(
            "SELECT price FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=? "
            "ORDER BY date DESC LIMIT 1", (game, pid, pr)).fetchone() or (None,))[0]
        out.append((pid, name, set_name, old, last(""), last(NORMAL), norm[0], norm[1], base))
    return out


def switch_card(pc, game, pid, old, now=None, need_history=True):
    """Move one card's main series from `old` to Normal inside the caller's
    transaction on pricecharting.db. Returns the Normal printing's newest
    stored price. With need_history a card whose Normal printing has no rows
    is left untouched (None); the nightly import passes False, because it
    writes tonight's Normal price itself straight after."""
    now = now or now_iso()
    key = (game, pid)
    normal = pc.execute(
        "SELECT date, price FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=? "
        "ORDER BY date", (*key, NORMAL)).fetchall()
    if not normal and need_history:
        return None
    base = pc.execute(
        "SELECT date, price FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=''",
        key).fetchall()
    kept = {d for (d,) in pc.execute(
        "SELECT date FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=?",
        (*key, old))}
    pc.execute("DELETE FROM tcg_nm_history WHERE game=? AND product_id=? AND printing IN ('', ?)",
               (*key, NORMAL))
    pc.executemany(
        "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, printing, date, price) "
        "VALUES (?,?,?,?,?)",
        [(*key, old, d, p) for d, p in base if d not in kept]
        + [(*key, "", d, p) for d, p in normal])
    pc.execute("INSERT OR REPLACE INTO tcg_csv_base VALUES (?,?,?,?,?)",
               (*key, NORMAL, SOURCE, now))
    pc.execute("UPDATE printing_variants SET is_base = (variant = ?) "
               "WHERE game=? AND product_id=?", (NORMAL, *key))

    # The PriceCharting page follows the printing when the Normal printing's
    # page is already known; otherwise the card keeps the page it has.
    try:
        npage = pc.execute(
            "SELECT pc_id, pc_name FROM pc_printing_matches WHERE game=? AND product_id=? "
            "AND printing=? AND status='ok' AND pc_id IS NOT NULL", (*key, NORMAL)).fetchone()
        cur = pc.execute("SELECT pc_id, pc_name FROM pricecharting WHERE game=? AND product_id=?",
                         key).fetchone()
    except sqlite3.OperationalError:
        npage = cur = None
    if npage and cur and cur[0] and npage[0] != cur[0]:
        graded = {pr: pc.execute(
            "SELECT grade, date, price FROM graded_price_history WHERE game=? AND product_id=? "
            "AND printing=?", (*key, pr)).fetchall() for pr in ("", NORMAL)}
        pc.execute("DELETE FROM graded_price_history WHERE game=? AND product_id=? "
                   "AND printing IN ('', ?)", (*key, NORMAL))
        pc.executemany(
            "INSERT OR REPLACE INTO graded_price_history (game, product_id, printing, grade, "
            "date, price) VALUES (?,?,?,?,?,?)",
            [(*key, old, g, d, p) for g, d, p in graded[""]]
            + [(*key, "", g, d, p) for g, d, p in graded[NORMAL]])
        pc.execute(
            "UPDATE pricecharting SET pc_id=?, ungraded=NULL, grade7=NULL, grade8=NULL, "
            "grade9=NULL, grade95=NULL, psa10=NULL, bgs10=NULL, cgc10=NULL, sgc10=NULL, "
            "sales_volume=NULL, updated_at=? WHERE game=? AND product_id=?",
            (npage[0], now, *key))
        pc.execute("DELETE FROM pc_printing_matches WHERE game=? AND product_id=? "
                   "AND printing IN (?, ?)", (*key, NORMAL, old))
        pc.execute("INSERT INTO pc_printing_matches VALUES (?,?,?,?,?,?,?)",
                   (*key, old, cur[0], cur[1], "ok", now))
        # The crawl refills the row's graded prices from the new page.
        pc.execute("DELETE FROM graded_crawled WHERE game=? AND product_id=?", key)
    return normal[-1][1] if normal else None


def stamp_catalog(game, done):
    """done = [(product_id, old printing, normal price)]: the catalog row's
    derived cache follows (the nightly import re-stamps it the same way)."""
    cc = sqlite3.connect(db_path(game), timeout=60)
    cols = {r[1] for r in cc.execute("PRAGMA table_info(cards)")}
    for pid, old, price in done:
        row = cc.execute("SELECT printings FROM cards WHERE product_id=?", (pid,)).fetchone()
        try:
            ps = json.loads(row[0]) if row and row[0] else [NORMAL, old]
        except ValueError:
            ps = [NORMAL, old]
        order = [NORMAL] + sorted(v for v in ps if v != NORMAL)
        cc.execute("UPDATE cards SET printings=?, base_printing=? WHERE product_id=?",
                   (json.dumps(order), NORMAL, pid))
        if "near_mint_price" in cols and price is not None:
            cc.execute("UPDATE cards SET near_mint_price=? WHERE product_id=?", (price, pid))
    cc.commit()
    cc.close()


def main():
    ap = argparse.ArgumentParser(description="Make the Normal printing each card's main price.")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--games", help="comma-separated subset (default: every priced game)")
    ap.add_argument("--list", type=int, default=12, help="cards to print per game")
    args = ap.parse_args()
    games = [g.strip() for g in args.games.split(",")] if args.games else priced_games()

    ro = sqlite3.connect(f"file:{os.path.normpath(PC_DB)}?mode=ro", uri=True, timeout=60)
    plan = {g: candidates(ro, g) for g in games}
    ro.close()
    total = 0
    for game, cands in plan.items():
        if not cands:
            continue
        total += len(cands)
        short = sum(1 for c in cands if c[6] < 8)
        print(f"[{game}] {len(cands)} card(s) move to Normal "
              f"({short} with under 8 Normal price points)")
        for pid, name, set_name, old, oldp, newp, n, first, _ in sorted(
                cands, key=lambda c: -(c[4] or 0))[:args.list]:
            print(f"    {pid} {name[:44]!r} ({set_name[:24]}): {old} "
                  f"{'$%.2f' % oldp if oldp else '-'} -> Normal "
                  f"{'$%.2f' % newp if newp else '-'} ({n} points since {first})")
    print(f"{total} card(s) in all")
    if not args.apply:
        print("dry run: nothing written")
        return

    pc = sqlite3.connect(PC_DB, timeout=120)
    now = now_iso()
    for game, cands in plan.items():
        done = []
        for i in range(0, len(cands), 100):        # short transactions beside the crawls
            pc.execute("BEGIN IMMEDIATE")
            for pid, _name, _set, old, *_ in cands[i:i + 100]:
                price = switch_card(pc, game, pid, old, now)
                if price is not None:
                    done.append((pid, old, price))
            pc.execute("COMMIT")
        if done:
            stamp_catalog(game, done)
            print(f"[{game}] switched {len(done)} card(s)")
    pc.close()


if __name__ == "__main__":
    main()
