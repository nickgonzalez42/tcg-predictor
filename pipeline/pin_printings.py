#!/usr/bin/env python3
"""
Pin multi-printing TCGplayer products to ONE printing (variant) for ungraded
pricing — and keep them pinned.

The problem (found 2026-08-08 on Neo Revelation Aerodactyl #83466): a vintage
product carries several printings (1st Edition / Unlimited) under one product
id. The search API's product-level marketPrice can reflect the PREMIUM printing
($33.63, 1st Edition) while our PriceCharting-era series tracked the common one
($7.48, Unlimited) — so the July source switch stitched a fake +350% step into
the card's history, its price, and the model's training data.

The fix is continuity: the detailed endpoint carries one Near Mint series PER
variant, so a card whose seam ratio looks wrong gets probed, and if multiple NM
variants exist we pin the one whose price best continues our own series. Pinned
cards are then excluded from the search sweep (scrape_tcg_nm_prices skips them)
and refreshed from their pinned variant's weekly series instead.

Modes (combine freely; the nightly runs --detect --refresh):
  --audit          seam scan: last PC-era ungraded vs latest TCG price, probe
                   candidates at ratio >= --ratio (one-time pass + safety net)
  --detect         self-jump scan: latest search price >= --ratio x the card's
                   own trailing 30-day median (catches TCGplayer re-featuring
                   a premium sku at any point in the future)
  --refresh        re-pull pinned cards' series, least-recently-refreshed
                   first (--refresh-days rotation, --limit cap)
  --pids g:pid ... audit specific cards regardless of ratio screens

State:
  ml_data/tcg_printing_pins.csv   the pins (curated data — rides the nightly
                                  CSV backup; sweep excludes these pids)
  printing_checked (pricecharting.db)        probe memo: single-variant /
                                  no-close-variant cards, not re-probed
  printing_pin_refreshed (pricecharting.db)  refresh rotation timestamps

Always exits 0 — a pinning hiccup must never fail the nightly.
"""

import argparse
import collections
import csv
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import freshen_current_week

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
PINS_CSV = os.path.join(BASE, "ml_data", "tcg_printing_pins.csv")
TCG_NM_ONLY_CSV = os.path.join(BASE, "ml_data", "tcg_nm_only.csv")
GAMES_DEFAULT = ["pokemon", "onepiece", "yugioh", "magic", "lorcana", "digimon",
                 "gundam", "starwars"]
# The pinned variant must land within this factor of the continuity baseline,
# else nothing is trusted and the card goes to review instead (a PC baseline
# can itself be wrong — see tcg_nm_only).
CLOSE_FACTOR = 1.6


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS printing_checked (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            result TEXT, checked_at TEXT,
            PRIMARY KEY (game, product_id));
        CREATE TABLE IF NOT EXISTS printing_pin_refreshed (
            game TEXT NOT NULL, product_id INTEGER NOT NULL, refreshed_at TEXT,
            PRIMARY KEY (game, product_id));
        """)
    conn.commit()


def load_pins():
    """[(game, pid, variant)] from the pins CSV."""
    if not os.path.exists(PINS_CSV):
        return []
    with open(PINS_CSV, newline="", encoding="utf-8") as f:
        return [(r["game"], int(r["product_id"]), r["variant"])
                for r in csv.DictReader(f) if r.get("product_id")]


def add_pin(game, pid, variant, sku_id, note):
    exists = os.path.exists(PINS_CSV)
    with open(PINS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["game", "product_id", "variant", "sku_id", "pinned_at", "note"])
        w.writerow([game, pid, variant, sku_id, now_iso(), note])


def load_nm_only():
    if not os.path.exists(TCG_NM_ONLY_CSV):
        return set()
    with open(TCG_NM_ONLY_CSV, newline="", encoding="utf-8") as f:
        return {(r["game"], int(r["product_id"])) for r in csv.DictReader(f)
                if r.get("product_id")}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def nm_variant_series(session, pid, delay):
    """variant -> (sku_id, [(YYYY-MM-DD, price)] newest-first) for every Near
    Mint printing with at least one non-zero weekly bucket."""
    h = tp.fetch_price_history(session, pid, delay)
    out = {}
    for s in (h.get("result") or h.get("results") or []) if h else []:
        if s.get("condition") != "Near Mint":
            continue
        pts = []
        for b in s.get("buckets", []):
            d = b.get("bucketStartDate") or b.get("date")
            p = _f(b.get("marketPrice"))
            if d and p:
                pts.append((d[:10], p))
        if pts:
            out[s.get("variant") or "?"] = (s.get("skuId"), pts)
    return out


def write_series(conn, game, pid, pts, replace_all):
    """Replace (or extend) the card's tcg_nm_history with a variant's weekly
    series. replace_all wipes the search-sourced rows first — they priced the
    wrong printing. The newest (current-week) bucket is restamped to the fetch
    day so pinned cards don't read days-stale."""
    pts = freshen_current_week(pts)
    if replace_all:
        # Only the BASE series is being repaired — labeled variant rows from
        # printing_ingest are correct per-variant data and must survive.
        conn.execute(
            "DELETE FROM tcg_nm_history WHERE game=? AND product_id=? AND printing=''",
            (game, pid))
    conn.executemany(
        "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, date, price) "
        "VALUES (?,?,?,?)", [(game, pid, d, p) for d, p in pts])
    conn.execute("INSERT OR REPLACE INTO printing_pin_refreshed VALUES (?,?,?)",
                 (game, pid, now_iso()))
    conn.commit()


