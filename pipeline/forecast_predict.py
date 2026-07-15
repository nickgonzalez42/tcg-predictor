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

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from forecast_deep import (load_matrix, static_features, traj_block,
                           set_matrix, extra_signal_matrices, market_features,
                           cum_stats, model_new, model_quantile, CUTOFF)

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

MODEL_VERSION = "forecast-deep-v4.2"  # v4.2: cross-game market features (global/game momentum)
                                      # v4.1: model-reported confidence, set/age/drawdown
                                      # features, ranked card-specific reasons
HORIZONS = {"1m": 1, "6m": 6, "12m": 12}
# Price data is monthly, so a true 1-week model has no training targets. The 1w
# horizon is the 1-month forecast pro-rated to 7 days — disclosed in its reason.
WEEK_FRACTION = 7 / 30.44
TARGETS = ["ungraded", "grade7", "grade8", "grade9", "grade95", "psa10", "bgs10", "cgc10", "sgc10"]
RET_CLIP = np.log(10.0)
DEFAULT_BAND = 0.5
MIN_SAMPLES = 200
MAX_TRAIN_SAMPLES = 3_000_000   # per (game, tier, horizon); see subsample below
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


def _pull(subject, pct, pid):
    """'<subject> lifts/drags the forecast' with per-card phrasing variety.
    Extreme percentages are real (a vintage set profile can dwarf a 'typical
    card') but read as bugs — above 100% switch to qualitative wording."""
    up = pct > 0
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
            cand.append((min(abs(p), 90), _pull(_stats_subject(traits), p, pid)))
        elif bucket == "identity":
            cand.append((min(abs(p), 90), _pull(_identity_subject(traits), p, pid)))
        elif bucket == "art":
            # prefer the concrete look-alike stat over a generic art clause
            clause = comp_clause(comp, comp_examples) or _pull("its artwork profile", p, pid)
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


def anchor_dates(game, grade):
    """pid -> real date of the tier's newest history point. The model anchors
    on the month bucket (whose price IS that newest point); this is the honest
    display date for it."""
    from forecast_deep import PC_DB
    rows = sqlite3.connect(PC_DB, timeout=180).execute(
        "SELECT product_id, MAX(date) FROM price_history_unified "
        "WHERE game=? AND grade=? GROUP BY product_id", (game, grade)).fetchall()
    return {pid: d[:10] for pid, d in rows if d}


