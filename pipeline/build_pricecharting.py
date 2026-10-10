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
import time
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
BLOCKLIST_CSV = os.path.join(BASE, "ml_data", "card_blocklist.csv")


def apply_blocklist(game, card_db):
    """Delete blocklisted cards from the catalog (match_review's 'Delete
    card'). Runs every night here — the first step after the scrapers — so a
    Sunday set-rescan that re-inserts a deleted card loses it again before
    anything downstream (matching, export, embedding, the site) can see it."""
    if not os.path.exists(BLOCKLIST_CSV):
        return
    with open(BLOCKLIST_CSV, newline="", encoding="utf-8") as f:
        pids = [int(r["product_id"]) for r in csv.DictReader(f) if r["game"] == game]
    if not pids:
        return
    con = sqlite3.connect(os.path.join(BASE, card_db), timeout=60)
    n = con.execute(
        f"DELETE FROM cards WHERE product_id IN ({','.join('?' * len(pids))})",
        pids).rowcount
    con.commit()
    con.close()
    if n:
        print(f"[{game}] blocklist: removed {n} re-scraped card(s)")


def load_overrides(game):
    """product_id -> pc_id force-matches for this game (human-verified, so they
    win over PC's tcg-id and skip the sanity gate)."""
    if not os.path.exists(OVERRIDES_CSV):
        return {}
    with open(OVERRIDES_CSV, newline="", encoding="utf-8") as f:
        return {int(r["product_id"]): int(r["pc_id"])
                for r in csv.DictReader(f) if r["game"] == game}


def import_game(game, card_db, csv_name, now):
    apply_blocklist(game, card_db)
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
        # pc_id 0 = human-verified EXCLUSION (PriceCharting has no correct
        # page): the stream loop above already skipped this product, so it
        # simply ends the run unmatched — unpriced rather than mispriced.
        if pc_id == 0:
            print(f"[{game}] override: {tid} excluded (no correct PC page)")
            continue
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


# The bulk price-guide CSVs (loose + all graded tiers, daily) came from the paid
# PriceCharting subscription, which has ended. Their files now sit frozen at the
# last good download. Rebuilding the match table from them would (a) re-freeze in
# stale prices and (b) write fake "today" snapshot points, so once they go stale
# we STOP rebuilding: the existing match table is preserved (scrape_graded_history
# still uses it for pc_id targets), graded prices come from the public
# product-page crawl, and this step does blocklist maintenance only.
# Gate at < 1 day: the download step ran minutes before this one, so a live CSV is
# always well under a day old — anything older means the feed is dead.
CSV_STALE_DAYS = 1


def csvs_usable():
    """True only if every bulk-download game's CSV is present and fresh. Games
    priced from their own scrapers (gundam/starwars, pc_category=None) don't gate
    this — only the ones that came from the retired paid bulk download."""
    for g in priced_games():
        if not GAMES[g].get("pc_category"):
            continue   # scraper-fed game, not a paid-download CSV
        p = os.path.join(BASE, GAMES[g]["pc_csv"])
        if not os.path.exists(p) or (time.time() - os.path.getmtime(p)) > CSV_STALE_DAYS * 86400:
            return False
    return True


SUGGEST_CSV = os.path.join(BASE, "ml_data", "pc_link_suggestions.csv")
REVIEWED_CSV = os.path.join(BASE, "ml_data", "pc_match_reviewed.csv")


