"""
The game registry — single source of truth for every game the project tracks.

Every pipeline script derives its game list and per-game paths from here, so
adding a game is one entry in GAMES (plus a pc_category once PriceCharting
starts covering it — until then the game is crawled and stored but has no
prices, and the site hides unpriced cards automatically).

Pokemon and One Piece keep their dedicated scrapers (typed stat columns from
the original build); every newer game uses the generic tcg_scraper.py, which
stores the shared columns plus the full customAttributes JSON.
"""

import os

from _paths import DATA_DIR as BASE   # data lives in the sibling one-piece/ dir

# Card art lives in S3 — the ONLY durable copy. Local image dirs are a staging
# area for new scrapes (embedded, uploaded by s3_upload_images.py, then pruned
# after a week); the site serves the bucket through CloudFront.
IMAGES_BUCKET = "cardstock-card-images"

GAMES = {
    "pokemon": {
        "label": "Pokémon",
        "tcg_line": "pokemon",                # TCGplayer productLineName filter value
        "db": "pokemon_cards.db",
        "images": "images_pokemon",
        "pc_category": "pokemon-cards",       # PriceCharting price-guide slug
        "pc_csv": "pricecharting_pokemon.csv",
        "pc_min_rows": 50_000,                # refuse a suspiciously small download
        "scraper": ["tcg_pokemon_scraper.py"],
    },
    "onepiece": {
        "label": "One Piece",
        "tcg_line": "one-piece-card-game",
        "db": "onepiece_cards.db",
        "images": "images",                   # legacy dir name from the first build
        "pc_category": "one-piece-cards",
        "pc_csv": "pricecharting_onepiece.csv",
        "pc_min_rows": 5_000,
        "scraper": ["tcg_onepiece_scraper.py"],
    },
    "yugioh": {
        "label": "Yu-Gi-Oh!",
        "tcg_line": "yugioh",
        "db": "yugioh_cards.db",
        "images": "images_yugioh",
        "pc_category": "yugioh-cards",
        "pc_csv": "pricecharting_yugioh.csv",
        "pc_min_rows": 40_000,
        "scraper": ["tcg_scraper.py", "--game", "yugioh"],
    },
    "magic": {
        "label": "Magic",
        "tcg_line": "magic",
        "db": "magic_cards.db",
        "images": "images_magic",
        "pc_category": "magic-cards",
        "pc_csv": "pricecharting_magic.csv",
        "pc_min_rows": 80_000,
        "scraper": ["tcg_scraper.py", "--game", "magic"],
    },
    "lorcana": {
        "label": "Lorcana",
        "tcg_line": "disney-lorcana",
        "db": "lorcana_cards.db",
        "images": "images_lorcana",
        "pc_category": "lorcana-cards",
        "pc_csv": "pricecharting_lorcana.csv",
        "pc_min_rows": 3_000,
        "scraper": ["tcg_scraper.py", "--game", "lorcana"],
    },
    "digimon": {
        "label": "Digimon",
        "tcg_line": "digimon-card-game",
        "db": "digimon_cards.db",
        "images": "images_digimon",
        "pc_category": "digimon-cards",
        "pc_csv": "pricecharting_digimon.csv",
        "pc_min_rows": 4_000,
        "scraper": ["tcg_scraper.py", "--game", "digimon"],
    },
    "gundam": {
        "label": "Gundam",
        "tcg_line": "gundam-card-game",
        "db": "gundam_cards.db",
        "images": "images_gundam",
        # PriceCharting covers gundam only as per-set "console" pages (no bulk
        # category slug) — scrape_gundam_prices.py crawls + matches them into
        # this CSV, which build_pricecharting.py then consumes like the rest.
        "pc_category": None,
        "pc_csv": "pricecharting_gundam.csv",
        "pc_min_rows": 500,
        "scraper": ["tcg_scraper.py", "--game", "gundam"],
    },
    "starwars": {
        "label": "Star Wars Unlimited",
        "tcg_line": "star-wars-unlimited",
        "db": "starwars_cards.db",
        "images": "images_starwars",
        # No bulk PriceCharting category exists for this game (every
        # candidate slug silently falls back to their full video-game
        # database instead of 404ing) — like gundam, PC covers it as per-set
        # "console" pages (star-wars-unlimited-<set>), which
        # scrape_starwars_prices.py crawls + matches into this CSV for
        # build_pricecharting.py to consume like the rest.
        "pc_category": None,
        "pc_csv": "pricecharting_starwars.csv",
        "pc_min_rows": 500,
        "scraper": ["tcg_scraper.py", "--game", "starwars"],
    },
}

ALL_GAMES = list(GAMES)


def priced_games():
    """Games with a PriceCharting price source — the only ones the
    unify/nm-price/ML steps can do anything with. Today that's all of them
    (every entry has a pc_csv path; gundam/starwars fill theirs from the
    console-page scraper rather than a bulk category), so the filter only
    bites if a future game is added with pc_csv=None to mark it unpriced."""
    return [g for g, cfg in GAMES.items() if cfg["pc_csv"]]


def db_path(game):
    return os.path.join(BASE, GAMES[game]["db"])


def image_dir(game):
    return os.path.join(BASE, GAMES[game]["images"])
