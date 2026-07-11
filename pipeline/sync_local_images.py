"""
Sync each card's `image_path` column with what is actually on disk.

image_path doubles as the "has art" flag: the API only serves cards whose art
has landed (pending-art cards stay in the database but appear nowhere on the
site). Run after the TCGplayer scrapes so newly downloaded images are counted.

Run:  .venv/bin/python sync_local_images.py
"""

import os
import sqlite3

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
from games import GAMES


def sync(game, image_dir):
    directory = os.path.join(BASE, image_dir)
    os.makedirs(directory, exist_ok=True)   # a brand-new game has no images yet
    on_disk = {
        int(name[:-4])
        for name in os.listdir(directory)
        if name.endswith(".jpg") and name[:-4].isdigit()
           and os.path.getsize(os.path.join(directory, name)) > 0
    }

    # Generous busy timeout: a catalog scrape may be writing this DB in parallel.
    con = sqlite3.connect(os.path.join(BASE, f"{game}_cards.db"), timeout=60)
    ids = [r[0] for r in con.execute("SELECT product_id FROM cards")]
    with_art = [(os.path.join(image_dir, f"{pid}.jpg"), pid) for pid in ids if pid in on_disk]
    without = [(pid,) for pid in ids if pid not in on_disk]
    con.executemany("UPDATE cards SET image_path=? WHERE product_id=?", with_art)
    con.executemany("UPDATE cards SET image_path=NULL WHERE product_id=?", without)
    con.commit()
    con.close()
    print(f"[{game}] {len(with_art)} cards with art, {len(without)} pending (hidden from the site)")


if __name__ == "__main__":
    for g, cfg in GAMES.items():
        sync(g, cfg["images"])