def probe_and_pin(conn, session, game, pid, baseline, source, delay, stats):
    """Probe one candidate; pin + repair when a close multi-variant exists."""
    variants = nm_variant_series(session, pid, delay)
    if len(variants) < 2:
        result = "single-variant" if variants else "no-nm-series"
        conn.execute("INSERT OR REPLACE INTO printing_checked VALUES (?,?,?,?)",
                     (game, pid, f"{result} ({source})", now_iso()))
        conn.commit()
        stats[result] += 1
        return
    best, best_dist = None, None
    for variant, (sku, pts) in variants.items():
        dist = abs(math.log(pts[0][1] / baseline))
        if best_dist is None or dist < best_dist:
            best, best_dist = (variant, sku, pts), dist
    variant, sku, pts = best
    if best_dist > math.log(CLOSE_FACTOR):
        conn.execute("INSERT OR REPLACE INTO printing_checked VALUES (?,?,?,?)",
                     (game, pid, f"no-close-variant ({source}, baseline "
                                 f"{baseline:.2f}, nearest {pts[0][1]:.2f})", now_iso()))
        conn.commit()
        stats["no-close-variant"] += 1
        print(f"  [{game}:{pid}] {len(variants)} variants but none near "
              f"${baseline:.2f} (closest {variant} ${pts[0][1]:.2f}) — review")
        return
    add_pin(game, pid, variant, sku, f"{source}; baseline {baseline:.2f}, "
                                     f"pinned {pts[0][1]:.2f}")
    write_series(conn, game, pid, pts, replace_all=True)
    stats["pinned"] += 1
    print(f"  [{game}:{pid}] PINNED '{variant}' (${pts[0][1]:.2f}, baseline "
          f"${baseline:.2f}; others: "
          + ", ".join(f"{v} ${p[0][1]:.2f}" for v, (_s, p) in variants.items()
                      if v != variant) + ")")


def seam_candidates(conn, games, ratio):
    """(game, pid, pc_baseline) where the current TCG price is >= ratio x the
    last PriceCharting-era ungraded price."""
    return conn.execute(
        f"""
        WITH pc_last AS (
          SELECT game, product_id, price FROM graded_price_history g
          WHERE printing='' AND grade='ungraded' AND date=(SELECT MAX(date) FROM graded_price_history
            WHERE game=g.game AND product_id=g.product_id AND grade='ungraded')
        ), tcg_now AS (
          SELECT game, product_id, price FROM tcg_nm_history t
          WHERE printing='' AND date=(SELECT MAX(date) FROM tcg_nm_history
            WHERE game=t.game AND product_id=t.product_id AND printing='')
        )
        SELECT p.game, p.product_id, p.price
        FROM pc_last p JOIN tcg_now t ON t.game=p.game AND t.product_id=p.product_id
        WHERE p.price >= 1 AND t.price / p.price >= ?
          AND p.game IN ({",".join("?" * len(games))})
        """, (ratio, *games)).fetchall()


