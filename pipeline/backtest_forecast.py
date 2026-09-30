#!/usr/bin/env python3
"""
Rolling-origin backtest for the forecast model (2026-08-11).

Replays the EXACT production path: forecast_game_target(as_of=origin) truncates
the price matrix before anything trains (leakage-free by construction), and the
calibration variants are computed from that run's own internals via the same
forecast_calib functions production applies — so a variant's backtest score is
the score of what would ship.

Variants per (game, target, horizon, origin):
  baseline  production today: raw point + scalar conformal widen
  debias    point/band shifted by the OOS weighted-median residual
  cqr       per-price-bucket (Mondrian) conformal widening
  both      debias + cqr
  pool      small games pooled (game categorical, feature-union), + both calib

Results checkpoint to ml_data/backtest_results.db after every cell — safe to
kill and re-run; finished cells are skipped. The trainer self-stops 4.5h after
boot, so chunk long runs per game.

Run (trainer):
  .venv/bin/python backtest_forecast.py --games lorcana,digimon,gundam,starwars \
      --targets ungraded,psa10 --origins 2024-09,2024-12,2025-03,2025-06,2025-09,2025-12
"""

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

import forecast_calib as fcal
import forecast_predict as fp
from forecast_deep import load_matrix_all, model_new, model_quantile

from _paths import DATA_DIR as BASE
# Overridable: on the TRAINER the default (inside ml_data/) is wrong — the
# nightly rsyncs ml_data Mac->trainer and clobbered chunk A's results there
# (2026-08-12). Trainer runs must write somewhere unsynced, e.g. ~/.
OUT_DB = os.environ.get("TCG_BT_OUT",
                        os.path.join(BASE, "ml_data", "backtest_results.db"))

HK = {"1m": 1, "6m": 6, "12m": 12}
POOL_GAMES = ["onepiece", "lorcana", "digimon", "gundam", "starwars"]


def ensure_out(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            run TEXT NOT NULL, game TEXT NOT NULL, target TEXT NOT NULL,
            horizon TEXT NOT NULL, origin TEXT NOT NULL, variant TEXT NOT NULL,
            n INTEGER, mae_w REAL, mae_raw REAL, med_ape REAL, dir_acc REAL,
            bias_mean REAL, bias_med REAL, coverage REAL, med_width REAL,
            seg_bias REAL, seg_widen REAL,
            PRIMARY KEY (run, game, target, horizon, origin, variant)
        )""")
    conn.commit()


def full_matrix(cache, game, target):
    if (game, target) not in cache:
        pids, prints, dates, P = load_matrix_all(game, target)
        idx = {(int(p), str(pr)): i for i, (p, pr) in enumerate(zip(pids, prints))}
        cache[(game, target)] = (idx, dates, P)
    return cache[(game, target)]


def score(pred, lo, hi, realized, base, label_n=None):
    """Metric row for one variant on one cell (all in log-return space)."""
    w = fp.price_weight(base)
    err = pred - realized
    ape = np.abs(np.expm1(pred) - np.expm1(realized)) / np.abs(np.expm1(realized) + 1e-9)
    return {
        "n": int(len(pred)),
        "mae_w": float(np.average(np.abs(err), weights=w)),
        "mae_raw": float(np.mean(np.abs(err))),
        "med_ape": float(np.median(ape)),
        "dir_acc": float(np.mean(np.sign(pred) == np.sign(realized))),
        "bias_mean": float(np.mean(err)),
        "bias_med": float(np.median(err)),
        "coverage": float(np.mean((realized >= lo) & (realized <= hi))),
        "med_width": float(np.median(hi - lo)),
    }


def variant_bands(d, variant):
    """(pred, lo, hi) for the emission cards under a calibration variant,
    computed from one production run's internals with the shared fcal code."""
    em = d["emit"]
    bias = d["bias"] if variant in ("debias", "both") else 0.0
    if variant in ("cqr", "both"):
        E = fcal.nonconformity(d["q10_test"], d["q90_test"], d["y_test"], bias)
        calib = fcal.fit_bucket_conformal(d["base_test"], E, fp.CONF_TARGET)
        widen = fcal.bucket_widen_for(em["base"], calib)
        seg_widen = float(np.median(calib["widen"]))
    else:
        E = fcal.nonconformity(d["q10_test"], d["q90_test"], d["y_test"], bias)
        widen = fcal.conformal_quantile(E, fp.CONF_TARGET)
        seg_widen = float(widen)
    ret, lo, hi = fcal.emit_bands(em["pred_now"], em["q10_now"], em["q90_now"],
                                  widen, bias, fp.RET_CLIP)
    return ret, lo, hi, bias, seg_widen


