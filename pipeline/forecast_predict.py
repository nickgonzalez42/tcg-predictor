"""
Generate per-card 6m/12m forecasts for EVERY grade tier that has enough history.

For each (game, tier, horizon):
  1. build (t -> t+h) log-return samples from the tier's monthly series
  2. temporal split (train < CUTOFF, test >= CUTOFF) -> measure out-of-sample
     log-return MAE, used as the confidence band (magnitude is noisier than direction)
  3. retrain on ALL samples, forecast from each card's latest month

Output: predictions.db `forecasts`
  (game, product_id, target, horizon, as_of, base_price, forecast_price, low, high, ret, ...)
plus an append-only copy of every first-issued forecast in `forecast_archive`,
which forecast_scorecard.py later grades against realized prices.

Run:  .venv/bin/python forecast_predict.py
"""

import csv
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from forecast_deep import (load_matrix, load_matrix_all, static_features, traj_block,
                           set_matrix, extra_signal_matrices, market_features,
                           cum_stats, model_new, model_quantile, rolling_cutoff,
                           art_pids)
import forecast_calib as fcal

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

MODEL_VERSION = "forecast-deep-v4.5"  # v4.5 (2026-10-10 review): absolute-error point
# loss, steeper price weights, label guards, publish-time per-direction shrink
# and width-stratified conformal bands (see forecast_calib). The archive keeps
# every version's rows and the site never filters on this tag.
# v4.4: art embedding PCA back in; cards
                                      # with no artwork on file are not forecast
                                      # v4.3: rolling OOS cutoff, log-price sample
                                      # weights, graded/raw premium features, split-
                                      # conformal bands, sqrt-t 1w bands, no img PCA
                                      # v4.2: cross-game market features
                                      # v4.1: confidence, set/age/drawdown, reasons
HORIZONS = {"1m": 1, "6m": 6, "12m": 12}
# (WEEK_FRACTION / the pro-rated 1w horizon removed 2026-08-14 — a true weekly
# model arrives once the daily NM data matures, ~Oct 2026.)
# bgs10/cgc10/sgc10 dropped 2026-08-01: their only source was the retired paid
# PriceCharting CSV; no public page carries them, and their unified history is
# no longer built — the segments would just load empty matrices.
TARGETS = ["ungraded", "grade7", "grade8", "grade9", "grade95", "psa10"]
RET_CLIP = np.log(10.0)
# Training-label guard (2026-10-10): a monthly move beyond 5x in either
# direction is a listing-repair/data event (a lone bogus TCGplayer ask
# stepping a card 5-10x and back), not a market move — the same bound the
# weekly report applies. Such labels taught the model phantom reversions.
LABEL_CLIP = np.log(5.0)
# Segments with no valid temporal split (young games) have NO out-of-sample
# evidence at all — default above MAX_SEGMENT_MAE so they publish flagged
# low-confidence with the caution note instead of passing silently.
DEFAULT_BAND = 0.65
MIN_SAMPLES = 200
CONF_TARGET = 0.80      # nominal band coverage; conformal widening enforces it
# Lowered 3M -> 1.2M on 2026-08-01: un-gating ungraded brought ~57k extra cards
# into the big segments (magic ungraded hit 2.57M train rows) and the full-size
# fit OOM-killed the nightly on this 8GB machine. The subsample keeps a random
# stratum of rows; watch the affected segments' OOS retMAE for drift.
MAX_TRAIN_SAMPLES = 1_200_000   # per (game, tier, horizon); see subsample below
# Publication gates: a segment whose out-of-sample retMAE is worse than this
# publishes nothing (mature games run ~0.08-0.53), and an individual card's
# low-confidence call beyond ~±200% log-return is dropped as decorated noise.
MAX_SEGMENT_MAE = 0.60
EXTREME_LOW_CONF_RET = 1.1

# ---- feature buckets for per-card attribution -------------------------------
# Trajectory features are already narrated directly (momentum/volatility/volume);
# the static features are grouped into buckets we can ablate one at a time.
TRAJ_COLS = {"logp", "ret1", "ret3", "ret12", "vol6", "hist", "logvol", "volchg",
             "age", "dd", "setret3", "setret12", "setrel",
             "mktret3", "mktret12", "gameret12", "gamerel12"}
STAT_COLS = {  # the card's printed stat line, both games
    "life", "power", "cost", "counter", "attribute", "subtypes", "color",
    "hp", "stage", "energy_type", "attack1", "attack2", "attack3", "attack4",
    "weakness", "resistance", "retreat_cost",
}
BUCKET_LABEL = {"stats": "stat line", "art": "art style", "identity": "rarity/set profile"}


def bucket_of(col):
    if col in TRAJ_COLS or col.startswith("sig_"):
        return "traj"
    if col.startswith("img"):
        return "art"
    if col in STAT_COLS:
        return "stats"
    return "identity"


def bucket_deltas(model, Xnow, X):
    """How much each feature bucket moves each card's forecast (in log-return).

    Ablation attribution: replace one bucket's columns with a 'typical card'
    value (median / most common in the training sample) and re-predict. The
    difference is that bucket's pull on this specific card — positive means the
    bucket lifts the forecast above a typical card's, negative means it drags.
    """
    preds = model.predict(Xnow)
    out = {}
    for b in ("stats", "art", "identity"):
        cols = [c for c in Xnow.columns if bucket_of(c) == b]
        if not cols:
            out[b] = np.zeros(len(Xnow))
            continue
        Xa = Xnow.copy()
        for c in cols:
            s = X[c]
            if isinstance(s.dtype, pd.CategoricalDtype):
                mode = s.mode(dropna=True)
                v = mode.iloc[0] if len(mode) else None
                Xa[c] = pd.Series([v] * len(Xa), dtype=s.dtype, index=Xa.index)
            else:
                Xa[c] = float(s.median())
        out[b] = preds - model.predict(Xa)
    return out


_COMPS_CACHE = {}


def art_comps(game):
    """product_id -> (comp_ret12, comp_n, comp_ids) from art_comps.py output."""
    if game not in _COMPS_CACHE:
        import json
        path = os.path.join(BASE, "ml_data", f"{game}_art_comps.csv")
        comps = {}
        if os.path.exists(path):
            df = pd.read_csv(path)
            ids_col = df["comp_ids"] if "comp_ids" in df else [None] * len(df)
            for pid, n, r, ids in zip(df["product_id"], df["comp_n"], df["comp_ret12"], ids_col):
                if pd.notna(r):
                    try:
                        parsed = tuple(int(x) for x in json.loads(ids)) if isinstance(ids, str) else ()
                    except (ValueError, TypeError):
                        parsed = ()
                    comps[int(pid)] = (float(r), int(n), parsed)
        _COMPS_CACHE[game] = comps
    return _COMPS_CACHE[game]


def _pct(logret):
    if logret is None or np.isnan(logret):
        return None
    return (np.exp(logret) - 1.0) * 100.0


def comp_clause(comp, examples=None):
    """'Cards with similar art …' from real look-alike retention (12-month),
    naming up to two of the actual comparable cards."""
    if comp is None:
        return None
    r = comp[0]
    if r >= 3.0:
        text = "cards with similar art more than tripled in value over the past year"
    elif r >= 2.0:
        text = "cards with similar art more than doubled in value over the past year"
    elif r >= 1.10:
        text = f"cards with similar art gained ~{(r - 1) * 100:.0f}% over the past year"
    elif r <= 0.90:
        text = f"cards with similar art lost ~{(1 - r) * 100:.0f}% over the past year"
    else:
        text = "cards with similar art held their value over the past year"
    if examples:
        text += f" (e.g. {', '.join(examples)})"
    return text


