#!/usr/bin/env python3
"""
One-off: find and remove TCGplayer "Image Coming Soon" pictures that runs
before 2026-10-23 saved in the bucket as card art.

S3 is the canonical image store and sync_local_images.py sets image_path
purely from what the bucket holds, so deleting the object IS the fix: the
next art-sync hides the card and the nightly imageless audit re-checks it
(placeholder_art.py keeps the picture out of every download since). Two more
steps matter. CloudFront caches art as immutable for a year, so each path is
invalidated, or a real image uploaded later under the same key would never
show. embed_images.py is resumable and never drops an id, so the picture's
CLIP row is removed from the game's npz here (art comps and the scanner
index are rebuilt from those files every night).

How objects are found, under every game prefix in the registry:
  etag   the listing's ETag is the md5 of a known encoding (our uploads are
         single-part, so ETag == md5; the 2026-11-13 census found no
         multipart object)
  size   the size of a known encoding with another ETag
  clip   the card's CLIP embedding sits next to a known placeholder's (finds
         an encoding nobody has seen yet; real art scores 0.47 at most)
Every candidate is downloaded and judged by its bytes
(placeholder_art.is_placeholder). Only verified objects are touched.

Dry run by default: it prints the verified list and everything --apply would
do, and writes nothing but the downloads (into --work-dir, or a temp dir it
removes again).
  .venv/bin/python purge_placeholder_art.py
  .venv/bin/python purge_placeholder_art.py --apply
--apply, for the verified objects only: one s3api delete-object per key
(exact keys, never a recursive delete or a sync --delete; the bucket is
versioned, so each delete leaves a marker and the old version restorable),
one CloudFront invalidation of their paths, the local staging copy when it
is the placeholder, the CLIP rows, then art-sync for each game touched. It
refuses to start while the nightly refresh runs (ml-embed rewrites the same
npz files and art-sync the same DBs). Pinned to the cardstock account: it
stops before any other aws call when the credentials resolve elsewhere.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, IMAGES_BUCKET, db_path, image_dir
from placeholder_art import PLACEHOLDER_MD5S, PLACEHOLDER_SIZES, has_placeholder_shape, \
    is_placeholder_file
from sync_local_images import sync as art_sync

AWS = shutil.which("aws") or "/usr/local/bin/aws"
CARDSTOCK_ACCT = "522029196375"
REGION = "us-east-1"
DISTRIBUTION = "ELS4KW7LFHWTT"      # d18pzythfo0bgy.cloudfront.net -> the images bucket
ML_DATA = os.path.join(BASE, "ml_data")
CLIP_MIN = 0.90       # cosine to a known placeholder's row; real art tops out near 0.47
PAGE = 20_000         # listing items per CLI call, so the CLI never holds all of magic
# The nightly's command lines. Matched in a `ps` listing, never handed to
# pgrep: the daytime crawlers pause for any process whose command line
# carries one of these names.
REFRESH_MARKERS = ("run_daily_refresh.sh", "weekly_refresh.py")


def aws(*args):
    """The AWS CLI on the pinned profile; a CompletedProcess, never raises."""
    try:
        return subprocess.run([AWS, *args], capture_output=True, text=True)
    except OSError as e:
        return subprocess.CompletedProcess([AWS, *args], 1, "", str(e))


def pin_account():
    """Every aws call after this runs on the cardstock account or not at all.
    A leaked AWS_PROFILE must never aim a delete at the client account
    (the same rule as run_daily_refresh.sh)."""
    os.environ["AWS_PROFILE"] = "default"
    os.environ["AWS_DEFAULT_REGION"] = REGION
    res = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text")
    acct = res.stdout.strip() if res.returncode == 0 else ""
    if acct != CARDSTOCK_ACCT:
        sys.exit(f"ABORT: aws resolves to account '{acct or 'none'}', "
                 f"not cardstock ({CARDSTOCK_ACCT}). Nothing was read or changed.")


def refresh_running():
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    return any(m in line for line in out.splitlines() for m in REFRESH_MARKERS)


def pid_of(key):
    name = key.rsplit("/", 1)[-1]
    return int(name[:-4]) if name.endswith(".jpg") and name[:-4].isdigit() else None


def list_candidates(game):
    """{pid: {key, etag, size, modified, found}} for objects under {game}/
    whose ETag or size is a known encoding's."""
    found, token = {}, None
    while True:
        cmd = ["s3api", "list-objects-v2", "--bucket", IMAGES_BUCKET, "--prefix", f"{game}/",
               "--page-size", "1000", "--max-items", str(PAGE), "--output", "json",
               "--query", "{Items: Contents[].[Key,ETag,Size,LastModified], NextToken: NextToken}"]
        if token:
            cmd += ["--starting-token", token]
        res = aws(*cmd)
        if res.returncode != 0:
            sys.exit(f"[{game}] bucket listing failed: {res.stderr.strip()[:200]}")
        page = json.loads(res.stdout or "{}")
        for key, etag, size, modified in page.get("Items") or []:
            etag = etag.strip('"')
            if "-" in etag:
                etag = ""                    # multipart: not an md5, judged by size
            if etag in PLACEHOLDER_MD5S:
                how = "etag"
            elif size in PLACEHOLDER_SIZES:
                how = "size"
            else:
                continue
            pid = pid_of(key)
            if pid is not None:
                found[pid] = {"key": key, "etag": etag, "size": size,
                              "modified": modified[:10], "found": [how]}
        token = page.get("NextToken")
        if not token:
            return found


