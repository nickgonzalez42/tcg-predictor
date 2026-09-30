"""
Scrape per-grade monthly price history from PriceCharting product pages.

The official API has no historic endpoint, but each product page embeds a
`VGPC.chart_data` object with monthly series (back to ~2020) per grade tier.
We fetch by the PriceCharting `id` (pc_id) stored during the CSV import, parse
chart_data, and write a long-format `graded_price_history` table.

Grades: used->ungraded, cib->grade7, new->grade8, graded->grade9,
        boxonly->grade95, manualonly->psa10. Prices are pennies; 0 = no data.

Fetching is parallel (thread pool, network-bound); DB writes happen on the main
thread. Resumable via --resume.

Run:  .venv/bin/python scrape_graded_history.py --game pokemon --workers 5 --resume
"""

import argparse
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import certifi

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

GRADE_MAP = {
    "used": "ungraded", "cib": "grade7", "new": "grade8",
    "graded": "grade9", "boxonly": "grade95", "manualonly": "psa10",
}
CHART_RE = re.compile(r"VGPC\.chart_data\s*=\s*(\{.*?\})\s*;", re.S)
SSL_CTX = ssl.create_default_context(cafile=certifi.where())


def fetch_chart(pc_id: int):
    url = f"https://www.pricecharting.com/game/{pc_id}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        html = resp.read().decode("utf-8", "ignore")
    m = CHART_RE.search(html)
    return json.loads(m.group(1)) if m else None


def rows_from_chart(game, product_id, chart):
    out = []
    for series, grade in GRADE_MAP.items():
        for ts_ms, pennies in chart.get(series, []):
            if not pennies:
                continue
            date = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))
            out.append((game, product_id, grade, date, round(pennies / 100, 2)))
    return out


def fetch_one(game, product_id, pc_id, delay):
    if delay:
        time.sleep(delay)
    patient = bool(os.environ.get("TCG_PATIENT"))
    attempt = 0
    while attempt < 3:
        attempt += 1
        try:
            chart = fetch_chart(pc_id)
            if not chart:
                return product_id, None, "no chart_data"
            return product_id, rows_from_chart(game, product_id, chart), None
        except urllib.error.HTTPError as e:
            if e.code == 429:          # rate limited: back off and retry
                time.sleep(20 * attempt)
                continue
            return product_id, None, f"HTTP {e.code}"
        except Exception as e:
            # No response at all = network trouble. In patient mode (multi-day
            # backfill) wait out the outage instead of burning through cards.
            if patient:
                print(f"    [patient] {type(e).__name__} — waiting 5 min before "
                      f"retrying pc_id {pc_id}", flush=True)
                time.sleep(300)
                attempt = 0
                continue
            return product_id, None, str(e)
    return product_id, None, "429 after retries"