def take_exact_suggestions(game):
    """Pop pc_link_suggest's exact links for a game: PriceCharting's own page
    lists the TCGplayer ID (the js-tcg-id-link anchor), so a suggestion sourced
    from it IS PriceCharting's mapping — no human review needed (per 2026-08-01
    decision; match_review only handles the ambiguous leftovers). Returns
    {product_id: pc_id} and rewrites the CSV without the consumed rows."""
    if not os.path.exists(SUGGEST_CSV):
        return {}
    with open(SUGGEST_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # value = (pc_id, console slug or None): the console is known when the
    # suggestion came from a discovered console page (2026-10-10), and is
    # stored so the sweep enumerates that new set from then on.
    take = {int(r["product_id"]): (int(r["pc_id"]), (r.get("console") or "").strip() or None)
            for r in rows if r["game"] == game and r.get("source") == "page-embedded tcg-id"}
    if take:
        keep = [r for r in rows
                if not (r["game"] == game and r.get("source") == "page-embedded tcg-id")]
        with open(SUGGEST_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(keep)
    return take


def mark_reviewed(rows):
    """Append (game, product_id, pc_id) confirms to pc_match_reviewed.csv so the
    forecast's graded match-review gate passes for auto-linked cards."""
    new = not os.path.exists(REVIEWED_CSV)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(REVIEWED_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["game", "product_id", "pc_id", "status", "reviewed_at"])
        w.writerows([[g, pid, pc, "confirmed", now] for g, pid, pc in rows])


def apply_confirmed_links(conn):
    """Insert new-card links into the frozen match table so scrape_graded_history
    will crawl their graded history. Two sources: human-confirmed overrides
    (pc_match_overrides.csv) and pc_link_suggest's exact page-embedded tcg-ids
    (auto-applied — see take_exact_suggestions). On the preserve path there is no
    bulk CSV to pull the PC row from, so we store just the pc_id (+ our catalog
    name as a placeholder); the graded crawl then fills the history and
    build_unified's current-graded refresh fills the price columns. pc_id 0 =
    confirmed exclusion (no correct PC page) — skipped. Only product_ids not
    already linked are added, so this is cheap and idempotent."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = 0
    auto_reviewed = []
    for game, (card_db, _csv) in SOURCES.items():
        overrides = load_overrides(game)                 # product_id -> pc_id
        exact_full = take_exact_suggestions(game)        # auto-links (no review)
        exact = {pid: pc for pid, (pc, _) in exact_full.items()}
        consoles = {pid: con for pid, (_, con) in exact_full.items() if con}
        merged = {**exact, **overrides}                  # a human override wins
        linked = {pid for (pid,) in conn.execute(
            "SELECT product_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL", (game,))}
        new_links = [(pid, pc) for pid, pc in merged.items() if pc and pid not in linked]
        auto_reviewed += [(game, pid, pc) for pid, pc in new_links if pid in exact]
        # Human OVERRIDES are authoritative for already-linked cards too — a
        # match found pointing at the wrong PC page (e.g. a [1st Edition] page
        # for a regular-print card) gets corrected, not silently ignored. Only
        # rows whose pc_id actually differs are touched (no nightly churn).
        for pid, pc in overrides.items():
            if pc and pid in linked:
                cur = conn.execute(
                    "SELECT pc_id FROM pricecharting WHERE game=? AND product_id=?",
                    (game, pid)).fetchone()
                if cur and cur[0] != pc:
                    conn.execute(
                        "UPDATE pricecharting SET pc_id=?, updated_at=? "
                        "WHERE game=? AND product_id=?", (pc, now, game, pid))
                    added += 1
        if not new_links:
            continue
        names = {}
        try:
            cc = sqlite3.connect(os.path.join(BASE, card_db))
            q = ",".join("?" * len(new_links))
            names = dict(cc.execute(
                f"SELECT product_id, name FROM cards WHERE product_id IN ({q})",
                [pid for pid, _ in new_links]))
            cc.close()
        except sqlite3.OperationalError:
            pass
        for pid, pc in new_links:
            if conn.execute("SELECT 1 FROM pricecharting WHERE game=? AND product_id=?",
                            (game, pid)).fetchone():
                conn.execute("UPDATE pricecharting SET pc_id=?, "
                             "pc_console=COALESCE(?, pc_console), updated_at=? "
                             "WHERE game=? AND product_id=?",
                             (pc, consoles.get(pid), now, game, pid))
            else:
                conn.execute(
                    "INSERT INTO pricecharting (game, product_id, pc_id, pc_name, pc_console, "
                    "updated_at) VALUES (?,?,?,?,?,?)",
                    (game, pid, pc, names.get(pid), consoles.get(pid), now))
            added += 1
    if added:
        conn.commit()
    if auto_reviewed:
        mark_reviewed(auto_reviewed)
    print(f"applied {added} new-card link(s) into the match table "
          f"({len(auto_reviewed)} auto-confirmed from page-embedded tcg-ids)")


def main():
    if not csvs_usable():
        # Subscription ended: keep the existing pricecharting match table intact
        # (graded now refreshes from the public crawl), apply any human-confirmed
        # new-card links, and run the nightly blocklist deletion so a re-scraped
        # blocklisted card can't reappear.
        print("PriceCharting bulk CSVs are stale/absent (subscription ended) — "
              "preserving the existing match table; graded is sourced from the "
              "public product-page crawl. Applying confirmed links + blocklist only.")
        for game, (card_db, csv_name) in SOURCES.items():
            apply_blocklist(game, card_db)
        conn = sqlite3.connect(OUT_DB, timeout=60)
        apply_confirmed_links(conn)
        conn.close()
        return

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
    conn.executemany("INSERT OR REPLACE INTO graded_price_history "
                     "(game, product_id, grade, date, price) VALUES (?,?,?,?,?)", points)
    print(f"appended {len(points)} snapshot points ({today}) to graded_price_history")


if __name__ == "__main__":
    main()
