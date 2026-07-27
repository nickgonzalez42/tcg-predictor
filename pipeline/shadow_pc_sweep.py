#!/usr/bin/env python3
"""Nightly SHADOW sweep: scrape every bulk-CSV game's console prices and diff
them against that night's paid CSV, so the day-over-day agreement accrues while
we still pay for the price-guide API. This is the pre-cutover observability
step — nothing downstream reads the _scraped CSVs it writes.

Appends a dated section to ~/Library/Logs/tcg-predictor/pc_shadow.log (the
morning-review trend) and echoes the same to stdout (the nightly refresh log).

ALWAYS exits 0: a scrape hiccup must never fail the nightly and block the model
or report. Delete this step at cutover, when scrape_pc_prices overwrites the
live CSV (--out-suffix '') and download_pricecharting.py is dropped.
"""
import contextlib
import datetime
import io
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_pc_prices as diff
import scrape_pc_prices as sweep

LOG = os.path.expanduser("~/Library/Logs/tcg-predictor/pc_shadow.log")


def run():
    games = sweep.bulk_games()
    buf = io.StringIO()

    # 1. scrape every bulk game's console pages into shadow (_scraped) CSVs.
    try:
        conn = sqlite3.connect(sweep.PC_DB, timeout=60)
        try:
            for game in games:
                with contextlib.redirect_stdout(buf):
                    sweep.scrape_game(conn, game, "_scraped", None)
        finally:
            conn.close()
    except Exception as e:                       # noqa: BLE001 — observability step
        print(f"[pc-shadow] scrape error (non-fatal): {e}", file=buf)

    # 2. diff each shadow CSV against tonight's paid CSV.
    try:
        with contextlib.redirect_stdout(buf):
            for game in games:
                diff.compare_game(game)
    except Exception as e:                        # noqa: BLE001 — observability step
        print(f"[pc-shadow] compare error (non-fatal): {e}", file=buf)

    return buf.getvalue()


def main():
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        body = run()
    except Exception as e:                         # noqa: BLE001 — never fail the nightly
        body = f"[pc-shadow] unexpected error (non-fatal): {e}\n"

    section = f"\n{'='*70}\n# shadow sweep {stamp}\n{'='*70}\n{body}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(section)
    except Exception as e:                         # noqa: BLE001
        print(f"[pc-shadow] could not write {LOG}: {e}")
    print(section)                                 # also into the nightly refresh log


if __name__ == "__main__":
    main()
    sys.exit(0)                                    # non-negotiable: never block the nightly
