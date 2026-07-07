"""
Build one monthly price-history table per card, every condition tier.

UNGRADED is a true blend (the only tier TCGplayer prices):
  - deep past  : PriceCharting ungraded (2020-12 .. 2025-05), scaled to TCG level
  - recent/now : TCGplayer ungraded (2025-06 .. present)  [source of truth]
PriceCharting is calibrated to the TCGplayer scale via a per-card median ratio
over the overlapping months (returns are scale-invariant, so this mainly makes
the seam continuous). One-time backfill; only TCGplayer is appended going forward.

GRADED tiers (grade7..psa10, bgs10, cgc10, sgc10) carry through as PriceCharting's
REAL values (TCGplayer has no graded prices, so nothing to stitch and no scaling).

Output: pricecharting.db `price_history_unified` (game, product_id, grade, date, price, source).

Run after the graded-history crawl completes:  .venv/bin/python build_unified_history.py
"""

import collections
import os
import sqlite3
import statistics

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
MIN_OVERLAP = 3

SOURCES = {"pokemon": "pokemon_cards.db", "onepiece": "onepiece_cards.db"}


def tcg_monthly(card_db, condition="Near Mint"):
    """product_id -> {YYYY-MM: month-end market_price} for the dominant variant at a condition.

    TCGplayer's Market Price is already a rolling average of recent sales, so we take the
    latest weekly value in each month (month-end) rather than re-averaging the weeks — the
    latest month then equals the latest live price.
    """
    rows = sqlite3.connect(card_db).execute(
        "SELECT product_id, variant, bucket_date, market_price FROM price_history "
        "WHERE market_price IS NOT NULL AND condition = ?", (condition,)).fetchall()
    variants = collections.defaultdict(collections.Counter)
    for pid, v, _, _ in rows:
        variants[pid][v] += 1
    dominant = {pid: c.most_common(1)[0][0] for pid, c in variants.items()}
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for pid, v, d, p in rows:
        if v == dominant[pid]:
            acc[pid][d[:7]].append((d, p))  # (bucket_date, price)
    # month-end = price at the latest bucket_date within the month
    return {pid: {m: max(dps, key=lambda x: x[0])[1] for m, dps in mo.items()}
            for pid, mo in acc.items()}


def pc_history(game):
    """grade -> product_id -> {YYYY-MM: price} from the crawled monthly history."""
    # Ordered by date so when a month holds both a chart point and a later weekly
    # snapshot point, the most recent (month-end) value wins deterministically.
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT grade, product_id, date, price FROM graded_price_history WHERE game=? "
        "ORDER BY date", (game,)).fetchall()
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for grade, pid, d, p in rows:
        out[grade][pid][d[:7]] = p
    return out


def build_game(game, card_db):
    path = os.path.join(BASE, card_db)
    tcg = tcg_monthly(path, "Near Mint")   # ungraded series = Near Mint
    pc = pc_history(game)
    pc_ungraded = pc.get("ungraded", {})

    # per-card ungraded calibration factor + global fallback
    factors = {}
    for pid in tcg.keys() & pc_ungraded.keys():
        u = pc_ungraded[pid]
        ratios = [tcg[pid][m] / u[m] for m in tcg[pid].keys() & u.keys() if u[m] > 0]
        if len(ratios) >= MIN_OVERLAP:
            factors[pid] = statistics.median(ratios)
    global_factor = statistics.median(factors.values()) if factors else 1.0

    rows = []

    # UNGRADED: stitched onto the TCGplayer scale
    for pid in set(tcg) | set(pc_ungraded):
        tcg_m, pc_m = tcg.get(pid, {}), pc_ungraded.get(pid, {})
        first_tcg = min(tcg_m) if tcg_m else None
        factor = factors.get(pid, global_factor)
        series = {}
        for m, p in pc_m.items():
            if first_tcg is None or m < first_tcg:
                series[m] = (round(p * factor, 2), "pricecharting")
        for m, p in tcg_m.items():
            series[m] = (round(p, 2), "tcgplayer")
        for m, (price, src) in series.items():
            rows.append((game, pid, "ungraded", m + "-01", price, src))

    # OTHER RAW CONDITIONS: TCGplayer only (no PriceCharting history to blend)
    for key, cond in [("lp", "Lightly Played"), ("mp", "Moderately Played")]:
        for pid, months in tcg_monthly(path, cond).items():
            for m, p in months.items():
                rows.append((game, pid, key, m + "-01", round(p, 2), "tcgplayer"))

    # GRADED tiers: PriceCharting real values, passthrough
    graded_rows = 0
    for grade, by_pid in pc.items():
        if grade == "ungraded":
            continue
        for pid, months in by_pid.items():
            for m, p in months.items():
                rows.append((game, pid, grade, m + "-01", p, "pricecharting"))
                graded_rows += 1

    print(f"[{game}] cards: {len(set(tcg)|set(pc_ungraded))} | calibrated: {len(factors)} "
          f"| global factor {global_factor:.2f} | ungraded rows {len(rows)-graded_rows} | graded rows {graded_rows}")
    return rows


def main():
    all_rows = []
    for game, card_db in SOURCES.items():
        all_rows += build_game(game, card_db)

    conn = sqlite3.connect(PC_DB, timeout=60)
    conn.executescript(
        """
        DROP TABLE IF EXISTS price_history_unified;
        CREATE TABLE price_history_unified (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL    NOT NULL,
            source     TEXT    NOT NULL,
            PRIMARY KEY (game, product_id, grade, date)
        );
        """
    )
    conn.executemany("INSERT OR REPLACE INTO price_history_unified VALUES (?,?,?,?,?,?)", all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(PC_DB)} (price_history_unified)")


if __name__ == "__main__":
    main()