def ensure_table(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS graded_price_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL    NOT NULL,
            PRIMARY KEY (game, product_id, grade, date)
        );

        -- Cards whose chart PAGE has been crawled. This must be its own table:
        -- pc-match appends daily snapshot rows into graded_price_history BEFORE
        -- this crawl runs, so "has any history row" would wrongly mark every
        -- matched card as done and the deep chart backfill would never happen.
        CREATE TABLE IF NOT EXISTS graded_crawled (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            crawled_at TEXT,
            PRIMARY KEY (game, product_id)
        );
        """
    )


def gather_targets(game, conn, resume, limit, refresh_days):
    """Due pages for one game as (game, product_id, pc_id) — value-first."""
    done = set()
    if resume:
        if refresh_days:
            # Rotation: a card counts as "done" only if it was crawled within the
            # last refresh_days. Older crawls (and never-crawled cards) fall back
            # into the target list, so graded prices keep refreshing from the
            # public product pages now that the paid daily-snapshot CSV is gone.
            # Ordered by psa10 DESC, value refreshes first each night.
            cutoff = (datetime.now(timezone.utc) - timedelta(days=refresh_days)).isoformat()
            done = set(r[0] for r in conn.execute(
                "SELECT product_id FROM graded_crawled WHERE game=? AND crawled_at > ?",
                (game, cutoff)))
        else:
            done = set(r[0] for r in conn.execute(
                "SELECT product_id FROM graded_crawled WHERE game=?", (game,)))

    targets = [(game, pid, pc, psa10) for pid, pc, psa10 in conn.execute(
        "SELECT product_id, pc_id, psa10 FROM pricecharting "
        "WHERE game=? AND pc_id IS NOT NULL "
        "ORDER BY psa10 DESC NULLS LAST", (game,)) if pid not in done]
    if limit:
        targets = targets[:limit]
    print(f"[{game}] {len(targets)} due | {len(done)} fresh", flush=True)
    return targets


def scrape_targets(conn, targets, workers, delay):
    ok = fail = total_rows = 0
    per_game = {}
    err_samples = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, game, pid, pc, delay): game
                for game, pid, pc, _psa10 in targets}
        for i, fut in enumerate(as_completed(futs), 1):
            game = futs[fut]
            g = per_game.setdefault(game, {"ok": 0, "fail": 0, "rows": 0})
            product_id, rows, err = fut.result()
            if err and err != "no chart_data":
                fail += 1
                g["fail"] += 1
                err_samples[err] = err_samples.get(err, 0) + 1
            else:
                # "no chart_data" counts as crawled: the page exists but PC has
                # no chart for it yet — retrying nightly forever buys nothing.
                if rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO graded_price_history "
                        "(game, product_id, grade, date, price) VALUES (?,?,?,?,?)", rows)
                    total_rows += len(rows)
                    g["rows"] += len(rows)
                conn.execute(
                    "INSERT OR REPLACE INTO graded_crawled VALUES (?,?,?)",
                    (game, product_id, datetime.now(timezone.utc).isoformat()))
                ok += 1
                g["ok"] += 1
                if ok % 50 == 0:
                    conn.commit()
            if i % 200 == 0:
                rate = i / (time.time() - t0)
                eta = (len(targets) - i) / rate / 3600 if rate else 0
                print(f"  {i}/{len(targets)} ok={ok} fail={fail} rows={total_rows} "
                      f"| {rate:.1f}/s ETA {eta:.1f}h", flush=True)
    conn.commit()
    for game, g in sorted(per_game.items()):
        print(f"[{game}] done: ok={g['ok']} fail={g['fail']} rows={g['rows']}", flush=True)
    print(f"TOTAL done: ok={ok} fail={fail} rows={total_rows}", flush=True)
    if err_samples:
        print("  error breakdown:", flush=True)
        for msg, n in sorted(err_samples.items(), key=lambda x: -x[1])[:6]:
            print(f"    {n:5d}  {msg[:90]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="pokemon", help="game, or 'all'")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.0, help="per-request delay (seconds)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap per game (guards a game mid-onboarding)")
    ap.add_argument("--total-limit", type=int, default=None,
                    help="cap the whole run across games; due pages compete on "
                         "psa10 value regardless of game, so a cohort of pages "
                         "maturing at once can never blow up the night")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--refresh-days", type=float, default=None,
                    help="re-crawl a card once its last crawl is older than this "
                         "many days (rotation); without it --resume skips forever")
    args = ap.parse_args()

    from games import priced_games
    games = priced_games() if args.game == "all" else [args.game]
    conn = sqlite3.connect(PC_DB)
    ensure_table(conn)
    targets = []
    for game in games:
        targets.extend(gather_targets(game, conn, args.resume, args.limit,
                                      args.refresh_days))
    if args.total_limit and len(targets) > args.total_limit:
        # Value-first, but with a per-game floor (2026-09-29): a pure global
        # psa10 ranking starved the cheap games entirely once the budget
        # tightened — a 2,400-page night gave digimon 5 pages, starwars 2,
        # onepiece 1 while pokemon/yugioh ate the rest. Each game keeps its
        # own most valuable dues up to the floor (a quarter of the budget
        # split evenly), then the remainder competes on psa10 across games,
        # so every game's ladder keeps rotating while expensive cards still
        # refresh most often.
        floor = max(1, args.total_limit // (4 * max(1, len(games))))
        by_game = {}
        for t in targets:
            by_game.setdefault(t[0], []).append(t)  # per-game order is psa10 DESC
        keep, rest = [], []
        for ts in by_game.values():
            keep.extend(ts[:floor])
            rest.extend(ts[floor:])
        rest.sort(key=lambda t: t[3] if t[3] is not None else -1, reverse=True)
        print(f"total budget: {args.total_limit} of {len(targets)} due pages "
              f"(per-game floor {floor}, remainder by psa10)", flush=True)
        targets = keep + rest[:max(0, args.total_limit - len(keep))]
    scrape_targets(conn, targets, args.workers, args.delay)
    conn.close()


if __name__ == "__main__":
    main()