def npz_path(game):
    return os.path.join(ML_DATA, f"{game}_img_emb.npz")


def npz_chunks(path, rows=8192):
    """(product_ids, embeddings) chunks of an embed_images.py npz, streamed
    out of the zip: the magic file is ~240 MB and this Mac has 8 GB."""
    with zipfile.ZipFile(path) as zf:
        with zf.open("product_id.npy") as f:
            ids = np.lib.format.read_array(f)
        with zf.open("emb.npy") as f:
            version = np.lib.format.read_magic(f)
            read_header = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                           else np.lib.format.read_array_header_2_0)
            shape, fortran, dtype = read_header(f)
            if fortran or len(shape) != 2 or shape[0] != len(ids):
                raise ValueError(f"{path}: unexpected emb layout {shape}")
            for start in range(0, shape[0], rows):
                n = min(rows, shape[0] - start)
                body = f.read(n * shape[1] * dtype.itemsize)
                yield ids[start:start + n], np.frombuffer(body, dtype=dtype).reshape(n, shape[1])


def clip_lookalikes(games, known):
    """({game: {pid: cosine}} for rows next to a known placeholder's row,
    {game: ids in the npz}). `known` is {game: pids proven by ETag}: their
    rows are the reference, so once a purge has removed them this channel
    finds nothing and the ETag and size channels carry on alone."""
    refs, present = [], {}
    for g in games:
        path = npz_path(g)
        if not os.path.exists(path):
            continue
        mine = np.array(sorted(known.get(g, ())), dtype=np.int64)
        present[g] = set()
        for ids, emb in npz_chunks(path):
            present[g].update(ids.tolist())
            refs.extend(np.asarray(emb[np.isin(ids, mine)], dtype=np.float32))
    if not refs:
        return {}, present
    ref = np.stack(refs)
    ref /= np.linalg.norm(ref, axis=1, keepdims=True)
    hits = {}
    for g in games:
        if g not in present:
            continue
        for ids, emb in npz_chunks(npz_path(g)):
            emb = emb.astype(np.float32)
            emb /= np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
            best = (emb @ ref.T).max(axis=1)
            for j in np.nonzero(best >= CLIP_MIN)[0]:
                hits.setdefault(g, {})[int(ids[j])] = round(float(best[j]), 4)
    return hits, present


def head(key):
    res = aws("s3api", "head-object", "--bucket", IMAGES_BUCKET, "--key", key,
              "--query", "[ETag,ContentLength,LastModified]", "--output", "json")
    if res.returncode != 0:
        return None
    etag, size, modified = json.loads(res.stdout)
    return etag.strip('"'), size, modified[:10]


def fetch(key, work_dir):
    """The object's bytes, downloaded into work_dir (read-only on S3)."""
    dest = os.path.join(work_dir, key.replace("/", "_"))
    res = aws("s3api", "get-object", "--bucket", IMAGES_BUCKET, "--key", key, dest,
              "--output", "json")
    if res.returncode != 0 or not os.path.exists(dest):
        return None
    with open(dest, "rb") as f:
        return f.read()


def describe(body):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(body)) as im:
            return f"{im.mode} {im.size[0]}x{im.size[1]}"
    except Exception:                               # noqa: BLE001
        return "not an image"


def verdict(body):
    """How the bytes prove the placeholder, or None when they do not."""
    md5 = hashlib.md5(body).hexdigest()
    if md5 in PLACEHOLDER_MD5S:
        return f"bytes: md5 {md5[:8]} (a known encoding)"
    if has_placeholder_shape(body):
        return f"bytes: placeholder shape ({describe(body)}), md5 {md5[:8]} not seen before"
    return None


