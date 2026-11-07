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

## Per-game articles (2026-11-06)

`game_articles.py` writes two articles per game per week into an `articles` table in
predictions.db (served at `/api/articles`, pages at `/articles/{slug}`): a **set
report** on the game's newest set (chase cards, the week's movers, rarities, price
index since release, price ladder, the model's outlook) and a **game report** on the
whole game (the week's drift, which sets moved, movers, groups that moved together,
price ladder, the model's record and picks). `article_editorial.py` writes the prose
(Claude, house voice) around the computed numbers; `article_pages.py` renders the
static twins the nightly rsyncs to `/srv/tcg/static/articles/`.

The step runs after `market_report` in `local_model_run.sh` and keys its games off the
run's UTC weekday: Mon Pokémon + Magic, Tue Yu-Gi-Oh! + One Piece, Wed Lorcana + Digimon,
Thu Star Wars Unlimited + Flesh and Blood, Fri Gundam + Riftbound, Sat Sorcery +
Cyberpunk, Sun none. `--all` writes every game at once; `--game X --kind set|game`
one article; `--no-prose` skips the Claude call.

## Secrets

`one-piece/.env` holds `PRICECHARTING_TOKEN`. It is git-ignored — never commit it.
