#!/usr/bin/env python3
"""Diff the scraped console CSVs against the paid bulk CSVs — the daily
shadow-mode gate before cutover. For each game: ungraded coverage vs the paid
set, price-agreement distribution (exact / within $0.01 / disagree), and the
new-card queue (pc_ids the scrape saw that the paid CSV / our map doesn't).

Ground truth = the paid pricecharting_{game}.csv still being downloaded nightly.
Run after scrape_pc_prices.py:  .venv/bin/python compare_pc_prices.py --games digimon
"""
import argparse
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES
from scrape_pc_prices import bulk_games, PC_DB


def matched_pcids(game):
    """pc_ids we actually track (matched to our catalog) — the denominator that
    matters for cutover. Paid-priced products absent from this set are things
    the site never showed anyway, not a coverage loss."""
    conn = sqlite3.connect(PC_DB, timeout=30)
    ids = {pid for (pid,) in conn.execute(
        "SELECT pc_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL", (game,))}
    conn.close()
    return ids


def money(s):
    s = (s or "").strip().lstrip("$").replace(",", "")
    return round(float(s), 2) if s else None


def load(path, col="loose-price"):
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["id"]): money(r[col]) for r in csv.DictReader(f) if r.get("id")}


def compare_game(game):
    paid = load(os.path.join(BASE, GAMES[game]["pc_csv"]))
    scraped = load(os.path.join(BASE, GAMES[game]["pc_csv"].replace(".csv", "_scraped.csv")))
    if paid is None or scraped is None:
        print(f"[{game}] missing CSV (paid={paid is not None}, scraped={scraped is not None}) — skip")
        return

    paid_priced = {k for k, v in paid.items() if v is not None}
    both = paid_priced & set(scraped)
    exact = within = disagree = 0
    worst = []
    for pid in both:
        pv, sv = paid[pid], scraped[pid]
        if sv is None:
            disagree += 1; worst.append((pid, pv, sv, "scraped-null")); continue
        if pv == sv:
            exact += 1
        elif abs(pv - sv) <= 0.01:
            within += 1
        else:
            disagree += 1
            worst.append((pid, pv, sv, f"{abs(pv-sv)/max(pv,0.01)*100:.0f}%"))

    missed = paid_priced - set(scraped)         # priced by paid, absent from scrape
    newq = set(scraped) - set(paid)             # scraped but not in paid (new-card queue)
    agree = 100.0 * (exact + within) / len(both) if both else 0.0

    # Cutover-relevant coverage is over the products we actually track, not the
    # whole paid CSV: a paid-priced product we never matched was never on the
    # site, so its absence from the scrape isn't a loss.
    tracked = matched_pcids(game)
    tracked_priced = paid_priced & tracked
    tracked_covered = both & tracked
    real_gap = (missed & tracked)               # tracked, priced by paid, not scraped
    untracked_missed = missed - tracked
    cov = 100.0 * len(tracked_covered) / len(tracked_priced) if tracked_priced else 0.0

    print(f"\n=== {game} ===")
    print(f"  cutover coverage (tracked products): {len(tracked_covered)}/{len(tracked_priced)} "
          f"({cov:.2f}%) | real gap: {len(real_gap)} | untracked paid-only (not a loss): {len(untracked_missed)}")
    print(f"  ungraded agreement: exact={exact} within-$0.01={within} disagree={disagree} "
          f"({agree:.2f}% agree) | new-card queue: {len(newq)}")
    if worst:
        print("  largest disagreements (pid, paid, scraped, delta):")
        for w in sorted(worst, key=lambda x: -(x[1] or 0))[:6]:
            print(f"    {w}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", help="comma-separated (default: all bulk-CSV games)")
    args = ap.parse_args()
    for game in (args.games.split(",") if args.games else bulk_games()):
        compare_game(game)


if __name__ == "__main__":
    main()
