#!/usr/bin/env python3
"""Export the image-search artifacts the API serves.

Two files into dotnet/API/Data/clip/ (gitignored, pushed like data):
  clip-visual.onnx   the open_clip ViT-B-32/laion2b IMAGE encoder — the same
                     model embed_images.py uses, so a photo embedded by the
                     API is searchable against the pipeline's card embeddings.
  card-index.bin     every game's L2-normalized embeddings + (game, pid) keys:
                     [int32 n][int32 dim][n × (byte game_idx, int32 pid)]
                     [n × dim float32 rows]. GAME_ORDER below is the byte's
                     meaning — keep it in sync with ImageSearchService.cs.

Rerun after a model change or when embeddings grow (Sunday's new cards);
not part of the nightly.
Run:  .venv/bin/python export_clip_onnx.py
"""
import os
import struct
import sys

import numpy as np
import torch
import open_clip

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

MODEL_NAME, PRETRAINED = "ViT-B-32", "laion2b_s34b_b79k"   # = embed_images.py
GAME_ORDER = ["pokemon", "magic", "yugioh", "onepiece",
              "lorcana", "digimon", "gundam", "starwars"]
OUT_DIR = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "clip")


class Visual(torch.nn.Module):
    """Image encoder + L2 norm, so the ONNX output is directly comparable to
    the (already normalized) card index rows via dot product."""
    def __init__(self, clip):
        super().__init__()
        self.visual = clip.visual

    def forward(self, x):
        f = self.visual(x)
        return f / f.norm(dim=-1, keepdim=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    model, _, _ = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    model.eval()

    onnx_path = os.path.join(OUT_DIR, "clip-visual.onnx")
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(Visual(model), dummy, onnx_path,
                      input_names=["image"], output_names=["embedding"],
                      dynamic_axes={"image": {0: "batch"}, "embedding": {0: "batch"}},
                      opset_version=17)
    print(f"wrote {onnx_path} ({os.path.getsize(onnx_path)/1e6:.0f} MB)")

    # parity: torch vs onnxruntime on the same random tensor
    import onnxruntime as ort
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    with torch.no_grad():
        ref = Visual(model)(dummy).numpy()
    got = sess.run(None, {"image": dummy.numpy()})[0]
    cos = float((ref * got).sum() / (np.linalg.norm(ref) * np.linalg.norm(got)))
    print(f"torch/onnx cosine parity: {cos:.6f}")
    assert cos > 0.999, "ONNX export does not match torch output"

    # card index
    keys, rows = [], []
    for gi, g in enumerate(GAME_ORDER):
        z = np.load(os.path.join(BASE, "ml_data", f"{g}_img_emb.npz"))
        emb = z["emb"].astype(np.float32)
        for pid in z["product_id"]:
            keys.append((gi, int(pid)))
        rows.append(emb)
        print(f"  {g}: {len(z['product_id']):,}")
    mat = np.vstack(rows)

    idx_path = os.path.join(OUT_DIR, "card-index.bin")
    with open(idx_path, "wb") as f:
        f.write(struct.pack("<ii", mat.shape[0], mat.shape[1]))
        for gi, pid in keys:
            f.write(struct.pack("<Bi", gi, pid))
        f.write(mat.tobytes())
    print(f"wrote {idx_path} ({os.path.getsize(idx_path)/1e6:.0f} MB, "
          f"{mat.shape[0]:,} cards x {mat.shape[1]}d)")


if __name__ == "__main__":
    main()