# ---- card-specific reason text ----------------------------------------------
# Raw printed attributes (rarity, set, stat line) let the attribution clauses
# name THIS card's traits instead of a generic bucket label.
_TRAITS_CACHE = {}


def card_traits(game):
    """product_id -> raw printed attributes used for specific reason wording."""
    if game not in _TRAITS_CACHE:
        cols = ["product_id", "name", "rarity", "set_name", "cost", "power",
                "hp", "stage", "energy_type"]
        df = pd.read_csv(os.path.join(BASE, "ml_data", f"{game}_cards.csv"))
        keep = [c for c in cols if c in df.columns]
        traits = {}
        for row in df[keep].itertuples(index=False):
            d = dict(zip(keep, row))
            traits[int(d.pop("product_id"))] = d
        _TRAITS_CACHE[game] = traits
    return _TRAITS_CACHE[game]


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _txt(v):
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _pull(subject, pct, pid, proj=None):
    """'<subject> lifts/drags the forecast' with per-card phrasing variety.
    Extreme percentages are real (a vintage set profile can dwarf a 'typical
    card') but read as bugs — above 100% switch to qualitative wording.
    When the pull and the headline forecast point opposite ways, say what the
    pull actually does (cushions a decline / holds back a gain) so the reason
    never reads as contradicting the number beside it."""
    up = pct > 0
    if proj is not None and up and proj < 0:
        variants = [
            f"{subject} cushions the decline (without it the projected drop would be steeper)",
            f"{subject} keeps the projected drop smaller than a typical card's",
        ]
        return variants[pid % len(variants)]
    if proj is not None and not up and proj > 0:
        variants = [
            f"{subject} holds the forecast back from a larger gain",
            f"{subject} trims what would otherwise be a bigger projected gain",
        ]
        return variants[pid % len(variants)]
    if abs(pct) >= 100:
        variants = [
            f"{subject} strongly {'lifts' if up else 'drags on'} the forecast",
            f"{subject} is a major {'tailwind' if up else 'headwind'} here",
        ]
    else:
        variants = [
            f"{subject} {'lifts' if up else 'drags'} the forecast ({pct:+.0f}% vs a typical card)",
            f"{subject} {'adds' if up else 'takes'} ~{abs(pct):.0f}% {'to' if up else 'off'} a typical card's outlook",
        ]
    return variants[pid % len(variants)]


def _stats_subject(traits):
    """Name the actual printed stat line (One Piece cost/power, Pokémon HP/stage)."""
    t = traits or {}
    power, cost, hp = _num(t.get("power")), _num(t.get("cost")), _num(t.get("hp"))
    if power is not None:
        return f"its {int(cost)}-cost, {int(power):,}-power stat line" if cost is not None \
            else f"its {int(power):,}-power stat line"
    if hp is not None:
        bits = f"{int(hp)}-HP"
        energy, stage = _txt(t.get("energy_type")), _txt(t.get("stage"))
        if energy:
            bits += f" {energy}"
        if stage:
            bits += f" {stage}"
        return f"its {bits} stat line"
    return "its printed stat line"


def _identity_subject(traits):
    """Name the actual rarity/set instead of 'rarity/set profile'."""
    t = traits or {}
    rarity, set_name = _txt(t.get("rarity")), _txt(t.get("set_name"))
    if rarity and set_name:
        return f"its {rarity} printing in {set_name}"
    if rarity:
        return f"its {rarity} printing"
    if set_name:
        return f"its {set_name} set profile"
    return "its rarity/set profile"


def make_reason(ret, mom3, trend12, vol6, horizon,
                vol_now=None, volchg=None, comp=None, deltas_i=None,
                traits=None, pid=0, set12=None, comp_examples=None):
    """Plain-English 'why': every candidate signal gets a salience score and only
    the strongest 2-3 survive, so each card leads with ITS story — a hot trend,
    a falling market, a standout stat line, a chase-rarity printing, or art
    whose look-alikes genuinely moved. Confidence (history depth) is deliberately
    NOT repeated here; the UI already shows it as a pill."""
    proj = _pct(ret)
    m3, t12 = _pct(mom3), _pct(trend12)
    cand = []  # (salience, clause)

    # --- trajectory: concrete, already card-specific ---
    if t12 is not None and abs(t12) >= 10:
        up = t12 > 0
        variants = [
            f"{'up' if up else 'down'} {abs(t12):.0f}% over the past year",
            f"a {abs(t12):.0f}% {'climb' if up else 'slide'} across twelve months",
        ]
        cand.append((min(abs(t12), 80), variants[pid % len(variants)]))
    if m3 is not None and abs(m3) >= 8:
        text = f"3-month momentum {m3:+.0f}%"
        if proj is not None and (proj > 0) != (m3 > 0):
            text += ", which the model expects to fade (mean-reversion)"
        cand.append((min(abs(m3) * 1.3, 85), text))
    if vol6 is not None and not np.isnan(vol6) and vol6 >= 0.15:
        cand.append((14, "a volatile price history widens the range"))

    # --- set performance: how the card's whole set is trading ---
    s12 = _pct(set12) if set12 is not None else None
    if s12 is not None and abs(s12) >= 10:
        set_name = _txt((traits or {}).get("set_name"))
        subject = f"its set ({set_name})" if set_name else "its set overall"
        cand.append((min(abs(s12) * 0.9, 75),
                     f"{subject} is {'up' if s12 > 0 else 'down'} {abs(s12):.0f}% over the past year"))

    # --- liquidity: only when it says something (thin, or clearly moving) ---
    if vol_now is not None and not np.isnan(vol_now) and vol_now > 0:
        rate = f"~{vol_now:.0f}/mo sold" if vol_now >= 1 else "under 1/mo sold"
        if vol_now < 3:
            cand.append((16, f"very thin trading ({rate}) makes the price noisy"))
        elif volchg is not None and not np.isnan(volchg) and abs(volchg) >= 0.4:
            cand.append((18, f"sales volume is {'rising' if volchg > 0 else 'falling'} ({rate})"))

    # --- trait attribution: each bucket competes on its own ablation pull ---
    for bucket, delta in (deltas_i or {}).items():
        p = _pct(delta)
        if p is None or abs(p) < 8:
            continue
        if bucket == "stats":
            cand.append((min(abs(p), 90), _pull(_stats_subject(traits), p, pid, proj)))
        elif bucket == "identity":
            cand.append((min(abs(p), 90), _pull(_identity_subject(traits), p, pid, proj)))
        elif bucket == "art":
            # prefer the concrete look-alike stat over a generic art clause
            clause = comp_clause(comp, comp_examples) or _pull("its artwork profile", p, pid, proj)
            cand.append((min(abs(p), 90), clause))
    # look-alikes that moved dramatically are worth naming even when the model
    # doesn't credit the art bucket for this card
    if comp is not None and not any("similar art" in c for _, c in cand):
        r = comp[0]
        if r >= 1.5 or r <= 0.6:
            cand.append((min(abs(r - 1) * 60, 70), comp_clause(comp, comp_examples)))

    cand.sort(key=lambda sc: -sc[0])
    top = [c for _, c in cand[:3]]
    if not top:
        return f"Projects {proj:+.0f}% over {horizon}. Few distinguishing signals — the trajectory is flat."
    lead = ("Key drivers", "Behind it", "What moves it", "Signals")[pid % 4]
    return f"Projects {proj:+.0f}% over {horizon}. {lead}: {'; '.join(top)}."


