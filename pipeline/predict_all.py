"""
Phase 3: train final per-game models and batch-predict EVERY card.

For each game we train two models on the priced cards:
  - full : tabular + CLIP image embeddings  (used for cards that have art)
  - tab  : tabular only                     (fallback for cards without art)
Then we predict a price for every card -- including the ~1.2k unpriced ones,
which are the genuinely useful "what's it worth?" outputs.

Results are written to a single SQLite `predictions` table (game + product_id
keyed) that the .NET API will read. Predictions are app-derived data, kept
separate from the read-only scraper card DBs.

Run:  .venv/bin/python predict_all.py
"""

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")
# Write where the .NET API reads its card DBs.
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "predictions.db")

MODEL_VERSION = "hgb-clip-vitb32-v1"
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


def new_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
        categorical_features="from_dtype", random_state=42,
    )


def prepare(game: str):
    df = pd.read_csv(os.path.join(DATA, f"{game}_cards.csv"))

    z = np.load(os.path.join(DATA, f"{game}_img_emb.npz"))
    dim = z["emb"].shape[1]
    img_cols = [f"img_{i}" for i in range(dim)]
    emb = pd.DataFrame(z["emb"], columns=img_cols)
    emb["product_id"] = z["product_id"]

    df = df.merge(emb, on="product_id", how="left")  # keep ALL cards
    has_image = df["img_0"].notna().to_numpy()

    if "release_date" in df.columns:
        df["release_year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.drop(columns=["release_date"])

    tab_cols = [c for c in df.columns if c not in DROP and not c.startswith("img_")]

    X = df[tab_cols + img_cols].copy()
    for col in NUMERIC_TEXT:
        if col in X.columns:
            X[col] = pd.to_numeric(X[col], errors="coerce")
    for col in X[tab_cols].select_dtypes(include="object").columns:
        X[col] = cap(X[col].astype("string").fillna("MISSING")).astype("category")

    priced = df[TARGET].notna().to_numpy() & (df[TARGET].to_numpy(dtype="float64", na_value=0) > 0)
    return df, X, tab_cols, img_cols, has_image, priced


def predict_game(game: str) -> pd.DataFrame:
    df, X, tab_cols, img_cols, has_image, priced = prepare(game)
    y = np.log1p(df[TARGET].astype(float))

    # Train: full (priced & imaged) and tabular fallback (all priced).
    full = new_model().fit(X.loc[priced & has_image], y[priced & has_image])
    tab = new_model().fit(X.loc[priced, tab_cols], y[priced])

    pred_log = np.empty(len(df))
    pred_log[has_image] = full.predict(X.loc[has_image])
    pred_log[~has_image] = tab.predict(X.loc[~has_image, tab_cols])
    predicted = np.clip(np.expm1(pred_log), 0, None)

    out = pd.DataFrame({
        "game": game,
        "product_id": df["product_id"].to_numpy(),
        "predicted_price": np.round(predicted, 2),
        "actual_price": df[TARGET].to_numpy(),
        "used_image": has_image.astype(int),
        "model_version": MODEL_VERSION,
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # Sanity: correlation of prediction vs actual on priced cards.
    pa = out.loc[priced]
    corr = np.corrcoef(np.log1p(pa["actual_price"]), np.log1p(pa["predicted_price"]))[0, 1]
    print(f"[{game}] {len(out)} cards scored | imaged {int(has_image.sum())} | "
          f"tabular {int((~has_image).sum())} | unpriced {int((~priced).sum())} | "
          f"train-fit corr(log) {corr:.3f}")
    return out


def write_db(frames):
    all_preds = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.executescript(
        """
        DROP TABLE IF EXISTS predictions;
        CREATE TABLE predictions (
            game            TEXT    NOT NULL,
            product_id      INTEGER NOT NULL,
            predicted_price REAL    NOT NULL,
            actual_price    REAL,
            used_image      INTEGER NOT NULL,
            model_version   TEXT    NOT NULL,
            scored_at       TEXT    NOT NULL,
            PRIMARY KEY (game, product_id)
        );
        """
    )
    all_preds.to_sql("predictions", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    print(f"\nwrote {len(all_preds)} rows -> {os.path.normpath(OUT_DB)}")


def main():
    frames = [predict_game(g) for g in ["onepiece", "pokemon"]]
    write_db(frames)


if __name__ == "__main__":
    main()
