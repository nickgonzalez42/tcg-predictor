#!/usr/bin/env python3
"""
Import the Seller Portal pricing CSVs (export_tcg_csv.py) into the price
tables — the CSV-night replacement for the TCGplayer search crawl
(scrape_tcg_nm_prices.py). Plan of 2026-10-17: CSV most nights, the crawl
once a week for market depth and a cross-check.

Input: one-piece/tcg_exports/incoming/<game>_<YYYY-MM-DD>.csv — one row per
SKU (condition x printing) with TCG Market Price / TCG Low Price / TCG Low
Price With Shipping. The CSV's "TCGplayer Id" is the SKU id, NOT the product
id the catalog keys on, and the printing rides in the Condition column
("Near Mint Cold Foil" = Near Mint, Cold Foil) in exactly the vocabulary
printing_variants already uses (Normal / Foil / Holofoil / 1st Edition …).

Writes (pricecharting.db unless noted):
  tcg_nm_history    one BASE row (printing='') per product = the Near Mint
                    market price of the product's base printing, plus one
                    LABELED row per other priced NM printing — the same shape
                    printing_ingest.py produces, so unify / forecast /
                    nm-price consume it unchanged.
  tcg_sku_map       sku_id -> (product_id, printing, condition), resolved from
                    the catalog's stored listing SKUs, else set+name+number,
                    else set+name. Sticky: later nights are a lookup and
                    survive renames/renumbering.
  tcg_csv_base      the product's base printing, decided ONCE (pin >
                    printing_variants.is_base > closest to the last base
                    price within 2x > a fixed priority order) and kept —
                    continuity over cleverness. A night the base printing
                    has no market price writes no base row (like the crawl
                    on a null marketPrice) rather than switching printings.
  tcg_csv_imports   one row per (game, date) with counts; makes reruns
                    idempotent (--force re-imports).
  one-piece/tcg_csv.db  condition_history: every priced SKU row (all
                    conditions, all printings; market, low, low+shipping) —
                    the LP/MP/HP series and asks the crawl never had. A
                    separate file so the 8 GB pricecharting.db (rsynced
                    nightly) doesn't grow by ~600k rows a day.
  cards.printings / base_printing (catalog DBs): derived cache, stamped the
                    way printing_ingest stamps it.

Exclusions mirror the crawl: tcg_nm_blocklist.csv -> nothing written;
tcg_nm_only.csv and tcg_printing_pins.csv -> no base row (the detailed-
endpoint refresh / pin refresh own those series), labeled rows still land.

Guard: a game whose base-row count is under 70% of its previous import (or,
the first time, of the crawl's last night) is refused and its file left in
incoming/ — a half-empty export must never read as a market crash.

After a successful import the file is gzipped into tcg_exports/archive/ and
removed from incoming/.

Run:
    .venv/bin/python import_tcg_csv.py                 # every file in incoming/
    .venv/bin/python import_tcg_csv.py --game lorcana
    .venv/bin/python import_tcg_csv.py --dry-run       # full report, no writes
Exit: 0 every file imported; 1 a game refused/failed; 2 nothing to import.
"""

import argparse
import collections
import csv
import glob
import gzip
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, ALL_GAMES, db_path
from export_tcg_csv import INCOMING_DIR, EXPORT_DIR, PORTAL_LINES, pick_option
from scrape_tcg_nm_prices import PC_DB, TCG_NM_ONLY_CSV, NM_BLOCKLIST_CSV, PINS_CSV

ARCHIVE_DIR = os.path.join(EXPORT_DIR, "archive")
CSV_DB = os.path.join(BASE, "tcg_csv.db")

CONDITIONS = ["Near Mint", "Lightly Played", "Moderately Played",
              "Heavily Played", "Damaged"]
# When nothing else decides a base printing (no pin, no prior series, no
# ingest verdict): the plain printing first, premium finishes last.
BASE_PRIORITY = ["Normal", "Unlimited", "1st Edition", "Limited", "Holofoil",
                 "Foil", "Reverse Holofoil", "Cold Foil", "Unlimited Holofoil",
                 "1st Edition Holofoil"]
