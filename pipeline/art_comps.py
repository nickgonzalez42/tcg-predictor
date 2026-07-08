"""
Art comparables: for each card, find the most visually similar cards (CLIP
embedding cosine similarity) and measure how those look-alikes held their value
over the past year. Powers reasoning like "visually similar cards held their
value" with real, verifiable numbers.

Output: ml_data/{game}_art_comps.csv  (product_id, comp_n, comp_ret12, comp_ids)
  comp_ret12 = median (latest price / price 12 months earlier) across neighbors
               that have both points in the unified ungraded (Near Mint) series.

Run after embeddings + unified history are fresh:
    ../../one-piece/.venv/bin/python art_comps.py
"""

import json
import os
import sqlite3

import numpy as np
import pandas as pd

from _paths import DATA_DIR as BASE

DATA = os.path.join(BASE, "ml_data")
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")

K = 12            # neighbors per card
MIN_COMPS = 5     # need at least this many priced neighbors to report a number
CHUNK = 2048


def retention12(game):
    """product_id -> latest_price / price_12_months_ago from the ungraded series."""
    rows = sqlite3.connect(PC_DB, timeout=30).execute(
        "SELECT product_id, date, price FROM price_history_unified "
        "WHERE game=? AND grade='ungraded' ORDER BY date", (game,)).fetchall()
    series = {}
    for pid, d, p in rows:
        series.setdefault(pid, {})[d[:7]] = p

    out = {}
    for pid, months in series.items():
        keys = sorted(months)
        last = keys[-1]
        y, m = int(last[:4]), int(last[5:7])
        target = f"{y-1:04d}-{m:02d}"
        past = [k for k in keys if k <= target]
        if past and months[past[-1]] > 0:
            out[pid] = months[last] / months[past[-1]]
    return out


def build_game(game):
    z = np.load(os.path.join(DATA, f"{game}_img_emb.npz"))
    pids, emb = z["product_id"], z["emb"].astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9

    ret = retention12(game)
    ret_arr = np.array([ret.get(int(p), np.nan) for p in pids])

    rows = []
    for start in range(0, len(pids), CHUNK):
        block = emb[start:start + CHUNK]
        sims = block @ emb.T                      # (chunk, N) cosine similarity
        # top K+1 then drop self
        top = np.argpartition(-sims, K + 1, axis=1)[:, :K + 1]
        for i in range(block.shape[0]):
            gi = start + i
            neigh = [j for j in top[i] if j != gi][:K]
            rets = ret_arr[neigh]
            rets = rets[np.isfinite(rets)]
            # clip extreme outliers so one moonshot neighbor can't skew the median
            rets = np.clip(rets, 0.1, 10.0)
            rows.append((
                int(pids[gi]),
                int(len(rets)),
                round(float(np.median(rets)), 4) if len(rets) >= MIN_COMPS else None,
                json.dumps([int(pids[j]) for j in neigh[:5]]),
            ))
        if (start // CHUNK) % 5 == 0:
            print(f"  [{game}] {min(start + CHUNK, len(pids))}/{len(pids)}", flush=True)

    df = pd.DataFrame(rows, columns=["product_id", "comp_n", "comp_ret12", "comp_ids"])
    out = os.path.join(DATA, f"{game}_art_comps.csv")
    df.to_csv(out, index=False)
    have = df["comp_ret12"].notna().sum()
    print(f"[{game}] {have}/{len(df)} cards with comp stats -> {out}", flush=True)


def main():
    for game in ["pokemon", "onepiece"]:
        build_game(game)


if __name__ == "__main__":
    main()
