"""
Sync each card's `image_path` column with the art that is actually in S3.

image_path doubles as the "has art" flag: the API only serves cards whose art
has landed (pending-art cards stay in the database but appear nowhere on the
site). S3 is the canonical image store — local dirs only stage new scrapes —
so the flag is set from a bucket listing, meaning "art is where browsers
fetch it". Run after s3_upload_images.py so today's new art is counted.

Run:  .venv/bin/python sync_local_images.py
"""

import os
import shutil
import sqlite3
import subprocess

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
from games import GAMES, IMAGES_BUCKET

AWS = shutil.which("aws") or "/usr/local/bin/aws"


def bucket_ids(game):
    """Product ids with a non-empty {game}/{id}.jpg object in the bucket."""
    out = subprocess.run(
        [AWS, "s3", "ls", f"s3://{IMAGES_BUCKET}/{game}/", "--recursive"],
        capture_output=True, text=True, check=True).stdout
    ids = set()
    for line in out.splitlines():
        # "2026-07-16 21:04:11     48213 pokemon/106999.jpg"
        parts = line.split()
        if len(parts) != 4:
            continue
        size, key = parts[2], parts[3]
        name = key.rsplit("/", 1)[-1]
        if key.endswith(".jpg") and name[:-4].isdigit() and int(size) > 0:
            ids.add(int(name[:-4]))
    return ids


def sync(game, image_dir):
    in_bucket = bucket_ids(game)

    # Generous busy timeout: a catalog scrape may be writing this DB in parallel.
    con = sqlite3.connect(os.path.join(BASE, f"{game}_cards.db"), timeout=60)
    ids = [r[0] for r in con.execute("SELECT product_id FROM cards")]
    with_art = [(os.path.join(image_dir, f"{pid}.jpg"), pid) for pid in ids if pid in in_bucket]
    without = [(pid,) for pid in ids if pid not in in_bucket]
    con.executemany("UPDATE cards SET image_path=? WHERE product_id=?", with_art)
    con.executemany("UPDATE cards SET image_path=NULL WHERE product_id=?", without)
    con.commit()
    con.close()
    print(f"[{game}] {len(with_art)} cards with art, {len(without)} pending (hidden from the site)")


if __name__ == "__main__":
    for g, cfg in GAMES.items():
        sync(g, cfg["images"])
