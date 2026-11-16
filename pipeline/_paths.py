"""Shared path resolution for the data pipeline.

The scripts live in the repo (tcg-predictor/pipeline/), but the data they read and
write — the scraper card DBs, CSVs, ml_data, images, the virtualenv, and .env — still
lives in the sibling `one-piece/` directory (too large / secret to commit).

DATA_DIR points there. Because `one-piece/` and `tcg-predictor/` are siblings, every
existing `DATA_DIR/...` and `DATA_DIR/../tcg-predictor/...` path in the scripts keeps
resolving correctly. Override with the TCG_DATA_DIR env var if the layout changes.
"""

import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tcg-predictor/
DATA_DIR = os.environ.get("TCG_DATA_DIR") or os.path.normpath(
    os.path.join(_REPO, "..", "one-piece"))

# The API's two SQLite databases live in the repo tree, next to the API that
# serves them. One definition (2026-11-15): 38 scripts each spelled this path
# out, so moving a database meant finding every copy.
API_CARDS = os.path.join(DATA_DIR, "..", "tcg-predictor", "dotnet", "API", "Data", "cards")
PC_DB = os.path.join(API_CARDS, "pricecharting.db")
PRED_DB = os.path.join(API_CARDS, "predictions.db")
