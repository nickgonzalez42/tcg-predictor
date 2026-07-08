"""
Generate per-card 6m/12m forecasts for EVERY grade tier that has enough history.

For each (game, tier, horizon):
  1. build (t -> t+h) log-return samples from the tier's monthly series
  2. temporal split (train < CUTOFF, test >= CUTOFF) -> measure out-of-sample
     log-return MAE, used as the confidence band (magnitude is noisier than direction)
  3. retrain on ALL samples, forecast from each card's latest month

Output: predictions.db `forecasts`
  (game, product_id, target, horizon, as_of, base_price, forecast_price, low, high, ret, ...)

Run:  .venv/bin/python forecast_predict.py
"""

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from forecast_deep import load_matrix, static_features, traj_block, volume_matrix, model_new, CUTOFF

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

MODEL_VERSION = "forecast-deep-v3"
HORIZONS = {"6m": 6, "12m": 12}
TARGETS = ["ungraded", "grade7", "grade8", "grade9", "grade95", "psa10", "bgs10", "cgc10", "sgc10"]
RET_CLIP = np.log(10.0)
DEFAULT_BAND = 0.5
MIN_SAMPLES = 200

# ---- feature buckets for per-card attribution -------------------------------
# Trajectory features are already narrated directly (momentum/volatility/volume);
# the static features are grouped into buckets we can ablate one at a time.
TRAJ_COLS = {"logp", "ret1", "ret3", "ret12", "vol6", "hist", "logvol", "volchg"}
STAT_COLS = {  # the card's printed stat line, both games
    "life", "power", "cost", "counter", "attribute", "subtypes", "color",
    "hp", "stage", "energy_type", "attack1", "attack2", "attack3", "attack4",
    "weakness", "resistance", "retreat_cost",
}
BUCKET_LABEL = {"stats": "stat line", "art": "art style", "identity": "rarity/set profile"}


def bucket_of(col):
    if col in TRAJ_COLS:
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
    """product_id -> (comp_ret12, comp_n) from art_comps.py output (or {} if absent)."""
    if game not in _COMPS_CACHE:
        path = os.path.join(BASE, "ml_data", f"{game}_art_comps.csv")
        comps = {}
        if os.path.exists(path):
            df = pd.read_csv(path)
            for pid, n, r in zip(df["product_id"], df["comp_n"], df["comp_ret12"]):
                if pd.notna(r):
                    comps[int(pid)] = (float(r), int(n))
        _COMPS_CACHE[game] = comps
    return _COMPS_CACHE[game]


def _pct(logret):
    if logret is None or np.isnan(logret):
        return None
    return (np.exp(logret) - 1.0) * 100.0


def comp_clause(comp):
    """'Cards with similar art …' from real look-alike retention (12-month)."""
    if comp is None:
        return None
    r, _n = comp
    if r >= 3.0:
        return "cards with similar art more than tripled in value over the past year"
    if r >= 2.0:
        return "cards with similar art more than doubled in value over the past year"
    if r >= 1.10:
        return f"cards with similar art gained ~{(r - 1) * 100:.0f}% over the past year"
    if r <= 0.90:
        return f"cards with similar art lost ~{(1 - r) * 100:.0f}% over the past year"
    return "cards with similar art held their value over the past year"


def driver_clause(deltas_i):
    """Name the card trait that pulls THIS card's forecast most (from ablation).

    Extreme percentages are real (a vintage card's set profile can dwarf a
    'typical card') but read as bugs — above 100% switch to qualitative wording.
    """
    best, best_pct = None, 0.0
    for b, d in deltas_i.items():
        p = _pct(d)
        if p is not None and abs(p) > abs(best_pct):
            best, best_pct = b, p
    if best is None or abs(best_pct) < 5:
        return None
    verb = "lifts" if best_pct > 0 else "drags"
    if abs(best_pct) >= 100:
        return f"its {BUCKET_LABEL[best]} strongly {verb} the forecast vs a typical card"
    return f"its {BUCKET_LABEL[best]} {verb} the forecast ({best_pct:+.0f}% vs a typical card)"


def make_reason(ret, mom3, trend12, vol6, hist, horizon,
                vol_now=None, volchg=None, comp=None, deltas_i=None):
    """Plain-English 'why': the model's input signals plus real look-alike
    comparables and an ablation-based trait attribution for this card."""
    proj = _pct(ret)
    m3, t12 = _pct(mom3), _pct(trend12)
    clauses = []
    if m3 is not None and abs(m3) >= 5:
        clauses.append(f"3-month momentum {m3:+.0f}%")
    if t12 is not None and abs(t12) >= 10:
        clauses.append(f"12-month trend {'up' if t12 > 0 else 'down'} {abs(t12):.0f}%")
    if vol6 is not None and not np.isnan(vol6):
        vlabel = "high" if vol6 >= 0.15 else "moderate" if vol6 >= 0.07 else "low"
        clauses.append(f"{vlabel} volatility" + (" (wide range)" if vlabel == "high" else ""))
    # sales volume: liquidity context (thin trading => noisier, less trustworthy)
    if vol_now is not None and not np.isnan(vol_now) and vol_now > 0:
        rate = f"~{vol_now:.0f}/mo sold" if vol_now >= 1 else "under 1/mo sold"
        if vol_now < 3:
            clauses.append(f"thin sales volume ({rate})")
        elif volchg is not None and not np.isnan(volchg) and abs(volchg) >= 0.4:
            clauses.append(f"{'rising' if volchg > 0 else 'falling'} sales volume ({rate})")
        else:
            clauses.append(f"steady sales volume ({rate})")
    # comparables: how the card's visual look-alikes actually performed
    cc = comp_clause(comp)
    if cc:
        clauses.append(cc)
    # attribution: which of this card's own traits moves its forecast most
    dc = driver_clause(deltas_i) if deltas_i else None
    if dc:
        clauses.append(dc)
    # note when the model leans against recent momentum
    if m3 is not None and proj is not None and abs(m3) >= 5 and (proj > 0) != (m3 > 0):
        clauses.append("model leans against recent momentum (mean-reversion)")

    drivers = "; ".join(clauses) if clauses else "flat recent trajectory"
    hist_txt = f"{int(hist)} months of history" if hist and not np.isnan(hist) else "little history"
    return f"Projects {proj:+.0f}% over {horizon}. Signals: {drivers}. Based on {hist_txt}."


