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

MODEL_VERSION = "forecast-deep-v2"
HORIZONS = {"6m": 6, "12m": 12}
TARGETS = ["ungraded", "grade7", "grade8", "grade9", "grade95", "psa10", "bgs10", "cgc10", "sgc10"]
RET_CLIP = np.log(10.0)
DEFAULT_BAND = 0.5
MIN_SAMPLES = 200


def _pct(logret):
    if logret is None or np.isnan(logret):
        return None
    return (np.exp(logret) - 1.0) * 100.0


def make_reason(ret, mom3, trend12, vol6, hist, horizon, vol_now=None, volchg=None):
    """Plain-English 'why' narrated from the model's own input signals for this card."""
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
                                 vol_now[i], volchg[i])
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
