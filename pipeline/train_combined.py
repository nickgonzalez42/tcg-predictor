"""
Phase 2b: does the card's appearance add predictive power on top of features?

Joins CLIP image embeddings (from embed_images.py) to the tabular features by
product_id, then trains TWO models on the exact same rows:
    (a) tabular only
    (b) tabular + image embeddings
so the difference isolates the lift from appearance (not from the row subset).

Run:  .venv/bin/python train_combined.py   (after embed_images.py)
"""

import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")

TARGET = "market_price"

DROP = {
    "product_id", "name", "clean_name", "set_url_name", "card_number",
    "product_url", "image_url", "image_path", "image_file", "has_local_image",
    "scraped_at", "history_fetched", "history_fetched_at",
    "description", "flavor_text", "detail_note",
    "lowest_price", "lowest_price_ship", "total_listings",
    TARGET,
}
NUMERIC_TEXT = ["life", "power", "cost", "counter", "hp", "retreat_cost"]
MAX_CATEGORIES = 250


def cap(s: pd.Series) -> pd.Series:
    keep = s.value_counts().head(MAX_CATEGORIES).index
    return s.where(s.isin(keep), other="OTHER")


def build(game: str):
    df = pd.read_csv(os.path.join(DATA, f"{game}_cards.csv"))
    df = df[df[TARGET].notna() & (df[TARGET] > 0)].copy()

    z = np.load(os.path.join(DATA, f"{game}_img_emb.npz"))
    dim = z["emb"].shape[1]
    img_cols = [f"img_{i}" for i in range(dim)]
    emb = pd.DataFrame(z["emb"], columns=img_cols)
    emb["product_id"] = z["product_id"]

    df = df.merge(emb, on="product_id", how="inner")  # priced AND has embedding

    if "release_date" in df.columns:
        df["release_year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.drop(columns=["release_date"])

    y = np.log1p(df[TARGET].astype(float))
    tab_cols = [c for c in df.columns if c not in DROP and not c.startswith("img_")]

    X = df[tab_cols + img_cols].copy()
    for col in NUMERIC_TEXT:
        if col in X.columns:
            X[col] = pd.to_numeric(X[col], errors="coerce")
    for col in X[tab_cols].select_dtypes(include="object").columns:
        X[col] = cap(X[col].astype("string").fillna("MISSING")).astype("category")

    return X, y, tab_cols, img_cols


def fit_report(X, y, label, train_idx, test_idx):
    model = HistGradientBoostingRegressor(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
        categorical_features="from_dtype", random_state=42,
    )
    model.fit(X.iloc[train_idx], y.iloc[train_idx])
    pred_log = model.predict(X.iloc[test_idx])
    actual, pred = np.expm1(y.iloc[test_idx]), np.expm1(pred_log)
    r2 = r2_score(y.iloc[test_idx], pred_log)
    mae = mean_absolute_error(actual, pred)
    medae = float(np.median(np.abs(actual - pred)))
    print(f"   {label:22s} R^2={r2:.3f} | MAE=${mae:,.2f} | MedAE=${medae:,.2f} | feats={X.shape[1]}")
    return r2


def run(game: str):
    X, y, tab_cols, img_cols = build(game)
    idx = np.arange(len(X))
    train_idx, test_idx = train_test_split(idx, test_size=0.2, random_state=42)

    print(f"\n===== {game.upper()} (imaged & priced: {len(X)} rows) =====")
    r2_tab = fit_report(X[tab_cols], y, "tabular only", train_idx, test_idx)
    r2_all = fit_report(X, y, "tabular + image", train_idx, test_idx)
    print(f"   -> appearance lift in R^2: {r2_all - r2_tab:+.3f}")


def main():
    for game in ["onepiece", "pokemon"]:
        run(game)


if __name__ == "__main__":
    main()
