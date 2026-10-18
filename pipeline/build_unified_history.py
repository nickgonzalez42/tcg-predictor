"""
Build one monthly price-history table per card, every condition tier.

Prices come from two sources, spliced into one series per (card, grade):

  ungraded          TCGplayer Near Mint market price (scrape_tcg_nm_prices.py),
                    overriding the PriceCharting loose price month-by-month;
                    PC loose remains only for months TCGplayer hasn't covered
                    (preserves the long pre-switch history for the model).
  grade7..sgc10     graded tiers — PriceCharting's crawled monthly history.

This is the app's ungraded headline + graded ladders + history charts + the
forecast model's training series. TCGplayer supplies the card catalog, details,
and images as before.

Output: pricecharting.db `price_history_unified` (game, product_id, grade, date, price, source).

Run after the NM crawl + graded-history crawl complete:
    .venv/bin/python build_unified_history.py
"""

import argparse
import collections
import csv
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone


def nominal_day():
    """The run's NOMINAL calendar day — UTC (2026-08-14 site-wide decision).
    The 22:00 CDT nightly is 03:00 UTC, so the ENTIRE run (crawl through
    trainer push, ending ~13:30 UTC) falls inside one UTC calendar day: the
    day after the local start evening. All batch labels, day-keyed decisions
    (Sunday full rebuild, Friday report, delta meta date), and the site's
    notion of 'today' share this clock — no more (now - 12h) hack."""
    return datetime.now(timezone.utc).date()

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
CORRECTIONS_CSV = os.path.join(BASE, "ml_data", "price_corrections.csv")
# Nightly incremental mode writes the day's table changes here; push_data.sh
# ships THIS (~15MB) to prod instead of the 4.4GB pricecharting.db. Sundays /
# --full rebuild from scratch and delete it, forcing the full-file push.
DELTA_DB = os.path.join(BASE, "ml_data", "unified_delta.db")
# TCGplayer owns the ungraded/Near Mint tier from this month forward; any
# PriceCharting ungraded point dated in or after it is dropped (PC only fills
# PRE-switch history). Without this, a card TCGplayer can't price (no NM market)
# keeps showing PC's stale daily snapshot as a lone, trend-breaking point.
SWITCH_MONTH = "2026-07"

# --- PriceCharting ungraded repairs (2026-10-18) ------------------------------
# Two artifacts live in PC's "loose" series and, untreated, in every ungraded
# label the model trains on and every history chart the site draws:
#  1. A valuation change between 2025-10 and 2025-11 for CHEAP cards: sub-$3
#     cards fell 70-89% in one month in every game (94% of sub-$3 Lorcana
#     cards dropped >50%; cards over $10 and every graded tier: ~0; TCGplayer
#     rows for the same month: ~0). Not a market move — PC re-based bulk
#     values. Fix: a card whose PC value fell more than BREAK_DROP across
#     that month has every PC month BEFORE it multiplied by the Nov/Oct
#     ratio, so the history keeps its shape in post-break units.
#  2. The PC -> TCGplayer seam at SWITCH_MONTH is unscaled since the 2026-08
#     ungraded wipe removed the overlap the original design scaled on:
#     paired step of -25% (lorcana) / -13% (pokemon) at 2026-06 -> 07. Fix:
#     the whole PC era is multiplied by first-TCG-month / last-PC-month
#     (winsorized), per card, so the two eras meet at one level.
# Both are applied here, in the DERIVED table, from the untouched sources —
# re-runnable and reversible by editing these constants. Factors land in
# `unified_repairs` for audit. Returns are scale-free, so the model sees the
# same relative moves; what changes is that the fake cliff and the seam
# step are gone, and pre-switch dollar levels follow TCGplayer's.
BREAK_FROM, BREAK_TO = "2025-10", "2025-11"
BREAK_DROP = -0.35          # log change below this (-30%) is the artifact, not a move
# Both ratios are measured on MEDIANS of up to SIDE_MONTHS months each side
# of the boundary (not a single month pair), so one junk point can't set a
# card's factor; with that robustness the seam bounds can be wide enough for
# penny cards, whose PC floor sits 5-10x above their TCGplayer price.
SIDE_MONTHS = 3
SEAM_RATIO_MIN, SEAM_RATIO_MAX = 0.1, 10.0  # winsor bounds on the per-card seam ratio
SEAM_MAX_GAP_MONTHS = 3     # last PC month to first TCG month; further apart = no bridge
SEAM_IGNORE = 0.02          # |log ratio| under this: leave as is

