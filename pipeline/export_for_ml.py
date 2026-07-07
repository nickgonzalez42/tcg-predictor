"""
Phase 0 export: dump each game's `cards` table to a per-game CSV for ML.

Design choices:
- One CSV per game (One Piece and Pokemon stay separate -> one model each).
- Every column is kept EXCEPT the redundant raw dumps (`raw_json`,
  `custom_attributes`); all genuine features are already promoted to columns.
- All rows are kept, including cards with no market price -- those are
  prediction targets later. Training should filter to priced rows.
- Two extra columns are added for the appearance model:
    image_file        absolute path to the local card image (if present)
    has_local_image   1/0
- The image is located by product_id (images/<id>.jpg), which is more
  complete than the sparse `image_path` column.

NOTE on target leakage: `lowest_price`, `lowest_price_ship`, and
`total_listings` are price-derived and must be dropped as FEATURES at train
time (the target is `market_price`). They are exported for reference only.
"""

import csv
import os
import sqlite3

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
OUT_DIR = os.path.join(BASE, "ml_data")

EXCLUDE = {"raw_json", "custom_attributes"}

GAMES = [
    {"name": "onepiece", "db": "onepiece_cards.db", "images": "images"},
    {"name": "pokemon", "db": "pokemon_cards.db", "images": "images_pokemon"},
]


def export(game: dict) -> None:
    db_path = os.path.join(BASE, game["db"])
    img_dir = os.path.join(BASE, game["images"])
    out_path = os.path.join(OUT_DIR, f"{game['name']}_cards.csv")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)") if r[1] not in EXCLUDE]
    out_cols = cols + ["image_file", "has_local_image"]

    total = priced = with_image = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(out_cols)
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM cards"):
            d = dict(row)
            img = os.path.join(img_dir, f"{d['product_id']}.jpg")
            has_image = os.path.exists(img)
            writer.writerow([d[c] for c in cols] + [img if has_image else "", int(has_image)])

            total += 1
            priced += d["market_price"] is not None
            with_image += has_image

    conn.close()
    print(f"{game['name']:9s} -> {out_path}")
    print(f"            {total} rows | {len(out_cols)} cols | "
          f"priced: {priced} ({total - priced} null) | "
          f"local image: {with_image} ({total - with_image} missing)")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for game in GAMES:
        export(game)


if __name__ == "__main__":
    main()