def card_meta(game, pids):
    """{pid: (name, set, price, shown)}: shown = what the API serves today
    (art on file AND a price, CardExtensions.VisibleInCatalog)."""
    if not pids or not os.path.exists(db_path(game)):
        return {}
    con = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            "SELECT product_id, name, set_name, near_mint_price, image_path FROM cards "
            f"WHERE product_id IN ({','.join(str(int(p)) for p in pids)})").fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2], r[3], bool(r[4]) and r[3] is not None) for r in rows}


def build_plan(games, work_dir):
    cands = {g: list_candidates(g) for g in games}
    known = {g: {p for p, c in cs.items() if "etag" in c["found"]} for g, cs in cands.items()}
    clip, in_npz = clip_lookalikes(games, known)
    for g, hits in clip.items():
        for pid, cos in hits.items():
            c = cands[g].get(pid)
            if c is not None and "etag" in c["found"]:
                continue                     # a reference row: it matches itself
            if c is None:
                key = f"{g}/{pid}.jpg"
                h = head(key)
                c = cands[g][pid] = (
                    {"key": key, "etag": h[0], "size": h[1], "modified": h[2], "found": []} if h
                    else {"key": key, "etag": "", "size": None, "modified": None, "found": [],
                          "missing": True})
            c["found"].append(f"clip {cos}")

    plan, rejected = {}, []
    for g in games:
        if not cands[g]:
            continue
        meta = card_meta(g, list(cands[g]))
        for pid, c in sorted(cands[g].items()):
            if c.get("missing"):
                rejected.append((c["key"], ", ".join(c["found"]),
                                 "no object in the bucket (the npz row is left alone)"))
                continue
            body = fetch(c["key"], work_dir)
            if body is None:
                rejected.append((c["key"], ", ".join(c["found"]), "download failed"))
                continue
            why = verdict(body)
            if why is None:
                rejected.append((c["key"], ", ".join(c["found"]),
                                 f"real art: md5 {hashlib.md5(body).hexdigest()[:8]}, "
                                 f"{describe(body)}"))
                continue
            if c["etag"] and c["etag"] != hashlib.md5(body).hexdigest():
                why += " (ETag differs from the bytes' md5)"
            name, set_name, price, shown = meta.get(pid, ("?", "?", None, False))
            local = os.path.join(image_dir(g), f"{pid}.jpg")
            staged = None
            if os.path.exists(local):
                staged = "remove" if is_placeholder_file(local) else "keep (real art staged)"
            plan.setdefault(g, []).append({
                "pid": pid, "key": c["key"], "path": f"/{c['key']}", "found": c["found"],
                "verified": why, "modified": c["modified"], "size": c["size"],
                "name": name, "set": set_name, "price": price, "shown": shown,
                "staged": staged, "in_npz": pid in in_npz.get(g, ())})
    return plan, rejected


def report(plan, rejected):
    total = sum(len(v) for v in plan.values())
    for g, items in plan.items():
        print(f"\n[{g}] {len(items)} placeholder object(s):")
        for it in items:
            price = f"${it['price']:.2f}" if it["price"] is not None else "no price"
            print(f"  {it['key']:22s} {str(it['name'])[:32]:32s} {str(it['set'])[:28]:28s} "
                  f"{price:>9s}  {'shown' if it['shown'] else 'hidden':6s} "
                  f"found by {'+'.join(it['found'])}; {it['verified']}; "
                  f"uploaded {it['modified']}")
    if rejected:
        print(f"\nnot the placeholder ({len(rejected)}), left alone:")
        for key, how, why in rejected:
            print(f"  {key:22s} found by {how}: {why}")
    if not total:
        print("\nno placeholder in the bucket: nothing to do")
        return
    paths = [it["path"] for items in plan.values() for it in items]
    staged = [f"{image_dir(g)}/{it['pid']}.jpg ({it['staged']})"
              for g, items in plan.items() for it in items if it["staged"]]
    print(f"\n--apply would, for these {total} object(s) and nothing else:")
    print(f"  delete {total} S3 object(s) in s3://{IMAGES_BUCKET}, one exact key per call "
          f"(versioned bucket: restorable)")
    print(f"  invalidate {len(paths)} path(s) on CloudFront {DISTRIBUTION}: {' '.join(paths)}")
    print(f"  local staging copies: {', '.join(staged) if staged else 'none on disk'}")
    for g, items in plan.items():
        ids = [it["pid"] for it in items if it["in_npz"]]
        print(f"  {npz_path(g)}: drop {len(ids)} CLIP row(s) {ids}")
    print(f"  art-sync {', '.join(plan)}: image_path -> NULL for these cards, so the site "
          f"hides them after the nightly push; the imageless audit re-checks them first")


