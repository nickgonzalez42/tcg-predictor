#!/usr/bin/env python3
"""
The home page's two card lists, picked once a night (2026-11-09, user:
"the featured cards should be cards that made big market, historical moves
based on price (backed up by at least some volume) and then the expected
moves are cards that are predicted to move big (again, with some volume).
Decide the cards, then run a volume crawl on those cards, if they're not
viable, pick new cards, check volume, repeat until the cards are ready.")

  featured   the biggest PAST price moves on the market: per game, per window
             (1 month / 6 months / 1 year), the strongest gainer and loser,
             from card_trends (the nightly past-change table).
  expected   the biggest FORECAST moves: per game the strongest 1-month
             gainer and loser (a game with no 1-month model yet takes its
             strongest early call), plus a global ranking for the ticker,
             from the forecasts table, inside the same sanity band the API
             used (0.5x to 2.2x, never a low-confidence call).

Every pick needs sales behind it. Each round, the next few candidates of
every unfilled slot get a FRESH TCGplayer sales fetch: directly (the crawl's
own session and politeness budget) when the daytime sales crawler is not
running, otherwise by handing the list to that crawler through
showcase_watch.json, which it splices to the front of its queue, and waiting
for it to finish them. A candidate with fewer than MIN_SOLD Near Mint copies
sold in the last SOLD_DAYS is dropped and the slot tries the next one, up to
ROUNDS rounds. Candidates that are already fresh (fetched within
FETCH_FRESH_DAYS) are not refetched.

Writes the `showcase` table in predictions.db, replacing a kind's rows only
when it produced at least one; the nightly push carries it to prod and
MoverService serves it (the old on-request ranking is the fallback when the
table is empty). Never fatal: any error leaves the standing rows and exits 0.

Run:  .venv/bin/python pick_showcase.py [--kind featured|expected] [--no-fetch]
          [--rounds N] [--dry-run]
"""
import argparse
import fcntl
import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, market_games, db_path
import tcg_pokemon_scraper as tp
from backfill_tcg_nm_history import SALES_SCHEMA, record_sales

CARDS = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards")
PRED_DB = os.path.join(CARDS, "predictions.db")
PC_DB = os.path.join(CARDS, "pricecharting.db")
LOCK_PATH = os.path.join(BASE, ".sales_volume.lock")          # the sales crawler's lock
WATCH_FILE = os.path.join(BASE, "ml_data", "showcase_watch.json")

PRICE_FLOOR = 10.0        # dollars; a game with nothing at this floor falls back to FLOOR_FALLBACK
FLOOR_FALLBACK = 1.0
FRESH_DAYS = 7            # a featured card's latest price must be this recent
HIST_BAND = (-85.0, 400.0)   # past moves outside this read as data breaks, not markets
SANE_LO, SANE_HI = 0.5, 2.2  # forecast band, as MoverService
MIN_SOLD = 3              # Near Mint copies ...
SOLD_DAYS = 56            # ... in this many days (two crawl visits)
FETCH_FRESH_DAYS = 2      # a sales fetch this recent is not repeated
ROUNDS = 4
PER_ROUND = 3             # candidates tried per unfilled slot per round
GLOBAL_EXPECTED = 24      # the ticker/tiles ranking depth
WINDOWS = (("1m", "pct_1m", "1m"), ("6m", "pct_6m", "6m"), ("1y", "pct_1y", "12m"))
RPM = 40.0
DELAY = 1.0
WAIT_PER_CARD_S = 4       # hand-off wait budget per card (40 rpm = 1.5 s, plus slack)
WAIT_MIN_S = 240

PRIZE_LIKE = ("%tournament%", "%winner%", "%1st place%", "%2nd place%", "%3rd place%",
              "%treasure cup%", "%regional%", "%finalist%", "%finals%")

