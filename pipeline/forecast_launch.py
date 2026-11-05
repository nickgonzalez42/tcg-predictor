#!/usr/bin/env python3
"""
Early estimates for NEW cards (horizon 'launch1m') with self-healing priors.

Backtest verdict (2026-08-22, 50k launch cards / 482 sets): static cohort
priors of the first-month return beat flat by <=4% while per-card noise is
±25-40% — so this module does NOT pretend to know the point. It ships an
honest early estimate: point = a SHRUNK cohort prior (starts at flat), bands
= the empirical dispersion of historical launch returns, and a demotion gate
that re-runs the backtest's comparison nightly on this system's own graded
record — any cohort whose predictions lose to flat is forced back to flat.
Signal only ever survives where the live track record proves it.

Self-healing loop:
  issue (here) -> archive -> scorecard grades at +28d (dated, same as 1m)
  -> next night's priors/demotions read those grades -> issue again.

Live drift (2026-11-05, user: "I want it to be at least a little bold"):
a cohort with no graded record used to sit at exactly flat until its first
calls came due (a whole new game, Cyberpunk, showed 0% on every card for a
month). The point now starts from the cohort's OWN movement over the last
LIVE_DAYS days (median log return of cards like it: game / rarity / price
band, falling back to game / rarity and then the game), shrunk by
LIVE_SHRINK, plus CARD_SHRINK of the card's deviation from that cohort,
each capped. The graded record blends in with weight n/(n+K_SHRINK) as it
accrues, so the live drift fades as the record grows, and the demotion gate
still forces any cohort that loses to flat back to flat.

Rollout: rows are written under horizon='launch1m', which the API's trend
windows do not serve — the track record accrues silently until the UI grows
an explicitly-labeled "early estimate" display. Separate scorecard row; never
feeds fcerr model signals (excluded in write_signals).

Gated on TCG_FC_LAUNCH=1 (exits 0 otherwise — safe in the model block loop).
Runs AFTER forecast_predict (which drops/rebuilds `forecasts`).
"""

import collections
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import market_games, db_path

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                      "predictions.db")

MIN_MONTHS = 2        # cards with >= this many buckets belong to the real model
FRESH_DAYS = 7        # need a TCG price this recent to anchor an estimate
K_SHRINK = 50         # prior weight n/(n+K): 50 graded rows = half strength
MIN_DEMOTE = 40       # graded rows needed before the demotion gate can rule
MIN_COHORT = 8        # historical launch cohort size for the dispersion seed
SEED_Q = (-0.40, -0.03, 0.35)   # q10/med/q90 fallback (backtest dispersion)
LIVE_DAYS = 28        # live drift: the newest TCG price vs the last one >= this old
LIVE_SHRINK = 0.5     # weight of the cohort's live drift where the graded record is thin
CARD_SHRINK = 0.25    # weight of the card's own deviation from its cohort
LIVE_CAP = 0.15       # caps (log return): cohort part, card part, whole prior
CARD_CAP = 0.10
PRIOR_CAP = 0.20


def clamp(x, c):
    return max(-c, min(c, x))


def band_of(p):
    return "<10" if p < 10 else "10-50" if p < 50 else "50-200" if p < 200 else "200+"


def next_month(m, k=1):
    y, mo = int(m[:4]), int(m[5:7]) + k
    return f"{y + (mo - 1) // 12:04d}-{(mo - 1) % 12 + 1:02d}"


