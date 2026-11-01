#!/usr/bin/env python3
"""
CLIP text embeddings for every card's name and rules text (2026-11-01, user
request: the model should consider card name and card text).

The art side already runs open_clip ViT-B-32 (embed_images.py); the same
model's text tower embeds "name. text" so no new dependency is added and the
two blocks live in one vector space. Output mirrors the art file so the
model can join it the same way:

  ml_data/<game>_txt_emb.npz  ->  product_id (int64), emb (float32 N x 512)

Every card gets a vector: the name is always present, and the rules text
joins it where the catalog has one (Pokémon's source omits text for about
half its cards; see the 2026-11-01 audit). HTML tags in the text are
stripped first. Resumable: cards already embedded are skipped, so the
nightly run only touches new cards. CLIP truncates at 77 tokens, which
covers a median card and clips the longest Yu-Gi-Oh! paragraphs.

Run:  .venv/bin/python embed_text.py             # every game with an export
      .venv/bin/python embed_text.py --game magic --limit 500
"""

import argparse
import html
import os
import re
import sys

import numpy as np
import pandas as pd
import torch
import open_clip

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES

DATA = os.path.join(BASE, "ml_data")
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
BATCH = 256
TAGS = re.compile(r"<[^>]+>")


def clean(text):
    t = html.unescape(TAGS.sub(" ", str(text or "")))
    return re.sub(r"\s+", " ", t).strip()


def get_device():
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_existing(path):
    if not os.path.exists(path):
        return {}
    z = np.load(path)
    return {int(p): e for p, e in zip(z["product_id"], z["emb"])}


def embed_game(game, model, tokenizer, device, limit=None, rebuild=False):
    csv_path = os.path.join(DATA, f"{game}_cards.csv")
    out_path = os.path.join(DATA, f"{game}_txt_emb.npz")
    if not os.path.exists(csv_path):
        print(f"[{game}] no export CSV yet — skipped")
        return
    df = pd.read_csv(csv_path, engine="python",
                     usecols=lambda c: c in ("product_id", "name", "description"))
    df = df.dropna(subset=["product_id"])
    df["product_id"] = df["product_id"].astype(int)
    if limit:
        df = df.head(limit)
    done = {} if rebuild else load_existing(out_path)
    todo = df[~df["product_id"].isin(done.keys())]
    with_text = int((todo["description"].fillna("").astype(str).str.len() > 0).sum())
    print(f"[{game}] {len(df):,} cards | {len(done):,} done | {len(todo):,} to embed "
          f"({with_text:,} with rules text)", flush=True)
    results = dict(done)
    ids, texts = [], []

    def flush():
        if not texts:
            return
        with torch.no_grad():
            feats = model.encode_text(tokenizer(texts).to(device))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        for pid, vec in zip(ids, feats.cpu().numpy().astype("float32")):
            results[pid] = vec
        ids.clear(); texts.clear()

    n = 0
    for pid, name, desc in zip(todo["product_id"], todo["name"], todo["description"]):
        name, desc = clean(name), clean(desc)
        if not name and not desc:
            continue
        ids.append(int(pid))
        texts.append(f"{name}. {desc}" if desc else name)
        if len(texts) >= BATCH:
            flush()
            n += BATCH
            if n % (BATCH * 40) == 0:
                print(f"   {n:,} embedded...", flush=True)
    flush()
    if not results:
        print(f"[{game}] nothing to embed")
        return
    keys = np.array(sorted(results), dtype="int64")
    emb = np.stack([results[k] for k in keys]).astype("float32")
    np.savez(out_path, product_id=keys, emb=emb)
    print(f"[{game}] saved {emb.shape[0]:,} text embeddings -> {out_path}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Embed card name + text with CLIP")
    ap.add_argument("--game", action="append")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rebuild", action="store_true", help="re-embed every card")
    args = ap.parse_args()
    device = get_device()
    model, _, _ = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    print(f"device: {device}")
    for game in (args.game or list(GAMES)):
        embed_game(game, model, tokenizer, device, args.limit, args.rebuild)


if __name__ == "__main__":
    main()
