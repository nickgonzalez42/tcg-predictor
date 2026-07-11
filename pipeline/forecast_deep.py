"""
Forecasting v2 on the deep unified history (monthly, ~5.5yr, multiple regimes).

Targets: ungraded AND psa10. Horizons: 1m / 6m / 12m.
Model: predict future log-return r = log(price[t+h]/price[t]) from
  - trajectory: log price, 1/3/12-month momentum, 6-month volatility, history length
  - static: card tabular features + PCA-compressed CLIP art embedding
then price forecast = price[t] * exp(r).

Honest evaluation: TEMPORAL backtest. Train on base months < CUTOFF (2025-01),
test on base months >= CUTOFF -> out-of-sample on the recent regime (not just
held-out cards). Reports directional accuracy, log-return MAE, median price APE.

Run:  .venv/bin/python forecast_deep.py
"""

import collections
import os
import sqlite3
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

HORIZONS = {"1m": 1, "6m": 6, "12m": 12}
TARGETS = ["ungraded", "psa10"]
CUTOFF = "2025-01"      # base months >= this are the out-of-sample test set
IMG_PCA = 24

DROP = {
    "product_id", "name", "clean_name", "set_url_name", "card_number",
    "product_url", "image_url", "image_path", "image_file", "has_local_image",
    "scraped_at", "history_fetched", "history_fetched_at",
    "description", "flavor_text", "detail_note",
    "lowest_price", "lowest_price_ship", "total_listings", "market_price",
}
NUMERIC_TEXT = ["life", "power", "cost", "counter", "hp", "retreat_cost"]
MAX_CATEGORIES = 250


def load_matrix(game, grade):
    rows = sqlite3.connect(PC_DB).execute(
        "SELECT product_id, date, price FROM price_history_unified WHERE game=? AND grade=?",
        (game, grade)).fetchall()
    by = collections.defaultdict(dict)
    for pid, d, p in rows:
        by[pid][d[:7]] = p
    dates = sorted({d[:7] for _, d, _ in rows})
    didx = {d: i for i, d in enumerate(dates)}
    pids = sorted(by)
    P = np.full((len(pids), len(dates)), np.nan)
    for i, pid in enumerate(pids):
        for m, price in by[pid].items():
            P[i, didx[m]] = price
    return np.array(pids), dates, P


