"""
Shared forecast calibration (2026-08-11): debias + bucketed conformal bands.

Both backtest_forecast.py and forecast_predict.py import THESE functions, so the
transforms the backtest scores are byte-for-byte the transforms production
applies. Both are fit on the segment's rolling out-of-sample window (the same
split forecast_predict already carves for its retMAE gate), so they are
leakage-free by construction and re-fit fresh every night.

- Debias: the live scorecard showed a persistent +12% optimism (fcerr baseline
  2026-08-09: bias +12.3%). The weighted-median OOS residual is subtracted from
  the point forecast (and shifts the band with it).

- Bucketed conformal: one global widening scalar forces the same fat band on
  every card even though miscoverage concentrates by price regime (cheap cards
  have wild %-returns; 2026-08-10 lorcana/psa10 held-out coverage 0.49-0.54
  needed a blanket +0.24). Mondrian split-conformal over log-price buckets
  widens each price stratum only as much as ITS coverage shortfall demands.
"""

import numpy as np
import pandas as pd


def weighted_median(x, w):
    x = np.asarray(x, float)
    w = np.asarray(w, float)
    order = np.argsort(x)
    cw = np.cumsum(w[order])
    return float(x[order][np.searchsorted(cw, 0.5 * cw[-1])])


def fit_debias(pred_oos, y_test, w_test):
    """Weighted-median of (predicted - realized) log-return residuals."""
    return fit_debias_raw(np.asarray(pred_oos) - np.asarray(y_test), w_test)


def fit_debias_raw(residuals, w_test):
    return float(weighted_median(residuals, w_test))


def conformal_quantile(E, target):
    """Standard split-conformal quantile with finite-sample correction."""
    n = len(E)
    if n == 0:
        return 0.0
    q = min(1.0, np.ceil((n + 1) * target) / n)
    return max(0.0, float(np.quantile(E, q)))


def fit_bucket_conformal(base_test, E, target=0.80, n_buckets=5, min_bucket=40):
    """Per-price-bucket conformal widening (Mondrian split-conformal).

    base_test: base price at month t for each OOS row.
    E: nonconformity max(q10 - y, y - q90) on the same rows.
    Buckets are log10-price quantiles of the OOS rows; a bucket too thin to
    calibrate falls back to the global widening.
    """
    E = np.asarray(E, float)
    lp = np.log10(np.maximum(np.asarray(base_test, float), 0.01))
    glob = conformal_quantile(E, target)
    inner = np.quantile(lp, np.linspace(0, 1, n_buckets + 1))[1:-1]
    idx = np.searchsorted(inner, lp)
    widen = []
    for b in range(n_buckets):
        Eb = E[idx == b]
        widen.append(conformal_quantile(Eb, target) if len(Eb) >= min_bucket else glob)
    return {"edges": [float(e) for e in inner], "widen": widen, "global": glob}


def bucket_widen_for(base_now, calib):
    """Per-card widening for emission-time base prices under a fitted calib."""
    lp = np.log10(np.maximum(np.asarray(base_now, float), 0.01))
    idx = np.searchsorted(np.asarray(calib["edges"], float), lp)
    return np.asarray(calib["widen"], float)[idx]


def nonconformity(q10_pred, q90_pred, y, bias=0.0):
    """CQR nonconformity score; `bias` shifts both interval ends first."""
    return np.maximum((np.asarray(q10_pred) - bias) - np.asarray(y),
                      np.asarray(y) - (np.asarray(q90_pred) - bias))


def emit_bands(pred_raw, q10_raw, q90_raw, widen, bias, ret_clip):
    """Point + band emission: debias shift, conformal widening, quantile-crossing
    guard, clip — one code path for every calibration variant (bias=0 and a
    scalar widen reproduce the uncalibrated production emission exactly)."""
    ret = np.clip(np.asarray(pred_raw) - bias, -ret_clip, ret_clip)
    lo = np.clip(np.minimum(np.asarray(q10_raw) - bias - widen, ret), -ret_clip, ret_clip)
    hi = np.clip(np.maximum(np.asarray(q90_raw) - bias + widen, ret), -ret_clip, ret_clip)
    return ret, lo, hi


# ---- pooled-games training (small-game donor pooling) -----------------------

def add_game_col(X, game):
    """Prepend a `game` categorical so a pooled model can condition on origin."""
    X = X.copy()
    X.insert(0, "game", pd.Categorical([game] * len(X)))
    return X


def concat_pooled(frames):
    """Concat per-game sample frames into one pooled frame.

    Columns are unioned (a feature absent in a donor game becomes NaN — HGB
    handles NaN natively). pd.concat silently degrades categorical columns with
    mismatched categories to object, which would crash HGB's from_dtype
    detection — so every column categorical in ANY frame is re-coerced to
    categorical over the union of its categories.
    """
    cat_cols = {}
    for f in frames:
        for c in f.columns:
            if isinstance(f[c].dtype, pd.CategoricalDtype):
                cat_cols.setdefault(c, set()).update(f[c].cat.categories)
    out = pd.concat(frames, ignore_index=True, sort=False)
    for c, cats in cat_cols.items():
        out[c] = pd.Categorical(out[c], categories=sorted(cats, key=str))
    return out
