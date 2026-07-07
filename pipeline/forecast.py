"""
Forecasting: predict each card's price 1 month and 6 months out.

Data reality: TCGplayer exposes ~1 year of weekly price history (a regular
7-day grid of 55 snapshots), so only short horizons are learnable. We model
the *future return* r = log(price[t+h] / price[t]) and turn it back into a
price = latest_price * exp(r).

Features per training sample (card at base week t):
  - trajectory: current log price, 4w & 12w momentum, 8w volatility,
    recent sales volume, weeks of history so far
  - static: the card's tabular features + a PCA-compressed CLIP art embedding
Target: log return to t+h.  Horizons: 1m = 4 weeks, 6m = 26 weeks.

Validation is by HELD-OUT CARDS (does it generalize to cards it never saw),
matching the "works for new cards too" goal. Final models retrain on all
cards, then every card is forecast from its latest known price.

Output: predictions.db `forecasts` table (read by the .NET API).

Run:  .venv/bin/python forecast.py
"""

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

MODEL_VERSION = "forecast-hgb-v1"
HORIZONS = {"1m": 4, "6m": 26}   # weeks (grid steps)
STEP = 2                          # stride over base weeks when building samples
IMG_PCA = 32                      # compress 512-d CLIP embedding
RET_CLIP = np.log(8.0)            # cap |predicted log-return| (=> 0.125x .. 8x)

# Static tabular feature handling (mirrors the value model).
DROP = {
    "product_id", "name", "clean_name", "set_url_name", "card_number",
    "product_url", "image_url", "image_path", "image_file", "has_local_image",
    "scraped_at", "history_fetched", "history_fetched_at",
    "description", "flavor_text", "detail_note",
    "lowest_price", "lowest_price_ship", "total_listings", "market_price",
}
NUMERIC_TEXT = ["life", "power", "cost", "counter", "hp", "retreat_cost"]
MAX_CATEGORIES = 250


def cap(s: pd.Series) -> pd.Series:
    keep = s.value_counts().head(MAX_CATEGORIES).index
    return s.where(s.isin(keep), other="OTHER")


def new_model():
    return HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, max_leaf_nodes=63,
        categorical_features="from_dtype", random_state=42,
    )


def load_price_matrix(game: str):
    """Return (product_ids, dates, price matrix P, volume matrix V) on the weekly grid."""
    conn = sqlite3.connect(os.path.join(BASE, f"{game}_cards.db"))
    ph = pd.read_sql(
        "SELECT product_id, variant, bucket_date, market_price, quantity_sold "
        "FROM price_history WHERE market_price IS NOT NULL", conn)
    conn.close()

    # One series per card: the most-traded variant.
    vol = ph.groupby(["product_id", "variant"])["quantity_sold"].sum().reset_index()
    dominant = vol.sort_values("quantity_sold").groupby("product_id").tail(1)[["product_id", "variant"]]
    ph = ph.merge(dominant, on=["product_id", "variant"])

    dates = sorted(ph["bucket_date"].unique())
    date_idx = {d: i for i, d in enumerate(dates)}
    pids = sorted(ph["product_id"].unique())
    pid_idx = {p: i for i, p in enumerate(pids)}

    P = np.full((len(pids), len(dates)), np.nan)
    V = np.full((len(pids), len(dates)), np.nan)
    r = ph["product_id"].map(pid_idx).to_numpy()
    c = ph["bucket_date"].map(date_idx).to_numpy()
    P[r, c] = ph["market_price"].to_numpy()
    V[r, c] = ph["quantity_sold"].to_numpy()
    return np.array(pids), dates, P, V


def static_features(game: str, pids: np.ndarray):
    """Tabular features + PCA(CLIP) per product_id, aligned to `pids`."""
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
        tab[col] = cap(tab[col].astype("string").fillna("MISSING")).astype("category")

    z = np.load(os.path.join(DATA, f"{game}_img_emb.npz"))
    n_comp = min(IMG_PCA, z["emb"].shape[1])
    pcs = PCA(n_components=n_comp, random_state=42).fit_transform(z["emb"])
    img = pd.DataFrame(pcs, columns=[f"imgpca_{i}" for i in range(n_comp)])
    img["product_id"] = z["product_id"]

    feat = tab.merge(img, on="product_id", how="left")
    feat = feat.set_index("product_id").reindex(pids).reset_index(drop=True)
    return feat


def trajectory_block(P, V, R, t):
    """Vectorized trajectory features for all cards at base week t."""
    def safe_div(a, b):
        return np.log(a / b)
    block = {
        "logp": np.log(P[:, t]),
        "mom_4": safe_div(P[:, t], P[:, t - 4]) if t >= 4 else np.full(P.shape[0], np.nan),
        "mom_12": safe_div(P[:, t], P[:, t - 12]) if t >= 12 else np.full(P.shape[0], np.nan),
        "vol_8": np.nanstd(R[:, max(0, t - 8):t], axis=1) if t >= 1 else np.full(P.shape[0], np.nan),
        "vmean_4": np.nanmean(V[:, max(0, t - 3):t + 1], axis=1),
        "hist_len": np.sum(np.isfinite(P[:, :t + 1]), axis=1).astype(float),
    }
    return pd.DataFrame(block)


