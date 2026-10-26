#!/usr/bin/env python3
"""
Periodic confirmation of card art for cards we photographed before release.

TCGplayer lists a set weeks before it ships, and the product image it serves
during pre-sale is often not the final one: a placeholder, a preliminary or
foreign-language scan, or a different crop that gets swapped on or after the
release date. Our scrapers download a card's image once (the night it first
appears, which for a new set is pre-market) and never look again, so every
card first seen in pre-sale could be carrying stale art forever
(reported 2026-10-26).

This step re-fetches the TCGplayer image for cards whose art may not be
final yet and replaces ours when the bytes changed:

  candidate   a card with art whose image was uploaded to S3 before the card
              had been out for FINAL_DAYS (unreleased, just released, or an
              old pre-sale fetch nobody re-checked). With no release date on
              file (One Piece stores it only inside raw_json; a few cards have
              none at all) a card is a candidate while its image is under
              NO_DATE_DAYS old.
  unchanged   same bytes as S3 (the S3 ETag is the md5 for our single-part
              uploads, so no download from S3 is needed). Once that happens
              on a check made FINAL_DAYS after release the card is marked
              final and never looked at again.
  changed     the fresh image is written locally, copied to S3 under the same
              key with the same cache headers, the CloudFront path is
              invalidated (the CDN caches art for a year), and the card's
              CLIP embedding is dropped from ml_data so the same night's
              ml-embed step re-embeds the new art (the scanner index and
              art-comps follow from that).
  no image    403/404 or the "Image Coming Soon" placeholder: ours stays.

Runs nightly on a rotation like audit_imageless.py (--limit per game,
--refresh-days between checks of the same card), cards closest to their
release date first. Always exits 0: a hiccup here must never fail the
refresh. --dry-run reports without writing anything (DB, disk, S3, CDN).

Known limit: a visitor whose browser cached the OLD image keeps it until
that cache entry expires (we serve art as immutable, max-age=1y); the URL
does not change. The CDN invalidation fixes it for everyone else.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import IMAGES_BUCKET, db_path, image_dir, market_games
import tcg_pokemon_scraper as tp

AWS = shutil.which("aws") or "/usr/local/bin/aws"
DISTRIBUTION = "ELS4KW7LFHWTT"      # d18pzythfo0bgy.cloudfront.net -> the images bucket
REGION = "us-east-1"
FINAL_DAYS = 14      # art unchanged this long after release = final
NO_DATE_DAYS = 45    # no release date on file: watch an image this long after upload
CACHE_CONTROL = "public, max-age=31536000, immutable"   # == s3_upload_images.py
ML_DATA = os.path.join(BASE, "ml_data")


def aws(*args):
    """Run the AWS CLI; returns (returncode, stdout). Never raises."""
    try:
        res = subprocess.run([AWS, *args, "--region", REGION], capture_output=True, text=True)
        return res.returncode, res.stdout
    except OSError as e:
        return 1, str(e)


def bucket_objects(game):
    """{product_id: (md5, uploaded_at date)} for every {game}/{id}.jpg in S3.

    One paginated listing per game (the CLI pages for us). Our uploads are
    single-part (verified 2026-10-26: zero multipart ETags), so the ETag is
    the plain md5 of the bytes. None on failure: the game is skipped rather
    than judged from a partial list.
    """
    code, out = aws("s3api", "list-objects-v2", "--bucket", IMAGES_BUCKET,
                    "--prefix", f"{game}/", "--output", "json",
                    "--query", "Contents[].[Key,ETag,LastModified,Size]")
    if code != 0:
        return None
    objs = {}
    try:
        for key, etag, mod, size in (json.loads(out) or []):
            name = key.rsplit("/", 1)[-1]
            if not name.endswith(".jpg") or not size:
                continue
            try:
                pid = int(name[:-4])
            except ValueError:
                continue
            etag = etag.strip('"')
            if "-" in etag:
                etag = ""            # multipart: unknown md5, compare by fetching
            objs[pid] = (etag, mod[:10])
    except (ValueError, TypeError):
        return None
    return objs


def release_dates(conn):
    """{product_id: 'YYYY-MM-DD' or None} for every card with art."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
    if "release_date" in cols:
        sql = "SELECT product_id, substr(release_date,1,10) FROM cards WHERE image_path IS NOT NULL"
    elif "raw_json" in cols:
        sql = ("SELECT product_id, substr(json_extract(raw_json,'$.releaseDate'),1,10) "
               "FROM cards WHERE image_path IS NOT NULL")
    else:
        sql = "SELECT product_id, NULL FROM cards WHERE image_path IS NOT NULL"
    out = {}
    for pid, rel in conn.execute(sql):
        out[pid] = rel if rel and len(rel) == 10 and rel[4] == "-" else None
    return out


