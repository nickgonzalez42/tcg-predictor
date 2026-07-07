# Data pipeline

Scrapes card data + prices from **TCGplayer** and the **PriceCharting API**, builds a
unified price history, and trains the price forecasts the API serves.

## Layout

The scripts are versioned here (`tcg-predictor/pipeline/`). The data they read and
write is **not** committed — it lives in the sibling `one-piece/` directory:

```
parent/
├── tcg-predictor/pipeline/   <- these scripts (in git)
│   └── _paths.py             <- resolves DATA_DIR -> ../../one-piece
└── one-piece/                <- data (NOT in git): *.db, images/, ml_data/, .venv/, .env
```

`_paths.py` computes `DATA_DIR` (override with the `TCG_DATA_DIR` env var). Because the
two folders are siblings, every path in the scripts resolves correctly unchanged.

Requirements: the virtualenv lives at `one-piece/.venv`. Recreate it from
`requirements.txt` if needed:

```
python3 -m venv ../../one-piece/.venv
../../one-piece/.venv/bin/pip install -r requirements.txt
```

## Weekly refresh (the main entry point)

Run from this directory with the pipeline venv:

```
../../one-piece/.venv/bin/python weekly_refresh.py          # everything
../../one-piece/.venv/bin/python weekly_refresh.py --list   # show steps
../../one-piece/.venv/bin/python weekly_refresh.py --from unify   # resume after a fix
```

Every step is **idempotent** and every source write is **append-only** (date-keyed
`INSERT OR REPLACE`), so a re-run never loses history; derived tables (unified series,
forecasts) are rebuilt from the sources.

## Secrets

`one-piece/.env` holds `PRICECHARTING_TOKEN`. It is git-ignored — never commit it.