def forecast_game_target(game, target, now):
    pids, dates, P = load_matrix(game, target)
    # Young games (PriceCharting only started tracking digimon/gundam in
    # 2025-09) have short matrices: train whatever horizons the depth allows —
    # the per-horizon MIN_SAMPLES gate below drops the ones that can't form
    # enough (t, t+k) pairs, so a 11-bucket game gets 1m/6m but no 12m yet.
    if P.shape[1] < 4 or len(pids) < 30:
        return []
    real_dates = anchor_dates(game, target)
    R = np.log(P[:, 1:] / P[:, :-1])
    # TCGplayer volume is no longer collected (pricing is PriceCharting-only);
    # the earlier volume A/B showed no accuracy gain, so the feature is dropped.
    V = None
    S = set_matrix(game, pids, P)
    EXTRA = extra_signal_matrices(game, pids, dates)
    MKT = market_features(game, dates)   # cross-game market context (level-1 blend)
    static = static_features(game, pids)
    last_idx = np.array([np.where(np.isfinite(P[i]))[0][-1] if np.isfinite(P[i]).any() else -1
                         for i in range(len(pids))])
    keep = last_idx >= 0

    CUM = cum_stats(P)

    # The trajectory block at month t is horizon-independent, so build it once
    # per t and slice it for every horizon (was: rebuilt 3x per pipeline run).
    samples = {h: ([], [], []) for h in HORIZONS}   # h -> (Xr, y, is_test)
    for t in range(1, len(dates) - min(HORIZONS.values())):
        tb_full = None
        for hname, k in HORIZONS.items():
            if t >= len(dates) - k:
                continue
            v = np.isfinite(P[:, t]) & np.isfinite(P[:, t + k]) & (P[:, t] > 0) & (P[:, t + k] > 0)
            if not v.any():
                continue
            if tb_full is None:
                tb_full = traj_block(P, R, t, V, S, EXTRA, CUM, MKT)
            tb = tb_full.loc[v].reset_index(drop=True)
            sb = static.iloc[np.where(v)[0]].reset_index(drop=True)
            Xr, ys, tests = samples[hname]
            Xr.append(pd.concat([tb, sb], axis=1))
            ys.append(np.log(P[v, t + k] / P[v, t]))
            tests.append(np.full(int(v.sum()), dates[t] >= CUTOFF))

    # "Now" features are also horizon-independent: one block per distinct
    # latest-priced month, assembled once and reused for every horizon.
    traj_now = pd.DataFrame(np.nan, index=np.arange(len(pids)),
                            columns=traj_block(P, R, 1, V, S, EXTRA, CUM, MKT).columns)
    for tv in np.unique(last_idx[keep]):
        blk = traj_block(P, R, int(tv), V, S, EXTRA, CUM, MKT)
        sel = last_idx == tv
        traj_now.loc[sel] = blk.loc[sel].to_numpy()

    rows = []
    for hname, k in HORIZONS.items():
        Xr, ys, tests = samples[hname]
        if not Xr:
            continue
        X = pd.concat(Xr, ignore_index=True)
        y = np.concatenate(ys)
        if len(y) < MIN_SAMPLES:
            continue
        test = np.concatenate(tests)

        # Magic-scale guard: a huge game (112k+ cards x months) can assemble
        # tens of millions of samples; past a few million, HGB gains nothing
        # but memory pressure. Uniform subsample keeps train/test proportions.
        if len(y) > MAX_TRAIN_SAMPLES:
            idx = np.random.default_rng(42).choice(len(y), MAX_TRAIN_SAMPLES, replace=False)
            X, y, test = X.iloc[idx].reset_index(drop=True), y[idx], test[idx]
            print(f"  [{game}/{target}/{hname}] subsampled {len(y)} of a larger pool", flush=True)

        # A NUMERIC feature with <2 distinct finite values (e.g. a feedback
        # signal that just switched on with a single graded forecast behind it)
        # crashes HGB's binning (sliding_window_view over distinct values).
        # Drop such columns; Xnow is built from X.columns so it follows
        # automatically. Categorical columns bin differently and are exempt.
        degenerate = []
        for c in X.columns:
            if isinstance(X[c].dtype, pd.CategoricalDtype):
                continue
            mn = X[c].min()
            if pd.isna(mn) or mn == X[c].max():
                degenerate.append(c)
        if degenerate:
            X = X.drop(columns=degenerate)
            print(f"  [{game}/{target}/{hname}] dropped degenerate feature(s): "
                  f"{', '.join(degenerate)}", flush=True)

        # confidence band from out-of-sample error, if the split is large enough
        # (HGB's binning needs a healthy sample count, so require a real train set)
        if test.sum() >= 50 and (~test).sum() >= 300:
            m = model_new().fit(X[~test], y[~test])
            band = float(mean_absolute_error(y[test], m.predict(X[test])))
            print(f"  [{game}/{target}/{hname}] OOS retMAE {band:.3f} "
                  f"(train {(~test).sum()}, test {test.sum()})", flush=True)
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

        model = model_new().fit(X, y)
        # Per-card scenario quantiles (10th/90th) — the model's OWN uncertainty.
        # A tight interval = a confident forecast; a wide one = anything can happen.
        q10 = model_quantile(0.10).fit(X, y)
        q90 = model_quantile(0.90).fit(X, y)

        Xnow = pd.concat([traj_now.loc[keep].reset_index(drop=True),
                          static.loc[keep].reset_index(drop=True)], axis=1)[X.columns]

        ret = np.clip(model.predict(Xnow), -RET_CLIP, RET_CLIP)
        # quantile crossings happen at the tails — enforce lo <= ret <= hi
        lo_ret = np.clip(np.minimum(q10.predict(Xnow), ret), -RET_CLIP, RET_CLIP)
        hi_ret = np.clip(np.maximum(q90.predict(Xnow), ret), -RET_CLIP, RET_CLIP)
        width = hi_ret - lo_ret   # 80% interval width in log-return
        conf = np.where(width <= 0.40, "high", np.where(width <= 0.90, "med", "low"))
        if unreliable:
            conf = np.full(len(ret), "low", dtype=object)

        deltas = bucket_deltas(model, Xnow, X)   # per-card trait attribution
        comps = art_comps(game)
        traits = card_traits(game)
        base = P[keep, last_idx[keep]]
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

        for i, (pid, a, b, r, f, lo, hi) in enumerate(zip(pids[keep], asof, base, ret, fc, low, high)):
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
            rows.append((game, int(pid), target, hname, a, round(float(b), 2),
                         float(f), float(lo), float(hi), round(float(r), 4), reason,
                         str(conf[i]), MODEL_VERSION, now, real_dates.get(int(pid), a)))

            # 1w: the 1-month forecast pro-rated to 7 days (monthly data has no
            # weekly targets to train on) — same drivers, disclosed in the text.
            # Composed through make_reason (not sliced out of the 1m text), so
            # phrasing changes there can't garble this row.
            if hname == "1m":
                rw = float(r) * WEEK_FRACTION
                lw = rw - (float(r) - float(lo_ret[i])) * WEEK_FRACTION
                hw = rw + (float(hi_ret[i]) - float(r)) * WEEK_FRACTION
                wreason = make_reason(rw, mom3[i], trend12[i], vol6[i], "1w",
                                      vol_now[i], volchg[i],
                                      comp=comp,
                                      deltas_i={bkt: d[i] for bkt, d in deltas.items()},
                                      traits=traits.get(int(pid)), pid=int(pid),
                                      set12=set12[i], comp_examples=examples)
                wreason += " Pro-rated from the 1-month model (price data is monthly)."
                if unreliable:
                    wreason += (" Caution: the model's recent out-of-sample accuracy for"
                                " this game and horizon is poor — treat this forecast as"
                                " speculative.")
                rows.append((game, int(pid), target, "1w", a, round(float(b), 2),
                             round(float(b) * float(np.exp(rw)), 2),
                             round(float(b) * float(np.exp(lw)), 2),
                             round(float(b) * float(np.exp(hw)), 2),
                             round(rw, 4), wreason, str(conf[i]), MODEL_VERSION, now,
                             real_dates.get(int(pid), a)))
    print(f"[{game}/{target}] {len(rows)} rows", flush=True)
    return rows