def build_samples(P, V, R, static, k):
    """Stack (trajectory + static -> log return) samples across base weeks."""
    n_weeks = P.shape[1]
    rows, targets, pid_pos = [], [], []
    for t in range(0, n_weeks - k, STEP):
        valid = np.isfinite(P[:, t]) & np.isfinite(P[:, t + k]) & (P[:, t] > 0) & (P[:, t + k] > 0)
        if not valid.any():
            continue
        traj = trajectory_block(P, V, R, t).loc[valid].reset_index(drop=True)
        traj["__pos"] = np.where(valid)[0]
        rows.append(traj)
        targets.append(np.log(P[valid, t + k] / P[valid, t]))
        pid_pos.append(np.where(valid)[0])
    traj_all = pd.concat(rows, ignore_index=True)
    y = np.concatenate(targets)
    pos = traj_all.pop("__pos").to_numpy()
    stat = static.iloc[pos].reset_index(drop=True)
    X = pd.concat([traj_all, stat], axis=1)
    return X, y, pos


def validate(X, y, pos, pids):
    """Held-out-CARD split, so test cards are unseen during training."""
    uniq = np.unique(pids[pos])
    train_ids, test_ids = train_test_split(uniq, test_size=0.2, random_state=42)
    test_id_set = set(test_ids.tolist())
    is_test = np.array([pids[p] in test_id_set for p in pos])

    model = new_model().fit(X[~is_test], y[~is_test])
    pred = model.predict(X[is_test])
    yt = y[is_test]
    dir_acc = float(np.mean(np.sign(pred) == np.sign(yt)))
    price_ape = np.abs(np.expm1(pred) - np.expm1(yt)) / (np.expm1(yt) + 1e-9)
    return {
        "n_test": int(is_test.sum()),
        "ret_mae": float(np.mean(np.abs(pred - yt))),
        "dir_acc": dir_acc,
        "median_price_ape": float(np.median(price_ape)),
    }


def forecast_game(game: str):
    pids, dates, P, V = load_price_matrix(game)
    R = np.log(P[:, 1:] / P[:, :-1])
    static = static_features(game, pids)

    # latest known price per card (base for the actual forecast).
    last_idx = np.array([np.where(np.isfinite(P[i]))[0][-1] if np.isfinite(P[i]).any() else -1
                         for i in range(len(pids))])

    result = pd.DataFrame({"product_id": pids})
    result["as_of_date"] = [dates[i] if i >= 0 else None for i in last_idx]
    result["base_price"] = [P[i, last_idx[i]] if last_idx[i] >= 0 else np.nan for i in range(len(pids))]

    print(f"\n===== {game.upper()} =====  cards with history: {len(pids)}")
    for hname, k in HORIZONS.items():
        X, y, pos = build_samples(P, V, R, static, k)
        metrics = validate(X, y, pos, pids)
        print(f"  [{hname}] samples={len(y):>7} | held-out cards: "
              f"ret_MAE(log)={metrics['ret_mae']:.3f} | dir_acc={metrics['dir_acc']:.1%} | "
              f"median price APE={metrics['median_price_ape']:.1%}")

        # Retrain on all samples, forecast from each card's latest week.
        model = new_model().fit(X, y)

        # "Now" trajectory features at each card's own last index. Compute one
        # block per distinct last-week value (<=55) instead of per card.
        keep = last_idx >= 0
        traj_now = pd.DataFrame(np.nan, index=np.arange(len(pids)), columns=trajectory_block(P, V, R, 1).columns)
        for t_val in np.unique(last_idx[keep]):
            block = trajectory_block(P, V, R, int(t_val))
            sel = last_idx == t_val
            traj_now.loc[sel] = block.loc[sel].to_numpy()

        Xnow = pd.concat([traj_now.loc[keep].reset_index(drop=True),
                          static.loc[keep].reset_index(drop=True)], axis=1)[X.columns]
        r = np.clip(model.predict(Xnow), -RET_CLIP, RET_CLIP)
        base = result.loc[keep, "base_price"].to_numpy()
        result.loc[keep, f"return_{hname}"] = r
        result.loc[keep, f"forecast_{hname}"] = np.round(base * np.exp(r), 2)

    result["game"] = game
    result["model_version"] = MODEL_VERSION
    result["scored_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result.dropna(subset=["base_price"])


def write_db(frames):
    cols = ["game", "product_id", "as_of_date", "base_price",
            "forecast_1m", "return_1m", "forecast_6m", "return_6m",
            "model_version", "scored_at"]
    out = pd.concat(frames, ignore_index=True)[cols]
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.executescript(
        """
        DROP TABLE IF EXISTS forecasts;
        CREATE TABLE forecasts (
            game          TEXT    NOT NULL,
            product_id    INTEGER NOT NULL,
            as_of_date    TEXT,
            base_price    REAL,
            forecast_1m   REAL,
            return_1m     REAL,
            forecast_6m   REAL,
            return_6m     REAL,
            model_version TEXT    NOT NULL,
            scored_at     TEXT    NOT NULL,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    out.to_sql("forecasts", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(out)} forecasts -> {os.path.normpath(OUT_DB)}")


def main():
    frames = [forecast_game(g) for g in ["onepiece", "pokemon"]]
    write_db(frames)


if __name__ == "__main__":
    main()
