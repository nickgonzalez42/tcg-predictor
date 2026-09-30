#!/usr/bin/env python3
"""
Remap graded-ladder matches that point at a SPECIAL-PRINT PriceCharting page
([1st Edition] / [Shadowless]) while our TCGplayer card is the regular print.

How it happened (found 2026-08-08): PriceCharting's special-print pages embed
the SAME TCGplayer product id as the regular page (TCGplayer bundles printings
under one product), so the page-embedded-tcg-id auto-confirm happily linked
"Machamp [1st Edition] #8" to our regular Machamp — giving the card a 1st
Edition graded ladder over an Unlimited ungraded price. 99 pokemon cards.

For each mismatched card this:
  1. finds the sibling PC product (same console, name minus the bracket tag)
     in the frozen PriceCharting catalog CSV;
  2. fetches the sibling's page and verifies it embeds OUR tcg id;
  3. repoints the match (pc_id / pc_name), NULLs the now-wrong graded price
     columns, and deletes the old page's graded_price_history + graded_crawled
     rows so the public-page rotation recrawls the RIGHT page's history;
  4. appends the correction to pc_match_overrides.csv (documentation + wins on
     any future full rebuild).
Cards with no sibling page keep their match — some vintage cards were ONLY
printed as 1st Edition (e.g. Base Set Machamp), so the special page IS the card.

Rerunnable: remapped cards stop matching the suspect screen. Always exits 0.

Run:  .venv/bin/python remap_special_prints.py [--games pokemon] [--dry-run]
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, db_path, priced_games
from scrape_gundam_prices import fetch          # polite 1 req/s + transient retry

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
OVERRIDES_CSV = os.path.join(BASE, "ml_data", "pc_match_overrides.csv")
TCG_ID_RE = re.compile(r"tcgplayer\.com/product/(\d+)")
TOKENS = ("1st edition", "shadowless")
TIER_COLS = ["ungraded", "grade7", "grade8", "grade9", "grade95", "psa10",
             "bgs10", "cgc10", "sgc10"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strip_tokens(pc_name):
    s = re.sub(r"\s*\[(1st Edition|Shadowless)\]\s*", " ", pc_name, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def pc_catalog(game):
    """(console, product-name) -> pc_id from the frozen PriceCharting CSV."""
    path = os.path.join(BASE, GAMES[game]["pc_csv"])
    out = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f):
            try:
                pc_id = int(r.get("id") or 0)
            except ValueError:
                continue
            console = (r.get("console-name") or r.get("console") or "").strip()
            name = (r.get("product-name") or "").strip()
            if pc_id and name:
                out[(console, name)] = pc_id
    return out


def existing_override_pids():
    if not os.path.exists(OVERRIDES_CSV):
        return set()
    with open(OVERRIDES_CSV, newline="", encoding="utf-8") as f:
        return {(r["game"], int(r["product_id"])) for r in csv.DictReader(f)
                if r.get("product_id")}


def add_override(game, pid, pc_id, note):
    exists = os.path.exists(OVERRIDES_CSV)
    with open(OVERRIDES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["game", "product_id", "pc_id", "note"])
        w.writerow([game, pid, pc_id, note])


def suspects(conn, game):
    """Match rows whose PC page is a special print but whose card is not."""
    rows = conn.execute(
        "SELECT product_id, pc_id, pc_name, pc_console FROM pricecharting "
        "WHERE game=? AND pc_name IS NOT NULL", (game,)).fetchall()
    names = dict(sqlite3.connect(db_path(game)).execute(
        "SELECT product_id, name FROM cards"))
    out = []
    for pid, pc_id, pc_name, pc_console in rows:
        pl = pc_name.lower()
        cl = (names.get(pid) or "").lower()
        if any(t in pl and t not in cl for t in TOKENS):
            out.append((pid, pc_id, pc_name, pc_console or ""))
    return out


def main():
    ap = argparse.ArgumentParser(description="Remap special-print PC matches.")
    ap.add_argument("--games", help="comma-separated subset (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    games = ([g.strip() for g in args.games.split(",")] if args.games
             else priced_games())

    conn = sqlite3.connect(PC_DB, timeout=60)
    overridden = existing_override_pids()
    total = {"remapped": 0, "no-sibling": 0, "verify-failed": 0, "kept": 0}
    for game in games:
        sus = suspects(conn, game)
        if not sus:
            continue
        print(f"[{game}] {len(sus)} special-print match(es) to check")
        cat = pc_catalog(game)
        for pid, old_pc, pc_name, pc_console in sus:
            if (game, pid) in overridden:
                total["kept"] += 1
                continue
            sib_name = strip_tokens(pc_name)
            sib_id = cat.get((pc_console, sib_name))
            if not sib_id or sib_id == old_pc:
                print(f"  [{game}:{pid}] no regular-print sibling for "
                      f"'{pc_name}' — match kept (card may only exist as "
                      f"special print)")
                total["no-sibling"] += 1
                continue
            status, html = fetch(f"https://www.pricecharting.com/game/{sib_id}")
            ids = ({int(x) for x in TCG_ID_RE.findall(urllib.parse.unquote(html))}
                   if status == 200 else set())
            if pid not in ids:
                print(f"  [{game}:{pid}] sibling '{sib_name}' (pc {sib_id}) "
                      f"did not verify (status {status}) — match kept for review")
                total["verify-failed"] += 1
                continue
            if args.dry_run:
                print(f"  [{game}:{pid}] would remap pc {old_pc} "
                      f"('{pc_name}') -> pc {sib_id} ('{sib_name}')")
                total["remapped"] += 1
                continue
            nulls = ", ".join(f"{c}=NULL" for c in TIER_COLS)
            conn.execute(
                f"UPDATE pricecharting SET pc_id=?, pc_name=?, {nulls}, "
                f"updated_at=? WHERE game=? AND product_id=?",
                (sib_id, sib_name, now_iso(), game, pid))
            conn.execute("DELETE FROM graded_price_history WHERE game=? AND product_id=?",
                         (game, pid))
            conn.execute("DELETE FROM graded_crawled WHERE game=? AND product_id=?",
                         (game, pid))
            conn.commit()
            add_override(game, pid, sib_id,
                         f"special-print remap {pc_name!r} -> {sib_name!r} 2026-08-08")
            total["remapped"] += 1
            print(f"  [{game}:{pid}] remapped pc {old_pc} ('{pc_name}') -> "
                  f"pc {sib_id} ('{sib_name}'); graded history cleared for recrawl")
    print(f"done: {total}")
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"remap_special_prints: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
