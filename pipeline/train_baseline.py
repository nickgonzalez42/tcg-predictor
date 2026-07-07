"""
Phase 1 baseline: does the card's *design* predict its price?

Trains one gradient-boosted-tree regression per game on the TABULAR features
only (no images yet), to check whether there's signal worth pursuing before
building the appearance pipeline.

- Model: sklearn HistGradientBoostingRegressor (native categorical + NaN
  handling, no OpenMP/system deps).
- Target: log1p(market_price); metrics reported back in dollars.
- Drops price-derived columns (target leakage) and identifiers/free text.
- High-cardinality categoricals are capped to the top categories (+ "OTHER").

Run:  .venv/bin/python train_baseline.py
"""

import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")

TARGET = "market_price"

# Never use as features: identifiers, free text, scrape metadata, and
# anything price-derived (would leak the target).
DROP = {
    "product_id", "name", "clean_name", "set_url_name", "card_number",
    "product_url", "image_url", "image_path", "image_file", "has_local_image",
    "scraped_at", "history_fetched", "history_fetched_at",
    "description", "flavor_text", "detail_note",
    "lowest_price", "lowest_price_ship", "total_listings",  # leakage
    TARGET,
}

# Text columns that are really numbers ("5000", "-", "1").
NUMERIC_TEXT = ["life", "power", "cost", "counter", "hp", "retreat_cost"]

MAX_CATEGORIES = 250  # HistGBR caps categorical cardinality at 255


def cap_categories(s: pd.Series, top_n: int = MAX_CATEGORIES) -> pd.Series:
    keep = s.value_counts().head(top_n).index
    return s.where(s.isin(keep), other="OTHER")


def prepare(df: pd.DataFrame):
    df = df[df[TARGET].notna() & (df[TARGET] > 0)].copy()
    y = np.log1p(df[TARGET].astype(float))

    if "release_date" in df.columns:
        df["release_year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        df = df.drop(columns=["release_date"])

    feature_cols = [c for c in df.columns if c not in DROP]
    X = df[feature_cols].copy()

    for col in NUMERIC_TEXT:
        if col in X.columns:
            X[col] = pd.to_numeric(X[col], errors="coerce")

    # Remaining object columns -> categorical (capped cardinality).
    for col in X.select_dtypes(include="object").columns:
        capped = cap_categories(X[col].astype("string").fillna("MISSING"))
        X[col] = capped.astype("category")

    return X, y


def run(game: str):
    path = os.path.join(DATA, f"{game}_cards.csv")
    df = pd.read_csv(path)
    X, y = prepare(df)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = HistGradientBoostingRegressor(
        max_iter=600,
        learning_rate=0.05,
        max_leaf_nodes=63,
        categorical_features="from_dtype",
        random_state=42,
    )
    model.fit(X_train, y_train)

    pred_log = model.predict(X_test)
    pred = np.expm1(pred_log)
    actual = np.expm1(y_test)

    mae = mean_absolute_error(actual, pred)
    medae = float(np.median(np.abs(actual - pred)))
    r2_log = r2_score(y_test, pred_log)

    print(f"\n===== {game.upper()} =====")
    print(f"rows trained/tested : {len(X_train)} / {len(X_test)}")
    print(f"features            : {X.shape[1]}")
    print(f"R^2 (log price)     : {r2_log:.3f}")
    print(f"MAE  (dollars)      : ${mae:,.2f}")
    print(f"Median AE (dollars) : ${medae:,.2f}")
    print(f"median actual price : ${float(actual.median()):,.2f}")

    # Permutation importance on a sample (faster, model-agnostic).
    sample = min(2000, len(X_test))
    Xs, ys = X_test.iloc[:sample], y_test.iloc[:sample]
    imp = permutation_importance(model, Xs, ys, n_repeats=5, random_state=42, n_jobs=-1)
    ranking = pd.Series(imp.importances_mean, index=X.columns).sort_values(ascending=False).head(15)
    print("top features (permutation importance):")
    for name, val in ranking.items():
        print(f"   {name:18s} {val:.4f}")


def main():
    for game in ["onepiece", "pokemon"]:
        run(game)


if __name__ == "__main__":
    main()