def drop_npz_rows(game, pids):
    """Remove pids from the game's npz, written whole to a temp file and
    swapped in, so a crash never leaves half a file."""
    path = npz_path(game)
    if not pids or not os.path.exists(path):
        return 0
    with np.load(path) as z:
        if set(z.files) != {"product_id", "emb"}:
            print(f"  [{game}] {path} holds {z.files}: left alone", file=sys.stderr)
            return 0
        ids, emb = z["product_id"], z["emb"]
    keep = ~np.isin(ids, np.array(sorted(pids), dtype=ids.dtype))
    dropped = int((~keep).sum())
    if dropped:
        tmp = path[:-len(".npz")] + ".purge-tmp.npz"
        np.savez(tmp, product_id=ids[keep], emb=emb[keep])
        os.replace(tmp, path)
    return dropped


def apply(plan):
    """Deletes first, then one invalidation, then the local clean-up: the CDN
    stops serving the picture even if a later step fails."""
    failed, removed = [], {}
    for g, items in plan.items():
        for it in items:
            res = aws("s3api", "delete-object", "--bucket", IMAGES_BUCKET, "--key", it["key"])
            if res.returncode == 0:
                removed.setdefault(g, []).append(it)
            else:
                failed.append(it["key"])
                print(f"  [{g}] delete failed for {it['key']}: {res.stderr.strip()[:160]}",
                      file=sys.stderr)
        print(f"[{g}] deleted {len(removed.get(g, []))} of {len(items)} object(s)")
    paths = [it["path"] for items in removed.values() for it in items]
    if paths:
        batch = json.dumps({"Paths": {"Quantity": len(paths), "Items": paths},
                            "CallerReference": "placeholder-purge-"
                                               f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}"})
        res = aws("cloudfront", "create-invalidation", "--distribution-id", DISTRIBUTION,
                  "--invalidation-batch", batch)
        print(f"CloudFront invalidation for {len(paths)} path(s): "
              f"{'requested' if res.returncode == 0 else 'FAILED: ' + res.stderr.strip()[:160]}")
        if res.returncode != 0:
            failed.append("invalidation")
    for g, items in removed.items():
        try:
            for it in items:
                local = os.path.join(image_dir(g), f"{it['pid']}.jpg")
                if os.path.exists(local) and is_placeholder_file(local):
                    os.remove(local)
            dropped = drop_npz_rows(g, {it["pid"] for it in items})
            print(f"[{g}] {dropped} CLIP row(s) dropped from {npz_path(g)}")
            art_sync(g, GAMES[g]["images"])
        except Exception as e:                      # noqa: BLE001
            failed.append(f"{g} clean-up ({type(e).__name__}: {e})")
    if failed:
        sys.exit(f"{len(failed)} step(s) failed: {', '.join(failed)}")


def main():
    ap = argparse.ArgumentParser(description="Find (and with --apply remove) TCGplayer "
                                             "placeholder pictures stored as card art")
    ap.add_argument("--games", help="comma-separated (default: every game in the registry)")
    ap.add_argument("--apply", action="store_true",
                    help="delete + invalidate + local + npz + art-sync (default: dry run)")
    ap.add_argument("--work-dir", help="keep the downloads and plan.json here "
                                       "(default: a temp dir, removed at the end)")
    args = ap.parse_args()
    games = args.games.split(",") if args.games else list(GAMES)
    unknown = [g for g in games if g not in GAMES]
    if unknown:
        sys.exit(f"unknown game(s): {', '.join(unknown)}")
    if args.apply and refresh_running():
        sys.exit("ABORT: the nightly refresh is running (it rewrites the same npz files "
                 "and card DBs). Run --apply after it finishes.")
    pin_account()

    with tempfile.TemporaryDirectory(prefix="placeholder-art-") as tmp:
        work_dir = args.work_dir or tmp
        os.makedirs(work_dir, exist_ok=True)
        plan, rejected = build_plan(games, work_dir)
        report(plan, rejected)
        if args.work_dir:
            with open(os.path.join(work_dir, "plan.json"), "w") as f:
                json.dump({"plan": plan, "rejected": rejected}, f, indent=1)
    if not plan:
        return
    if not args.apply:
        print("\ndry run: nothing was changed. Re-run with --apply to do the above.")
        return
    apply(plan)


if __name__ == "__main__":
    main()