def forecast_game_target(game, target, now):
    pids, dates, P = load_matrix(game, target)
    if P.shape[1] < 14 or len(pids) < 30:
        return []
    R = np.log(P[:, 1:] / P[:, :-1])
    V = volume_matrix(game, pids, dates)
    static = static_features(game, pids)
    last_idx = np.array([np.where(np.isfinite(P[i]))[0][-1] if np.isfinite(P[i]).any() else -1
                         for i in range(len(pids))])
    keep = last_idx >= 0

    rows = []
    for hname, k in HORIZONS.items():
        Xr, y, is_test = [], [], []
        for t in range(1, len(dates) - k):
            v = np.isfinite(P[:, t]) & np.isfinite(P[:, t + k]) & (P[:, t] > 0) & (P[:, t + k] > 0)
            if not v.any():
                continue
            tb = traj_block(P, R, t, V).loc[v].reset_index(drop=True)
            sb = static.iloc[np.where(v)[0]].reset_index(drop=True)
            Xr.append(pd.concat([tb, sb], axis=1))
            y.append(np.log(P[v, t + k] / P[v, t]))
            is_test.append(np.full(int(v.sum()), dates[t] >= CUTOFF))
        if not Xr:
            continue
        X = pd.concat(Xr, ignore_index=True)
        y = np.concatenate(y)
        if len(y) < MIN_SAMPLES:
            continue
        test = np.concatenate(is_test)

        # confidence band from out-of-sample error, if the split is large enough
        # (HGB's binning needs a healthy sample count, so require a real train set)
        if test.sum() >= 50 and (~test).sum() >= 300:
            m = model_new().fit(X[~test], y[~test])
            band = float(mean_absolute_error(y[test], m.predict(X[test])))
        else:
            band = DEFAULT_BAND

        model = model_new().fit(X, y)

        traj_now = pd.DataFrame(np.nan, index=np.arange(len(pids)), columns=traj_block(P, R, 1, V).columns)
        for tv in np.unique(last_idx[keep]):
            blk = traj_block(P, R, int(tv), V)
            sel = last_idx == tv
            traj_now.loc[sel] = blk.loc[sel].to_numpy()
        Xnow = pd.concat([traj_now.loc[keep].reset_index(drop=True),
                          static.loc[keep].reset_index(drop=True)], axis=1)[X.columns]

        ret = np.clip(model.predict(Xnow), -RET_CLIP, RET_CLIP)
        deltas = bucket_deltas(model, Xnow, X)   # per-card trait attribution
        comps = art_comps(game)
        base = P[keep, last_idx[keep]]
        fc = np.round(base * np.exp(ret), 2)
        low = np.round(base * np.exp(ret - band), 2)
        high = np.round(base * np.exp(ret + band), 2)
        asof = [dates[i] + "-01" for i in last_idx[keep]]

        # trajectory signals the model saw, for per-card reasoning
        tn = traj_now.loc[keep].reset_index(drop=True)
        mom3, trend12, vol6, hist = (tn["ret3"].to_numpy(), tn["ret12"].to_numpy(),
                                     tn["vol6"].to_numpy(), tn["hist"].to_numpy())
        vol_now = np.expm1(tn["logvol"].to_numpy()) if "logvol" in tn else np.full(len(tn), np.nan)
        volchg = tn["volchg"].to_numpy() if "volchg" in tn else np.full(len(tn), np.nan)

        for i, (pid, a, b, r, f, lo, hi) in enumerate(zip(pids[keep], asof, base, ret, fc, low, high)):
            reason = make_reason(r, mom3[i], trend12[i], vol6[i], hist[i], hname,
                                 vol_now[i], volchg[i],
                                 comp=comps.get(int(pid)),
                                 deltas_i={bkt: d[i] for bkt, d in deltas.items()})
            rows.append((game, int(pid), target, hname, a, round(float(b), 2),
                         float(f), float(lo), float(hi), round(float(r), 4), reason, MODEL_VERSION, now))
    print(f"[{game}/{target}] {len(rows)} rows", flush=True)
    return rows


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_rows = []
    for game in ["pokemon", "onepiece"]:
        for target in TARGETS:
            try:
                all_rows += forecast_game_target(game, target, now)
            except Exception as e:
                print(f"[{game}/{target}] SKIPPED: {type(e).__name__}: {e}", flush=True)

    conn = sqlite3.connect(OUT_DB, timeout=60)
    conn.executescript(
        """
        DROP TABLE IF EXISTS forecasts;
        CREATE TABLE forecasts (
            game TEXT NOT NULL, product_id INTEGER NOT NULL,
            target TEXT NOT NULL, horizon TEXT NOT NULL,
            as_of TEXT, base_price REAL, forecast_price REAL,
            low REAL, high REAL, ret REAL, reason TEXT, model_version TEXT, scored_at TEXT,
            PRIMARY KEY (game, product_id, target, horizon)
        );
        """
    )
    conn.executemany("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", all_rows)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_rows)} rows -> {os.path.normpath(OUT_DB)} (forecasts)")


if __name__ == "__main__":
    main()