def static_features(game, pids):
    df = pd.read_csv(os.path.join(DATA, f"{game}_cards.csv"))
    if "release_date" in df.columns:
        df["release_year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.drop(columns=["release_date"])
    tab_cols = [c for c in df.columns if c not in DROP]
    tab = df[["product_id"] + tab_cols].copy()
    for col in NUMERIC_TEXT:
        if col in tab.columns:
            tab[col] = pd.to_numeric(tab[col], errors="coerce")
    for col in tab[tab_cols].select_dtypes(include="object").columns:
        keep = tab[col].value_counts().head(MAX_CATEGORIES).index
        tab[col] = tab[col].astype("string").fillna("MISSING").where(lambda s: s.isin(keep), "OTHER").astype("category")

    z = np.load(os.path.join(DATA, f"{game}_img_emb.npz"))
    n = min(IMG_PCA, z["emb"].shape[1])
    pcs = PCA(n_components=n, random_state=42).fit_transform(z["emb"])
    img = pd.DataFrame(pcs, columns=[f"img{i}" for i in range(n)])
    img["product_id"] = z["product_id"]

    feat = tab.merge(img, on="product_id", how="left").set_index("product_id").reindex(pids).reset_index(drop=True)
    return feat


_SET_LABELS_CACHE = {}


def _set_labels(game):
    """product_id -> set_name, cached (the CSV is several MB and per-game constant)."""
    if game not in _SET_LABELS_CACHE:
        df = pd.read_csv(os.path.join(DATA, f"{game}_cards.csv"))
        _SET_LABELS_CACHE[game] = dict(zip(df["product_id"].astype(int),
                                           df["set_name"].astype("string").fillna("")))
    return _SET_LABELS_CACHE[game]


def set_matrix(game, pids, P):
    """cards x months matrix of each card's SET price index (median member price).

    Lets the model see set-level performance directly (a rising set lifts its
    cards) and each card's price relative to its set. Time-aligned: the index
    at month t only uses prices at month t, so training samples see exactly
    what was knowable then. Sets with <3 priced members stay NaN.
    """
    import warnings
    name_of = _set_labels(game)
    labels = np.array([name_of.get(int(p), "") for p in pids])
    S = np.full_like(P, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN months
        for s in np.unique(labels):
            rows = labels == s
            if not s or rows.sum() < 3:
                continue
            S[rows] = np.nanmedian(P[rows], axis=0)
    return S


def extra_signal_matrices(game, pids, dates):
    """Optional external signals -> {name: cards x months matrix}.

    Drop a CSV at ml_data/{game}_extra_signals.csv with columns
    (product_id, month YYYY-MM, <numeric signal columns...>) — e.g. tournament
    play rates or news/hype scores — and every column joins the model as a
    monthly feature (sig_<name>) with no code change. Absent file = no-op.
    """
    path = os.path.join(DATA, f"{game}_extra_signals.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    didx = {d: i for i, d in enumerate(dates)}
    pidx = {int(p): i for i, p in enumerate(pids)}
    out = {}
    for col in df.columns:
        if col in ("product_id", "month"):
            continue
        M = np.full((len(pids), len(dates)), np.nan)
        for pid, m, v in zip(df["product_id"], df["month"], df[col]):
            i, j = pidx.get(int(pid)), didx.get(str(m)[:7])
            if i is not None and j is not None:
                M[i, j] = v
        out[col] = M
    return out



def cum_stats(P):
    """Cumulative per-month stats, computed ONCE per price matrix so traj_block
    is O(cards) per month instead of re-scanning every earlier month."""
    finite = np.isfinite(P)
    runmax = np.fmax.accumulate(np.where(finite, P, -np.inf), axis=1)
    runmax = np.where(np.isfinite(runmax), runmax, np.nan)
    first = np.where(finite.any(1), finite.argmax(1), -1)   # first priced month (-1 = never)
    return {
        "runmax": runmax,
        "hist": np.cumsum(finite, axis=1).astype(float),
        "first": first,
    }


def traj_block(P, R, t, V=None, S=None, EXTRA=None, CUM=None):
    n = P.shape[0]
    def m(k):
        return np.log(P[:, t] / P[:, t - k]) if t >= k else np.full(n, np.nan)
    cum = CUM if CUM is not None else cum_stats(P[:, :t + 1])
    tc = t if CUM is not None else -1   # cum arrays span all months only when precomputed
    first = cum["first"]
    block = {
        "logp": np.log(P[:, t]),
        "ret1": m(1), "ret3": m(3), "ret12": m(12),
        "vol6": np.nanstd(R[:, max(0, t - 6):t], axis=1) if t >= 1 else np.full(n, np.nan),
        "hist": cum["hist"][:, tc],
        # months since the card first had a price (new prints behave differently)
        "age": np.where((first >= 0) & (first <= t), t - first, np.nan).astype(float),
        # drawdown from the running all-time high as of month t
        "dd": np.log(P[:, t] / cum["runmax"][:, tc]),
    }
    if V is not None:
        block["logvol"] = np.log1p(V[:, t])                                    # recent monthly units sold
        block["volchg"] = (np.log1p(V[:, t]) - np.log1p(V[:, t - 3])           # 3-month volume trend
                           if t >= 3 else np.full(n, np.nan))
    if S is not None:
        # set performance: the card's set index momentum + price vs its set
        block["setret3"] = np.log(S[:, t] / S[:, t - 3]) if t >= 3 else np.full(n, np.nan)
        block["setret12"] = np.log(S[:, t] / S[:, t - 12]) if t >= 12 else np.full(n, np.nan)
        block["setrel"] = np.log(P[:, t] / S[:, t])
    for name, M in (EXTRA or {}).items():
        block[f"sig_{name}"] = M[:, t]   # external signals (tournament/news/...)
    return pd.DataFrame(block)


def run(game, grade, static, model_new):
    pids, dates, P = load_matrix(game, grade)
    if P.shape[1] < 14:
        print(f"  [{game}/{grade}] too few months ({P.shape[1]}) — skip")
        return
    stat = static.reindex(range(len(pids)))  # static already aligned by caller per game
    R = np.log(P[:, 1:] / P[:, :-1])
    # TCGplayer sales volume is no longer collected (an A/B showed no accuracy
    # gain, and the legacy price_history tables were dropped).
    V = None
    S = set_matrix(game, pids, P)
    EXTRA = extra_signal_matrices(game, pids, dates)
    CUM = cum_stats(P)
    cutoff_idx = next((i for i, d in enumerate(dates) if d >= CUTOFF), len(dates))

    print(f"\n[{game} / {grade}]  cards={len(pids)}  months={len(dates)} ({dates[0]}..{dates[-1]})")
    for hname, k in HORIZONS.items():
        Xr, y, is_test = [], [], []
        for t in range(1, len(dates) - k):
            valid = np.isfinite(P[:, t]) & np.isfinite(P[:, t + k]) & (P[:, t] > 0) & (P[:, t + k] > 0)
            if not valid.any():
                continue
            tb = traj_block(P, R, t, V, S, EXTRA, CUM).loc[valid].reset_index(drop=True)
            sb = stat.iloc[np.where(valid)[0]].reset_index(drop=True)
            Xr.append(pd.concat([tb, sb], axis=1))
            y.append(np.log(P[valid, t + k] / P[valid, t]))
            is_test.append(np.full(valid.sum(), dates[t] >= CUTOFF))
        if not Xr:
            continue
        X = pd.concat(Xr, ignore_index=True)
        y = np.concatenate(y)
        test = np.concatenate(is_test)
        if test.sum() < 50 or (~test).sum() < 50:
            print(f"  [{hname}] insufficient split (train {(~test).sum()}, test {test.sum()})")
            continue
        model = model_new().fit(X[~test], y[~test])
        pred = model.predict(X[test])
        yt = y[test]
        dir_acc = np.mean(np.sign(pred) == np.sign(yt))
        ape = np.abs(np.expm1(pred) - np.expm1(yt)) / (np.expm1(yt) + 1e-9)
        print(f"  [{hname:3}] train {(~test).sum():>7} test {test.sum():>6} | "
              f"dir {dir_acc:5.1%} | retMAE {mean_absolute_error(yt, pred):.3f} | medAPE {np.median(ape):5.1%}")


def model_new():
    return HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, max_leaf_nodes=63,
        categorical_features="from_dtype", random_state=42)


def model_quantile(q):
    """Same architecture trained on pinball loss — per-card scenario quantiles."""
    return HistGradientBoostingRegressor(
        loss="quantile", quantile=q,
        max_iter=400, learning_rate=0.05, max_leaf_nodes=63,
        categorical_features="from_dtype", random_state=42)


def main():
    for game in ["pokemon", "onepiece"]:
        # static aligned to the ungraded pid ordering; reload per grade inside run via reindex
        for grade in TARGETS:
            pids, _, _ = load_matrix(game, grade)
            stat = static_features(game, pids)
            run(game, grade, stat, model_new)


if __name__ == "__main__":
    main()
