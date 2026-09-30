#!/usr/bin/env python3
"""
Phase 2 of multi-printing (plan: tcg-predictor/notes/printing-plan.md): pull
per-printing PriceCharting history — graded ladders AND deep ungraded years —
into the LABELED namespace of graded_price_history.

For every non-base variant we've ingested (printing_variants, Phase 1), the
matching PC sibling page is derived from the card's existing base match:
"Ampharos #56" + variant "Reverse Holofoil" -> "Ampharos [Reverse Holo] #56",
looked up in the frozen PC catalog CSV, then fetched ONCE — the fetch doubles
as verification (the page must embed OUR tcg product id). Chart series map to
tiers exactly like the nightly graded crawl (GRADE_MAP).

NON-DISRUPTION CONTRACT (the 2026-08-09 rescue incident rules):
  - writes ONLY labeled rows (printing != '') — every nightly consumer filters
    printing='', so this data is invisible until Phase 3 opens it;
  - runs OUTSIDE the nightly (daytime batches, --limit capped); no nightly
    step is added until the one-time backlog drains;
  - asserts the base ('') graded row count is UNCHANGED after every run;
  - one writer per namespace: labeled graded rows belong to THIS script only.

State: pc_printing_matches (game, product_id, printing) -> pc_id + status
(ok / no-page-in-catalog / verify-failed / no-chart / unmapped-variant).
Rerunnable: resolved and memoized targets are skipped; --retry-failed re-probes
the failures. Always exits 0.

Run:  .venv/bin/python pc_printing_backfill.py --limit 500
      .venv/bin/python pc_printing_backfill.py --pids pokemon:83466
"""