def archive(conn, rows):
    """Keep the FIRST forecast issued per (card, tier, horizon, as_of) forever.

    `forecasts` is dropped and rebuilt every run, so it can never answer "what
    did the model say last month?". This table can: INSERT OR IGNORE means a
    nightly retrain on the same underlying price month never rewrites history.
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
            PRIMARY KEY (game, product_id, target, horizon, as_of)
        )
        """)
    added = conn.executemany(
        "INSERT OR IGNORE INTO forecast_archive "
        "(game, product_id, target, horizon, as_of, base_price, forecast_price,"
        " low, high, ret, confidence, model_version, scored_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [r[:10] + r[11:14] for r in rows]).rowcount   # drops r[10] (bulky reason) and r[14] (anchor_date):
                                                      # the archive keys on the MONTH bucket so a nightly
                                                      # rerun on the same price month stays a no-op
    print(f"archived {added} first-issued forecast(s) for later grading")


def main():
    import argparse
    from games import priced_games

    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append",
                    help="retrain only this game (repeatable); merges into the "
                         "existing forecasts table instead of rebuilding it")
    args = ap.parse_args()
    games = args.game if args.game else priced_games()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_rows = []
    for game in games:
        for target in TARGETS:
            try:
                all_rows += forecast_game_target(game, target, now)
            except Exception as e:
                print(f"[{game}/{target}] SKIPPED: {type(e).__name__}: {e}", flush=True)

    conn = sqlite3.connect(OUT_DB, timeout=60)
    schema = """
        CREATE TABLE IF NOT EXISTS forecasts (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT, base_price REAL, forecast_price REAL,
            low REAL, high REAL, ret REAL, reason TEXT,
            confidence TEXT,          -- model-reported: high | med | low (80% interval width)
            model_version TEXT, scored_at TEXT,
            anchor_date TEXT,         -- REAL date of the anchor price (as_of is its month bucket)
            PRIMARY KEY (game, product_id, target, horizon)
        );
        """
    if args.game:
        # Partial rerun: replace only the requested games' rows.
        conn.executescript(schema)
        conn.executemany("DELETE FROM forecasts WHERE game = ?", [(g,) for g in games])
    else:
        conn.executescript("DROP TABLE IF EXISTS forecasts;" + schema)
    conn.executemany("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", all_rows)
    archive(conn, all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(OUT_DB)} (forecasts)")


if __name__ == "__main__":
    main()
