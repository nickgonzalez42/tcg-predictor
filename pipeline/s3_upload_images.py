"""
Upload newly scraped card art to S3 and prune the local staging dirs.

S3 (bucket in games.IMAGES_BUCKET, versioned) is the canonical image store;
the site serves it through CloudFront. Local image dirs only stage new
scrapes: files are CLIP-embedded first (ml-embed runs earlier in the
refresh), uploaded here, and deleted once they are KEEP_DAYS old — by then
they have been in the bucket for a week of successful runs.

The sync never deletes bucket objects (no --delete anywhere), and pruning
only happens when the game's upload succeeded.

Run:  .venv/bin/python s3_upload_images.py            # upload + prune
      .venv/bin/python s3_upload_images.py --no-prune # upload only
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

from games import GAMES, IMAGES_BUCKET, image_dir

AWS = shutil.which("aws") or "/usr/local/bin/aws"
KEEP_DAYS = 7


def upload(game):
    directory = image_dir(game)
    if not os.path.isdir(directory) or not any(
            name.endswith(".jpg") for name in os.listdir(directory)):
        print(f"[{game}] nothing staged")
        return True

    result = subprocess.run(
        [AWS, "s3", "sync", directory, f"s3://{IMAGES_BUCKET}/{game}/",
         "--exclude", "*", "--include", "*.jpg",
         "--cache-control", "public, max-age=31536000, immutable",
         "--only-show-errors"])
    if result.returncode != 0:
        print(f"[{game}] UPLOAD FAILED (exit {result.returncode})", file=sys.stderr)
        return False
    print(f"[{game}] upload ok")
    return True


def prune(game):
    directory = image_dir(game)
    if not os.path.isdir(directory):
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name.endswith(".jpg") and os.path.getmtime(path) < cutoff:
            os.remove(path)
            removed += 1
    if removed:
        print(f"[{game}] pruned {removed} staged files older than {KEEP_DAYS} days")


def main():
    ap = argparse.ArgumentParser(description="Upload card art to S3, prune staging")
    ap.add_argument("--no-prune", action="store_true", help="upload only, keep all local files")
    args = ap.parse_args()

    failed = []
    for game in GAMES:
        if upload(game):
            if not args.no_prune:
                prune(game)
        else:
            failed.append(game)
    if failed:
        sys.exit(f"uploads failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
