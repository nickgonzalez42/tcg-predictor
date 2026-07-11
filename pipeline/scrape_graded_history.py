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
        """
    )


def scrape_game(game, conn, workers, delay, resume, limit):
    done = set()
    if resume:
        done = set(r[0] for r in conn.execute(
            "SELECT DISTINCT product_id FROM graded_price_history WHERE game=?", (game,)))

    targets = [r for r in conn.execute(
        "SELECT product_id, pc_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL "
        "ORDER BY psa10 DESC NULLS LAST", (game,)) if r[0] not in done]
    if limit:
        targets = targets[:limit]

    print(f"[{game}] {len(targets)} to scrape | {len(done)} already done | "
          f"{workers} workers, {delay}s/req", flush=True)

    ok = fail = total_rows = 0
    err_samples = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_one, game, pid, pc, delay) for pid, pc in targets]
        for i, fut in enumerate(as_completed(futs), 1):
            product_id, rows, err = fut.result()
            if err:
                fail += 1
                err_samples[err] = err_samples.get(err, 0) + 1
            else:
                conn.executemany("INSERT OR REPLACE INTO graded_price_history VALUES (?,?,?,?,?)", rows)
                total_rows += len(rows)
                ok += 1
                if ok % 50 == 0:
                    conn.commit()
            if i % 200 == 0:
                rate = i / (time.time() - t0)
                eta = (len(targets) - i) / rate / 3600 if rate else 0
                print(f"  [{game}] {i}/{len(targets)} ok={ok} fail={fail} rows={total_rows} "
                      f"| {rate:.1f}/s ETA {eta:.1f}h", flush=True)
    conn.commit()
    print(f"[{game}] done: ok={ok} fail={fail} rows={total_rows}", flush=True)
    if err_samples:
        print("  error breakdown:", flush=True)
        for msg, n in sorted(err_samples.items(), key=lambda x: -x[1])[:6]:
            print(f"    {n:5d}  {msg[:90]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="pokemon", help="game, or 'all'")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.0, help="per-request delay (seconds)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    from games import priced_games
    games = priced_games() if args.game == "all" else [args.game]
    conn = sqlite3.connect(PC_DB)
    ensure_table(conn)
    for game in games:
        scrape_game(game, conn, args.workers, args.delay, args.resume, args.limit)
    conn.close()


if __name__ == "__main__":
    main()