def launch_seed_quantiles(conn):
    """Per-game q10/med/q90 of historical first-month launch returns —
    the dispersion (not the point) that makes early bands honest."""
    monthly = collections.defaultdict(dict)
    for g, pid, m, p in conn.execute(
            "SELECT game, product_id, substr(date,1,7), price FROM ("
            "  SELECT game, product_id, date, price, ROW_NUMBER() OVER ("
            "    PARTITION BY game, product_id, substr(date,1,7) ORDER BY date DESC) rn"
            "  FROM price_history_unified WHERE grade='ungraded' AND printing='') "
            "WHERE rn=1"):
        if p and p > 0:
            monthly[(g, pid)][m] = p
    sets = {}
    for gdb in market_games():
        c = sqlite3.connect(db_path(gdb), timeout=30)
        for pid, s in c.execute("SELECT product_id, set_name FROM cards"):
            sets[(gdb, pid)] = s or "?"
        c.close()
    game_start, births = {}, {}
    for key, ser in monthly.items():
        births[key] = min(ser)
        game_start[key[0]] = min(game_start.get(key[0], "9999"), births[key])
    cohorts = collections.defaultdict(list)
    for (g, pid), b in births.items():
        cohorts[(g, sets.get((g, pid), "?"), b)].append(pid)
    rets = collections.defaultdict(list)
    for (g, s, lm), pids in cohorts.items():
        if s == "?" or len(pids) < MIN_COHORT or lm <= next_month(game_start[g], 2):
            continue
        for pid in pids:
            ser = monthly[(g, pid)]
            if lm in ser and next_month(lm) in ser:
                r = math.log(ser[next_month(lm)] / ser[lm])
                if abs(r) <= 2.5:
                    rets[g].append(r)
    out = {}
    for g, rs in rets.items():
        if len(rs) >= 100:
            rs.sort()
            out[g] = (rs[len(rs) // 10], rs[len(rs) // 2], rs[-max(1, len(rs) // 10)])
    return out


def graded_priors(pred_conn, attrs):
    """(n graded, median realized return) per cohort key + demotions from THIS
    system's graded record. The caller blends the median in with weight
    n/(n+K_SHRINK); a demoted key is (n, 0.0) and overrides everything."""
    try:
        rows = pred_conn.execute(
            "SELECT game, product_id, ret, realized_ret, base_price FROM forecast_archive "
            "WHERE horizon='launch1m' AND realized_ret IS NOT NULL "
            "  AND base_price > 0").fetchall()
    except sqlite3.Error:
        return {}, set()
    by_key = collections.defaultdict(list)
    for g, pid, ret, real, base in rows:
        rar, bd = attrs.get((g, pid), ("?", "?"))[1], band_of(base)
        for key in ((g, rar, bd), (g, rar), (g,)):
            by_key[key].append((ret or 0.0, real))
    priors, demoted = {}, set()
    for key, lst in by_key.items():
        reals = sorted(r for _, r in lst)
        n = len(reals)
        if n >= MIN_DEMOTE:
            mae_pred = sum(abs(p - r) for p, r in lst) / n
            mae_flat = sum(abs(r) for _, r in lst) / n
            if mae_pred >= mae_flat:
                demoted.add(key)      # the live record says: predict flat
                priors[key] = (n, 0.0)
                continue
        priors[key] = (n, reals[n // 2])
    return priors, demoted


def live_drift(pc, newest, attrs, today):
    """Each card's log return over the last LIVE_DAYS days (its newest TCGplayer
    price vs the last one at least that old, within twice the window), and the
    median of that over every cohort key with >= MIN_COHORT cards."""
    since = (today - timedelta(days=LIVE_DAYS * 2)).isoformat()
    until = (today - timedelta(days=LIVE_DAYS)).isoformat()
    past = {}
    for g, pid, _d, p in pc.execute(
            "SELECT game, product_id, MAX(date), price FROM tcg_nm_history "
            "WHERE printing='' AND date BETWEEN ? AND ? AND price > 0 "
            "GROUP BY game, product_id", (since, until)):
        past[(g, pid)] = p
    card = {}
    for key, (_d, p) in newest.items():
        q = past.get(key)
        if q and p and p > 0 and key in attrs:
            r = math.log(p / q)
            if abs(r) <= 2.5:
                card[key] = r
    by_key = collections.defaultdict(list)
    for (g, pid), r in card.items():
        rar, bd = attrs[(g, pid)][1], band_of(newest[(g, pid)][1])
        for k in ((g, rar, bd), (g, rar), (g,)):
            by_key[k].append(r)
    cohort = {}
    for k, rs in by_key.items():
        if len(rs) >= MIN_COHORT:
            rs.sort()
            cohort[k] = rs[len(rs) // 2]
    return card, cohort


def main():
    if os.environ.get("TCG_FC_LAUNCH") != "1":
        print("forecast_launch: TCG_FC_LAUNCH not set — skipped")
        return
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    now_iso = now.isoformat(timespec="seconds")
    fresh_cut = (now.date() - timedelta(days=FRESH_DAYS)).isoformat()

    pc = sqlite3.connect(PC_DB, timeout=120)
    seeds = launch_seed_quantiles(pc)
    buckets = dict(pc.execute(
        "SELECT game || ':' || product_id, COUNT(DISTINCT substr(date,1,7)) "
        "FROM price_history_unified WHERE grade='ungraded' AND printing='' "
        "GROUP BY game, product_id"))
    newest = {}
    for g, pid, d, p in pc.execute(
            "SELECT game, product_id, MAX(date), price FROM tcg_nm_history "
            "WHERE printing='' GROUP BY game, product_id"):
        newest[(g, pid)] = (d, p)

    attrs = {}
    for g in market_games():
        c = sqlite3.connect(db_path(g), timeout=30)
        for pid, s, r, nm, img in c.execute(
                "SELECT product_id, set_name, rarity, near_mint_price, image_path FROM cards"):
            if nm is not None and img:
                attrs[(g, pid)] = (s or "?", r or "?")
        c.close()
    card_drift, cohort_drift = live_drift(pc, newest, attrs, now.date())
    pc.close()

    out = sqlite3.connect(OUT_DB, timeout=120)
    priors, demoted = graded_priors(out, attrs)

    # Cards the main model covers keep its call: a standard row (1m/6m/12m) at
    # the base printing, or its own single-bucket EARLY call (2026-10-23:
    # forecast_predict publishes those under this horizon with model_version
    # '-early'). This tier only fills what it left blank (no art, no CLIP row,
    # a segment that could not train).
    # In a game the model DID publish, a card with >= MIN_MONTHS buckets and no
    # row was left out on purpose (stale anchor) and stays blank. 2026-11-04:
    # that bucket test used to apply to every game, which froze the four
    # CSV-only games at their 2026-10-31 anchors: once they reached two buckets
    # it skipped them, but their segments still cannot train (MIN_SAMPLES), so
    # nothing re-anchored the rows and the tiles showed a flat call's price
    # drift as an early call (sorcery/521512 anchored $0.52, price $0.56, read
    # -7%). Now a game with no standard ungraded rows at all is re-anchored
    # nightly until the model takes it over.
    standard = {(g, pid) for g, pid in out.execute(
        "SELECT DISTINCT game, product_id FROM forecasts WHERE printing='' "
        "AND target='ungraded' AND horizon IN ('1m','6m','12m')")}
    trained = {g for g, _ in standard}
    covered = standard | {(g, pid) for g, pid in out.execute(
        "SELECT game, product_id FROM forecasts WHERE horizon='launch1m' "
        "AND printing='' AND model_version LIKE '%-early'")}
    # Rows this tier issued for cards the model has since taken over are
    # stale the day the standard row lands (frozen at an old anchor, always
    # flat) and only ever surfaced where a window had no standard row -- the
    # 6M/1Y tiles of a 1M-only game (2026-11-05). Drop them.
    n_stale = out.execute(
        "DELETE FROM forecasts WHERE horizon='launch1m' AND model_version='launch-v1' "
        "AND EXISTS (SELECT 1 FROM forecasts s WHERE s.game=forecasts.game "
        "AND s.product_id=forecasts.product_id AND s.printing='' AND s.target='ungraded' "
        "AND s.horizon IN ('1m','6m','12m'))").rowcount
    rows = []
    for (g, pid), (s, rar) in attrs.items():
        if (g, pid) in covered:
            continue                      # the model's own call stands
        if g in trained and buckets.get(f"{g}:{pid}", 0) >= MIN_MONTHS:
            continue                      # real model's territory
        d_p = newest.get((g, pid))
        if not d_p or d_p[0] < fresh_cut or not d_p[1] or d_p[1] <= 0:
            continue                      # no fresh TCG anchor -> stay blank
        base = float(d_p[1])
        bd = band_of(base)
        keys = ((g, rar, bd), (g, rar), (g,))
        key = next((k for k in keys if k in priors), None)
        n, med = priors[key] if key else (0, 0.0)
        live = next((cohort_drift[k] for k in keys if k in cohort_drift), 0.0)
        own = card_drift.get((g, pid))
        if key in demoted:
            prior, basis = 0.0, "cohort demoted to flat by its graded record"
        else:
            w = n / (n + K_SHRINK)
            cohort_part = w * med + (1 - w) * LIVE_SHRINK * clamp(live, LIVE_CAP)
            card_part = CARD_SHRINK * clamp(own - live, CARD_CAP) if own is not None else 0.0
            prior = clamp(cohort_part + card_part, PRIOR_CAP)
            basis = (f"graded record n={n} median {med:+.3f}; cohort drift {live:+.3f} "
                     f"over {LIVE_DAYS}d" + (f"; this card {own:+.3f}" if own is not None else ""))
        q10, _, q90 = seeds.get(g, SEED_Q)
        lo, hi = base * math.exp(min(q10, prior - 0.05)), base * math.exp(max(q90, prior + 0.05))
        why = (f"Early estimate: young price series (new card or repaired "
               f"data); cohort {g}/{rar}/{bd} prior {prior:+.3f} ({basis}; "
               f"self-healing vs graded record); bands = historical launch dispersion")
        rows.append((g, pid, "ungraded", "launch1m", today, round(base, 2),
                     round(base * math.exp(prior), 2), round(lo, 2), round(hi, 2),
                     round(prior, 4), why, "low", "launch-v1", now_iso, d_p[0], ""))

    out.executemany(
        "INSERT OR REPLACE INTO forecasts (game, product_id, target, horizon, as_of, "
        "base_price, forecast_price, low, high, ret, reason, confidence, model_version, "
        "scored_at, anchor_date, printing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    out.executemany(
        "INSERT OR IGNORE INTO forecast_archive (game, product_id, target, horizon, "
        "as_of, base_price, forecast_price, low, high, ret, confidence, model_version, "
        "scored_at, printing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[11], r[12],
          r[13], r[15]) for r in rows])
    out.commit()
    n_dem = len(demoted)
    n_bold = sum(1 for r in rows if abs(r[9]) >= 0.0005)
    print(f"forecast_launch: {len(rows)} early estimate(s) issued "
          f"({len(priors)} graded cohort keys, {len(cohort_drift)} cohorts with live drift, "
          f"{n_dem} demoted to flat, {n_bold} non-flat calls); "
          f"{n_stale} stale row(s) of model-covered cards dropped")
    out.close()


if __name__ == "__main__":
    # Never fails the model block: an early-estimate hiccup must not stop the push.
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"forecast_launch: non-fatal error: {type(e).__name__}: {e}",
              file=sys.stderr)
    sys.exit(0)
