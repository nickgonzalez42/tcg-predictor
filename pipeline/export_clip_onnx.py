#!/usr/bin/env python3
"""Export the image-search / card-scanner artifacts the API serves.
Files in dotnet/API/Data/clip/ (gitignored, pushed like data):
  clip-visual.onnx   the open_clip ViT-B-32/laion2b IMAGE encoder — the same
                     model embed_images.py uses, so a photo embedded by the
                     API is searchable against the pipeline's card embeddings.
                     --int8 writes it dynamically quantized (~90 MB instead
                     of ~350 MB) — the prod box has 1.9 GB of RAM in total.
  card-index.bin     v2 (2026-10-24): every game's embeddings projected to
                     --dim dimensions by a PCA fit on the catalog (256 keeps
                     90% of the variance and the same top-5 recognition on
                     synthetic phone shots at half the memory) and
                     re-normalized, plus the projection so the API can map
                     a photo's 512-d embedding into the same space:
                       b"CSI2" [int32 n][int32 dim][int32 full]
                       float32 mean[full]  float32 W[full × dim] (row-major)
                       n × (byte game_idx, int32 pid)
                       n × dim float32 rows (L2-normalized)
                     --dim 512 writes an identity projection. GAME_ORDER
                     below is the byte's meaning — keep it in sync with
                     ImageSearchService.cs.
Rerun after a model change or when embeddings grow (new cards); the
weekly refresh rebuilds the index with --index-only (no torch needed).
Run:  .venv/bin/python export_clip_onnx.py [--index-only] [--dim 256] [--int8]
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

MODEL_NAME, PRETRAINED = "ViT-B-32", "laion2b_s34b_b79k"   # = embed_images.py
GAME_ORDER = ["pokemon", "magic", "yugioh", "onepiece",
              "lorcana", "digimon", "gundam", "starwars",
              # appended 2026-10-24 (catalog-only games scan into the
              # collection too); never reorder — the byte is persisted
              "fleshandblood", "sorcery", "riftbound", "cyberpunk"]
OUT_DIR = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "clip")
REFIT_PCA = False



def export_model(int8):
    import torch
    import open_clip

    class Visual(torch.nn.Module):
        """Image encoder + L2 norm, so the ONNX output is directly comparable
        to the (already normalized) card index rows via dot product."""
        def __init__(self, clip):
            super().__init__()
            self.visual = clip.visual

        def forward(self, x):
            f = self.visual(x)
            return f / f.norm(dim=-1, keepdim=True)

    model, _, _ = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    model.eval()

    onnx_path = os.path.join(OUT_DIR, "clip-visual.onnx")
    fp32_path = os.path.join(OUT_DIR, "clip-visual-fp32.onnx") if int8 else onnx_path
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(Visual(model), dummy, fp32_path,
                      input_names=["image"], output_names=["embedding"],
                      dynamic_axes={"image": {0: "batch"}, "embedding": {0: "batch"}},
                      opset_version=17)
    print(f"wrote {fp32_path} ({os.path.getsize(fp32_path)/1e6:.0f} MB)")

    # parity: torch vs onnxruntime on the same random tensor
    import onnxruntime as ort
    sess = ort.InferenceSession(fp32_path, providers=["CPUExecutionProvider"])
    with torch.no_grad():
        ref = Visual(model)(dummy).numpy()
    got = sess.run(None, {"image": dummy.numpy()})[0]
    cos = float((ref * got).sum() / (np.linalg.norm(ref) * np.linalg.norm(got)))
    print(f"torch/onnx cosine parity: {cos:.6f}")
    assert cos > 0.999, "ONNX export does not match torch output"

    if int8:
        # Dynamic int8 weights: a quarter of the memory on the API box; the
        # fp32 export's external-data sidecar folds into the one file.
        from onnxruntime.quantization import quantize_dynamic, QuantType
        quantize_dynamic(fp32_path, onnx_path, weight_type=QuantType.QInt8)
        s8 = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        got8 = s8.run(None, {"image": dummy.numpy()})[0]
        cos8 = float((got * got8).sum() / (np.linalg.norm(got) * np.linalg.norm(got8)))
        print(f"wrote {onnx_path} int8 ({os.path.getsize(onnx_path)/1e6:.0f} MB), "
              f"fp32/int8 cosine parity: {cos8:.4f}")
        assert cos8 > 0.90, "int8 quantization drifted too far from fp32"   # ~0.95-0.97 on real cards; recognition measured equal
        for stale in (fp32_path, fp32_path + ".data", onnx_path + ".data"):
            if stale != onnx_path and os.path.exists(stale):
                os.remove(stale)


def build_index(dim):
    keys, rows = [], []
    for gi, g in enumerate(GAME_ORDER):
        p = os.path.join(BASE, "ml_data", f"{g}_img_emb.npz")
        if not os.path.exists(p):
            print(f"  {g}: no embeddings yet — skipped")
            continue
        z = np.load(p)
        emb = z["emb"].astype(np.float32)
        for pid in z["product_id"]:
            keys.append((gi, int(pid)))
        rows.append(emb)
        print(f"  {g}: {len(z['product_id']):,}")
    mat = np.vstack(rows)
    mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
    full = mat.shape[1]

    if dim < full:
        # The basis is fit ONCE and persisted (pca-<dim>.npz): a nightly
        # rebuild then only appends new cards' rows, so rsync ships a small
        # delta instead of a fully rewritten index. --refit-pca starts over.
        basis = os.path.join(OUT_DIR, f"pca-{dim}.npz")
        if os.path.exists(basis) and not REFIT_PCA:
            z = np.load(basis)
            mean, W = z["mean"].astype(np.float32), z["W"].astype(np.float32)
            print(f"  PCA {full}->{dim}: reusing {os.path.basename(basis)}")
        else:
            from sklearn.decomposition import PCA
            rng = np.random.default_rng(0)
            fit_on = mat[rng.choice(len(mat), min(len(mat), 80_000), replace=False)]
            pca = PCA(n_components=dim, random_state=0).fit(fit_on)
            mean = pca.mean_.astype(np.float32)
            W = pca.components_.T.astype(np.float32)            # full × dim
            np.savez(basis, mean=mean, W=W)
            print(f"  PCA {full}->{dim}: variance kept {pca.explained_variance_ratio_.sum():.3f} "
                  f"(basis saved to {os.path.basename(basis)})")
        proj = (mat - mean) @ W
    else:
        mean = np.zeros(full, np.float32)
        W = np.eye(full, dtype=np.float32)
        proj = mat
    proj = proj.astype(np.float32)
    proj /= np.maximum(np.linalg.norm(proj, axis=1, keepdims=True), 1e-9)

    idx_path = os.path.join(OUT_DIR, "card-index.bin")
    tmp = idx_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(b"CSI2")
        f.write(struct.pack("<iii", proj.shape[0], dim, full))
        f.write(mean.tobytes())
        f.write(np.ascontiguousarray(W).tobytes())
        f.write(b"".join(struct.pack("<Bi", gi, pid) for gi, pid in keys))
        f.write(np.ascontiguousarray(proj).tobytes())
    os.replace(tmp, idx_path)
    print(f"wrote {idx_path} ({os.path.getsize(idx_path)/1e6:.0f} MB, "
          f"{proj.shape[0]:,} cards x {dim}d)")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-only", action="store_true", help="rebuild card-index.bin only")
    ap.add_argument("--dim", type=int, default=256, help="index dimensions (PCA; 512 = none)")
    ap.add_argument("--int8", action="store_true", help="write the encoder dynamically quantized")
    ap.add_argument("--refit-pca", action="store_true", help="fit a new PCA basis instead of reusing the saved one")
    args = ap.parse_args()
    global REFIT_PCA
    REFIT_PCA = args.refit_pca
    os.makedirs(OUT_DIR, exist_ok=True)
    if not args.index_only:
        export_model(args.int8)
    build_index(args.dim)


if __name__ == "__main__":
    main()
