#!/usr/bin/env python3
"""
Nightly TCGplayer-vs-PriceCharting price integrity sweep (2026-08-16).

Compares each high-value card's latest TCGplayer NM price against its latest
PriceCharting ungraded sales price and writes every >= 2x divergence to
ml_data/nm_pc_divergence_review.csv for HUMAN review. No auto-action: the two
failure classes are mirror images and ratios alone cannot separate them --
  bogus_listing  a lone absurd TCGplayer listing (Umbreon H30, $4,999.99 vs
                 ~$1,800 sales) -> fix = tcg_nm_blocklist.csv (PC owns ungraded)
  pc_junk        PC carrying junk for serialized/promo/UTR cards it barely
                 tracks ($2.45 for a $1,850 Treasure Cup card) -> fix =
                 price_corrections.csv or nothing (TCGplayer is already right)
The `class` column is a HINT from the PSA10-ladder heuristic (PC junk pages
are often internally consistent, so trust it only as triage).

Pure SQL over data already collected -- no crawling. Non-fatal in the nightly.

Run:  .venv/bin/python sweep_nm_vs_pc.py [--min-price 100] [--ratio 2.0]
"""

import argparse
import csv
import os
import sqlite3

from _paths import DATA_DIR as BASE
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
OUT_CSV = os.path.join(BASE, "ml_data", "nm_pc_divergence_review.csv")
BLOCKLIST_CSV = os.path.join(BASE, "ml_data", "tcg_nm_blocklist.csv")


REVIEWED_CSV = os.path.join(BASE, "ml_data", "nm_pc_reviewed.csv")

# Policy (user decision 2026-08-17): for SERIALIZED cards, TCGplayer is the
# authoritative source — PC barely tracks them and carries junk (a $3.90
# "price" on a $575 serialized Lurrus). They never enter the review queue.
# Markers only, not bare "serial" — yugioh's "Serial Spell" is a card name.
SERIALIZED_MARKERS = ("Serial Numbered", "Serial Number]", "(Serialized)")


def serialized_pids(game, pids):
    """Subset of pids whose card name carries a serialized marker."""
    from games import db_path
    conn = sqlite3.connect(db_path(game), timeout=30)
    q = ",".join("?" * len(pids))
    out = set()
    for pid, name in conn.execute(
            f"SELECT product_id, name FROM cards WHERE product_id IN ({q})", list(pids)):
        if name and any(m in name for m in SERIALIZED_MARKERS):
            out.add(pid)
    return out


def blocklisted():
    if not os.path.exists(BLOCKLIST_CSV):
        return set()
    with open(BLOCKLIST_CSV, newline="", encoding="utf-8") as f:
        return {(r["game"], int(r["product_id"])) for r in csv.DictReader(f)
                if r.get("product_id")}


def reviewed_prices():
    """(game, pid) -> tcg price at review time (nm_price_review.py memos).
    A reviewed card only re-flags if its TCG price drifts >20% from this."""
    out = {}
    if os.path.exists(REVIEWED_CSV):
        with open(REVIEWED_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["decision"] != "skip":
                    try:
                        out[(r["game"], int(r["product_id"]))] = float(r["tcg_nm"])
                    except ValueError:
                        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-price", type=float, default=100.0)
    ap.add_argument("--ratio", type=float, default=2.0)
    ap.add_argument("--pc-fresh-days", type=int, default=45)
    args = ap.parse_args()

    conn = sqlite3.connect(PC_DB, timeout=60)
    # Liquidity per card (user policy 2026-08-17): TCGplayer is authoritative
    # WHENEVER its price is sales-backed, and a sales-backed price MOVES — a
    # price frozen for weeks means no sales, i.e. a bare listing. Distinct
    # prices over the trailing 30 days: >1 = liquid (auto-resolve, TCG right);
    # 1 = frozen (the Umbreon class — the review queue).
    liquidity = dict(conn.execute("""
        SELECT game || ':' || product_id, COUNT(DISTINCT price)
        FROM tcg_nm_history
        WHERE printing='' AND date >= date('now', '-30 day')
        GROUP BY game, product_id""").fetchall())
    rows = conn.execute("""
WITH tcg AS (
  SELECT game, product_id, price, date,
         ROW_NUMBER() OVER (PARTITION BY game, product_id ORDER BY date DESC) rn
  FROM tcg_nm_history WHERE printing=''),
pcu AS (
  SELECT game, product_id, price, date,
         ROW_NUMBER() OVER (PARTITION BY game, product_id ORDER BY date DESC) rn
  FROM graded_price_history
  WHERE grade='ungraded' AND printing='' AND price > 0
    AND date >= date('now', ?1)),
g10 AS (
  SELECT game, product_id, price,
         ROW_NUMBER() OVER (PARTITION BY game, product_id ORDER BY date DESC) rn
  FROM graded_price_history
  WHERE grade='psa10' AND printing='' AND price > 0
    AND date >= date('now', '-90 day'))
SELECT t.game, t.product_id, t.price, t.date, p.price, p.date, COALESCE(x.price, 0)
FROM tcg t
JOIN pcu p ON p.game=t.game AND p.product_id=t.product_id AND p.rn=1
LEFT JOIN g10 x ON x.game=t.game AND x.product_id=t.product_id AND x.rn=1
WHERE t.rn=1 AND ((t.price >= ?2 AND t.price / p.price >= ?3)
               OR (p.price >= ?2 AND p.price / t.price >= ?3))""",
        (f"-{args.pc_fresh_days} day", args.min_price, args.ratio)).fetchall()

    skip = blocklisted()
    seen = reviewed_prices()
    by_game = {}
    for g, pid, *_ in rows:
        by_game.setdefault(g, set()).add(pid)
    serialized = {g: serialized_pids(g, p) for g, p in by_game.items()}
    n_serialized = n_liquid = 0
    out = []
    for g, pid, T, td, P, pd, G in rows:
        if (g, pid) in skip:
            continue
        if pid in serialized.get(g, ()):
            n_serialized += 1
            continue   # policy: TCGplayer authoritative for serialized cards
        # Deflated direction (2026-08-22, Timetwister $19.99 vs PC $4,552 and
        # Sanji CS2023 $37 vs $106): a junk TCG listing can sit BELOW the real
        # market too. Stricter liquidity bar there (>=3 distinct prices/30d)
        # — lowball markets show trickle movement that isn't a real market.
        deflated = P > T
        if liquidity.get(f"{g}:{pid}", 0) > (2 if deflated else 1):
            n_liquid += 1
            continue   # price moves = sales-backed = TCG right
        base = seen.get((g, pid))
        if base and base > 0 and abs(T / base - 1) <= 0.20:
            continue   # human already ruled on this price level
        cls = "deflated_listing" if deflated else "stale_listing"
        out.append((cls, g, pid, round(T, 2), td, round(P, 2), pd,
                    round(G, 2) or "", round(max(T, P) / min(T, P), 1)))

    out.sort(key=lambda r: -r[3])
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class_hint", "game", "product_id", "tcg_nm", "tcg_date",
                    "pc_ungraded", "pc_date", "pc_psa10", "ratio"])
        w.writerows(out)
    print(f"nm-vs-pc sweep: {len(out)} frozen-price suspect(s) >= {args.ratio}x "
          f"-> {os.path.basename(OUT_CSV)} "
          f"(auto-resolved: {n_liquid} liquid, {n_serialized} serialized — TCGplayer authoritative)")


if __name__ == "__main__":
    # Non-fatal: an integrity-report hiccup must never fail the nightly.
    import sys
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"sweep_nm_vs_pc: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
    sys.exit(0)
