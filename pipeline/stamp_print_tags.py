#!/usr/bin/env python3
"""
Stamp special-print names onto cards whose PriceCharting match says the card
IS a special print but whose TCGplayer name doesn't show it.

TCGplayer split some vintage printings into separate products but named ours
ambiguously ("Victreebel (14)" — actually the 1st Edition product, $114, with
a 1st Edition graded ladder; the Unlimited printing is a different product we
don't carry). Without a tag a visitor can't tell which printing they're
looking at. The PC match page name carries the truth ("Victreebel
[1st Edition] #14"), so this appends " (1st Edition)" / " (Shadowless)" to the
card's display name.

Idempotent: once stamped, the name contains the token and the screen skips it.
Runs nightly right after pc-match, so the Sunday catalog re-scrape (which
rewrites names from TCGplayer) gets re-stamped the same night. Always exits 0.
"""

import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import db_path, priced_games

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
# bracket text in the PC page name -> display suffix
TOKENS = {"1st edition": "1st Edition", "shadowless": "Shadowless"}


def main():
    pc = sqlite3.connect(PC_DB, timeout=60)
    for game in priced_games():
        matches = pc.execute(
            "SELECT product_id, pc_name FROM pricecharting "
            "WHERE game=? AND pc_name IS NOT NULL AND ("
            + " OR ".join("lower(pc_name) LIKE ?" for _ in TOKENS) + ")",
            (game, *[f"%[{t}]%" for t in TOKENS])).fetchall()
        if not matches:
            continue
        con = sqlite3.connect(db_path(game), timeout=60)
        names = dict(con.execute("SELECT product_id, name FROM cards"))
        stamped = 0
        for pid, pc_name in matches:
            name = names.get(pid)
            if not name:
                continue
            pl, nl = pc_name.lower(), name.lower()
            add = [label for tok, label in TOKENS.items()
                   if f"[{tok}]" in pl and tok not in nl]
            if add:
                con.execute("UPDATE cards SET name=? WHERE product_id=?",
                            (name + "".join(f" ({a})" for a in add), pid))
                stamped += 1
        con.commit()
        con.close()
        if stamped:
            print(f"[{game}] stamped print tag on {stamped} card name(s)")
    pc.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"stamp_print_tags: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
