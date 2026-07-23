"""
Import PriceCharting graded prices and match them to our cards.

PriceCharting rows carry a `tcg-id` = the TCGplayer product id, which is exactly
our cards' `product_id`, so matching is an EXACT join (no fuzzy name logic).

Their tcg-id mapping is occasionally WRONG (e.g. a $6.50 deck reprint mapped to
a $1,250 promo's id), so matches are sanity-gated against our last known
TCGplayer market price: a >=25x disagreement on a valuable card quarantines the
match entirely (better unpriced than wrongly priced). Anything >=10x is written
to ml_data/pc_match_review.csv for manual review.

Human-verified corrections live in ml_data/pc_match_overrides.csv
(game, product_id, pc_id, note): the product is force-matched to the CSV row
with that PC id, its own tcg-id row is ignored, and no other product may claim
the target row (PC once crossed the tcg-ids of two Jinbe P-030 printings).

CSV prices are dollar strings ("$373.53"); we store them as REAL USD to match
our existing market_price. Output: a `pricecharting` table keyed (game, product_id)
in predictions.db-adjacent `pricecharting.db`, read-only app data separate from
the scraper card DBs.

Run:  .venv/bin/python build_pricecharting.py
"""

import csv
import os
import sqlite3
from datetime import datetime, timezone

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

# game -> (our card DB, PriceCharting CSV), from the game registry
from games import GAMES, priced_games
SOURCES = {g: (GAMES[g]["db"], GAMES[g]["pc_csv"]) for g in priced_games()}