def realized_for(em, k, idx_full, dates_full, P_full, n_trunc):
    """Realized log-return per emitted card: anchor month + k in the FULL
    matrix (matches live grading: realized_at = as-of + horizon)."""
    out = np.full(len(em["pids"]), np.nan)
    for i, (pid, pr, li) in enumerate(zip(em["pids"], em["prints"], em["last_idx"])):
        row = idx_full.get((int(pid), str(pr)))
        tcol = int(li) + k
        if row is None or tcol >= len(dates_full):
            continue
        p0, p1 = P_full[row, int(li)], P_full[row, tcol]
        if np.isfinite(p0) and np.isfinite(p1) and p0 > 0 and p1 > 0:
            out[i] = np.log(p1 / p0)
    return out


def run_cell(conn, run_tag, game, target, origin, horizons, variants, cache,
             capture_frames=False):
    done = set(conn.execute(
        "SELECT variant, horizon FROM results WHERE run=? AND game=? AND target=? AND origin=?",
        (run_tag, game, target, origin)).fetchall())
    todo = [v for v in variants
            if any((v, h) not in done for h in horizons)]
    if not todo:
        print(f"[skip] {game}/{target}@{origin} already done", flush=True)
        return None
    debug = {"capture_frames": capture_frames} if capture_frames else {}
    hmap = {h: HK[h] for h in horizons}
    fp.forecast_game_target(game, target, now=f"bt-{origin}", as_of=origin,
                            horizons=hmap, debug=debug)
    idx_full, dates_full, P_full = full_matrix(cache, game, target)
    n_trunc = sum(1 for d in dates_full if d <= origin)
    for h in horizons:
        d = debug.get(h)
        if not d or d.get("q10_test") is None or "emit" not in d:
            print(f"[{game}/{target}/{h}@{origin}] no OOS internals — skipped", flush=True)
            continue
        realized = realized_for(d["emit"], HK[h], idx_full, dates_full, P_full, n_trunc)
        ok = np.isfinite(realized)
        if ok.sum() < 30:
            print(f"[{game}/{target}/{h}@{origin}] only {int(ok.sum())} matured — skipped",
                  flush=True)
            continue
        for v in todo:
            if v == "pool" or (v, h) in done:
                continue   # pooled runs handled by run_pool()
            ret, lo, hi, bias, seg_widen = variant_bands(d, v)
            m = score(ret[ok], lo[ok], hi[ok], realized[ok], d["emit"]["base"][ok])
            conn.execute(
                "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_tag, game, target, h, origin, v, m["n"], m["mae_w"], m["mae_raw"],
                 m["med_ape"], m["dir_acc"], m["bias_mean"], m["bias_med"],
                 m["coverage"], m["med_width"], bias, seg_widen))
            conn.commit()
            print(f"[{game}/{target}/{h}@{origin}] {v:8s} n={m['n']} "
                  f"maeW={m['mae_w']:.3f} dir={m['dir_acc']:.2f} "
                  f"bias={m['bias_med']:+.3f} cov={m['coverage']:.2f} "
                  f"width={m['med_width']:.2f}", flush=True)
    return debug