def jump_candidates(conn, games, ratio):
    """(game, pid, trailing_median) where the latest stored price is >= ratio x
    the card's own trailing 30-day median (>= 5 prior points)."""
    rows = conn.execute(
        f"""
        SELECT game, product_id, date, price FROM tcg_nm_history
        WHERE printing='' AND date >= date('now', '-31 days')
          AND game IN ({",".join("?" * len(games))})
        ORDER BY game, product_id, date
        """, games).fetchall()
    series = collections.defaultdict(list)
    for game, pid, d, p in rows:
        series[(game, pid)].append(p)
    out = []
    for (game, pid), ps in series.items():
        prior = ps[:-1]
        if len(prior) >= 5:
            med = sorted(prior)[len(prior) // 2]
            if med > 0 and ps[-1] / med >= ratio:
                out.append((game, pid, med))
    return out


def refresh_pins(conn, session, games, days, limit, delay):
    pins = [(g, pid, v) for g, pid, v in load_pins() if g in games]
    if not pins:
        return
    last = dict(((g, p), r) for g, p, r in conn.execute(
        "SELECT game, product_id, refreshed_at FROM printing_pin_refreshed"))
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    due = sorted((p for p in pins
                  if datetime.fromisoformat(last.get((p[0], p[1]), "1970-01-01T00:00:00+00:00"))
                  .timestamp() < cutoff),
                 key=lambda p: last.get((p[0], p[1]), ""))[:limit]
    print(f"refreshing {len(due)} of {len(pins)} pinned card(s) "
          f"(rotation {days}d, limit {limit})")
    for game, pid, variant in due:
        variants = nm_variant_series(session, pid, delay)
        if variant not in variants:
            print(f"  [{game}:{pid}] pinned variant '{variant}' missing from "
                  f"response — kept prior series", file=sys.stderr)
            conn.execute("INSERT OR REPLACE INTO printing_pin_refreshed VALUES (?,?,?)",
                         (game, pid, now_iso()))
            conn.commit()
            continue
        # replace_all: the pinned variant fully OWNS the base series — a plain
        # append would leave behind rows from anything that wrote '' in error
        # (the 2026-08-08 rescue poisoning left exactly such rows).
        write_series(conn, game, pid, variants[variant][1], replace_all=True)


def main():
    ap = argparse.ArgumentParser(description="Pin multi-printing products to one variant.")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--pids", nargs="*", default=[], help="game:product_id tokens")
    ap.add_argument("--games", help="comma-separated subset (default: all)")
    ap.add_argument("--ratio", type=float, default=2.5)
    ap.add_argument("--refresh-days", type=float, default=7.0)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--delay", type=float, default=tp.DEFAULT_DELAY)
    ap.add_argument("--rpm", type=float, default=tp.DEFAULT_RPM)
    args = ap.parse_args()

    games = ([g.strip() for g in args.games.split(",")] if args.games
             else GAMES_DEFAULT)
    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    conn = sqlite3.connect(PC_DB, timeout=60)
    init_db(conn)
    session = tp.make_session()

    pinned = {(g, p) for g, p, _v in load_pins()}
    checked = {(g, p) for g, p in conn.execute(
        "SELECT game, product_id FROM printing_checked")}
    nm_only = load_nm_only()   # their PC baseline is DECLARED wrong — never pin
    skip = pinned | checked | nm_only

    stats = collections.Counter()
    todo = []
    for token in args.pids:
        g, _, pid = token.partition(":")
        todo.append((g, int(pid), None, "manual"))
    if args.audit:
        todo += [(g, pid, base, "seam-audit")
                 for g, pid, base in seam_candidates(conn, games, args.ratio)
                 if (g, pid) not in skip]
    if args.detect:
        todo += [(g, pid, base, "self-jump")
                 for g, pid, base in jump_candidates(conn, games, args.ratio)
                 if (g, pid) not in skip and (g, pid) not in {(t[0], t[1]) for t in todo}]
    if todo:
        print(f"probing {len(todo)} candidate(s)")
    for i, (game, pid, baseline, source) in enumerate(todo, 1):
        if baseline is None:   # manual token: baseline = last PC ungraded
            row = conn.execute(
                "SELECT price FROM graded_price_history WHERE game=? AND product_id=? AND printing='' "
                "AND grade='ungraded' ORDER BY date DESC LIMIT 1", (game, pid)).fetchone()
            if not row:
                print(f"  [{game}:{pid}] no PC-era baseline — skipped", file=sys.stderr)
                continue
            baseline = row[0]
        probe_and_pin(conn, session, game, pid, baseline, source, args.delay, stats)
        if i % 50 == 0:
            print(f"  ...{i}/{len(todo)} probed ({stats['pinned']} pinned)", flush=True)

    if args.refresh:
        refresh_pins(conn, session, games, args.refresh_days, args.limit, args.delay)

    if todo:
        print(f"done: {dict(stats)}; pins total "
              f"{len(load_pins())} -> {os.path.normpath(PINS_CSV)}")
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"pin_printings: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
