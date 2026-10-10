#!/usr/bin/env python3
"""
Booster-box prices per set — a set-level demand signal for the pre-release
estimates (user suggestion, 2026-10-10: "pack/box price affects individual
card price a lot").

Two sources, two jobs:

  * HISTORY (training): PriceCharting tracks every set's Booster Box as its
    own product. The bulk CSVs we hold (pricecharting_<game>.csv, last pulled
    2026-07-30 before the subscription lapsed) list those products with their
    PriceCharting ids; the public product page for an id carries the same
    monthly `VGPC.chart_data` the graded-history crawler already parses. From
    it we take the box's price over the set's first ~2.5 months — the "launch
    box price" that lines up with the card-level launch label.

  * LIVE (prediction): an upcoming set has no PriceCharting history yet, but
    TCGplayer lists its Booster Box as a presale product with a market price.
    For every set that still has unpriced cards we read that presale price.

Output: ml_data/sealed_boxes.csv, one row per (game, set): the box's launch
price and latest PriceCharting price, the TCGplayer presale/market price, and
where each came from. forecast_prerelease.py turns it into features (the
set's box price relative to the game's recent sets).

Set mapping: PriceCharting "console" names differ from TCGplayer set names,
so sets map through the cards we already matched (pricecharting.pc_console
per product_id -> the set's majority console).

Resumable: chart data is cached per PriceCharting id in ml_data/sealed_boxes_cache.json
and refetched only when stale (30 days) or when the set is still young.
"""

import argparse
import collections
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE   # noqa: E402
from games import GAMES, priced_games, db_path   # noqa: E402
import tcg_pokemon_scraper as tp   # noqa: E402  (session + rate limiting + retries)
import scrape_graded_history as sgh   # noqa: E402  (fetch_chart on the public product page)

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
ML = os.path.join(BASE, "ml_data")
OUT_CSV = os.path.join(ML, "sealed_boxes.csv")
CACHE = os.path.join(ML, "sealed_boxes_cache.json")
LAUNCH_DAYS = 75          # box "launch price" window after the set's release
CACHE_DAYS = 30           # refetch a chart after this long
YOUNG_DAYS = 120          # sets younger than this always refetch (prices still forming)
PC_DELAY = 1.5            # seconds between PriceCharting page fetches


def parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def set_release_dates(game):
    """set_name -> earliest card release date (ISO) from the game's own DB."""
    conn = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    out = {}
    if "release_date" in cols:
        for s, d in conn.execute("SELECT set_name, MIN(substr(release_date,1,10)) FROM cards "
                                 "WHERE release_date IS NOT NULL GROUP BY set_name"):
            out[s] = d
    else:   # one piece: release date rides in the attributes JSON
        for s, ca in conn.execute("SELECT set_name, custom_attributes FROM cards"):
            try:
                d = (json.loads(ca or "{}").get("releaseDate") or "")[:10]
            except ValueError:
                continue
            if d and (s not in out or d < out[s]):
                out[s] = d
    conn.close()
    return out


def unpriced_sets(game, today):
    """Sets with unpriced, recently/soon released cards — the live targets."""
    conn = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    look = (today - timedelta(days=45)).isoformat()
    if "release_date" in cols:
        rows = conn.execute(
            "SELECT DISTINCT set_name FROM cards WHERE near_mint_price IS NULL "
            "AND image_path IS NOT NULL AND substr(release_date,1,10) >= ?", (look,)).fetchall()
        out = [r[0] for r in rows]
    else:
        out = []
        for s, ca in conn.execute("SELECT DISTINCT set_name, custom_attributes FROM cards "
                                  "WHERE near_mint_price IS NULL AND image_path IS NOT NULL"):
            try:
                d = (json.loads(ca or "{}").get("releaseDate") or "")[:10]
            except ValueError:
                continue
            if d >= look and s not in out:
                out.append(s)
    conn.close()
    return out


def console_to_set(game):
    """PriceCharting console name -> our set name, via already-matched cards."""
    pc = sqlite3.connect(f"file:{PC_DB}?mode=ro", uri=True, timeout=60)
    cards = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    sets = dict(cards.execute("SELECT product_id, set_name FROM cards"))
    votes = collections.defaultdict(collections.Counter)
    for pid, con in pc.execute("SELECT product_id, pc_console FROM pricecharting "
                               "WHERE game=? AND pc_console IS NOT NULL", (game,)):
        if pid in sets and sets[pid]:
            votes[con][sets[pid]] += 1
    pc.close(); cards.close()
    return {con: c.most_common(1)[0][0] for con, c in votes.items()}


def csv_boxes(game):
    """[(pc_id, console, csv_release)] for the plain 'Booster Box' of each set."""
    path = os.path.join(BASE, GAMES[game]["pc_csv"])
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("product-name") or "").strip().lower() != "booster box":
                continue
            try:
                out.append((int(row["id"]), row.get("console-name") or "", row.get("release-date") or ""))
            except (KeyError, ValueError):
                continue
    return out


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    return {}


def chart_series(chart):
    """Monthly (date, price) for a sealed product: PriceCharting's 'used'
    series is the box's market price; 'new' backstops the rare page that
    only tracks sealed-new."""
    for key in ("used", "new"):
        pts = [(time.strftime("%Y-%m-%d", time.gmtime(ts / 1000)), round(p / 100, 2))
               for ts, p in chart.get(key, []) if p]
        if pts:
            return sorted(pts)
    return []