def run_pool(conn, run_tag, target, origin, horizons, cache, debugs):
    """Pooled variant: one model over the small games' captured frames, game as
    a categorical, per-game debias+cqr from the pooled OOS predictions."""
    for h in horizons:
        frames, metas = [], []
        for game, dbg in debugs.items():
            d = dbg.get(h)
            if not d or "X" not in d or "emit" not in d:
                continue
            frames.append((game, d))
        if len(frames) < 2:
            print(f"[pool/{target}/{h}@{origin}] <2 games with frames — skipped", flush=True)
            continue
        Xp = fcal.concat_pooled([fcal.add_game_col(d["X"], g) for g, d in frames])
        for c in Xp.columns:   # halve memory; HGB bins to float32 anyway
            if Xp[c].dtype == np.float64:
                Xp[c] = Xp[c].astype(np.float32)
        yp = np.concatenate([d["y"] for _, d in frames])
        tp = np.concatenate([d["test"] for _, d in frames])
        wp = np.concatenate([d["w"] for _, d in frames])
        bp = np.concatenate([d["base_t"] for _, d in frames])
        if len(yp) > fp.MAX_TRAIN_SAMPLES:
            sel = np.random.default_rng(42).choice(len(yp), fp.MAX_TRAIN_SAMPLES, replace=False)
            Xp, yp, tp, wp, bp = Xp.iloc[sel].reset_index(drop=True), yp[sel], tp[sel], wp[sel], bp[sel]
        # Same degenerate-column guard as production: a column with <50 finite
        # values (or a constant) in the fit slice crashes HGB's binning.
        drop = []
        counts = Xp.loc[~tp].count()
        for c in Xp.columns:
            if isinstance(Xp[c].dtype, pd.CategoricalDtype):
                continue
            if counts[c] < 50 or Xp[c].min() == Xp[c].max() \
                    or Xp[c][~tp].min() == Xp[c][~tp].max():
                drop.append(c)
        if drop:
            Xp = Xp.drop(columns=drop)
            print(f"[pool/{target}/{h}@{origin}] dropped degenerate: {len(drop)} col(s)",
                  flush=True)
        gcol = Xp["game"].to_numpy()
        print(f"[pool/{target}/{h}@{origin}] {len(yp)} rows from "
              f"{'/'.join(g for g, _ in frames)}", flush=True)
        m_oos = model_new().fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        q10t = model_quantile(0.10).fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        q90t = model_quantile(0.90).fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        mF = model_new().fit(Xp, yp, sample_weight=wp)
        q10F = model_quantile(0.10).fit(Xp, yp, sample_weight=wp)
        q90F = model_quantile(0.90).fit(Xp, yp, sample_weight=wp)
        idx_all = np.arange(len(yp))
        for game, d in frames:
            gt = tp & (gcol == game)
            if gt.sum() < 50:
                continue
            po = m_oos.predict(Xp[gt])
            yt, wt, bt = yp[gt], wp[gt], bp[gt]
            bias = fcal.fit_debias(po, yt, wt)
            E = fcal.nonconformity(q10t.predict(Xp[gt]), q90t.predict(Xp[gt]), yt, bias)
            calib = fcal.fit_bucket_conformal(bt, E, fp.CONF_TARGET)
            em = d["emit"]
            Xn = fcal.add_game_col(em["Xnow"], game).reindex(columns=Xp.columns)
            # Recast every categorical to the POOLED categories: pandas codes
            # depend on the dtype's category list, so a per-game dtype would
            # silently misencode against the pooled fit.
            for c in Xp.columns:
                if isinstance(Xp[c].dtype, pd.CategoricalDtype):
                    Xn[c] = pd.Categorical(Xn[c], categories=Xp[c].cat.categories)
            ret, lo, hi = fcal.emit_bands(
                mF.predict(Xn), q10F.predict(Xn), q90F.predict(Xn),
                fcal.bucket_widen_for(em["base"], calib), bias, fp.RET_CLIP)
            idx_full, dates_full, P_full = full_matrix(cache, game, target)
            realized = realized_for(em, HK[h], idx_full, dates_full, P_full, 0)
            ok = np.isfinite(realized)
            if ok.sum() < 30:
                continue
            met = score(ret[ok], lo[ok], hi[ok], realized[ok], em["base"][ok])
            conn.execute(
                "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_tag, game, target, h, origin, "pool", met["n"], met["mae_w"],
                 met["mae_raw"], met["med_ape"], met["dir_acc"], met["bias_mean"],
                 met["bias_med"], met["coverage"], met["med_width"], bias,
                 float(np.median(calib["widen"]))))
            conn.commit()
            print(f"[{game}/{target}/{h}@{origin}] pool     n={met['n']} "
                  f"maeW={met['mae_w']:.3f} dir={met['dir_acc']:.2f} "
                  f"bias={met['bias_med']:+.3f} cov={met['coverage']:.2f} "
                  f"width={met['med_width']:.2f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="lorcana,digimon,gundam,starwars")
    ap.add_argument("--targets", default="ungraded,psa10")
    ap.add_argument("--horizons", default="6m")
    ap.add_argument("--origins", default="2024-09,2024-12,2025-03,2025-06,2025-09,2025-12")
    ap.add_argument("--variants", default="baseline,debias,cqr,both")
    ap.add_argument("--run", default="bt1", help="results namespace")
    args = ap.parse_args()

    assert os.environ.get("TCG_FC_DEBIAS") != "1" and os.environ.get("TCG_FC_CQR") != "1", \
        "backtest computes variants itself — run with calibration flags OFF"

    games = args.games.split(",")
    targets = args.targets.split(",")
    horizons = args.horizons.split(",")
    origins = args.origins.split(",")
    variants = args.variants.split(",")
    want_pool = "pool" in variants

    conn = sqlite3.connect(OUT_DB)
    ensure_out(conn)
    cache = {}
    for origin in origins:
        for target in targets:
            debugs = {}
            for game in games:
                try:
                    dbg = run_cell(conn, args.run, game, target, origin, horizons,
                                   variants, cache,
                                   capture_frames=want_pool and game in POOL_GAMES)
                    if dbg:
                        debugs[game] = dbg
                except Exception as e:
                    print(f"[{game}/{target}@{origin}] FAILED: {type(e).__name__}: {e}",
                          flush=True)
            if want_pool and debugs:
                pool_done = conn.execute(
                    "SELECT COUNT(*) FROM results WHERE run=? AND target=? AND origin=?"
                    " AND variant='pool'", (args.run, target, origin)).fetchone()[0]
                if not pool_done:
                    try:
                        run_pool(conn, args.run, target, origin, horizons, cache, debugs)
                    except Exception as e:
                        print(f"[pool/{target}@{origin}] FAILED: {type(e).__name__}: {e}",
                              flush=True)
    conn.close()
    print("backtest complete", flush=True)


if __name__ == "__main__":
    main()