SCHEMA = """
CREATE TABLE IF NOT EXISTS showcase (
    kind        TEXT NOT NULL,              -- featured | expected
    game        TEXT NOT NULL,
    product_id  INTEGER NOT NULL,
    printing    TEXT NOT NULL DEFAULT '',   -- '' = base printing
    slot        TEXT NOT NULL,              -- featured: 1m | 6m | 1y ; expected: game | global
    side        TEXT,                       -- up | down
    horizon     TEXT,                       -- forecast horizon shown with the card
    rank        INTEGER NOT NULL,           -- order within the kind
    pct         REAL,                       -- the move that earned the pick (%)
    price       REAL,
    sold        INTEGER,                    -- Near Mint copies sold in the last SOLD_DAYS
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (kind, game, product_id, printing, slot, side)
);
"""


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
def showable_cards(game):
    """product_id -> name for cards with art on disk and a name that is not a
    tournament prize card (those trade at outlier prices)."""
    con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    where = " AND ".join(f"lower(name) NOT LIKE '{p}'" for p in PRIZE_LIKE)
    rows = con.execute(
        "SELECT product_id, name FROM cards "
        f"WHERE image_path IS NOT NULL AND image_path != '' AND name IS NOT NULL AND {where}"
    ).fetchall()
    con.close()
    return dict(rows)


def featured_candidates(pred, game, ok):
    """{(window, side): [cand, ...]} ranked by the size of the past move.
    cand = dict(game, pid, printing '', pct, price, horizon)."""
    today = datetime.now(timezone.utc).date()
    fresh = (today - timedelta(days=FRESH_DAYS)).isoformat()
    rows = pred.execute(
        "SELECT product_id, latest_price, latest_date, pct_1m, pct_6m, pct_1y FROM card_trends "
        "WHERE game=? AND grade='ungraded' AND latest_price IS NOT NULL AND latest_date >= ?",
        (game, fresh)).fetchall()
    rows = [r for r in rows if r[0] in ok]
    floor = PRICE_FLOOR if any(r[1] >= PRICE_FLOOR for r in rows) else FLOOR_FALLBACK
    out = {}
    for window, col, horizon in WINDOWS:
        idx = {"pct_1m": 3, "pct_6m": 4, "pct_1y": 5}[col]
        pool = [r for r in rows if r[1] >= floor and r[idx] is not None
                and HIST_BAND[0] <= r[idx] <= HIST_BAND[1] and abs(r[idx]) >= 0.5]
        ups = sorted((r for r in pool if r[idx] > 0), key=lambda r: -r[idx])
        downs = sorted((r for r in pool if r[idx] < 0), key=lambda r: r[idx])
        for side, lst in (("up", ups), ("down", downs)):
            out[(window, side)] = [dict(game=game, pid=r[0], printing="", pct=r[idx],
                                        price=r[1], horizon=horizon) for r in lst]
    return out


def expected_candidates(pred, game, ok):
    """{side: [cand, ...]} for the game's 1-month forecasts (falling back to
    the early tier when the game has none), ranked by the size of the call."""
    def pull(horizon, allow_low):
        q = ("SELECT product_id, printing, base_price, forecast_price, confidence FROM forecasts "
             "WHERE game=? AND target='ungraded' AND horizon=? AND base_price >= ? "
             "AND forecast_price >= base_price * ? AND forecast_price <= base_price * ?")
        rows = pred.execute(q, (game, horizon, FLOOR_FALLBACK, SANE_LO, SANE_HI)).fetchall()
        rows = [r for r in rows if r[0] in ok and (allow_low or r[4] != "low")]
        floor = PRICE_FLOOR if any(r[2] >= PRICE_FLOOR for r in rows) else FLOOR_FALLBACK
        rows = [r for r in rows if r[2] >= floor]
        return [dict(game=game, pid=r[0], printing=r[1] or "", pct=(r[3] / r[2] - 1) * 100,
                     price=r[2], horizon=horizon) for r in rows]
    cands = pull("1m", False) or pull("1m", True)
    if not cands:
        cands = pull("launch1m", True)
    ups = sorted((c for c in cands if c["pct"] > 0), key=lambda c: -c["pct"])
    downs = sorted((c for c in cands if c["pct"] < 0), key=lambda c: c["pct"])
    return {"up": ups, "down": downs}