# The deep graded tiers (Beckett/CGC/SGC 10) were available ONLY from the paid
# PriceCharting bulk CSV — no public page carries them — so with the subscription
# gone they can't be refreshed. They were never in the forecast model (ungraded +
# grade7..psa10 only); rather than serve three permanently-frozen tiers, drop them
# from the unified history. Their raw graded_price_history rows are left in place
# (non-destructive), just not emitted to the app.
DROP_GRADES = {"bgs10", "cgc10", "sgc10"}
# Graded tiers still served (public product pages carry these six; ungraded now
# comes from TCGplayer, so it's excluded from the match-table current refresh).
REFRESH_TIERS = ["grade7", "grade8", "grade9", "grade95", "psa10"]

from games import priced_games


def load_corrections(game):
    """(product_id, grade) -> [(from_date, to_date, price|None, floor|None)] corrections.

    Applied at build time so the raw crawl stays as-scraped (auditable) while
    the serving/model layer gets the fix — and a recrawl can't resurrect the
    bad points. Three forms of source-side damage PC never repairs:
      price set     -> REPLACE the range (a real card mispriced for months,
                       e.g. Maleficent D23 raw at $2.50 while graded held $1k+)
      price empty   -> DROP the range (history from before the card existed —
                       there is no true value to substitute)
      min_price set -> DROP only points BELOW that floor in the range (sub-
                       threshold source errors, e.g. serialized cards PC listed
                       at a few dollars while their true floor is $40+); points
                       at or above the floor pass through untouched.
    grade '*' applies to every tier; an omitted date range spans all dates."""
    if not os.path.exists(CORRECTIONS_CSV):
        return {}
    out = collections.defaultdict(list)
    with open(CORRECTIONS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["game"] == game:
                floor = (r.get("min_price") or "").strip()
                out[(int(r["product_id"]), r["grade"])].append(
                    (r["from_date"] or "0000", r["to_date"] or "9999",
                     float(r["price"]) if r["price"].strip() else None,
                     float(floor) if floor else None))
    return out
GAMES = priced_games()


def _month_index(m):
    y, mo = int(m[:4]), int(m[5:7])
    return y * 12 + mo


def repair_pc_ungraded(months, nm_months):
    """Apply the two PC ungraded repairs to one card's PC month dict
    {YYYY-MM: (date, price)} IN PLACE, given its TCGplayer months (same
    shape; may be empty). Returns (nov_factor, seam_factor) — 1.0 = no-op."""
    import math
    import statistics

    def side_median(src, keys):
        vals = [src[k][1] for k in keys if k in src and src[k][1] and src[k][1] > 0]
        return statistics.median(vals) if vals else None

    nov_f = seam_f = 1.0
    if not months:
        return nov_f, seam_f
    # Up to SIDE_MONTHS months ending at BREAK_FROM vs starting at BREAK_TO.
    before_keys = sorted(m for m in months if m <= BREAK_FROM)[-SIDE_MONTHS:]
    after_keys = sorted(m for m in months if m >= BREAK_TO)[:SIDE_MONTHS]
    if BREAK_FROM in months and BREAK_TO in months:
        p_before, p_after = side_median(months, before_keys), side_median(months, after_keys)
        if p_before and p_after and math.log(p_after / p_before) < BREAK_DROP:
            nov_f = p_after / p_before
            for m, (d, p) in list(months.items()):
                if m < BREAK_TO and p is not None:
                    months[m] = (d, p * nov_f)
    if nm_months:
        last_pc = max(months)
        first_tcg = min(nm_months)
        gap = _month_index(first_tcg) - _month_index(last_pc)
        if 0 < gap <= SEAM_MAX_GAP_MONTHS:
            p_pc = side_median(months, sorted(months)[-SIDE_MONTHS:])
            p_tcg = side_median(nm_months, sorted(nm_months)[:SIDE_MONTHS])
            if p_pc and p_tcg:
                r = p_tcg / p_pc
                if abs(math.log(r)) >= SEAM_IGNORE:
                    seam_f = min(max(r, SEAM_RATIO_MIN), SEAM_RATIO_MAX)
                    for m, (d, p) in list(months.items()):
                        if p is not None:
                            months[m] = (d, p * seam_f)
    return nov_f, seam_f


def boundary_report(game, rows):
    """Paired median month-over-month log change of the ungraded base
    series across the two repaired boundaries — the guard that says the
    repairs took (both should read ~0 now; they read -2.0 / -0.25 for
    Lorcana before). rows: (game, pid, grade, date, price, source)."""
    import math, statistics
    by = collections.defaultdict(dict)
    for g, pid, grade, d, p, src in rows:
        if grade == "ungraded" and p and p > 0:
            by[d[:7]][pid] = p
    prev_switch = f"{int(SWITCH_MONTH[:4]) - (SWITCH_MONTH[5:7] == '01')}-" \
                  f"{(int(SWITCH_MONTH[5:7]) - 2) % 12 + 1:02d}"
    out = []
    for a, b in ((BREAK_FROM, BREAK_TO), (prev_switch, SWITCH_MONTH)):
        pr = [math.log(by[b][pid] / by[a][pid]) for pid in by.get(b, {})
              if pid in by.get(a, {}) and by[a][pid] > 0]
        out.append(f"{a}->{b}: {statistics.median(pr):+.3f} (n={len(pr)})" if pr
                   else f"{a}->{b}: n/a")
    print(f"[{game}] boundary check (paired median log change, ungraded): " + "; ".join(out))


def pc_history(game):
    """grade -> product_id -> {YYYY-MM: (real_date, price)} from the crawled history.

    Ordered by date so when a month holds both a chart point and later daily
    snapshot points, the most recent value wins — and the bucket carries that
    point's REAL date, so "as of" displays don't undersell freshness (a July
    bucket updated by yesterday's snapshot is dated yesterday, not July 1)."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT grade, product_id, date, price FROM graded_price_history "
        "WHERE game=? AND printing='' "
        "ORDER BY date", (game,)).fetchall()
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for grade, pid, d, p in rows:
        out[grade][pid][d[:7]] = (d[:10], p)
    return out


def tcg_nm_history(game):
    """product_id -> {YYYY-MM: (real_date, price)} from the TCGplayer Near Mint
    crawl (scrape_tcg_nm_prices.py) — the ungraded price source.

    Same month-bucketing as pc_history (latest day in a month wins, bucket
    carries its real date), so the two sources splice seamlessly into one
    ungraded series."""
    try:
        rows = sqlite3.connect(PC_DB, timeout=120).execute(
            "SELECT product_id, date, price FROM tcg_nm_history "
            "WHERE game=? AND printing='' "
            "ORDER BY date", (game,)).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return {}    # table appears after the first NM crawl
        # Anything else (a locked database, most likely) must FAIL the build:
        # an empty map here silently reverts every card to PriceCharting-only
        # history with no current price (seen in a 2026-10-18 dry run while
        # the graded crawl held the lock) — a loud failure is the safe one.
        raise RuntimeError(f"[{game}] could not read tcg_nm_history: {e}") from e
    out = collections.defaultdict(dict)
    for pid, d, p in rows:
        out[pid][d[:7]] = (d[:10], p)
    return out


TCG_NM_ONLY_CSV = os.path.join(BASE, "ml_data", "tcg_nm_only.csv")
NM_BLOCKLIST_CSV = os.path.join(BASE, "ml_data", "tcg_nm_blocklist.csv")


def load_nm_blocklist(game):
    """product_ids whose TCGplayer NM price is BOGUS (e.g. a lone stale
    $4,999.99 listing on a ~$1,800 card): ungraded stays PriceCharting-owned —
    no switch-month drop, no NM override. The crawl also skips them (see
    scrape_tcg_nm_prices.load_nm_only), so no new NM rows accrue."""
    if not os.path.exists(NM_BLOCKLIST_CSV):
        return set()
    with open(NM_BLOCKLIST_CSV, newline="", encoding="utf-8") as f:
        return {int(r["product_id"]) for r in csv.DictReader(f)
                if r["game"] == game and r.get("product_id")}


def load_tcg_nm_only(game):
    """product_ids whose ungraded tier must come EXCLUSIVELY from TCGplayer NM.

    For these, PriceCharting crossed tcg-ids / carried phantom or absorbed
    ungraded history, so we drop PC ungraded AND the PC-era ungraded
    price_corrections (they were workarounds for that bad data) and keep only the
    TCGplayer NM series (backfill_tcg_nm_history.py seeds ~1yr of weekly points).
    Graded tiers are unaffected."""
    if not os.path.exists(TCG_NM_ONLY_CSV):
        return set()
    with open(TCG_NM_ONLY_CSV, newline="", encoding="utf-8") as f:
        return {int(r["product_id"]) for r in csv.DictReader(f)
                if r["game"] == game and r.get("product_id")}


def suspects(game):
    """Cards whose PriceCharting match failed the sanity gate — excluded from
    the unified history so stale mismatched points can't resurface."""
    try:
        return {r[0] for r in sqlite3.connect(PC_DB, timeout=30).execute(
            "SELECT product_id FROM pc_match_suspects WHERE game=?", (game,))}
    except sqlite3.OperationalError:
        return set()   # table appears after the first gated pc-match run


def build_game(game):
    rows = []
    counts = collections.Counter()
    skip = suspects(game)
    corrections = load_corrections(game)
    n_corrected = 0
    hist = pc_history(game)

    # Drop PriceCharting ungraded from the switch month forward — TCGplayer owns
    # it now. PC ungraded before the switch stays (fills the long history the
    # model needs). This removes the lone stale PC "current" point on cards
    # TCGplayer has no market for.
    # Per-card bridge (2026-08-15): a card whose NM crawl started late in the
    # cutover keeps PC's months BETWEEN the switch and its first TCGplayer
    # month — the blanket delete left those cards with a multi-week hole that
    # PC actually priced. Cards with no TCGplayer coverage at all still drop
    # everything at/after the switch (the original stale-lone-point fix).
    nm = tcg_nm_history(game)
    nm_blocked = load_nm_blocklist(game)
    n_pc_dropped = 0
    for pid, months in list(hist["ungraded"].items()):
        if pid in nm_blocked:
            continue   # blocklisted: PC owns ungraded outright
        first_nm = min(nm[pid]) if nm.get(pid) else None
        for m in [mm for mm in months if mm >= SWITCH_MONTH]:
            if first_nm is not None and m < first_nm:
                continue   # bridge month: PC keeps it until TCGplayer arrives
            del months[m]
            n_pc_dropped += 1

    # Ungraded is now sourced from TCGplayer Near Mint. It OVERRIDES the
    # PriceCharting loose price month-by-month wherever TCGplayer has a value;
    # months TCGplayer hasn't covered keep the PC loose history, so the model's
    # long ungraded series survives the switch (graded tiers stay 100% PC).
    # (`nm` loaded above for the per-card bridge.) Blocklist trumps
    # tcg_nm_only: a card in both must NOT get the tcg-only treatment
    # (2026-08-17: Umbreon H30's stale conflict re-served its bogus listing).
    tcg_only = load_tcg_nm_only(game) - nm_blocked
    # PC ungraded repairs (see the constants): on the PC months only, BEFORE
    # TCGplayer's months are merged in, so the seam ratio is measured between
    # the two sources and the TCG era itself is never touched. TCGplayer-only
    # cards have no PC era to repair.
    n_nov = n_seam = 0
    for pid, months in hist["ungraded"].items():
        if pid in tcg_only:
            continue
        nov_f, seam_f = repair_pc_ungraded(months, nm.get(pid, {}))
        if nov_f != 1.0 or seam_f != 1.0:
            REPAIRS.append((game, pid, "", nov_f, seam_f))
            n_nov += nov_f != 1.0
            n_seam += seam_f != 1.0
    print(f"[{game}] PC ungraded repairs: {n_nov} card(s) re-leveled across "
          f"{BREAK_FROM}->{BREAK_TO}, {n_seam} scaled at the {SWITCH_MONTH} seam")
    nm_months = sum(len(m) for m in nm.values())
    for pid, months in nm.items():
        if pid not in tcg_only and pid not in nm_blocked:
            for m, dp in months.items():
                hist["ungraded"][pid][m] = dp
    # TCGplayer-only cards: ungraded is EXACTLY the TCGplayer NM series (drop all
    # PC ungraded, not just override the current month). A card with no TCGplayer
    # NM at all then shows no ungraded price rather than PC's wrong one.
    for pid in tcg_only:
        hist["ungraded"][pid] = dict(nm.get(pid, {}))
    if nm:
        print(f"[{game}] ungraded <- TCGplayer NM: {len(nm)} cards, "
              f"{nm_months} month-points (override PC loose)"
              f"{f'; {len(tcg_only & set(nm))} TCGplayer-only' if tcg_only else ''}"
              f"; dropped {n_pc_dropped} PC ungraded >= {SWITCH_MONTH}")

    for grade, by_pid in hist.items():
        if grade in DROP_GRADES:
            continue
        nm_for_grade = nm if grade == "ungraded" else {}
        for pid, months in by_pid.items():
            if pid in skip:
                continue
            nm_months_pid = nm_for_grade.get(pid, {})
            # TCGplayer-only cards: the TCGplayer NM series is authoritative for
            # ungraded, so skip the PC-era ungraded corrections. Graded tiers of
            # the same card still get their corrections (e.g. phantom-graded drops).
            if grade == "ungraded" and pid in tcg_only:
                fixes = []
            else:
                fixes = corrections.get((pid, grade), []) + corrections.get((pid, "*"), [])
            for (d, p) in months.values():
                dropped = False
                for lo, hi, price, floor in fixes:
                    if not (lo <= d <= hi):
                        continue
                    if floor is not None:
                        if p is not None and p < floor:
                            dropped = True
                            n_corrected += 1
                            break
                        continue          # at/above floor: leave for other fixes
                    if price is None:
                        dropped = True
                    else:
                        p = price
                    n_corrected += 1
                    break
                if dropped:
                    continue
                # A month present in the NM map came from TCGplayer (it overrode
                # PC loose above); everything else is PriceCharting.
                src = "tcgplayer" if d[:7] in nm_months_pid else "pricecharting"
                rows.append((game, pid, grade, d, p, src))
                counts[grade] += 1
    if n_corrected:
        print(f"[{game}] {n_corrected} point(s) replaced by price_corrections.csv")
    print(f"[{game}] rows per tier: " +
          ", ".join(f"{g}={n}" for g, n in sorted(counts.items())))
    boundary_report(game, rows)
    return rows


def build_game_labeled(game):
    """Per-printing unified rows (Phase 3). The labeled namespace — TCG variant
    NM series from printing_ingest + PC sibling-page history from
    pc_printing_backfill — spliced with the SAME rules as the base series:
    month buckets, TCGplayer overrides PC ungraded from SWITCH_MONTH, deep
    grades dropped. price_corrections are NOT applied (they were authored
    against the base series)."""
    conn = sqlite3.connect(PC_DB, timeout=60)
    hist = collections.defaultdict(dict)   # (pid, printing, grade) -> {month: (date, price)}
    for printing, grade, pid, d, p in conn.execute(
            "SELECT printing, grade, product_id, date, price FROM graded_price_history "
            "WHERE game=? AND printing != '' ORDER BY date", (game,)):
        if grade not in DROP_GRADES:
            hist[(pid, printing, grade)][d[:7]] = (d[:10], p)
    for key, months in hist.items():
        if key[2] == "ungraded":
            for m in [mm for mm in months if mm >= SWITCH_MONTH]:
                del months[m]
    nmv = collections.defaultdict(dict)    # (pid, printing) -> {month: (date, price)}
    for printing, pid, d, p in conn.execute(
            "SELECT printing, product_id, date, price FROM tcg_nm_history "
            "WHERE game=? AND printing != '' ORDER BY date", (game,)):
        nmv[(pid, printing)][d[:7]] = (d[:10], p)
    conn.close()
    # Same PC repairs as the base series, per (card, printing), before merge.
    for (pid, printing, grade), months in hist.items():
        if grade == "ungraded" and months:
            nov_f, seam_f = repair_pc_ungraded(months, nmv.get((pid, printing), {}))
            if nov_f != 1.0 or seam_f != 1.0:
                REPAIRS.append((game, pid, printing, nov_f, seam_f))
    for (pid, printing), months in nmv.items():
        hist[(pid, printing, "ungraded")].update(months)
    skip = suspects(game)
    rows = []
    for (pid, printing, grade), months in hist.items():
        if pid in skip:
            continue
        nm_m = nmv.get((pid, printing), {})
        for m, (d, p) in months.items():
            src = "tcgplayer" if (grade == "ungraded" and m in nm_m) else "pricecharting"
            rows.append((game, pid, printing, grade, d, p, src))
    return rows


def refresh_match_current(conn):
    """Point the pricecharting match table's CURRENT graded columns at the latest
    crawled graded_price_history price per card/tier. The match table is frozen
    since the PriceCharting subscription ended (build_pricecharting preserve path)
    and the site reads current graded prices from it — so without this the graded
    ladder would show stale pre-cutover prices while the history chart stays fresh,
    and newly-linked cards would show no ladder at all. Ungraded is left alone
    (headline ungraded is TCGplayer, not PC loose); deep tiers were dropped."""
    ph = ",".join("?" * len(REFRESH_TIERS))
    latest = conn.execute(
        f"SELECT g.game, g.product_id, g.grade, g.price FROM graded_price_history g "
        f"JOIN (SELECT game, product_id, grade, MAX(date) md FROM graded_price_history "
        f"      WHERE printing='' AND grade IN ({ph}) GROUP BY game, product_id, grade) m "
        f"  ON g.game=m.game AND g.product_id=m.product_id AND g.grade=m.grade "
        # OUTER filter too: a LABELED row can share the base row's date and
        # silently win the join (Charizard 84195's base psa10 became the
        # Reverse Holo's $5,999.99 on 2026-08-10).
        f"  AND g.date=m.md WHERE g.printing=''", REFRESH_TIERS).fetchall()
    want = collections.defaultdict(dict)
    for game, pid, grade, price in latest:
        want[(game, pid)][grade] = price
    cols = ", ".join(REFRESH_TIERS)
    have = {(g, p): dict(zip(REFRESH_TIERS, rest)) for g, p, *rest in conn.execute(
        f"SELECT game, product_id, {cols} FROM pricecharting")}
    # Only rows whose price actually CHANGED are written + stamped: a same-value
    # UPDATE still dirties the page (needless churn for the delta ship / rsync),
    # and most nights only the graded rotation's crawl moves anything. updated_at
    # therefore means "this card's graded prices last changed".
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = set()
    for key, tiers in want.items():
        cur = have.get(key)
        if cur is None:
            continue          # card not in the match table
        diff = {t: v for t, v in tiers.items() if cur.get(t) != v}
        if diff:
            changed.add(key)
            set_clause = ", ".join(f"{t}=?" for t in diff) + ", updated_at=?"
            conn.execute(
                f"UPDATE pricecharting SET {set_clause} WHERE game=? AND product_id=?",
                (*diff.values(), now, *key))
    conn.commit()
    print(f"match table: {len(changed)} card(s) with changed graded price(s) refreshed")
    return changed


def sync_incremental(conn, all_rows, labeled_rows):
    """Sync price_history_unified to the freshly-built desired set IN PLACE and
    stage the exact difference in DELTA_DB — insert new/changed rows, delete
    vanished ones — so the end state equals a full rebuild while the file's
    unchanged pages stay physically untouched. push_data.sh ships the ~15MB
    delta to prod (which applies it) instead of the multi-GB database."""
    if os.path.exists(DELTA_DB):
        os.remove(DELTA_DB)
    conn.execute("ATTACH ? AS delta", (DELTA_DB,))
    conn.executescript(
        """
        CREATE TABLE delta.ins (game TEXT, product_id INTEGER, printing TEXT,
                                grade TEXT, date TEXT, price REAL, source TEXT);
        CREATE TABLE delta.del (game TEXT, product_id INTEGER, printing TEXT,
                                grade TEXT, date TEXT);
        CREATE TEMP TABLE new_rows (
            game       TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            printing   TEXT    NOT NULL DEFAULT '',
            grade      TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            price      REAL,
            source     TEXT,
            PRIMARY KEY (game, product_id, printing, grade, date)
        ) WITHOUT ROWID;
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO new_rows (game, product_id, printing, grade, "
        "date, price, source) VALUES (?,?, '', ?,?,?,?)", all_rows)
    conn.executemany(
        "INSERT OR REPLACE INTO new_rows (game, product_id, printing, grade, "
        "date, price, source) VALUES (?,?,?,?,?,?,?)", labeled_rows)
    conn.execute(
        """
        INSERT INTO delta.ins
        SELECT n.game, n.product_id, n.printing, n.grade, n.date, n.price, n.source
          FROM new_rows n
          LEFT JOIN price_history_unified u
            ON u.game = n.game AND u.product_id = n.product_id
           AND u.printing = n.printing
           AND u.grade = n.grade AND u.date = n.date
         WHERE u.game IS NULL OR u.price IS NOT n.price OR u.source IS NOT n.source
        """)
    conn.execute(
        """
        INSERT INTO delta.del
        SELECT u.game, u.product_id, u.printing, u.grade, u.date
          FROM price_history_unified u
          LEFT JOIN new_rows n
            ON n.game = u.game AND n.product_id = u.product_id
           AND n.printing = u.printing
           AND n.grade = u.grade AND n.date = u.date
         WHERE n.game IS NULL
        """)
    n_ins = conn.execute("SELECT COUNT(*) FROM delta.ins").fetchone()[0]
    n_del = conn.execute("SELECT COUNT(*) FROM delta.del").fetchone()[0]
    conn.execute(
        "DELETE FROM price_history_unified "
        "WHERE (game, product_id, printing, grade, date) "
        "IN (SELECT game, product_id, printing, grade, date FROM delta.del)")
    conn.execute("INSERT OR REPLACE INTO price_history_unified SELECT * FROM delta.ins")
    conn.execute("DROP TABLE new_rows")
    conn.commit()
    return n_ins, n_del


REPAIRS = []   # (game, product_id, printing, nov_factor, seam_factor) this build


def write_repairs(conn):
    """Audit trail of the PC ungraded repairs applied by this build (replaced
    per game each run, so it always describes the table as it stands)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unified_repairs (
            game        TEXT    NOT NULL,
            product_id  INTEGER NOT NULL,
            printing    TEXT    NOT NULL DEFAULT '',
            nov_factor  REAL    NOT NULL,   -- applied to PC months < 2025-11 (1 = none)
            seam_factor REAL    NOT NULL,   -- applied to every PC month (1 = none)
            applied_at  TEXT    NOT NULL,
            PRIMARY KEY (game, product_id, printing)
        )""")
    games = {r[0] for r in REPAIRS} | set(GAMES)
    conn.executemany("DELETE FROM unified_repairs WHERE game=?", [(g,) for g in games])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany("INSERT OR REPLACE INTO unified_repairs VALUES (?,?,?,?,?,?)",
                     [(g, pid, pr, nf, sf, now) for g, pid, pr, nf, sf in REPAIRS])
    conn.commit()
    print(f"unified_repairs: {len(REPAIRS):,} (card, printing) factor row(s) recorded")


def main():
    ap = argparse.ArgumentParser(description="Build the unified price-history table.")
    ap.add_argument("--full", action="store_true",
                    help="drop + rebuild from scratch (automatic on Sundays or "
                         "when the table/index is missing); otherwise sync "
                         "incrementally and stage a delta for the prod push")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + print the per-game reports, write NOTHING")
    ap.add_argument("--game", help="restrict to one game (with --dry-run)")
    args = ap.parse_args()
    games = [args.game] if args.game else GAMES
    if args.game and not args.dry_run:
        ap.error("--game is for --dry-run only (a partial write would drop the other games)")

    if args.dry_run:
        for game in games:
            rows = build_game(game)
            lab = build_game_labeled(game)
            print(f"[{game}] dry run: {len(rows):,} base + {len(lab):,} labeled rows; "
                  f"{sum(1 for r in REPAIRS if r[0] == game):,} repair factor(s); nothing written")
        return

    conn = sqlite3.connect(PC_DB, timeout=60)
    have = (conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name IN "
        "('price_history_unified', 'idx_history_tier')").fetchone()[0] == 2
        and "printing" in {r[1] for r in conn.execute(
            "PRAGMA table_info(price_history_unified)")})
    full = args.full or nominal_day().weekday() == 6 or not have

    all_rows = []
    labeled_rows = []
    for game in GAMES:
        all_rows += build_game(game)
        labeled_rows += build_game_labeled(game)
    if labeled_rows:
        print(f"labeled (per-printing) rows: {len(labeled_rows):,}")
    write_repairs(conn)

    if full:
        conn.executescript(
            """
            DROP TABLE IF EXISTS price_history_unified;
            CREATE TABLE price_history_unified (
                game       TEXT    NOT NULL,
                product_id INTEGER NOT NULL,
                printing   TEXT    NOT NULL DEFAULT '',  -- '' = base printing
                grade      TEXT    NOT NULL,
                date       TEXT    NOT NULL,
                price      REAL    NOT NULL,
                source     TEXT    NOT NULL,
                PRIMARY KEY (game, product_id, printing, grade, date)
            );
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO price_history_unified (game, product_id, "
            "printing, grade, date, price, source) VALUES (?,?, '', ?,?,?,?)", all_rows)
        conn.executemany(
            "INSERT OR REPLACE INTO price_history_unified (game, product_id, "
            "printing, grade, date, price, source) VALUES (?,?,?,?,?,?,?)", labeled_rows)
        conn.commit()
        # Covering index for the API's tier-history reads (latest/anchor prices,
        # sparklines). The PK autoindex can find the rows but not carry the price,
        # so each row costs a main-table fetch — a whole-game history sort on magic
        # touches 3M+ rows that way. Built after the bulk insert (faster); the
        # nightly incremental path updates it in place.
        conn.execute(
            "CREATE INDEX idx_history_tier ON price_history_unified "
            "(game, grade, product_id, date, price)")
        conn.commit()
        # A stale delta must never ride a later push on top of a full rebuild.
        if os.path.exists(DELTA_DB):
            os.remove(DELTA_DB)
        print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(PC_DB)} "
              f"(price_history_unified, full rebuild)")
        refresh_match_current(conn)
        conn.close()
        return

    n_ins, n_del = sync_incremental(conn, all_rows, labeled_rows)
    changed = refresh_match_current(conn)
    # Stage the changed match rows + an integrity check for the prod apply:
    # prod verifies its row count equals ours afterwards, else push_data.sh
    # falls back to the full-file push.
    conn.execute("CREATE TABLE delta.match_rows AS SELECT * FROM pricecharting WHERE 0")
    if changed:
        conn.executescript(
            "CREATE TEMP TABLE changed_keys (game TEXT, product_id INTEGER, "
            "PRIMARY KEY (game, product_id)) WITHOUT ROWID;")
        conn.executemany("INSERT INTO changed_keys VALUES (?,?)", sorted(changed))
        conn.execute(
            "INSERT INTO delta.match_rows SELECT p.* FROM pricecharting p "
            "JOIN changed_keys k ON k.game = p.game AND k.product_id = p.product_id")
        conn.execute("DROP TABLE changed_keys")
    total = conn.execute("SELECT COUNT(*) FROM price_history_unified").fetchone()[0]
    conn.execute("CREATE TABLE delta.meta (date TEXT, expected_unified_rows INTEGER)")
    conn.execute("INSERT INTO delta.meta VALUES (?, ?)", (nominal_day().isoformat(), total))
    conn.commit()
    conn.execute("DETACH delta")
    print(f"\nincremental: +{n_ins} / -{n_del} unified row(s) (table at {total:,}), "
          f"{len(changed)} match row(s) -> {os.path.normpath(DELTA_DB)} "
          f"({os.path.getsize(DELTA_DB) / 1e6:.1f} MB)")
    conn.close()


if __name__ == "__main__":
    main()