# CSV column -> our column (graded tiers)
PRICE_COLS = {
    "loose-price": "ungraded",
    "cib-price": "grade7",
    "new-price": "grade8",
    "graded-price": "grade9",
    "box-only-price": "grade95",
    "manual-only-price": "psa10",
    "bgs-10-price": "bgs10",
    "condition-17-price": "cgc10",
    "condition-18-price": "sgc10",
}


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def to_int(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


# Sanity gate vs our last known TCGplayer market price (frozen reference).
# Vintage NM legitimately runs ~10x above PC's any-condition "loose", so only
# egregious (>=25x) disagreements on valuable cards are quarantined.
REVIEW_RATIO = 10       # log for human review
QUARANTINE_RATIO = 25   # drop the match
MIN_REFERENCE = 50      # only gate cards whose reference price is meaningful

OVERRIDES_CSV = os.path.join(BASE, "ml_data", "pc_match_overrides.csv")


def load_overrides(game):
    """product_id -> pc_id force-matches for this game (human-verified, so they
    win over PC's tcg-id and skip the sanity gate)."""
    if not os.path.exists(OVERRIDES_CSV):
        return {}
    with open(OVERRIDES_CSV, newline="", encoding="utf-8") as f:
        return {int(r["product_id"]): int(r["pc_id"])
                for r in csv.DictReader(f) if r["game"] == game}


def import_game(game, card_db, csv_name, now):
    con = sqlite3.connect(os.path.join(BASE, card_db))
    ours = set(r[0] for r in con.execute("SELECT product_id FROM cards"))
    if not ours:
        con.close()
        print(f"[{game}] no cards scraped yet — skipping")
        return [], [], []
    try:
        reference = dict(con.execute(
            "SELECT product_id, market_price FROM cards "
            "WHERE market_price IS NOT NULL AND market_price >= ?", (MIN_REFERENCE,)))
    except sqlite3.OperationalError:
        # Games added after the PriceCharting cutover never had a TCGplayer
        # market price, so there is no frozen reference to gate against.
        reference = {}
    con.close()

    overrides = load_overrides(game)           # product_id -> forced pc_id
    wanted_pc = set(overrides.values())
    override_rows = {}                         # pc_id -> its CSV row

    rows, seen, suspects, review = [], set(), [], []
    total_pc = matched = 0
    with open(os.path.join(BASE, csv_name), encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f):
            pc_id = to_int(r.get("id", ""))
            if pc_id in wanted_pc:
                # An override's target row is exclusively the override's — even
                # if its tcg-id points at some (other) product of ours.
                override_rows[pc_id] = r
                continue
            tid = to_int(r.get("tcg-id", ""))
            if tid is None:
                continue
            total_pc += 1
            if tid not in ours or tid in seen or tid in overrides:
                continue
            seen.add(tid)
            rec = {our: money(r.get(col)) for col, our in PRICE_COLS.items()}

            ref, loose = reference.get(tid), rec["ungraded"]
            if ref and loose:
                ratio = max(ref / loose, loose / ref)
                if ratio >= REVIEW_RATIO:
                    review.append((game, tid, r.get("product-name"), ref, loose, round(ratio, 1)))
                if ratio >= QUARANTINE_RATIO:
                    suspects.append((game, tid, f"pc loose {loose} vs tcg market {ref} ({ratio:.0f}x)"))
                    continue   # better unpriced than wrongly priced

            matched += 1
            rows.append((
                game, tid, to_int(r.get("id", "")),
                rec["ungraded"], rec["grade7"], rec["grade8"], rec["grade9"], rec["grade95"],
                rec["psa10"], rec["bgs10"], rec["cgc10"], rec["sgc10"],
                to_int(r.get("sales-volume", "")),
                r.get("console-name"), r.get("product-name"), now,
            ))

    # Hand-pinned matches, added last from the rows collected above. No sanity
    # gate: a human already compared the listings.
    for tid, pc_id in sorted(overrides.items()):
        r = override_rows.get(pc_id)
        if r is None or tid not in ours:
            print(f"[{game}] OVERRIDE UNRESOLVED: {tid} -> pc {pc_id} "
                  f"({'pc row missing from CSV' if r is None else 'not one of our cards'})")
            continue
        rec = {our: money(r.get(col)) for col, our in PRICE_COLS.items()}
        matched += 1
        rows.append((
            game, tid, pc_id,
            rec["ungraded"], rec["grade7"], rec["grade8"], rec["grade9"], rec["grade95"],
            rec["psa10"], rec["bgs10"], rec["cgc10"], rec["sgc10"],
            to_int(r.get("sales-volume", "")),
            r.get("console-name"), r.get("product-name"), now,
        ))
        print(f"[{game}] override: {tid} -> pc {pc_id} ({r.get('product-name')})")

    print(f"[{game}] our cards: {len(ours)} | PC rows w/ tcg-id: {total_pc} | matched: {matched} "
          f"({matched/len(ours)*100:.1f}%) | quarantined: {len(suspects)} | flagged for review: {len(review)}")
    return rows, suspects, review


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_rows, all_suspects, all_review = [], [], []
    for game, (card_db, csv_name) in SOURCES.items():
        rows, suspects, review = import_game(game, card_db, csv_name, now)
        all_rows += rows
        all_suspects += suspects
        all_review += review

    with open(os.path.join(BASE, "ml_data", "pc_match_review.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game", "product_id", "pc_name", "tcg_market", "pc_loose", "ratio"])
        w.writerows(all_review)

    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.executescript(
        """
        DROP TABLE IF EXISTS pricecharting;
        CREATE TABLE pricecharting (
            game         TEXT    NOT NULL,
            product_id   INTEGER NOT NULL,
            pc_id        INTEGER,
            ungraded     REAL,
            grade7       REAL,
            grade8       REAL,
            grade9       REAL,
            grade95      REAL,
            psa10        REAL,
            bgs10        REAL,
            cgc10        REAL,
            sgc10        REAL,
            sales_volume INTEGER,
            pc_console   TEXT,
            pc_name      TEXT,
            updated_at   TEXT,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    conn.executescript(
        """
        DROP TABLE IF EXISTS pc_match_suspects;
        CREATE TABLE pc_match_suspects (
            game TEXT NOT NULL, product_id INTEGER NOT NULL, reason TEXT,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    conn.executemany("INSERT INTO pc_match_suspects VALUES (?,?,?)", all_suspects)
    conn.executemany(
        "INSERT INTO pricecharting VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", all_rows)
    append_snapshot_history(conn, all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(OUT_DB)}")


# Tiers in the order they sit in the snapshot rows (indices 3..11).
HISTORY_TIERS = ["ungraded", "grade7", "grade8", "grade9", "grade95",
                 "psa10", "bgs10", "cgc10", "sgc10"]


def append_snapshot_history(conn, snapshot_rows):
    """Append today's snapshot prices into graded_price_history (append-only).

    The chart-page crawl only backfills a card's history once; these weekly
    snapshot points keep every matched card's graded series growing — including
    bgs10/cgc10/sgc10, which chart pages don't carry. Keyed by date, so re-runs
    on the same day are idempotent and old history is never touched.
    """
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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    points = [
        (row[0], row[1], tier, today, row[3 + i])
        for row in snapshot_rows
        for i, tier in enumerate(HISTORY_TIERS)
        if row[3 + i] is not None
    ]
    conn.executemany("INSERT OR REPLACE INTO graded_price_history VALUES (?,?,?,?,?)", points)
    print(f"appended {len(points)} snapshot points ({today}) to graded_price_history")


if __name__ == "__main__":
    main()
