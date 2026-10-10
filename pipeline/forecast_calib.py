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


# ---- publish-time calibration (2026-10-10 model review) -----------------------
# The live scorecard showed the point forecast LOSING to persistence on
# typical miss (11.07% vs 10.24% over 1.9M graded 1m calls), the damage
# concentrated in over-sized calls (predicted -13..-19% drops realizing
# -2..-8%), while 80% bands covered 60%: the "high confidence" (narrow)
# stratum was ~2.5x too tight and the wide stratum over-covered. Two
# empirical corrections, fit on the out-of-sample split at train time and
# refreshed from the graded live archive as it accrues (forecast_predict):
#   shrink — per-direction multiplier on the point, grid-searched to minimize
#            weighted MAE (0 publishes persistence, 1 the raw model). Held-out
#            simulation: ungraded miss 10.89 -> 10.17%, PSA10 10.34 -> 9.52%.
#   width-stratified conformal — Mondrian widening keyed by the band's OWN raw
#            width (and price), so tight bands get the push wide ones don't.
#            Held-out: ungraded coverage 60.7 -> 82.1%.

SHRINK_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
WIDTH_EDGES = (0.25, 0.60)          # raw q90 - q10 (log) -> narrow / mid / wide
WIDTH_LABELS = ("narrow", "mid", "wide")


def fit_shrink(pred, y, w=None, grid=SHRINK_GRID, min_n=100):
    """{'up': a, 'down': a}: per-sign point multiplier minimizing weighted MAE."""
    pred = np.asarray(pred, float)
    y = np.asarray(y, float)
    w = np.ones_like(pred) if w is None else np.asarray(w, float)
    out = {}
    for name, m in (("up", pred > 0), ("down", pred <= 0)):
        if m.sum() < min_n:
            out[name] = 1.0
            continue
        p, yy, ww = pred[m], y[m], w[m]
        out[name] = float(min(grid, key=lambda a: np.average(np.abs(a * p - yy), weights=ww)))
    return out


def apply_shrink(pred, shrink):
    pred = np.asarray(pred, float)
    return np.where(pred > 0, pred * shrink["up"], pred * shrink["down"])


def width_stratum(width):
    """0/1/2 = narrow/mid/wide for a raw (pre-widen) log band width."""
    return np.searchsorted(np.asarray(WIDTH_EDGES, float), np.asarray(width, float))


def fit_width_conformal(q10_t, q90_t, y_t, bias, base_t, target=0.80,
                        min_bucket=100, n_price=3):
    """Widening per (width stratum x price bucket); a thin cell falls back to
    its stratum, a thin stratum to the global widening."""
    E = nonconformity(q10_t, q90_t, y_t, bias)
    s = width_stratum(np.asarray(q90_t, float) - np.asarray(q10_t, float))
    lp = np.log10(np.maximum(np.asarray(base_t, float), 0.01))
    edges = np.quantile(lp, np.linspace(0, 1, n_price + 1))[1:-1]
    pb = np.searchsorted(edges, lp)
    glob = conformal_quantile(E, target)
    strata, cov = [], []
    for si in range(len(WIDTH_LABELS)):
        Es = E[s == si]
        sw = conformal_quantile(Es, target) if len(Es) >= min_bucket else glob
        cells = []
        for b in range(n_price):
            Eb = E[(s == si) & (pb == b)]
            cells.append(conformal_quantile(Eb, target) if len(Eb) >= min_bucket else sw)
        strata.append(cells)
        cov.append(float(np.mean(Es <= 0)) if len(Es) else None)
    return {"edges": [float(e) for e in edges], "strata": strata, "global": glob,
            "stratum_cov": cov}


def width_widen_for(q10_now, q90_now, base_now, calib):
    s = width_stratum(np.asarray(q90_now, float) - np.asarray(q10_now, float))
    lp = np.log10(np.maximum(np.asarray(base_now, float), 0.01))
    pb = np.searchsorted(np.asarray(calib["edges"], float), lp)
    return np.asarray(calib["strata"], float)[s, pb]


def merge_width_calib(calib, live):
    """Raise each (stratum, price) widen to the live archive's per-stratum
    value where that is larger — the scalar rolling widen's max policy, kept
    per stratum. `live` maps stratum index -> widen."""
    if not live:
        return calib
    strata = [[max(v, live.get(si, 0.0)) for v in cells]
              for si, cells in enumerate(calib["strata"])]
    return {**calib, "strata": strata,
            "global": max(calib["global"], max(live.values()))}