import argparse
import collections
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import certifi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import priced_games
from remap_special_prints import pc_catalog
from scrape_graded_history import CHART_RE, GRADE_MAP, UA

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
TCG_ID_RE = re.compile(r"tcgplayer\.com/product/(\d+)")
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# TCGplayer variant name -> PC bracket text candidates (first catalog hit wins).
# "Unlimited" is special: the sibling is the base page name WITHOUT its bracket.
VARIANT_BRACKETS = {
    "1st Edition": ["1st Edition"],
    "1st Edition Holofoil": ["1st Edition"],  # PC's holo-rare page IS the holo
    "Shadowless": ["Shadowless"],
    "Reverse Holofoil": ["Reverse Holo", "Reverse Foil"],
    "Holofoil": ["Holo", "Holofoil"],
    "Foil": ["Foil"],
    # De-bracketed base page: for vintage holo rares PC's unbracketed page IS
    # the (unlimited) holo; a non-base "Normal" variant has no separate page
    # unless the base page carried a bracket to strip.
    "Unlimited": None,
    "Unlimited Holofoil": None,
    "Normal": None,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pc_printing_matches (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL,
            pc_id      INTEGER,
            pc_name    TEXT,
            status     TEXT,
            checked_at TEXT,
            PRIMARY KEY (game, product_id, printing))
        """)
    conn.commit()


def candidate_names(base_pc_name, variant):
    """PC page names the variant could live under, derived from the base page."""
    if variant not in VARIANT_BRACKETS:
        return None                                   # unmapped variant
    if VARIANT_BRACKETS[variant] is None:             # Unlimited = de-bracketed base
        stripped = re.sub(r"\s*\[[^\]]+\]\s*", " ", base_pc_name)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return [stripped] if stripped != base_pc_name else []
    m = re.match(r"^(.*?)(\s+#\S+)?$", base_pc_name.strip())
    stem, suffix = m.group(1), (m.group(2) or "")
    return [f"{stem} [{b}]{suffix}" for b in VARIANT_BRACKETS[variant]]


def fetch_verified(pc_id):
    """(chart_dict|None, tcg_ids, http_status) for one PC page — one request
    serves both the price chart and the embedded-tcg-id verification."""
    req = urllib.request.Request(
        f"https://www.pricecharting.com/game/{pc_id}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return None, set(), e.code
    m = CHART_RE.search(html)
    chart = json.loads(m.group(1)) if m else None
    ids = {int(x) for x in TCG_ID_RE.findall(urllib.parse.unquote(html))}
    return chart, ids, 200


def main():
    ap = argparse.ArgumentParser(description="Backfill per-printing PC history.")
    ap.add_argument("--limit", type=int, default=500, help="max page fetches this run")
    ap.add_argument("--pids", nargs="*", default=[], help="game:product_id tokens")
    ap.add_argument("--games", help="comma-separated subset (default: all)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-probe verify-failed/no-chart targets too")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()
    games = ([g.strip() for g in args.games.split(",")] if args.games
             else priced_games())

    conn = sqlite3.connect(PC_DB, timeout=120)
    init_db(conn)
    before_base = conn.execute(
        "SELECT COUNT(*) FROM graded_price_history WHERE printing=''").fetchone()[0]

    done_status = ("ok", "no-page-in-catalog", "unmapped-variant") if args.retry_failed \
        else ("ok", "no-page-in-catalog", "unmapped-variant", "verify-failed", "no-chart")
    done = {(g, p, v) for g, p, v, s in conn.execute(
        "SELECT game, product_id, printing, status FROM pc_printing_matches")
        if s in done_status}

    q = f"""
        SELECT v.game, v.product_id, v.variant, v.latest_price, p.pc_name, p.pc_console
        FROM printing_variants v
        JOIN pricecharting p ON p.game = v.game AND p.product_id = v.product_id
        WHERE v.is_base = 0 AND v.variant != '(none)' AND p.pc_name IS NOT NULL
          AND v.game IN ({",".join("?" * len(games))})
        ORDER BY v.latest_price DESC
        """
    only = {(g, int(pid)) for g, _, pid in (t.partition(":") for t in args.pids)}
    targets = [(g, pid, var, name, console)
               for g, pid, var, _lp, name, console in conn.execute(q, games)
               if (g, pid, var) not in done and (not only or (g, pid) in only)]
    targets = targets[:args.limit]
    if not targets:
        print("nothing to backfill")
        return
    print(f"backfilling {len(targets)} (card, printing) page(s) at 1 req/s")

    cats = {}
    stats = collections.Counter()
    for i, (game, pid, variant, base_name, console) in enumerate(targets, 1):
        def memo(status, pc_id=None, pc_name=None):
            conn.execute("INSERT OR REPLACE INTO pc_printing_matches VALUES (?,?,?,?,?,?,?)",
                         (game, pid, variant, pc_id, pc_name, status, now_iso()))
            conn.commit()
            stats[status] += 1
        cands = candidate_names(base_name, variant)
        if cands is None:
            memo("unmapped-variant")
            continue
        if game not in cats:
            cats[game] = pc_catalog(game)
        pc_id, pc_name = None, None
        for cand in cands:
            hit = cats[game].get(((console or "").strip(), cand))
            if hit:
                pc_id, pc_name = hit, cand
                break
        if not pc_id:
            memo("no-page-in-catalog")
            continue
        time.sleep(args.delay)
        chart, ids, status = fetch_verified(pc_id)
        if status == 429:
            print("  rate limited — stopping this run early", file=sys.stderr)
            break
        if pid not in ids:
            memo("verify-failed", pc_id, pc_name)
            continue
        if not chart:
            memo("no-chart", pc_id, pc_name)
            continue
        rows = []
        for series, grade in GRADE_MAP.items():
            for ts_ms, pennies in chart.get(series, []):
                if pennies:
                    rows.append((game, pid, variant, grade,
                                 time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000)),
                                 round(pennies / 100, 2)))
        conn.executemany(
            "INSERT OR REPLACE INTO graded_price_history "
            "(game, product_id, printing, grade, date, price) VALUES (?,?,?,?,?,?)",
            rows)
        memo("ok", pc_id, pc_name)
        stats["rows"] += len(rows)
        if i % 50 == 0:
            print(f"  {i}/{len(targets)} ({stats['ok']} ok, {stats['rows']} rows)",
                  flush=True)

    after_base = conn.execute(
        "SELECT COUNT(*) FROM graded_price_history WHERE printing=''").fetchone()[0]
    assert after_base == before_base, \
        f"BASE ROWS CHANGED {before_base} -> {after_base} — investigate before rerunning!"
    print(f"done: {dict(stats)}; base('') rows unchanged at {before_base:,} ✓")
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"pc_printing_backfill: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