def price_weight(p):
    """Training sample weight from the base price. 42-74% of ungraded cards sit
    under $1, where a single $0.25 tick is a ±100% log-return — unweighted,
    that noise dominates the loss. Weight ∝ log-price keeps every card in
    training (and every card still gets a forecast) while making the model
    attend to value-relevant moves: $0.25 -> 0.2, $1 -> 0.5, $10 -> 1, $100+ -> 1.5-2.
    """
    # Doubles per price decade (2026-10-10 review): 88% of forecasts are
    # sub-$10 cards whose daily market price is mostly noise; they still
    # get forecasts, they just stop dominating the fit.
    return np.clip(2.0 ** (np.log10(np.maximum(p, 0.01)) - 1.0), 0.15, 4.0)


# Cross-tier context: the graded/raw premium is one of the most informative
# signals in the hobby (premium expansion/mean-reversion), and each tier's
# model previously trained blind to every other tier. Cached per game — the
# same pair serves all nine target tiers.
_PREMIUM_CACHE = {}


def premium_matrices(game, pids, dates):
    """{name: cards x months} — log(psa10/ungraded) level and its 3-month
    change, aligned to the current tier's (pids, dates) grid. NaN where either
    side is missing; HGB handles NaN natively. Fed through the EXTRA-signal
    path, so the features arrive as sig_premium / sig_premchg3."""
    if game not in _PREMIUM_CACHE:
        _PREMIUM_CACHE.clear()   # one game at a time; keep memory flat
        _PREMIUM_CACHE[game] = (load_matrix(game, "ungraded"), load_matrix(game, "psa10"))
    (upids, udates, U), (gpids, gdates, G) = _PREMIUM_CACHE[game]
    if len(gpids) == 0 or len(upids) == 0:
        return {}

    uidx = {int(p): i for i, p in enumerate(upids)}
    gidx = {int(p): i for i, p in enumerate(gpids)}
    ud = {d: j for j, d in enumerate(udates)}
    gd = {d: j for j, d in enumerate(gdates)}
    ucols = np.array([ud.get(d, -1) for d in dates])
    gcols = np.array([gd.get(d, -1) for d in dates])
    um, gm = ucols >= 0, gcols >= 0

    PREM = np.full((len(pids), len(dates)), np.nan)
    for i, p in enumerate(pids):
        ui, gi = uidx.get(int(p)), gidx.get(int(p))
        if ui is None or gi is None:
            continue
        urow = np.full(len(dates), np.nan)
        grow = np.full(len(dates), np.nan)
        urow[um] = U[ui, ucols[um]]
        grow[gm] = G[gi, gcols[gm]]
        ok = np.isfinite(urow) & np.isfinite(grow) & (urow > 0) & (grow > 0)
        PREM[i, ok] = np.log(grow[ok] / urow[ok])

    CH3 = np.full_like(PREM, np.nan)
    if PREM.shape[1] > 3:
        CH3[:, 3:] = PREM[:, 3:] - PREM[:, :-3]
    out = {"premium": PREM, "premchg3": CH3}

    # Slice 5 — cross-tier ladder (TCG_FC_FEAT_LADDER, backtest-gated,
    # default OFF): the card's graded premium relative to its SET's median
    # premium that month. "Rich or cheap against the cohort norm" is the
    # mean-reversion signal the raw premium level can't carry, since set
    # norms differ wildly. Sets with <3 premium-priced members stay NaN.
    if os.environ.get("TCG_FC_FEAT_LADDER"):
        import warnings
        from forecast_deep import _set_labels
        name_of = _set_labels(game)
        labels = np.array([name_of.get(int(p), "") for p in pids])
        REL = np.full_like(PREM, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            for s in np.unique(labels):
                rows = labels == s
                if not s or rows.sum() < 3:
                    continue
                REL[rows] = PREM[rows] - np.nanmedian(PREM[rows], axis=0)
        out["premrel"] = REL
    return out


def liquidity_matrices(game, target, pids, dates):
    """Slice 4b — NM tape liquidity (TCG_FC_FEAT_LIQ, backtest-gated,
    default OFF). From tcg_nm_history, per (card, month), over a trailing
    3-bucket window: sig_liqmove = fraction of consecutive prints whose
    price actually CHANGED (a 90-day flat tape is an asking-price echo, not
    a market), and sig_liqobs = log1p(prints observed). Ungraded only — the
    daily NM tape is a TCGplayer thing; months before collection began
    (2026-07) stay NaN and HGB handles the missing era natively. Trailing
    windows only, so --as-of backtests can't see past their cutoff."""
    if target != "ungraded" or not os.environ.get("TCG_FC_FEAT_LIQ"):
        return {}
    from forecast_deep import PC_DB
    didx = {d: j for j, d in enumerate(dates)}
    pidx = {}
    for i, p in enumerate(pids):
        pidx.setdefault(int(p), []).append(i)
    obs, mov, last = {}, {}, {}
    conn = sqlite3.connect(PC_DB, timeout=180)
    for pid, m, price in conn.execute(
            "SELECT product_id, substr(date, 1, 7), price FROM tcg_nm_history "
            "WHERE game=? ORDER BY product_id, date", (game,)):
        if pid not in pidx or price is None:
            continue
        k = (pid, m)
        obs[k] = obs.get(k, 0) + 1
        prev = last.get(pid)
        if prev is not None and abs(price - prev) > 1e-9:
            mov[k] = mov.get(k, 0) + 1
        last[pid] = price
    conn.close()
    if not obs:
        return {}
    shape = (len(pids), len(dates))
    O = np.zeros(shape)
    Mv = np.zeros(shape)
    seen = np.zeros(shape)
    for (pid, m), n in obs.items():
        j = didx.get(m)
        if j is not None:
            for i in pidx[pid]:
                O[i, j] += n
                seen[i, j] = 1.0
    for (pid, m), n in mov.items():
        j = didx.get(m)
        if j is not None:
            for i in pidx[pid]:
                Mv[i, j] += n
    w = 3
    def trail(C):
        c = np.cumsum(C, axis=1)
        out_ = c.copy()
        out_[:, w:] = c[:, w:] - c[:, :-w]
        return out_
    Ow, Mw, Sw = trail(O), trail(Mv), trail(seen)
    pairs = np.maximum(Ow - Sw, 1.0)   # ~ n-1 consecutive pairs per observed month
    MOVE = np.where(Sw > 0, Mw / pairs, np.nan)
    OBS = np.where(Sw > 0, np.log1p(Ow), np.nan)
    return {"liqmove": MOVE, "liqobs": OBS}


def anchor_dates_all(game, grade):
    """(pid, printing) -> real date of that series' newest history point."""
    from forecast_deep import PC_DB
    rows = sqlite3.connect(PC_DB, timeout=180).execute(
        "SELECT product_id, printing, MAX(date) FROM price_history_unified "
        "WHERE game=? AND grade=? GROUP BY product_id, printing", (game, grade)).fetchall()
    return {(pid, pr): d[:10] for pid, pr, d in rows if d}


def anchor_dates(game, grade):
    """pid -> real date of the tier's newest history point. The model anchors
    on the month bucket (whose price IS that newest point); this is the honest
    display date for it."""
    from forecast_deep import PC_DB
    rows = sqlite3.connect(PC_DB, timeout=180).execute(
        "SELECT product_id, MAX(date) FROM price_history_unified "
        "WHERE printing='' AND game=? AND grade=? GROUP BY product_id", (game, grade)).fetchall()
    return {pid: d[:10] for pid, d in rows if d}


REVIEWED_CSV = os.path.join(BASE, "ml_data", "pc_match_reviewed.csv")


def reviewed_pids(game):
    """Match-review gate: product_ids whose PriceCharting match the user has
    confirmed (match_review.py -> pc_match_reviewed.csv) at the pc_id the
    match table currently carries. A new or drifted match waits OUT of the
    model until confirmed — an unreviewed wrong match would train and forecast
    on some other card's prices. Returns None (gate off) until the review
    file exists, so a lost/renamed file can't silently zero the forecasts."""
    if not os.path.exists(REVIEWED_CSV):
        return None
    ok = {}
    with open(REVIEWED_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):   # later rows win: confirm -> override
            if r["game"] == game:
                ok[int(r["product_id"])] = int(r["pc_id"])
    from forecast_deep import PC_DB
    conn = sqlite3.connect(PC_DB, timeout=180)
    cur = conn.execute("SELECT product_id, pc_id FROM pricecharting "
                       "WHERE game=? AND pc_id IS NOT NULL", (game,)).fetchall()
    conn.close()
    return {pid for pid, pc in cur if ok.get(pid) == pc}


def _rolling_live_widen(game, target, hname, days=150, min_n=200):
    """Conformal widen implied by RECENTLY GRADED live forecasts (self-healing
    calibration source). None when the archive is missing or too thin — the
    split-conformal path then stands alone. Read-only on OUT_DB; a backtest
    or first run without an archive is a silent no-op."""
    try:
        c = sqlite3.connect(OUT_DB, timeout=10)
        rows = c.execute(
            "SELECT base_price, low, high, realized_ret FROM forecast_archive "
            "WHERE game=? AND target=? AND horizon=? AND realized_ret IS NOT NULL "
            "  AND low > 0 AND high > 0 AND base_price > 0 "
            "  AND substr(model_version, 1, 2) != '__' "
            "  AND graded_at >= datetime('now', ?)",
            (game, target, hname, f"-{days} day")).fetchall()
        c.close()
    except sqlite3.Error:
        return None
    if len(rows) < min_n:
        return None
    E = [max(math.log(lo / b) - r, r - math.log(hi / b))
         for b, lo, hi, r in rows]
    return float(fcal.conformal_quantile(np.array(E), CONF_TARGET))


def _rolling_live_width_widen(game, target, hname, days=150, min_n=200):
    """Per-width-stratum version of _rolling_live_widen: {stratum: widen}
    from recently graded live rows, keyed by each row's RAW band width
    (published width for pre-v4.5 rows, which were barely widened)."""
    try:
        c = sqlite3.connect(OUT_DB, timeout=10)
        rows = c.execute(
            "SELECT base_price, low, high, realized_ret, raw_width FROM forecast_archive "
            "WHERE game=? AND target=? AND horizon=? AND realized_ret IS NOT NULL "
            "  AND low > 0 AND high > 0 AND base_price > 0 "
            "  AND substr(model_version, 1, 2) != '__' "
            "  AND graded_at >= datetime('now', ?)",
            (game, target, hname, f"-{days} day")).fetchall()
        c.close()
    except sqlite3.Error:
        return None
    if len(rows) < min_n:
        return None
    E = np.array([max(math.log(lo / b) - r, r - math.log(hi / b)) for b, lo, hi, r, _ in rows])
    width = np.array([rw if rw is not None else math.log(hi / lo) for _, lo, hi, _, rw in rows])
    s = fcal.width_stratum(width)
    return {int(si): float(fcal.conformal_quantile(E[s == si], CONF_TARGET))
            for si in np.unique(s) if (s == si).sum() >= min_n}


def _rolling_live_shrink(game, target, hname, version, days=120, min_n=500):
    """Per-direction shrink refit on recently graded live rows of THIS model
    version (a loss change rescales predictions, so other versions' rows
    would mis-calibrate it). Fits on the RAW archived point so a shrink is
    never compounded. None until enough same-version rows have graded."""
    try:
        c = sqlite3.connect(OUT_DB, timeout=10)
        rows = c.execute(
            "SELECT COALESCE(raw_ret, ret), realized_ret, base_price FROM forecast_archive "
            "WHERE game=? AND target=? AND horizon=? AND realized_ret IS NOT NULL "
            "  AND model_version=? AND base_price > 0 "
            "  AND graded_at >= datetime('now', ?)",
            (game, target, hname, version, f"-{days} day")).fetchall()
        c.close()
    except sqlite3.Error:
        return None
    if len(rows) < 2 * min_n:
        return None
    a = np.array(rows, float)
    return fcal.fit_shrink(a[:, 0], a[:, 1], price_weight(a[:, 2]), min_n=min_n)


RAW_EXTRA = {}   # (game, pid, printing, target, horizon) -> (raw_ret, raw_width)


def forecast_game_target(game, target, now, as_of=None, horizons=None, debug=None):
    horizons = horizons or HORIZONS
    # Calibration flags (2026-08-11) — validated by backtest_forecast.py before
    # being switched on; both transforms are fit on the segment's rolling OOS
    # window each run, so they track the current model, not stale cohorts.
    # Horizon-scoped: "1" = every horizon, else a comma list ("6m,12m") — the
    # 1m backtest (btA 2026-08-12) showed debias HURTS at 1m (noisy short-window
    # bias estimates) while the 6m/12m miscalibration is where the value is.
    def _flag_horizons(name):
        v = os.environ.get(name, "")
        if v == "1":
            return set(horizons)
        return {h.strip() for h in v.split(",") if h.strip()}
    debias_h = _flag_horizons("TCG_FC_DEBIAS")
    cqr_h = _flag_horizons("TCG_FC_CQR")
    # Backtest rows carry a "__" version so the scorecard's accuracy table and
    # feedback signals never count them (its existing test-row convention).
    version = f"__bt-{as_of}" if as_of else MODEL_VERSION
    pids, prints, dates, P = load_matrix_all(game, target)
    # v4.4: artwork is a model input, and a card with no artwork on file is
    # not forecast at all (~0-2% of priced cards per game).
    arts = art_pids(game)
    have = np.fromiter((p in arts for p in pids), dtype=bool, count=len(pids))
    # Match-review gate applies to GRADED tiers only: those prices ride the
    # PriceCharting match, so an unconfirmed/drifted match must sit out (it would
    # train on some other card's graded ladder). UNGRADED is TCGplayer Near Mint,
    # matched by the card's OWN product_id — no PC match involved — so it's
    # forecast for any card with an NM series, PriceCharting-matched or not. This
    # brings the ~57k TCGplayer-priced-but-unmatched cards into the ungraded model.
    ok = reviewed_pids(game) if target != "ungraded" else None
    if ok is not None:
        before = int(have.sum())
        have &= np.fromiter((p in ok for p in pids), dtype=bool, count=len(pids))
        held = before - int(have.sum())
        if held:
            print(f"[{game}/{target}] {held} card(s) held out pending match review", flush=True)
    pids, prints, P = pids[have], prints[have], P[have]
    if as_of:
        # Pretend the run happened at the end of `as_of` month: drop every
        # later price column BEFORE anything trains or anchors, so neither
        # the model nor the trajectory features can see past the cutoff.
        n = sum(1 for d in dates if d <= as_of)
        dates, P = dates[:n], P[:, :n]
    # Young games (PriceCharting only started tracking digimon/gundam in
    # 2025-09) have short matrices: train whatever horizons the depth allows —
    # the per-horizon MIN_SAMPLES gate below drops the ones that can't form
    # enough (t, t+k) pairs, so a 11-bucket game gets 1m/6m but no 12m yet.
    if P.shape[1] < 4 or len(pids) < 30:
        return []
    real_dates = anchor_dates_all(game, target)
    stale_pids = set()   # cards suppressed by the stale-anchor gate (see below)
    thin_pids = set()    # cards suppressed by the minimum-history gate
    R = np.log(P[:, 1:] / P[:, :-1])
    # TCGplayer volume is no longer collected (pricing is PriceCharting-only);
    # the earlier volume A/B showed no accuracy gain, so the feature is dropped.
    V = None
    S = set_matrix(game, pids, P)
    EXTRA = extra_signal_matrices(game, pids, dates)
    EXTRA.update(premium_matrices(game, pids, dates))   # graded/raw premium context
    EXTRA.update(liquidity_matrices(game, target, pids, dates))  # slice 4b, flag-gated
    MKT = market_features(game, dates)   # cross-game market context (level-1 blend)
    static = static_features(game, pids)   # v4.4: art PCA in (artless cards dropped above)
    last_idx = np.array([np.where(np.isfinite(P[i]))[0][-1] if np.isfinite(P[i]).any() else -1
                         for i in range(len(pids))])
    keep = last_idx >= 0

    CUM = cum_stats(P)

    # Rolling out-of-sample window per horizon: the last few base months whose
    # outcome has matured (a fixed calendar cutoff ages into training the gate
    # model on a shrinking minority of the data).
    cutoffs = {h: rolling_cutoff(dates, k) for h, k in horizons.items()}

    # The trajectory block at month t is horizon-independent, so build it once
    # per t and slice it for every horizon (was: rebuilt 3x per pipeline run).
    samples = {h: ([], [], [], [], []) for h in horizons}  # h -> (Xr, y, is_test, w, base_t)
    for t in range(1, len(dates) - min(horizons.values())):
        tb_full = None
        for hname, k in horizons.items():
            if t >= len(dates) - k:
                continue
            v = np.isfinite(P[:, t]) & np.isfinite(P[:, t + k]) & (P[:, t] > 0) & (P[:, t + k] > 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                r_lab = np.log(P[:, t + k] / P[:, t])
            v &= np.abs(np.where(np.isfinite(r_lab), r_lab, 0.0)) <= LABEL_CLIP
            if not v.any():
                continue
            if tb_full is None:
                tb_full = traj_block(P, R, t, V, S, EXTRA, CUM, MKT)
            tb = tb_full.loc[v].reset_index(drop=True)
            sb = static.iloc[np.where(v)[0]].reset_index(drop=True)
            Xr, ys, tests, ws, bs = samples[hname]
            Xr.append(pd.concat([tb, sb], axis=1))
            ys.append(np.log(P[v, t + k] / P[v, t]))
            tests.append(np.full(int(v.sum()), dates[t] >= cutoffs[hname]))
            ws.append(price_weight(P[v, t]))
            bs.append(P[v, t])   # base price at t: bucketed conformal strata

    # "Now" features are also horizon-independent: one block per distinct
    # latest-priced month, assembled once and reused for every horizon.
    traj_now = pd.DataFrame(np.nan, index=np.arange(len(pids)),
                            columns=traj_block(P, R, 1, V, S, EXTRA, CUM, MKT).columns)
    for tv in np.unique(last_idx[keep]):
        blk = traj_block(P, R, int(tv), V, S, EXTRA, CUM, MKT)
        sel = last_idx == tv
        traj_now.loc[sel] = blk.loc[sel].to_numpy()

    rows = []
    for hname, k in horizons.items():
        use_debias = hname in debias_h
        use_cqr = hname in cqr_h
        Xr, ys, tests, ws, bs = samples[hname]
        if not Xr:
            continue
        X = pd.concat(Xr, ignore_index=True)
        y = np.concatenate(ys)
        if len(y) < MIN_SAMPLES:
            continue
        test = np.concatenate(tests)
        w = np.concatenate(ws)
        base_t = np.concatenate(bs)

        # Magic-scale guard: a huge game (112k+ cards x months) can assemble
        # tens of millions of samples; past a few million, HGB gains nothing
        # but memory pressure. Uniform subsample keeps train/test proportions.
        if len(y) > MAX_TRAIN_SAMPLES:
            idx = np.random.default_rng(42).choice(len(y), MAX_TRAIN_SAMPLES, replace=False)
            X, y, test, w, base_t = (X.iloc[idx].reset_index(drop=True), y[idx],
                                     test[idx], w[idx], base_t[idx])
            print(f"  [{game}/{target}/{hname}] subsampled {len(y)} of a larger pool", flush=True)

        # A NUMERIC feature with <2 distinct finite values (e.g. a feedback
        # signal that just switched on with a single graded forecast behind it)
        # crashes HGB's binning (sliding_window_view over distinct values).
        # Checked on the TRAIN SLICE as well as the full frame: the OOS split
        # fits on rows outside the test window, and a signal that only exists
        # in the last few months is finite in X yet ALL-NaN inside that slice —
        # sklearn then derives an EMPTY distinct-value array and dies with
        # "window shape cannot be larger than input array shape" (the 2026-08-02
        # nightly: pokemon/onepiece/digimon segments as their post-cutover
        # signals started accruing). Drop such columns; Xnow is built from
        # X.columns so it follows automatically. Categorical columns bin
        # differently and are exempt.
        degenerate = []
        train_slice = ~test
        # Non-NaN support per column within the fit slice: HGB's own internal
        # 90/10 early-stopping split can strand a column's ONLY finite values on
        # the validation side (magic/ungraded, 2026-08-02: a signal finite on a
        # handful of 1.08M rows), which reaches the same empty-array crash even
        # when the column passes the distinct-value checks. A column finite on
        # fewer than MIN_FINITE rows carries no signal — drop it outright.
        MIN_FINITE = 50
        finite_counts = X.loc[train_slice].count()
        for c in X.columns:
            if isinstance(X[c].dtype, pd.CategoricalDtype):
                continue
            drop = finite_counts[c] < MIN_FINITE
            if not drop:
                for view in (X[c], X[c][train_slice]):
                    mn = view.min()
                    if pd.isna(mn) or mn == view.max():
                        drop = True
                        break
            if drop:
                degenerate.append(c)
        if degenerate:
            X = X.drop(columns=degenerate)
            print(f"  [{game}/{target}/{hname}] dropped degenerate feature(s): "
                  f"{', '.join(degenerate)}", flush=True)

        # confidence band from out-of-sample error, if the split is large enough
        # (HGB's binning needs a healthy sample count, so require a real train set).
        # Gate on the WEIGHTED error — "can the model call value-relevant cards?" —
        # with the raw number printed alongside for continuity with older logs.
        conf_widen = 0.0
        bias = 0.0
        calib = None
        shrink = {"up": 1.0, "down": 1.0}   # publish-time point multipliers
        wcal = None                          # width-stratified widening
        if test.sum() >= 50 and (~test).sum() >= 300:
            m = model_new().fit(X[~test], y[~test], sample_weight=w[~test])
            pred_oos = m.predict(X[test])
            # Debias: weighted-median OOS residual. Fit unconditionally (so the
            # log tracks it) but applied only under the flag — and everything
            # downstream (band, conformal scores) is measured on the SAME
            # predictions the emission will use.
            bias = fcal.fit_debias(pred_oos, y[test], w[test])
            applied_bias = bias if use_debias else 0.0
            # Per-direction shrink (2026-10-10): the multiplier on the point
            # that minimizes weighted OOS MAE. Bootstraps the live archive
            # fit below, which takes over once this version has graded rows.
            shrink = fcal.fit_shrink(pred_oos - applied_bias, y[test], w[test])
            print(f"  [{game}/{target}/{hname}] OOS shrink up x{shrink['up']:g} "
                  f"down x{shrink['down']:g}", flush=True)
            band = float(mean_absolute_error(y[test], pred_oos - applied_bias,
                                             sample_weight=w[test]))
            raw = float(mean_absolute_error(y[test], pred_oos - applied_bias))
            print(f"  [{game}/{target}/{hname}] OOS retMAE {band:.3f} weighted "
                  f"(raw {raw:.3f}; train {(~test).sum()}, test {test.sum()})", flush=True)
            print(f"  [{game}/{target}/{hname}] OOS bias {bias:+.3f}"
                  + (" (applied)" if use_debias else " (measured only)"), flush=True)

            # Split-conformal band widening: quantile models fit on train only,
            # nonconformity scored on the held-out window, and the final bands
            # widen by the amount that makes recent coverage hit CONF_TARGET.
            # (The final quantile models refit on ALL data below — applying the
            # same widening there is the standard practical approximation.)
            q10_pred_t = q90_pred_t = None
            if test.sum() >= 200:
                q10t = model_quantile(0.10).fit(X[~test], y[~test], sample_weight=w[~test])
                q90t = model_quantile(0.90).fit(X[~test], y[~test], sample_weight=w[~test])
                q10_pred_t, q90_pred_t = q10t.predict(X[test]), q90t.predict(X[test])
                E = fcal.nonconformity(q10_pred_t, q90_pred_t, y[test], applied_bias)
                conf_widen = fcal.conformal_quantile(E, CONF_TARGET)
                print(f"  [{game}/{target}/{hname}] conformal widen +{conf_widen:.3f} "
                      f"(held-out coverage was {float(np.mean(E <= 0)):.2f})", flush=True)
                # Bucketed (Mondrian) variant: per-price-stratum widening.
                calib = fcal.fit_bucket_conformal(base_t[test], E, CONF_TARGET)
                wcal = fcal.fit_width_conformal(q10_pred_t, q90_pred_t, y[test],
                                                applied_bias, base_t[test], CONF_TARGET)
                print(f"  [{game}/{target}/{hname}] width-stratum widen "
                      + " | ".join(f"{lab} +{min(c):.2f}..{max(c):.2f} (cov {cv if cv is None else round(cv, 2)})"
                                   for lab, c, cv in zip(fcal.WIDTH_LABELS, wcal["strata"],
                                                         wcal["stratum_cov"])), flush=True)
                # Rolling live recalibration (2026-08-22, self-healing): blend
                # in the nonconformity of RECENTLY GRADED live forecasts so a
                # regime the split never saw still widens bands within days of
                # its first matured cohorts (the 6m/12m under-coverage fix —
                # arms itself as those horizons mature). Measured always;
                # applied per TCG_FC_ROLLCAL (comma horizons, "1" = all).
                live_widen = _rolling_live_widen(game, target, hname)
                if live_widen is not None:
                    roll_on = hname in _flag_horizons("TCG_FC_ROLLCAL")
                    print(f"  [{game}/{target}/{hname}] rolling live widen "
                          f"+{live_widen:.3f} "
                          f"({'applied' if roll_on else 'measured only'})", flush=True)
                    if roll_on:
                        conf_widen = max(conf_widen, live_widen)
                        live_w = _rolling_live_width_widen(game, target, hname)
                        if live_w:
                            wcal = fcal.merge_width_calib(wcal, live_w)
                            print(f"  [{game}/{target}/{hname}] live width-stratum widen "
                                  + " ".join(f"{fcal.WIDTH_LABELS[k]}+{v:.2f}" for k, v in sorted(live_w.items())),
                                  flush=True)
                        live_s = _rolling_live_shrink(game, target, hname, version)
                        if live_s:
                            shrink = live_s
                            print(f"  [{game}/{target}/{hname}] live shrink up x{shrink['up']:g} "
                                  f"down x{shrink['down']:g} (archive)", flush=True)
                        calib = {**calib,
                                 "global": max(calib["global"], live_widen),
                                 "widen": [max(w_, live_widen)
                                           for w_ in calib["widen"]]}
                if use_cqr:
                    print(f"  [{game}/{target}/{hname}] bucket widen "
                          + "/".join(f"{v:+.2f}" for v in calib["widen"]), flush=True)
            if debug is not None:
                debug[hname] = {
                    "pred_oos": pred_oos, "y_test": y[test].copy(),
                    "w_test": w[test].copy(), "base_test": base_t[test].copy(),
                    "q10_test": q10_pred_t, "q90_test": q90_pred_t,
                    "bias": bias, "conf_widen": conf_widen, "calib": calib,
                    "band": band,
                }
                if debug.get("capture_frames"):
                    debug[hname].update({"X": X, "y": y, "test": test, "w": w,
                                         "base_t": base_t})
        else:
            band = DEFAULT_BAND

        # Segments whose out-of-sample error says the model can't call this
        # (game, tier, horizon) — typical for young games whose only training
        # regime is their launch mania/crash — still publish (per product
        # decision 2026-07-12), but every row is forced to low confidence and
        # carries an explicit caution in its reasoning text.
        unreliable = band > MAX_SEGMENT_MAE
        if unreliable:
            print(f"  [{game}/{target}/{hname}] retMAE {band:.2f} > {MAX_SEGMENT_MAE} "
                  f"— publishing flagged low-confidence", flush=True)

        model = model_new().fit(X, y, sample_weight=w)
        # Per-card scenario quantiles (10th/90th) — the model's OWN uncertainty.
        # A tight interval = a confident forecast; a wide one = anything can happen.
        q10 = model_quantile(0.10).fit(X, y, sample_weight=w)
        q90 = model_quantile(0.90).fit(X, y, sample_weight=w)

        Xnow = pd.concat([traj_now.loc[keep].reset_index(drop=True),
                          static.loc[keep].reset_index(drop=True)], axis=1)[X.columns]

        base = P[keep, last_idx[keep]]
        pred_now = model.predict(Xnow)
        q10_now = q10.predict(Xnow)
        q90_now = q90.predict(Xnow)
        # Per-card widening under CQR (each card gets its price stratum's
        # widening); the classic scalar otherwise. Debias shifts point + band.
        if wcal is not None:
            widen = fcal.width_widen_for(q10_now, q90_now, base, wcal)
        elif use_cqr and calib is not None:
            widen = fcal.bucket_widen_for(base, calib)
        else:
            widen = conf_widen
        # Publish-time shrink on the (debiased) point; bands stay anchored on
        # the scenario quantiles. Raw values ride along to the archive so the
        # live refit never compounds a shrink on an already-shrunk number.
        b_ = bias if use_debias else 0.0
        pred_pub = fcal.apply_shrink(pred_now - b_, shrink) + b_
        raw_ret_now = np.clip(pred_now - b_, -RET_CLIP, RET_CLIP)
        raw_width_now = q90_now - q10_now
        # quantile crossings happen at the tails — emit_bands enforces
        # lo <= ret <= hi and applies the conformal adjustment measured above.
        ret, lo_ret, hi_ret = fcal.emit_bands(pred_pub, q10_now, q90_now, widen,
                                              b_, RET_CLIP)
        width = hi_ret - lo_ret   # 80% interval width in log-return
        conf = np.where(width <= 0.40, "high", np.where(width <= 0.90, "med", "low"))
        if unreliable:
            conf = np.full(len(ret), "low", dtype=object)

        if debug is not None and hname in debug:
            debug[hname]["emit"] = {
                "pids": pids[keep].copy(), "prints": prints[keep].copy(),
                "base": base.copy(), "pred_now": pred_now, "q10_now": q10_now,
                "q90_now": q90_now, "last_idx": last_idx[keep].copy(),
                "Xnow": Xnow if debug.get("capture_frames") else None,
            }

        deltas = bucket_deltas(model, Xnow, X)   # per-card trait attribution
        comps = art_comps(game)
        traits = card_traits(game)
        fc = np.round(base * np.exp(ret), 2)
        low = np.round(base * np.exp(lo_ret), 2)
        high = np.round(base * np.exp(hi_ret), 2)
        asof = [dates[i] + "-01" for i in last_idx[keep]]

        # trajectory signals the model saw, for per-card reasoning
        tn = traj_now.loc[keep].reset_index(drop=True)
        mom3, trend12, vol6 = tn["ret3"].to_numpy(), tn["ret12"].to_numpy(), tn["vol6"].to_numpy()
        vol_now = np.expm1(tn["logvol"].to_numpy()) if "logvol" in tn else np.full(len(tn), np.nan)
        volchg = tn["volchg"].to_numpy() if "volchg" in tn else np.full(len(tn), np.nan)
        set12 = tn["setret12"].to_numpy() if "setret12" in tn else np.full(len(tn), np.nan)

        # Stale-anchor gate (live runs only): a card whose newest REAL price for
        # this tier is months old would still "forecast" off that stale anchor,
        # and its junk % change can top the catalog's movers sorts (2026-08-01:
        # Chopper Treasure Cup, ungraded frozen at June 1, ranked page 1 of the
        # 1m list). Ungraded is crawled nightly so 45 days is generous; graded
        # rotates ~monthly on month-bucketed charts, so 75. Training above is
        # unaffected — this only suppresses publishing. Backtests (as_of) keep
        # every card: their anchors are historical by construction.
        stale_cut = (datetime.now(timezone.utc)
                     - timedelta(days=45 if target == "ungraded" else 75)).strftime("%Y-%m-%d")

        # Minimum-history gate (2026-08-19): a card whose tier series has a
        # single monthly bucket has never shown the model one realized return —
        # its "forecast" is a pure trait guess (the 2026-08-18 wipe left
        # 1-bucket cards that promptly emitted 1m/6m/12m rows off one fresh
        # point). Require >= 2 buckets. Surgical: ~1.2k ungraded cards sit at
        # 1 bucket vs ~59k at 2, and a young card graduates at its second
        # month boundary. Live runs only — backtests keep every card.
        hist_k = np.isfinite(P[keep]).sum(axis=1)

        prints_k = prints[keep]
        for i, (pid, a, b, r, f, lo, hi) in enumerate(zip(pids[keep], asof, base, ret, fc, low, high)):
            pr = str(prints_k[i])
            if not as_of and real_dates.get((int(pid), pr), a) < stale_cut:
                stale_pids.add((int(pid), pr))
                continue
            if not as_of and hist_k[i] < 2:
                thin_pids.add((int(pid), pr))
                continue
            comp = comps.get(int(pid))
            # resolve up to two look-alike cards by name, skipping self-references
            examples = []
            if comp:
                own = _txt((traits.get(int(pid)) or {}).get("name"))
                for cid in comp[2]:
                    nm = _txt((traits.get(int(cid)) or {}).get("name"))
                    if nm and nm != own and nm not in examples:
                        examples.append(nm)
                    if len(examples) == 2:
                        break
            reason = make_reason(r, mom3[i], trend12[i], vol6[i], hname,
                                 vol_now[i], volchg[i],
                                 comp=comp,
                                 deltas_i={bkt: d[i] for bkt, d in deltas.items()},
                                 traits=traits.get(int(pid)), pid=int(pid),
                                 set12=set12[i], comp_examples=examples)
            # Honesty caveats replace the old suppression gates: flagged, not hidden.
            if unreliable:
                reason += (" Caution: the model's recent out-of-sample accuracy for this"
                           " game and horizon is poor — treat this forecast as speculative.")
            if conf[i] == "low" and abs(float(r)) > EXTREME_LOW_CONF_RET:
                reason += (" This is an extreme swing the model itself has low confidence"
                           " in — such calls have historically been unreliable.")
            RAW_EXTRA[(game, int(pid), pr, target, hname)] = (
                float(raw_ret_now[i]), float(raw_width_now[i]))
            rows.append((game, int(pid), target, hname, a, round(float(b), 2),
                         float(f), float(lo), float(hi), round(float(r), 4), reason,
                         str(conf[i]), version, now, real_dates.get((int(pid), pr), a),
                         pr))
            # (1w rows removed 2026-08-14: they were the 1-month forecast
            # pro-rated to 7 days — synthetic, and retired ahead of the true
            # weekly model planned once daily data matures, ~Oct 2026.)
    print(f"[{game}/{target}] {len(rows)} rows"
          + (f" ({len(stale_pids)} stale-anchored card(s) not published)" if stale_pids else "")
          + (f" ({len(thin_pids)} single-bucket card(s) not published)" if thin_pids else ""),
          flush=True)
    return rows


def archive(conn, rows):
    """Archive issued forecasts for later grading.

    `forecasts` is dropped and rebuilt every run, so it can never answer "what
    did the model say last month?". This table can. Two retention regimes
    (2026-08-14):

    - 1m: EVERY nightly forecast is kept (as_of = the issue date), so a
      freshly matured 1m dot appears on card pages daily. Once an issue
      month's cohorts have all matured, forecast_scorecard.py's compactor
      keeps each card's MEDIAN-|error| row (a real forecast — real issue
      date, base and outcome; 2026-08-29, no synthetic averages) and
      persists the month's full stats to forecast_accuracy_monthly —
      long-term growth is identical to the old one-row-per-month.
    - 6m/12m: one FIRST-issued row per anchor month (as_of = month 1st,
      INSERT OR IGNORE dedupes) — a year of nightly near-identical copies
      awaiting maturity would be hundreds of millions of pending rows.

    forecast_scorecard.py grades each row (fills the realized_* columns) once
    its horizon has elapsed, and feeds the errors back into training.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_archive (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT NOT NULL,
            base_price REAL, forecast_price REAL, low REAL, high REAL, ret REAL,
            confidence TEXT, model_version TEXT, scored_at TEXT,
            realized_price REAL, realized_ret REAL, realized_at TEXT, graded_at TEXT,
            printing TEXT NOT NULL DEFAULT '',   -- '' = base printing
            PRIMARY KEY (game, product_id, printing, target, horizon, as_of)
        )
        """)
    # ~470k 1m rows/night held to maturity (2026-08-27): compaction, grading
    # and audits all slice by (horizon, as_of) — unindexed, each was a full
    # scan of a multi-GB table (minutes on the t3.small serving copy).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_horizon_asof "
                 "ON forecast_archive(horizon, as_of)")
    # v4.5 (2026-10-10): the raw (pre-shrink) point and raw band width ride
    # along so the live calibration refits on what the model said, not on
    # what was published. Additive columns; older rows read NULL.
    have = {r[1] for r in conn.execute("PRAGMA table_info(forecast_archive)")}
    for col in ("raw_ret", "raw_width"):
        if col not in have:
            conn.execute(f"ALTER TABLE forecast_archive ADD COLUMN {col} REAL")
    payload = []
    for r in rows:   # drops r[10] (bulky reason) and r[14] (anchor_date)
        rec = list(r[:10] + r[11:14] + (r[15],))
        rec += list(RAW_EXTRA.get((r[0], r[1], r[15], r[2], r[3]), (None, None)))
        if r[3] == "1m":
            # Nightly cohort: key on the ISSUE date. r[13] is the run's `now`
            # ISO timestamp; backtest rows keep their month key (their `now`
            # is a synthetic tag, and __bt rows are ignored anyway).
            iso = str(r[13])[:10]
            if len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
                rec[4] = iso
        payload.append(tuple(rec))
    added = conn.executemany(
        "INSERT OR IGNORE INTO forecast_archive "
        "(game, product_id, target, horizon, as_of, base_price, forecast_price,"
        " low, high, ret, confidence, model_version, scored_at, printing,"
        " raw_ret, raw_width)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload).rowcount
    RAW_EXTRA.clear()
    print(f"archived {added} forecast(s) for later grading")


def main():
    import argparse
    from games import priced_games

    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append",
                    help="retrain only this game (repeatable); merges into the "
                         "existing forecasts table instead of rebuilding it")
    ap.add_argument("--as-of", metavar="YYYY-MM",
                    help="backtest: truncate all price history after this month "
                         "and archive theoretical 1m forecasts tagged __bt-<month>; "
                         "the live forecasts table is left untouched")
    ap.add_argument("--db", metavar="PATH",
                    help="write to this predictions.db instead of the live one")
    args = ap.parse_args()
    if args.as_of and not (len(args.as_of) == 7 and args.as_of[4] == "-"
                           and args.as_of.replace("-", "").isdigit()):
        ap.error("--as-of must look like YYYY-MM")
    games = args.game if args.game else priced_games()
    # --as-of backtests train 1m only. Otherwise TCG_FC_HORIZONS (e.g. "1m")
    # limits the run's horizons: Thursday nights run 1m only (2026-10-09,
    # user decision — 6m/12m stay unvalidated until their first cohorts
    # mature in 2027, their calls barely move between runs, and skipping
    # them cuts the model block roughly in two-thirds). Sunday's full
    # rebuild trains all three; the standing 6m/12m rows keep serving
    # between Sundays (see the horizon-scoped delete below).
    env_h = os.environ.get("TCG_FC_HORIZONS", "").strip()
    if args.as_of:
        horizons = {"1m": 1}
    elif env_h:
        horizons = {h: HORIZONS[h] for h in env_h.split(",") if h in HORIZONS} or None
    else:
        horizons = None
    subset = horizons is not None and set(horizons) != set(HORIZONS)
    if subset:
        print(f"[forecast] horizon-limited run: {sorted(horizons)} only "
              f"(other horizons' standing forecasts untouched)", flush=True)

    # Make sure the archive carries the v4.5 raw columns BEFORE any segment
    # trains: the live width/shrink refits read them, and archive() only
    # adds them at the end of the run — without this the first calibrated
    # run would find no raw columns and skip the live refresh everywhere.
    try:
        c0 = sqlite3.connect(args.db or OUT_DB, timeout=60)
        have = {r[1] for r in c0.execute("PRAGMA table_info(forecast_archive)")}
        if have:
            for col in ("raw_ret", "raw_width"):
                if col not in have:
                    c0.execute(f"ALTER TABLE forecast_archive ADD COLUMN {col} REAL")
            c0.commit()
        c0.close()
    except sqlite3.Error as e:
        print(f"[forecast] archive column check skipped: {e}", flush=True)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_seg = len(games) * len(TARGETS)
    done_seg = 0
    print(f"[forecast] plan: {len(games)} games x {len(TARGETS)} tiers = {total_seg} segments", flush=True)
    all_rows = []
    for game in games:
        for target in TARGETS:
            done_seg += 1
            try:
                all_rows += forecast_game_target(game, target, now, args.as_of, horizons)
            except Exception as e:
                print(f"[{game}/{target}] SKIPPED: {type(e).__name__}: {e}", flush=True)
            # Parseable progress for the dashboard (counts every segment, so it
            # reaches N/N even when a young-game tier is skipped without rows).
            print(f"[forecast] {done_seg}/{total_seg} segments ({game}/{target})", flush=True)

    out_db = args.db or OUT_DB
    conn = sqlite3.connect(out_db, timeout=60)
    if args.as_of:
        # Backtest: archive-only. The live forecasts table keeps serving the
        # real current forecasts; only the append-only history gains rows.
        archive(conn, all_rows)
        conn.commit()
        conn.close()
        print(f"\nbacktest {args.as_of}: {len(all_rows)} theoretical forecast(s) "
              f"archived -> {os.path.normpath(out_db)}")
        return
    # Publish gate: segment failures are swallowed above (SKIPPED) so one bad
    # game can't kill the whole run — but a systemic failure (missing cards
    # CSV, corrupt embedding file) would surface as most segments skipping,
    # and rebuilding from the survivors would silently wipe games off the
    # site with exit 0. Refuse to publish if a game that had forecasts now
    # has none, or the total collapsed; the old table keeps serving.
    # Horizon-limited runs compare like for like: a 1m-only night produces a
    # third of a full run's rows, which would trip the collapse ratio against
    # an all-horizons baseline.
    h_where, h_args = "", ()
    if subset:
        h_where = f" WHERE horizon IN ({','.join('?' * len(horizons))})"
        h_args = tuple(horizons)
    try:
        prev = dict(((g, t), n) for g, t, n in conn.execute(
            f"SELECT game, target, COUNT(*) FROM forecasts{h_where} "
            "GROUP BY game, target", h_args))
    except sqlite3.OperationalError:
        prev = {}   # first run against this DB: nothing to protect
    checked = ({k: n for k, n in prev.items() if k[0] in games} if args.game
               else {k: n for k, n in prev.items() if k[1] in TARGETS})
    if checked:
        new_counts = {}
        for r in all_rows:
            k = (r[0], r[2])
            new_counts[k] = new_counts.get(k, 0) + 1
        # Per (game, target), not per game: 2026-08-02 a single crashed segment
        # (magic/ungraded — the game's headline tier and the movers' input)
        # shipped because magic still had graded rows. Any segment that had a
        # real presence and now returns nothing blocks the publish.
        gone = [k for k, n in checked.items() if n >= 1000 and not new_counts.get(k)]
        total_prev = sum(checked.values())
        total_new = sum(new_counts.get(k, 0) for k in checked)
        if gone or total_new < total_prev * 0.5:
            print(f"[forecast] PUBLISH GATE: refusing to rebuild — segments with "
                  f"no rows: {[f'{g}/{t}' for g, t in gone] or 'none'}; rows "
                  f"{total_new} vs {total_prev} previous. Existing forecasts "
                  f"left untouched.", flush=True)
            conn.close()
            raise SystemExit(1)

    schema = """
        CREATE TABLE IF NOT EXISTS forecasts (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT, base_price REAL, forecast_price REAL,
            low REAL, high REAL, ret REAL, reason TEXT,
            confidence TEXT,          -- model-reported: high | med | low (80% interval width)
            model_version TEXT, scored_at TEXT,
            anchor_date TEXT,         -- REAL date of the anchor price (as_of is its month bucket)
            printing TEXT NOT NULL DEFAULT '',   -- '' = base printing
            PRIMARY KEY (game, product_id, printing, target, horizon)
        );
        """
    if args.game:
        # Partial rerun: replace only the requested games' rows (and only the
        # run's horizons when limited).
        conn.executescript(schema)
        if subset:
            conn.executemany("DELETE FROM forecasts WHERE game = ? AND horizon = ?",
                             [(g, h) for g in games for h in horizons])
        else:
            conn.executemany("DELETE FROM forecasts WHERE game = ?", [(g,) for g in games])
    elif subset:
        # Horizon-limited full run: replace those horizons' rows only. The
        # standing 6m/12m (and launch1m) forecasts must survive a 1m-only
        # Thursday or the site would lose them until Sunday.
        conn.executescript(schema)
        conn.executemany("DELETE FROM forecasts WHERE horizon = ?",
                         [(h,) for h in horizons])
    else:
        conn.executescript("DROP TABLE IF EXISTS forecasts;" + schema)
    conn.executemany("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", all_rows)
    archive(conn, all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(out_db)} (forecasts)")


if __name__ == "__main__":
    main()
