"""
Phase 2a: compute CLIP image embeddings for each card's art.

Reads the per-game export CSV (for product_id + image_file), runs the local
card images through CLIP, and saves L2-normalized embeddings keyed by
product_id. These get joined to the tabular features by product_id later.

- Model: open_clip ViT-B-32 (512-d). Runs on MPS (Apple Silicon) if available.
- Only cards with a local image (has_local_image == 1) are embedded.
- Resumable: if the output file exists, already-embedded product_ids are skipped.

Output: ml_data/<game>_img_emb.npz  ->  arrays `product_id` (int64), `emb` (float32 N x 512)

Run:  .venv/bin/python embed_images.py            # both games
      .venv/bin/python embed_images.py --limit 200  # quick smoke test
"""

import argparse
import os

import numpy as np
import pandas as pd
import torch
import open_clip
from PIL import Image

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir
DATA = os.path.join(BASE, "ml_data")

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
BATCH = 64


def get_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_existing(out_path: str):
    if not os.path.exists(out_path):
        return {}
    z = np.load(out_path)
    return {int(pid): emb for pid, emb in zip(z["product_id"], z["emb"])}


def embed_game(game: str, model, preprocess, device: str, limit: int | None):
    csv_path = os.path.join(DATA, f"{game}_cards.csv")
    out_path = os.path.join(DATA, f"{game}_img_emb.npz")

    df = pd.read_csv(csv_path, usecols=["product_id", "image_file", "has_local_image"])
    df = df[df["has_local_image"] == 1].dropna(subset=["image_file"])
    if limit:
        df = df.head(limit)

    done = load_existing(out_path)
    todo = df[~df["product_id"].isin(done.keys())]
    print(f"\n[{game}] {len(df)} with images | {len(done)} already done | {len(todo)} to embed")

    results = dict(done)
    batch_imgs, batch_ids = [], []
    processed = skipped = 0

    def flush():
        nonlocal batch_imgs, batch_ids
        if not batch_imgs:
            return
        with torch.no_grad():
            tensor = torch.stack(batch_imgs).to(device)
            feats = model.encode_image(tensor)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        feats = feats.cpu().numpy().astype("float32")
        for pid, vec in zip(batch_ids, feats):
            results[int(pid)] = vec
        batch_imgs, batch_ids = [], []

    for pid, path in zip(todo["product_id"], todo["image_file"]):
        try:
            img = Image.open(path).convert("RGB")
        except Exception:
            skipped += 1
            continue
        batch_imgs.append(preprocess(img))
        batch_ids.append(pid)
        if len(batch_imgs) >= BATCH:
            flush()
            processed += BATCH
            if processed % (BATCH * 20) == 0:
                print(f"   {processed} embedded...")
    flush()

    ids = np.array(sorted(results.keys()), dtype="int64")
    emb = np.stack([results[i] for i in ids]).astype("float32")
    np.savez(out_path, product_id=ids, emb=emb)
    print(f"[{game}] saved {emb.shape[0]} embeddings (dim {emb.shape[1]}) -> {out_path}"
          f" | unreadable skipped: {skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only embed first N images per game (smoke test)")
    ap.add_argument("--games", nargs="+", default=["onepiece", "pokemon"])
    args = ap.parse_args()

    device = get_device()
    print(f"device: {device} | model: {MODEL_NAME}/{PRETRAINED}")
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    model = model.to(device).eval()

    for game in args.games:
        embed_game(game, model, preprocess, device, args.limit)


if __name__ == "__main__":
    main()