def fetch_fresh(session, pid, delay):
    """Fresh TCGplayer bytes, or None (no CDN image / placeholder / error)."""
    try:
        resp = tp.request_with_retries(session, "GET", tp.IMAGE_URL.format(pid=pid),
                                       delay, retry_forbidden=False)
        if resp is None or resp.status_code != 200 or not resp.content:
            return None
        if hashlib.md5(resp.content).hexdigest() == tp.PLACEHOLDER_MD5:
            return None
        return resp.content
    except Exception:
        return None


def s3_md5(game, pid):
    """md5 of the object in S3 — only for the (never seen) multipart case."""
    try:
        res = subprocess.run([AWS, "s3", "cp", f"s3://{IMAGES_BUCKET}/{game}/{pid}.jpg", "-",
                              "--region", REGION], capture_output=True)
        return hashlib.md5(res.stdout).hexdigest() if res.returncode == 0 else None
    except OSError:
        return None


def drop_from_npz(game, pids):
    """Remove product ids from ml_data/{game}_img_emb.npz so embed_images.py
    (resumable: it never re-embeds an id it already has) embeds the new art."""
    path = os.path.join(ML_DATA, f"{game}_img_emb.npz")
    if not pids or not os.path.exists(path):
        return 0
    try:
        import numpy as np
        z = np.load(path)
        ids, emb = z["product_id"], z["emb"]
        keep = ~np.isin(ids, np.array(sorted(pids), dtype=ids.dtype))
        dropped = int((~keep).sum())
        if dropped:
            np.savez(path, product_id=ids[keep], emb=emb[keep])
        return dropped
    except Exception as e:                      # noqa: BLE001
        print(f"  [{game}] npz drop failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 0


def recheck_game(game, session, args, today, invalidate):
    objs = bucket_objects(game)
    if objs is None:
        print(f"[{game}] could not list the bucket — skipped")
        return
    conn = sqlite3.connect(db_path(game), timeout=60)
    conn.row_factory = None
    if not args.dry_run:
        conn.execute("CREATE TABLE IF NOT EXISTS art_recheck ("
                     "product_id INTEGER PRIMARY KEY, checked_at TEXT, etag TEXT, "
                     "replaced INTEGER DEFAULT 0, finalized_at TEXT)")
    state = {}
    try:
        for pid, checked, replaced, final in conn.execute(
                "SELECT product_id, checked_at, replaced, finalized_at FROM art_recheck"):
            state[pid] = (checked, replaced or 0, final)
    except sqlite3.OperationalError:
        pass                                     # dry run on a DB without the table

    rels = release_dates(conn)
    cutoff_checked = (datetime.now(timezone.utc) - timedelta(days=args.refresh_days)).isoformat()
    cands = []
    for pid, rel in rels.items():
        obj = objs.get(pid)
        if not obj:
            continue
        checked, _replaced, final = state.get(pid, (None, 0, None))
        if final or (checked and checked >= cutoff_checked):
            continue
        uploaded = date.fromisoformat(obj[1])
        if rel:
            rd = date.fromisoformat(rel)
            if uploaded >= rd + timedelta(days=FINAL_DAYS):
                continue                         # fetched after the art had settled
            prio = abs((rd - today).days)        # closest to release first
        else:
            if uploaded < today - timedelta(days=NO_DATE_DAYS):
                continue
            prio = 10_000 + (today - uploaded).days
        cands.append((prio, checked is not None, checked or "", pid, rel, obj))
    cands.sort()
    pool = len(cands)
    if args.limit:
        cands = cands[:args.limit]
    if not cands:
        conn.close()
        print(f"[{game}] no pre-release art due for a check")
        return

    now = datetime.now(timezone.utc).isoformat()
    changed = same = noimg = finalized = 0
    replaced_pids = []
    img_dir = image_dir(game)
    for _p, _c, _k, pid, rel, (etag, _up) in cands:
        fresh = fetch_fresh(session, pid, args.delay)
        if fresh is None:
            noimg += 1
            outcome = "noimg"
        else:
            md5 = hashlib.md5(fresh).hexdigest()
            ours = etag or s3_md5(game, pid)
            if ours == md5:
                same += 1
                outcome = "same"
            else:
                changed += 1
                outcome = "changed"
                if args.verbose or args.dry_run:
                    print(f"  [{game}] {pid} rel={rel or '?'}: art changed "
                          f"({len(fresh):,} B, {ours[:8] if ours else '?'} -> {md5[:8]})"
                          + (" [dry run]" if args.dry_run else ""))
                if not args.dry_run:
                    os.makedirs(img_dir, exist_ok=True)
                    local = os.path.join(img_dir, f"{pid}.jpg")
                    tmp = None
                    try:
                        fd, tmp = tempfile.mkstemp(suffix=".jpg", dir=img_dir)
                        with os.fdopen(fd, "wb") as f:
                            f.write(fresh)
                        code, _ = aws("s3", "cp", tmp, f"s3://{IMAGES_BUCKET}/{game}/{pid}.jpg",
                                      "--content-type", "image/jpeg",
                                      "--cache-control", CACHE_CONTROL, "--only-show-errors")
                        if code != 0:
                            print(f"  [{game}] {pid}: S3 upload failed — kept the old art",
                                  file=sys.stderr)
                            changed -= 1
                            outcome = "upload-failed"
                        else:
                            os.replace(tmp, local)
                            tmp = None
                            replaced_pids.append(pid)
                            invalidate.append(f"/{game}/{pid}.jpg")
                            etag = md5
                    finally:
                        if tmp and os.path.exists(tmp):
                            os.remove(tmp)
        # Final once unchanged on a check made FINAL_DAYS after release (or,
        # with no date, once the image has aged NO_DATE_DAYS): nothing left
        # for TCGplayer to swap. A card whose art just changed gets at least
        # one more look.
        final_at = None
        if outcome == "same":
            if rel and today >= date.fromisoformat(rel) + timedelta(days=FINAL_DAYS):
                final_at = now
            elif not rel and date.fromisoformat(_up) <= today - timedelta(days=NO_DATE_DAYS):
                final_at = now
        if final_at:
            finalized += 1
        if not args.dry_run:
            prev = state.get(pid, (None, 0, None))
            conn.execute("INSERT OR REPLACE INTO art_recheck VALUES (?,?,?,?,?)",
                         (pid, now, etag, prev[1] + (1 if outcome == "changed" else 0), final_at))
    if not args.dry_run:
        conn.commit()
    conn.close()
    dropped = 0 if args.dry_run else drop_from_npz(game, set(replaced_pids))
    print(f"[{game}] {len(cands)} of {pool} due checked: {changed} replaced, {same} unchanged, "
          f"{noimg} no image on TCGplayer, {finalized} now final"
          + (f", {dropped} embeddings dropped for re-embed" if dropped else "")
          + (" [dry run]" if args.dry_run else ""))


def main():
    ap = argparse.ArgumentParser(description="Re-check art for cards photographed before release")
    ap.add_argument("--games", help="comma-separated (default: every priced game)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--rpm", type=float, default=60)
    ap.add_argument("--limit", type=int, default=None, help="max cards PER GAME this run")
    ap.add_argument("--refresh-days", type=float, default=3,
                    help="leave a card alone this long after its last check")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    tp.RATE_LIMITER = tp.RateLimiter(rpm=args.rpm, min_interval=args.delay)
    session = tp.make_session()
    today = datetime.now(timezone.utc).date()
    invalidate = []
    for game in (args.games.split(",") if args.games else market_games()):
        recheck_game(game, session, args, today, invalidate)

    if invalidate and not args.dry_run:
        batch = json.dumps({"Paths": {"Quantity": len(invalidate), "Items": invalidate},
                            "CallerReference": f"art-recheck-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"})
        code, _ = aws("cloudfront", "create-invalidation", "--distribution-id", DISTRIBUTION,
                      "--invalidation-batch", batch)
        print(f"CloudFront invalidation for {len(invalidate)} path(s): "
              f"{'requested' if code == 0 else 'FAILED (old art stays cached up to a year)'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # noqa: BLE001
        print(f"recheck_prerelease_art: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