# ---------------------------------------------------------------------------
# Sales: fresh fetch, then the volume test
# ---------------------------------------------------------------------------
class Sales:
    def __init__(self, pc, no_fetch=False):
        self.pc = pc
        self.no_fetch = no_fetch
        self.session = None
        self.fetched = 0
        pc.executescript(SALES_SCHEMA)

    def scanned_at(self, pairs):
        out = {}
        for game in {g for g, _ in pairs}:
            out.update({(game, pid): f or "" for pid, f in self.pc.execute(
                "SELECT product_id, fetched_at FROM tcg_sales_scan WHERE game=?", (game,))})
        return out

    def ensure_fresh(self, pairs):
        """Fetch sales for the pairs whose last fetch is older than
        FETCH_FRESH_DAYS: directly under the crawler's lock, or through the
        running crawler's watch list."""
        if self.no_fetch or not pairs:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=FETCH_FRESH_DAYS)).isoformat()
        scanned = self.scanned_at(pairs)
        need = [p for p in dict.fromkeys(pairs) if scanned.get(p, "") < cutoff]
        if not need:
            log(f"  sales: all {len(pairs)} candidate(s) fetched within {FETCH_FRESH_DAYS} days")
            return
        lock = open(LOCK_PATH, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            self.via_crawler(need)
            return
        try:
            self.fetch_direct(need)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def fetch_direct(self, need):
        if self.session is None:
            tp.RATE_LIMITER = tp.RateLimiter(rpm=RPM, min_interval=DELAY)
            self.session = tp.make_session()
        log(f"  sales: fetching {len(need)} card(s) directly")
        fails = 0
        for game, pid in need:
            try:
                h = tp.fetch_price_history(self.session, pid, DELAY)
            except Exception as e:   # one bad card never stops the pick
                fails += 1
                log(f"    {game}/{pid}: fetch failed ({type(e).__name__})")
                continue
            if h is None:
                fails += 1
                continue
            for attempt in range(5):
                try:
                    record_sales(self.pc, game, pid, h)
                    self.pc.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower() or attempt == 4:
                        raise
                    self.pc.rollback()
                    time.sleep(5)
            self.fetched += 1
        if fails:
            log(f"  sales: {fails} fetch(es) failed; those cards are judged on what is stored")

    def via_crawler(self, need):
        """The daytime crawler holds the lock: give it the list and wait."""
        made = datetime.now(timezone.utc).isoformat()
        with open(WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump({"made": made, "for": datetime.now(timezone.utc).date().isoformat(),
                       "cards": [{"game": g, "pid": p} for g, p in need]}, f)
        budget = max(WAIT_MIN_S, WAIT_PER_CARD_S * len(need))
        log(f"  sales: the sales crawler is running; handed it {len(need)} card(s) "
            f"(showcase_watch.json), waiting up to {budget // 60} min")
        t0 = time.time()
        while time.time() - t0 < budget:
            time.sleep(15)
            scanned = self.scanned_at(need)
            left = [p for p in need if scanned.get(p, "") < made]
            if not left:
                log(f"  sales: crawler finished all {len(need)} in {int(time.time() - t0)} s")
                self.fetched += len(need)
                return
        log(f"  sales: crawler finished {len(need) - len(left)} of {len(need)} in time; "
            f"the rest are judged on what is stored")
        self.fetched += len(need) - len(left)

    def sold(self, pairs):
        """(game, pid) -> Near Mint copies sold in the last SOLD_DAYS."""
        since = (datetime.now(timezone.utc).date() - timedelta(days=SOLD_DAYS)).isoformat()
        out = {}
        for game, pid in dict.fromkeys(pairs):
            q = self.pc.execute(
                "SELECT COALESCE(SUM(quantity_sold), 0) FROM tcg_sales_history "
                "WHERE game=? AND product_id=? AND condition='Near Mint' AND bucket_date >= ?",
                (game, pid, since)).fetchone()[0]
            out[(game, pid)] = int(q or 0)
        return out


# ---------------------------------------------------------------------------
# Filling slots
# ---------------------------------------------------------------------------
def fill_slots(slots, sales, rounds, taken=None):
    """slots: {key: [cand, ...]} ranked. Each round the next PER_ROUND
    candidates of every unfilled slot are fetched and tested together; the
    first viable one fills the slot. Returns {key: cand} with cand['sold'].
    `taken` (set of (game, pid)) keeps one card from filling several slots
    of the same game when alternatives exist."""
    taken = taken if taken is not None else set()
    filled, cursor = {}, {k: 0 for k in slots}
    for rnd in range(1, rounds + 1):
        batch = {}
        for key, cands in slots.items():
            if key in filled:
                continue
            tried = []
            while len(tried) < PER_ROUND and cursor[key] < len(cands):
                c = cands[cursor[key]]
                cursor[key] += 1
                if (c["game"], c["pid"]) in taken:
                    continue
                tried.append(c)
            if tried:
                batch[key] = tried
        if not batch:
            break
        pairs = [(c["game"], c["pid"]) for cs in batch.values() for c in cs]
        log(f"  round {rnd}: {len(batch)} slot(s) open, {len(set(pairs))} candidate(s)")
        sales.ensure_fresh(pairs)
        sold = sales.sold(pairs)
        for key, cands in batch.items():
            for c in cands:
                n = sold[(c["game"], c["pid"])]
                if n >= MIN_SOLD and (c["game"], c["pid"]) not in taken:
                    filled[key] = dict(c, sold=n)
                    taken.add((c["game"], c["pid"]))
                    break
    return filled


def pick_featured(pred, sales, games, rounds):
    rows = []
    for game in games:
        ok = showable_cards(game)
        slots = featured_candidates(pred, game, ok)
        log(f"[{game}] featured: " + ", ".join(f"{w}/{s}:{len(c)}" for (w, s), c in slots.items()))
        filled = fill_slots(slots, sales, rounds)
        for (window, side), c in filled.items():
            rows.append(("featured", game, c["pid"], c["printing"], window, side, c["horizon"],
                         c["pct"], c["price"], c["sold"]))
        missing = [f"{w}/{s}" for (w, s) in slots if (w, s) not in filled]
        if missing:
            log(f"[{game}] featured: no viable card for {', '.join(missing)}")
    order = {w: i for i, (w, _c, _h) in enumerate(WINDOWS)}
    rows.sort(key=lambda r: (order[r[4]], games.index(r[1]), r[5] != "up"))
    return rows


def pick_expected(pred, sales, games, rounds):
    rows = []
    per_game = {}
    all_up, all_down = [], []
    for game in games:
        ok = showable_cards(game)
        cands = expected_candidates(pred, game, ok)
        per_game[game] = cands
        all_up.extend(cands["up"]); all_down.extend(cands["down"])
        log(f"[{game}] expected: up {len(cands['up'])}, down {len(cands['down'])}"
            + (f" ({cands['up'][0]['horizon']})" if cands["up"] else ""))
    # Per-game guarantee: strongest gainer + loser each.
    slots = {}
    for game, cands in per_game.items():
        for side in ("up", "down"):
            if cands[side]:
                slots[(game, side)] = cands[side]
    taken = set()
    filled = fill_slots(slots, sales, rounds, taken)
    for (game, side), c in filled.items():
        rows.append(("expected", game, c["pid"], c["printing"], "game", side, c["horizon"],
                     c["pct"], c["price"], c["sold"]))
    # Global ranking, alternating up/down like the ticker.
    all_up.sort(key=lambda c: -c["pct"]); all_down.sort(key=lambda c: c["pct"])
    merged = []
    for i in range(max(len(all_up), len(all_down))):
        if i < len(all_up): merged.append(all_up[i])
        if i < len(all_down): merged.append(all_down[i])
    merged = [c for c in merged if (c["game"], c["pid"]) not in taken]
    kept, pos = [], 0
    for rnd in range(1, rounds + 1):
        need = GLOBAL_EXPECTED - len(kept)
        if need <= 0 or pos >= len(merged):
            break
        batch = merged[pos:pos + need + PER_ROUND]
        pos += len(batch)
        log(f"  global round {rnd}: testing {len(batch)} candidate(s) for {need} slot(s)")
        sales.ensure_fresh([(c["game"], c["pid"]) for c in batch])
        sold = sales.sold([(c["game"], c["pid"]) for c in batch])
        for c in batch:
            n = sold[(c["game"], c["pid"])]
            if n >= MIN_SOLD and (c["game"], c["pid"]) not in taken and len(kept) < GLOBAL_EXPECTED:
                kept.append(dict(c, sold=n)); taken.add((c["game"], c["pid"]))
    for c in kept:
        rows.append(("expected", c["game"], c["pid"], c["printing"], "global",
                     "up" if c["pct"] > 0 else "down", c["horizon"], c["pct"], c["price"], c["sold"]))
    return rows


def write(pred, kind, rows, dry_run):
    if not rows:
        log(f"{kind}: nothing picked; the standing rows stay")
        return
    if dry_run:
        for r in rows[:12]:
            log(f"  {kind} {r[1]}/{r[2]} {r[4]}/{r[5]} {r[6]} {r[7]:+.0f}% ${r[8]:.2f} sold {r[9]}")
        log(f"{kind}: {len(rows)} row(s) (dry run, not written)")
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pred.execute("BEGIN")
    pred.execute("DELETE FROM showcase WHERE kind=?", (kind,))
    pred.executemany(
        "INSERT OR REPLACE INTO showcase (kind, game, product_id, printing, slot, side, horizon, "
        "rank, pct, price, sold, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], i, r[7], r[8], r[9], now) for i, r in enumerate(rows)])
    pred.execute("COMMIT")
    log(f"{kind}: {len(rows)} row(s) written")


def main():
    ap = argparse.ArgumentParser(description="Pick the home page's featured and expected cards")
    ap.add_argument("--kind", choices=["featured", "expected"], help="only this list")
    ap.add_argument("--no-fetch", action="store_true", help="judge volume on stored sales only")
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pred-db", default=PRED_DB, help=argparse.SUPPRESS)
    ap.add_argument("--pc-db", default=PC_DB, help=argparse.SUPPRESS)
    args = ap.parse_args()
    t0 = time.time()
    pred = sqlite3.connect(args.pred_db, timeout=120, isolation_level=None)
    pred.execute("PRAGMA busy_timeout=120000")
    pred.executescript(SCHEMA)
    pc = sqlite3.connect(args.pc_db, timeout=600)
    pc.execute("PRAGMA busy_timeout=600000")
    sales = Sales(pc, no_fetch=args.no_fetch)
    games = market_games()
    if args.kind in (None, "featured"):
        write(pred, "featured", pick_featured(pred, sales, games, args.rounds), args.dry_run)
    if args.kind in (None, "expected"):
        write(pred, "expected", pick_expected(pred, sales, games, args.rounds), args.dry_run)
    pc.commit(); pc.close(); pred.close()
    log(f"pick_showcase: done in {int(time.time() - t0)} s, {sales.fetched} sales fetch(es)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # never let the showcase take the nightly down
        print(f"pick_showcase: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