def launch_and_latest(points, release):
    if not points:
        return None, None
    latest = points[-1][1]
    if not release:
        return None, latest
    hi = (release + timedelta(days=LAUNCH_DAYS)).isoformat()
    win = [p for d, p in points if release.isoformat() <= d <= hi]
    if not win:   # monthly points: take the first one after release if the window is empty
        after = [p for d, p in points if d >= release.isoformat()]
        win = after[:1]
    launch = sorted(win)[len(win) // 2] if win else None
    return launch, latest


def tcg_box_price(session, game, set_name):
    """TCGplayer market (or lowest listing) price of the set's Booster Box."""
    payload = {
        "algorithm": "sales_synonym_v2", "from": 0, "size": 24,
        "filters": {"term": {"productLineName": [GAMES[game]["tcg_line"]],
                             "setName": [set_name],
                             "productTypeName": ["Sealed Products"]},
                    "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}},
                          "filters": {"term": {"sellerStatus": "Live", "channelId": 0},
                                      "range": {"quantity": {"gte": 1}},
                                      "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {},
    }
    resp = tp.request_with_retries(session, "POST", tp.SEARCH_URL, tp.DEFAULT_DELAY,
                                   params={"q": "", "isList": "false"}, data=json.dumps(payload))
    if resp is None or resp.status_code != 200:
        return None, None
    try:
        results = (resp.json().get("results") or [{}])[0].get("results", [])
    except ValueError:
        return None, None
    boxes = [p for p in results
             if "booster box" in (p.get("productName") or "").lower()
             and "case" not in (p.get("productName") or "").lower()]
    if not boxes:
        return None, None
    best = min(boxes, key=lambda p: len(p.get("productName") or ""))   # the plain box, not a variant
    price = best.get("marketPrice") or best.get("lowestPrice")
    try:
        return (float(price) if price else None), int(float(best.get("productId") or 0)) or None
    except (TypeError, ValueError):
        return None, None


def main():
    ap = argparse.ArgumentParser(description="Per-set booster-box prices (PriceCharting history + TCGplayer presale).")
    ap.add_argument("--game", action="append")
    ap.add_argument("--no-live", action="store_true", help="skip the TCGplayer presale lookups")
    ap.add_argument("--no-history", action="store_true", help="skip PriceCharting chart fetches (cache only)")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cache = load_cache()
    tp.RATE_LIMITER = tp.RateLimiter(rpm=tp.DEFAULT_RPM, min_interval=tp.DEFAULT_DELAY)
    session = tp.make_session() if not args.no_live else None

    rows = {}   # (game, set) -> row dict
    for game in (args.game or priced_games()):
        releases = set_release_dates(game)
        c2s = console_to_set(game)
        boxes = csv_boxes(game)
        mapped = [(pid, c2s[con], rel) for pid, con, rel in boxes if con in c2s]
        fetched = 0
        for pc_id, set_name, csv_rel in mapped:
            rel = parse_date(releases.get(set_name)) or parse_date(csv_rel)
            key = str(pc_id)
            ent = cache.get(key)
            stale = (ent is None or (today - parse_date(ent.get("fetched_at"))).days > CACHE_DAYS
                     or (rel and (today - rel).days < YOUNG_DAYS
                         and (today - parse_date(ent.get("fetched_at"))).days > 3))
            if stale and not args.no_history:
                try:
                    time.sleep(PC_DELAY)
                    chart = sgh.fetch_chart(pc_id)
                    pts = chart_series(chart) if chart else []
                    cache[key] = {"fetched_at": today.isoformat(), "points": pts}
                    fetched += 1
                except Exception as e:   # noqa: BLE001 — one page never stops the sweep
                    print(f"  [{game}] pc {pc_id} ({set_name}): {type(e).__name__}", file=sys.stderr)
                    if ent is None:
                        continue
            pts = [tuple(p) for p in cache.get(key, {}).get("points", [])]
            launch, latest = launch_and_latest(pts, rel)
            rows[(game, set_name)] = {
                "game": game, "set_name": set_name, "pc_id": pc_id,
                "release_date": rel.isoformat() if rel else "",
                "box_launch": launch if launch is not None else "",
                "box_latest": latest if latest is not None else "",
                "box_points": len(pts), "tcg_box_price": "", "tcg_box_product_id": "",
                "updated_at": now_iso,
            }
        print(f"[{game}] {len(boxes)} booster boxes in the PriceCharting CSV, {len(mapped)} mapped to "
              f"sets, {fetched} charts fetched, "
              f"{sum(1 for r in rows.values() if r['game'] == game and r['box_launch'] != '')} launch prices")

        if session is not None:
            live = 0
            for set_name in unpriced_sets(game, today):
                price, pid = tcg_box_price(session, game, set_name)
                if price is None:
                    continue
                row = rows.setdefault((game, set_name), {
                    "game": game, "set_name": set_name, "pc_id": "",
                    "release_date": releases.get(set_name, ""), "box_launch": "", "box_latest": "",
                    "box_points": 0, "tcg_box_price": "", "tcg_box_product_id": "", "updated_at": now_iso,
                })
                row["tcg_box_price"], row["tcg_box_product_id"] = price, pid or ""
                live += 1
            print(f"[{game}] {live} live TCGplayer box prices for sets with unpriced cards")

    os.makedirs(ML, exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(cache, f)
    cols = ["game", "set_name", "pc_id", "release_date", "box_launch", "box_latest", "box_points",
            "tcg_box_price", "tcg_box_product_id", "updated_at"]
    # keep rows for games not run this time
    old = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                old[(r["game"], r["set_name"])] = r
    ran = set(args.game or priced_games())
    merged = {k: v for k, v in old.items() if k[0] not in ran}
    merged.update(rows)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(merged):
            w.writerow({c: merged[k].get(c, "") for c in cols})
    print(f"sealed_prices: {len(merged)} set rows -> {OUT_CSV}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # noqa: BLE001
        print(f"sealed_prices: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
