#!/usr/bin/env python3
"""
One-off: find and remove TCGplayer "Image Coming Soon" placeholders that an
older run saved as card art (2026-10-22: 22 One Piece starter-deck cards, the
very first ever scraped, plus a handful in Digimon / Yu-Gi-Oh).

S3 is the canonical image store and sync_local_images.py sets image_path
purely from what the bucket holds, so deleting the object IS the fix: the
next art-sync hides the card (exactly like the other ~287 art-less One Piece
cards) and the weekly imageless audit re-checks it. Two extra steps matter:
CloudFront caches images as immutable for a year, so the key is invalidated
(otherwise a real image uploaded later under the same key would never show);
and embed_images.py is resumable and never drops an id, so the placeholder's
CLIP row is removed from the game's npz by hand.

Dry-run by default — prints the verified list and does nothing.
  .venv/bin/python purge_placeholder_art.py            # list
  .venv/bin/python purge_placeholder_art.py --apply    # s3 rm + invalidate + local + npz + art-sync
Needs the aws CLI on the cardstock account (the scripts hard-abort otherwise).
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, IMAGES_BUCKET, db_path, image_dir, market_games
from sync_local_images import sync as art_sync
from tcg_pokemon_scraper import PLACEHOLDER_MD5, PLACEHOLDER_SIZE

AWS = shutil.which("aws") or "/usr/local/bin/aws"
DISTRIBUTION = "ELS4KW7LFHWTT"      # d18pzythfo0bgy.cloudfront.net -> the images bucket
ETAG = f'"{PLACEHOLDER_MD5}"'       # single-part uploads: ETag == md5


def aws(*args, binary=False):
    """text=False for object bodies (a JPEG is not UTF-8)."""
    return subprocess.run([AWS, *args], capture_output=True, text=not binary)


def list_candidates(game):
    """(key, verified) for every object that is, or may be, the placeholder.
    ETag match is proof; a size-only match is verified by hashing the bytes."""
    out, token = [], None
    while True:
        cmd = ["s3api", "list-objects-v2", "--bucket", IMAGES_BUCKET, "--prefix", f"{game}/",
               "--region", "us-east-1", "--max-items", "1000"]
        if token:
            cmd += ["--starting-token", token]
        res = aws(*cmd)
        if res.returncode != 0:
            raise SystemExit(f"[{game}] list failed: {res.stderr.strip()[:200]}")
        page = json.loads(res.stdout or "{}")
        for o in page.get("Contents", []):
            if o["ETag"] == ETAG:
                out.append((o["Key"], True))
            elif o["Size"] == PLACEHOLDER_SIZE:
                body = aws("s3", "cp", f"s3://{IMAGES_BUCKET}/{o['Key']}", "-",
                           "--region", "us-east-1", binary=True)
                out.append((o["Key"], hashlib.md5(body.stdout).hexdigest() == PLACEHOLDER_MD5))
        token = page.get("NextToken")
        if not token:
            break
    return out


def pid_of(key):
    name = key.rsplit("/", 1)[-1]
    return int(name[:-4]) if name.endswith(".jpg") and name[:-4].isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", help="comma-separated (default: every market game)")
    ap.add_argument("--apply", action="store_true", help="delete + invalidate + resync (default: dry run)")
    args = ap.parse_args()
    games = args.games.split(",") if args.games else market_games()

    acct = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text").stdout.strip()
    if acct != "522029196375":
        raise SystemExit(f"wrong AWS account {acct!r} — the images bucket lives in cardstock")

    plan = {}
    for g in games:
        hits = [(k, pid_of(k)) for k, ok in list_candidates(g) if ok]
        hits = [(k, p) for k, p in hits if p is not None]
        if not hits:
            continue
        con = sqlite3.connect(f"file:{db_path(g)}?mode=ro", uri=True, timeout=60)
        meta = {r[0]: r[1:] for r in con.execute(
            f"SELECT product_id, name, set_name, near_mint_price FROM cards "
            f"WHERE product_id IN ({','.join(str(p) for _, p in hits)})")}
        con.close()
        plan[g] = hits
        print(f"[{g}] {len(hits)} placeholder object(s):")
        for k, p in hits:
            n, s, price = meta.get(p, ("?", "?", None))
            print(f"   {p:>8}  {str(n)[:30]:30s} {str(s)[:30]:30s} ${price}")
    total = sum(len(v) for v in plan.values())
    if not total:
        print("nothing to do")
        return
    if not args.apply:
        print(f"\ndry run: {total} object(s) would be removed. Re-run with --apply.")
        return

    for g, hits in plan.items():
        keys = [k for k, _ in hits]
        for k in keys:
            res = aws("s3", "rm", f"s3://{IMAGES_BUCKET}/{k}", "--region", "us-east-1")
            if res.returncode != 0:
                raise SystemExit(f"rm failed for {k}: {res.stderr.strip()[:200]}")
        res = aws("cloudfront", "create-invalidation", "--distribution-id", DISTRIBUTION,
                  "--paths", *[f"/{k}" for k in keys])
        print(f"[{g}] removed {len(keys)} object(s); invalidation "
              f"{'submitted' if res.returncode == 0 else 'FAILED: ' + res.stderr.strip()[:120]}")
        # Local staging copy, if any, must not resurrect it on the next upload.
        for _, p in hits:
            lp = os.path.join(image_dir(g), f"{p}.jpg")
            if os.path.exists(lp):
                os.remove(lp)
        # The placeholder's CLIP row: embed_images.py would never drop it.
        npz = os.path.join(BASE, "ml_data", f"{g}_img_emb.npz")
        if os.path.exists(npz):
            z = np.load(npz)
            keep = ~np.isin(z["product_id"], [p for _, p in hits])
            np.savez(npz, product_id=z["product_id"][keep], emb=z["emb"][keep])
            print(f"[{g}] npz: dropped {int((~keep).sum())} embedding(s), {int(keep.sum())} remain")
        # Flip image_path from the bucket's new state: the cards are hidden
        # until the imageless audit finds real art for them.
        art_sync(g, GAMES[g]["images"])


if __name__ == "__main__":
    main()