MIN_RATIO = 0.70          # refuse a game whose base rows fall below this x previous
CONTINUITY_X = 2.0        # "closest to last base price" only counts within 2x


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def norm_num(s):
    """Collector numbers: '004/102' == '4/102', 'OP01-001' == 'op01 1'."""
    return re.sub(r"\b0+(\d)", r"\1", norm(s))


def parse_condition(s):
    """'Near Mint Cold Foil' -> ('Near Mint', 'Cold Foil'); 'Damaged' ->
    ('Damaged', 'Normal'); sealed ('Unopened') -> None."""
    for c in CONDITIONS:
        if s == c:
            return c, "Normal"
        if s.startswith(c + " "):
            return c, s[len(c) + 1:].strip()
    return None


def money(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------

def init_pc(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tcg_sku_map (
            game       TEXT    NOT NULL,
            sku_id     INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL,
            condition  TEXT    NOT NULL,
            source     TEXT    NOT NULL,   -- listing | name+number | name
            mapped_at  TEXT    NOT NULL,
            PRIMARY KEY (game, sku_id)
        );
        CREATE INDEX IF NOT EXISTS idx_skumap_pid ON tcg_sku_map(game, product_id);
        CREATE TABLE IF NOT EXISTS tcg_csv_base (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL,
            source     TEXT    NOT NULL,   -- pin | ingest | continuity | priority | only
            decided_at TEXT    NOT NULL,
            PRIMARY KEY (game, product_id)
        );
        CREATE TABLE IF NOT EXISTS tcg_sku_resolve (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            n_skus     INTEGER,
            checked_at TEXT    NOT NULL,
            PRIMARY KEY (game, product_id)
        );
        CREATE TABLE IF NOT EXISTS tcg_csv_imports (
            game               TEXT NOT NULL,
            date               TEXT NOT NULL,
            file               TEXT,
            rows               INTEGER,
            products           INTEGER,
            matched            INTEGER,
            base_rows          INTEGER,
            labeled_rows       INTEGER,
            cond_rows          INTEGER,
            unmatched_products INTEGER,
            imported_at        TEXT,
            PRIMARY KEY (game, date)
        );
    """)
    conn.commit()


def init_csv_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS condition_history (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL,
            condition  TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            market     REAL,
            low        REAL,
            low_ship   REAL,
            PRIMARY KEY (game, product_id, printing, condition, date)
        );
        CREATE INDEX IF NOT EXISTS idx_cond_pid ON condition_history(game, product_id);
    """)
    conn.commit()


# ----------------------------------------------------------------------------
# Curated lists (same files the crawl honors)
# ----------------------------------------------------------------------------

def load_pids(path, game):
    out = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            out = {int(r["product_id"]) for r in csv.DictReader(f)
                   if r.get("game") == game and r.get("product_id")}
    return out


def load_pin_variants(game):
    out = {}
    if os.path.exists(PINS_CSV):
        with open(PINS_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game") == game and r.get("product_id"):
                    out[int(r["product_id"])] = r["variant"]
    return out


# ----------------------------------------------------------------------------
# Catalog side
# ----------------------------------------------------------------------------

# Display-name suffixes stamp_print_tags.py appends for special prints (the
# CSV carries the plain TCGplayer name, and the printing in its Condition).
STAMPED = re.compile(r"\s*\((1st edition|shadowless|unlimited|limited)\)\s*$", re.I)


def load_catalog(game):
    """pid -> (name, set, number, base_printing), the listing-SKU map, and
    the name keys used to resolve SKUs the listings didn't carry:
      key4 (set, name, number, rarity)  rarity collections list one card
                                        under several rarities with the same
                                        name AND number
      key3 (set, name, number)
      key2 (set, name)
    Names are keyed every way the CSV might spell them: the display name,
    the display name minus a stamped print suffix, and clean_name."""
    con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
    bp = "base_printing" if "base_printing" in cols else "NULL"
    cn = "clean_name" if "clean_name" in cols else "NULL"
    rows = con.execute(
        f"SELECT product_id, name, set_name, card_number, {bp}, raw_json, {cn}, rarity "
        "FROM cards").fetchall()
    con.close()
    cards, listing_skus = {}, {}
    key4, key3, key2 = (collections.defaultdict(set) for _ in range(3))
    sets = set()
    for pid, name, sn, num, base, rj, clean, rarity in rows:
        cards[pid] = (name, sn, num, base)
        sets.add(norm(sn))
        names = {norm(name), norm(STAMPED.sub("", name or "")), norm(clean)} - {""}
        for nm_ in names:
            key4[(norm(sn), nm_, norm_num(num), norm(rarity))].add(pid)
            key3[(norm(sn), nm_, norm_num(num))].add(pid)
            key2[(norm(sn), nm_)].add(pid)
        if rj and "productConditionId" in rj:
            try:
                for l in json.loads(rj).get("listings") or []:
                    s = l.get("productConditionId")
                    if s is not None:
                        listing_skus[int(float(s))] = pid
            except (ValueError, TypeError):
                pass
    return cards, listing_skus, (key4, key3, key2), sets


def load_last_base(conn, game):
    """pid -> (last base price, date), plus the crawl's best recent night
    (count, date) as the first import's guard baseline. "Best" = the date
    with the most products among each card's latest rows: the newest date
    alone can be a sliver (87 Magic rows dated today by the sales backfill
    on 2026-10-17), not the crawl's full night."""
    rows = conn.execute(
        "SELECT product_id, price, MAX(date) FROM tcg_nm_history "
        "WHERE game=? AND printing='' GROUP BY product_id", (game,)).fetchall()
    last = {pid: (p, d) for pid, p, d in rows}
    if not rows:
        return last, 0, None
    per_day = collections.Counter(d for _, _, d in rows)
    day, n = max(per_day.items(), key=lambda kv: kv[1])
    return last, n, day


def load_ingest_base(conn, game):
    return dict(conn.execute(
        "SELECT product_id, variant FROM printing_variants WHERE game=? AND is_base=1",
        (game,)).fetchall())


# ----------------------------------------------------------------------------
# One game
# ----------------------------------------------------------------------------

def choose_base(priced, sticky, pin, ingest, cat_base, last_price):
    """(printing, source). Order: pin > sticky > CONTINUITY (the printing
    whose price is closest to the card's last base price, within 2x) >
    printing-ingest's verdict > the only priced printing > priority list.
    Continuity outranks the ingest verdict on purpose: the base series is
    what the site charts and the model trains on, and the first dry run
    (2026-10-17) showed ingest's is_base switching Magic 'Oboro
    Breezecaller' from the $1.29 Normal series to a $303 Foil one."""
    if pin and pin in priced:
        return pin, "pin"
    if sticky:
        return sticky, "sticky"          # even if unpriced tonight: no switch
    if last_price and last_price > 0:
        best, dist = None, None
        for v, p in priced.items():
            d = abs(math.log(p / last_price))
            if dist is None or d < dist:
                best, dist = v, d
        if dist is not None and dist <= math.log(CONTINUITY_X):
            return best, "continuity"
    # A known base printing (ingest verdict / catalog stamp) is honored even
    # when it carries no market price tonight: the caller then writes no
    # base row, instead of switching to whatever printing IS priced
    # (Pokemon 'Lass (1st Edition)': 1st Edition unpriced one night would
    # otherwise have flipped the $100 series to the $9 Unlimited printing).
    hint = ingest or cat_base
    if hint:
        return hint, "ingest"
    if len(priced) == 1:
        return next(iter(priced)), "only"
    for v in BASE_PRIORITY:
        if v in priced:
            return v, "priority"
    return sorted(priced)[0], "priority"


def import_file(path, game, day, pc, csvdb, dry_run, force):
    """Returns (ok, summary dict)."""
    s = collections.Counter()
    already = pc.execute("SELECT base_rows FROM tcg_csv_imports WHERE game=? AND date=?",
                         (game, day)).fetchone()
    if already and not force:
        log(f"[{game}] {day} already imported ({already[0]} base rows) — skipping (--force to redo)")
        return True, None

    cards, listing_skus, (key4, key3, key2), cat_sets = load_catalog(game)
    sku_map = {sku: (pid, pr, cond) for sku, pid, pr, cond in pc.execute(
        "SELECT sku_id, product_id, printing, condition FROM tcg_sku_map WHERE game=?", (game,))}
    sticky = dict(pc.execute(
        "SELECT product_id, printing FROM tcg_csv_base WHERE game=?", (game,)).fetchall())
    ingest = load_ingest_base(pc, game)
    last, crawl_count, crawl_day = load_last_base(pc, game)
    pins = load_pin_variants(game)
    nm_only = load_pids(TCG_NM_ONLY_CSV, game)
    blocked = load_pids(NM_BLOCKLIST_CSV, game)
    prev = pc.execute("SELECT base_rows FROM tcg_csv_imports WHERE game=? AND date<? "
                      "ORDER BY date DESC LIMIT 1", (game, day)).fetchone()
    baseline = prev[0] if prev else crawl_count

    # --- read the CSV ---------------------------------------------------
    nm = collections.defaultdict(dict)         # pid -> {printing: market}
    cond_rows = []                             # (pid, printing, cond, market, low, low_ship)
    new_map = []                               # (sku, pid, printing, cond, source)
    unmatched = collections.Counter()          # (set, name) -> rows
    line_seen = collections.Counter()
    csv_products = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s["rows"] += 1
            line_seen[r["Product Line"]] += 1
            pc_ = parse_condition(r["Condition"])
            if pc_ is None:
                s["sealed_rows"] += 1
                continue
            cond, printing = pc_
            csv_products.add((r["Set Name"], r["Product Name"], r["Number"]))
            try:
                sku = int(r["TCGplayer Id"])
            except (TypeError, ValueError):
                s["bad_sku"] += 1
                continue
            pid, src = None, None
            if sku in sku_map:
                pid = sku_map[sku][0]
                src = "map"
            elif sku in listing_skus:
                pid, src = listing_skus[sku], "listing"
            else:
                k3 = (norm(r["Set Name"]), norm(r["Product Name"]), norm_num(r["Number"]))
                k4 = k3 + (norm(r["Rarity"]),)
                k2 = k3[:2]
                if len(key4.get(k4, ())) == 1:
                    pid, src = next(iter(key4[k4])), "name+number+rarity"
                elif len(key3.get(k3, ())) == 1:
                    pid, src = next(iter(key3[k3])), "name+number"
                elif len(key2.get(k2, ())) == 1:
                    pid, src = next(iter(key2[k2])), "name"
            if pid is None:
                unmatched[(r["Set Name"], r["Product Name"])] += 1
                s["unmatched_rows"] += 1
                if norm(r["Set Name"]) not in cat_sets:
                    s["unmatched_new_set_rows"] += 1
                continue
            s[f"via_{src}"] += 1
            if src != "map":
                new_map.append((sku, pid, printing, cond, src))
            if pid in blocked:
                s["blocked_rows"] += 1
                continue
            market = money(r["TCG Market Price"])
            low = money(r["TCG Low Price"])
            low_ship = money(r["TCG Low Price With Shipping"])
            if market is not None:
                cond_rows.append((pid, printing, cond, market, low, low_ship))
                if cond == "Near Mint":
                    nm[pid][printing] = market

    dominant = line_seen.most_common(1)[0][0] if line_seen else ""
    if pick_option([dominant], PORTAL_LINES[game]) is None:
        log(f"[{game}] REFUSED: file's product line is {dominant!r}")
        return False, None

    # --- base printing per product, then the rows -------------------------
    base_rows, labeled_rows, decisions = [], [], []
    stamps = []
    cont = collections.Counter()
    jumps = []                                 # (|log ratio|, pid, last, new, base, src, priced)
    for pid, priced in nm.items():
        cat_base = cards.get(pid, (None, None, None, None))[3]
        lp = last.get(pid, (None, None))[0]
        base, src = choose_base(priced, sticky.get(pid), pins.get(pid), ingest.get(pid),
                                cat_base, lp)
        if src != "sticky":
            decisions.append((pid, base, src))
            s[f"base_{src}"] += 1
        if base in priced and pid not in nm_only and pid not in pins:
            base_rows.append((pid, "", day, priced[base]))
            if lp:
                ratio = priced[base] / lp
                cont["within_10pct" if abs(math.log(ratio)) <= math.log(1.10) else
                     "over_2x" if ratio >= 2 else "under_half" if ratio <= 0.5 else "moved"] += 1
                if ratio >= 2 or ratio <= 0.5:
                    jumps.append((abs(math.log(ratio)), pid, lp, priced[base], base, src, priced))
        elif base not in priced:
            s["base_unpriced_tonight"] += 1
        for v, p in priced.items():
            if v != base:
                labeled_rows.append((pid, v, day, p))
        order = [base] + sorted(v for v in priced if v != base)
        stamps.append((json.dumps(order), base, pid))
    cat_missing = sum(1 for pid in cards if pid not in nm)

    guard_ok = baseline == 0 or len(base_rows) >= MIN_RATIO * baseline
    summary = dict(
        game=game, day=day, rows=s["rows"], products=len(csv_products),
        matched_products=len(nm), base_rows=len(base_rows), labeled_rows=len(labeled_rows),
        cond_rows=len(cond_rows), unmatched_products=len(unmatched), catalog_without_price=cat_missing,
        baseline=baseline, guard_ok=guard_ok)
    log(f"[{game}] {day}: {s['rows']:,} rows -> {len(nm):,} products priced "
        f"(via map {s['via_map']:,} / listing {s['via_listing']:,} / name+number+rarity "
        f"{s['via_name+number+rarity']:,} / name+number {s['via_name+number']:,} / name "
        f"{s['via_name']:,}); unmatched {len(unmatched):,} products ({s['unmatched_rows']:,} "
        f"rows, {s['unmatched_new_set_rows']:,} in sets the catalog lacks); blocked "
        f"{s['blocked_rows']:,} rows; catalog cards with no CSV price {cat_missing:,}")
    log(f"[{game}]   base rows {len(base_rows):,} (baseline {baseline:,}"
        f"{' crawl ' + crawl_day if not prev and crawl_day else ' previous import'}), "
        f"labeled {len(labeled_rows):,}, condition rows {len(cond_rows):,}, "
        f"base unpriced tonight {s['base_unpriced_tonight']:,}")
    if decisions:
        log(f"[{game}]   new base decisions {len(decisions):,}: " +
            ", ".join(f"{k[5:]} {v:,}" for k, v in s.items() if k.startswith("base_") and k != "base_unpriced_tonight"))
    if cont:
        log(f"[{game}]   continuity vs last base price: " +
            ", ".join(f"{k} {v:,}" for k, v in cont.most_common()))
    # The biggest jumps, so a wrong base-printing pick (or a real move) is
    # visible in the night's log instead of surfacing weeks later in a chart.
    for _, pid, lp, newp, base, src, priced in sorted(jumps, reverse=True)[:8]:
        name = (cards.get(pid) or ("?",))[0]
        log(f"[{game}]     jump {pid} {name[:38]!r}: {lp:.2f} -> {newp:.2f} "
            f"base={base} ({src}); NM printings {{"
            + ", ".join(f"{v}: {p:.2f}" for v, p in sorted(priced.items())) + "}")
    if unmatched:
        by_set = collections.Counter()
        for (sn, _), n in unmatched.items():
            by_set[sn] += 1
        log(f"[{game}]   unmatched products by set (top 5): " +
            "; ".join(f"{sn!r} {n}" for sn, n in by_set.most_common(5)))
    if not guard_ok:
        log(f"[{game}] REFUSED: {len(base_rows):,} base rows < {MIN_RATIO:.0%} of baseline "
            f"{baseline:,} — file left in incoming/ for inspection")
        return False, summary
    if dry_run:
        log(f"[{game}]   dry run — nothing written")
        return True, summary

    # --- writes -----------------------------------------------------------
    now = now_iso()
    pc.execute("BEGIN")
    pc.executemany(
        "INSERT OR REPLACE INTO tcg_nm_history (game, product_id, printing, date, price) "
        "VALUES (?,?,?,?,?)", [(game, *r) for r in base_rows + labeled_rows])
    pc.executemany(
        "INSERT OR IGNORE INTO tcg_sku_map VALUES (?,?,?,?,?,?,?)",
        [(game, sku, pid, pr, cond, src, now) for sku, pid, pr, cond, src in new_map])
    pc.executemany(
        "INSERT OR REPLACE INTO tcg_csv_base VALUES (?,?,?,?,?)",
        [(game, pid, base, src, now) for pid, base, src in decisions])
    pc.execute(
        "INSERT OR REPLACE INTO tcg_csv_imports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (game, day, os.path.basename(path), s["rows"], summary["products"], len(nm),
         len(base_rows), len(labeled_rows), len(cond_rows), len(unmatched), now))
    pc.execute("COMMIT")
    csvdb.execute("BEGIN")
    csvdb.executemany(
        "INSERT OR REPLACE INTO condition_history VALUES (?,?,?,?,?,?,?,?)",
        [(game, pid, pr, cond, day, m, lo, ls) for pid, pr, cond, m, lo, ls in cond_rows])
    csvdb.execute("COMMIT")
    card_con = sqlite3.connect(db_path(game), timeout=60)
    cols = {r[1] for r in card_con.execute("PRAGMA table_info(cards)")}
    for col in ("printings", "base_printing"):
        if col not in cols:
            card_con.execute(f"ALTER TABLE cards ADD COLUMN {col} TEXT")
    card_con.executemany(
        "UPDATE cards SET printings=?, base_printing=? WHERE product_id=?", stamps)
    card_con.commit()
    card_con.close()
    # Archive the file (gzip) and clear it from incoming/.
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    gz = os.path.join(ARCHIVE_DIR, os.path.basename(path) + ".gz")
    with open(path, "rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    os.remove(path)
    log(f"[{game}]   written; archived to {gz} ({os.path.getsize(gz) // 1_000_000} MB)")
    return True, summary


RESOLVE_RECHECK_DAYS = 30


def resolve_skus(pc, games, limit, rpm):
    """Map SKUs for catalog cards the CSV could not match by name — products
    TCGplayer renamed since the catalog crawl (Yu-Gi-Oh rarity collections'
    PCR/PUR/Ultra Rare variants, 2026-10-17) and anything else the name
    keys miss. One detailed-endpoint request per card returns every SKU id
    with its printing + condition, so the NEXT import matches those rows by
    SKU. Value-first, bounded per night; cards with no SKUs are memoized and
    re-probed after RESOLVE_RECHECK_DAYS. Same polite transport as every
    TCGplayer crawl here."""
    import tcg_pokemon_scraper as tp
    tp.RATE_LIMITER = tp.RateLimiter(rpm=rpm, min_interval=1.0)
    session = tp.make_session()
    cutoff = (datetime.now(timezone.utc).timestamp() - RESOLVE_RECHECK_DAYS * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
    budget = limit
    for game in games:
        if budget <= 0:
            break
        # "Mapped" means a NEAR MINT SKU is known: a stored listing SKU often
        # maps one condition of a renamed product (so the product isn't
        # unknown) while its NM SKU — the one the price series needs — is
        # still unmatched.
        mapped = {pid for (pid,) in pc.execute(
            "SELECT DISTINCT product_id FROM tcg_sku_map WHERE game=? AND condition='Near Mint'",
            (game,))}
        tried = {pid for (pid,) in pc.execute(
            "SELECT product_id FROM tcg_sku_resolve WHERE game=? AND checked_at > ?",
            (game, cutoff_iso))}
        con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
        cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
        order = "ORDER BY near_mint_price DESC" if "near_mint_price" in cols else ""
        cands = [pid for (pid,) in con.execute(f"SELECT product_id FROM cards {order}")
                 if pid not in mapped and pid not in tried]
        con.close()
        if not cands:
            log(f"[{game}] every catalog card has a Near Mint SKU mapped (or was probed "
                f"within {RESOLVE_RECHECK_DAYS} days) — nothing to resolve")
            continue
        take = cands[:budget]
        log(f"[{game}] resolving SKUs for {len(take):,} of {len(cands):,} unmapped cards")
        got = 0
        for pid in take:
            h = tp.fetch_price_history(session, pid, 1.0)
            series = (h.get("result") or h.get("results") or []) if h else []
            rows = []
            for s_ in series:
                sku = s_.get("skuId")
                if sku is None:
                    continue
                try:
                    sku = int(float(sku))
                except (TypeError, ValueError):
                    continue
                rows.append((game, sku, pid, s_.get("variant") or "Normal",
                             s_.get("condition") or "", "detailed", now_iso()))
            pc.execute("BEGIN")
            pc.executemany("INSERT OR IGNORE INTO tcg_sku_map VALUES (?,?,?,?,?,?,?)", rows)
            pc.execute("INSERT OR REPLACE INTO tcg_sku_resolve VALUES (?,?,?,?)",
                       (game, pid, len(rows), now_iso()))
            pc.execute("COMMIT")
            got += bool(rows)
        budget -= len(take)
        log(f"[{game}]   {got:,} cards gained SKUs, {len(take) - got:,} had none")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="all", help="all or one game")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--resolve", type=int, metavar="N",
                    help="instead of importing: map SKUs for up to N catalog cards the CSV "
                         "couldn't match (detailed endpoint, value-first)")
    ap.add_argument("--rpm", type=float, default=40, help="--resolve request ceiling per minute")
    ap.add_argument("--force", action="store_true", help="re-import an already-imported (game, date)")
    ap.add_argument("--non-fatal", action="store_true",
                    help="always exit 0 (refresh step: a refused game falls back to the crawl)")
    a = ap.parse_args()
    rc = run(a)
    if rc and a.non_fatal:
        log(f"(exit {rc} downgraded to 0: --non-fatal — the crawl covers any game without an import)")
        return 0
    return rc


def run(a):
    games = ALL_GAMES if a.game == "all" else [a.game]
    bad = [g for g in games if g not in GAMES]
    if bad:
        log(f"unknown game(s) {bad}")
        return 1

    if a.resolve:
        pc = sqlite3.connect(PC_DB, timeout=300)
        pc.isolation_level = None
        init_pc(pc)
        try:
            return resolve_skus(pc, games, a.resolve, a.rpm)
        finally:
            pc.close()

    files = []
    for path in sorted(glob.glob(os.path.join(INCOMING_DIR, "*.csv"))):
        m = re.match(r"([a-z]+)_(\d{4}-\d{2}-\d{2})\.csv$", os.path.basename(path))
        if m and m.group(1) in games:
            files.append((m.group(2), m.group(1), path))
    if not files:
        log(f"nothing to import in {INCOMING_DIR}")
        return 2
    files.sort()   # oldest day first, so a backlog lands in order

    pc = sqlite3.connect(PC_DB, timeout=300)
    pc.isolation_level = None     # explicit BEGIN/COMMIT above
    init_pc(pc)
    csvdb = sqlite3.connect(CSV_DB, timeout=120)
    csvdb.isolation_level = None
    init_csv_db(csvdb)
    ok_all = True
    for day, game, path in files:
        try:
            ok, _ = import_file(path, game, day, pc, csvdb, a.dry_run, a.force)
        except Exception as e:                   # noqa: BLE001
            log(f"[{game}] FAILED {os.path.basename(path)}: {type(e).__name__}: {e}")
            try:
                pc.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            try:
                csvdb.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            ok = False
        ok_all = ok_all and ok
    pc.close()
    csvdb.close()
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
