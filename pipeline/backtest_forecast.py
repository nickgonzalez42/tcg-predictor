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
import collections
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
# Cross-game transfer test (2026-10-17): can the BIG games' history inform the
# small games' models? --donors names games whose training frames join the
# pool without being evaluated (frames-only capture: no model fits for them,
# rows capped by --pool-cap so a 9 GB Mac stays out of swap). Three pooled
# readings per evaluated cell:
#   pool       pooled model + per-game debias/CQR calibration (the 2026-08 variant)
#   pool_base  pooled model + the BASELINE's scalar conformal, no debias — the
#              like-for-like comparison against `baseline`
#   blend      segment point blended toward the pooled point by n/(n+K), n =
#              the segment's own train rows (the launch tier's shrinkage shape)
# plus `flat` (persistence: zero return) as the yardstick the 2026-10-10 review
# set for every model decision.
BLEND_K = 20_000


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
    # Young-card segment (--young): one row per (population, reading).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results_young (
            run TEXT NOT NULL, game TEXT NOT NULL, target TEXT NOT NULL,
            horizon TEXT NOT NULL, origin TEXT NOT NULL,
            pop TEXT NOT NULL, variant TEXT NOT NULL,
            n INTEGER, n_sets INTEGER, spearman REAL, mae_w REAL, mae_raw REAL,
            dir_acc REAL, top_dec_ret REAL, bot_dec_ret REAL, all_ret REAL,
            prec10 REAL,
            PRIMARY KEY (run, game, target, horizon, origin, pop, variant)
        )""")
    # Per-card rows behind results_young, so a reading can be sliced by
    # price band / rarity / set after the fact (is the ranking skill real at
    # the expensive end, or a floor effect among penny cards?).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS young_cards (
            run TEXT NOT NULL, game TEXT NOT NULL, target TEXT NOT NULL,
            horizon TEXT NOT NULL, origin TEXT NOT NULL, product_id INTEGER NOT NULL,
            set_name TEXT, rarity TEXT, in_launch INTEGER, base REAL,
            pred_model REAL, pred_prior REAL, realized REAL,
            PRIMARY KEY (run, game, target, horizon, origin, product_id)
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


# Young-card segment (2026-10-20): can the production model RANK cards that
# have a single monthly bucket at the origin — the population the live
# minimum-history gate suppresses and the launch tier covers with an honest
# flat — or is flat the ceiling? Scored only on cards BORN in the origin
# month, with the questions a new-set screener actually asks: rank
# correlation, and whether the top decile by forecast outran the rest.
# Three readings per cell:
#   model  the baseline variant's point (unshrunk: ranks identical to what
#          ships; its MAE is a touch optimistic vs the published number)
#   flat   zero return — the launch tier's current output
#   prior  the launch tier's shape: median first-month return of the card's
#          rarity cohort over launches that matured BEFORE the origin
# and two populations: every origin-born card ("young"), and only those in a
# set that is itself new at the origin (>= SET_MIN base cards born that
# month, "launch") — which keeps source-onboarding months (digimon 2025-10,
# the 2026-07 PriceCharting->TCGplayer seam) from masquerading as launches.
SET_MIN = 30
PRIOR_MIN = 8


def _spearman(a, b):
    if len(a) < 3:
        return float("nan")
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def _decile_stats(pred, realized):
    """Mean realized log-return of the top / bottom decile by pred, of the
    whole population, and precision@10% (share of the pred top decile that
    is also in the realized top decile)."""
    n = len(pred)
    k = max(1, n // 10)
    op, orr = np.argsort(pred), np.argsort(realized)
    top_p, bot_p = op[-k:], op[:k]
    top_r = set(orr[-k:].tolist())
    return (float(realized[top_p].mean()), float(realized[bot_p].mean()),
            float(realized.mean()), float(np.mean([i in top_r for i in top_p])))


def score_young(conn, run_tag, game, target, h, origin, d, realized,
                idx_full, dates_full, P_full, n_trunc):
    em = d["emit"]
    o_col = n_trunc - 1
    rows = np.array([idx_full.get((int(p), str(pr)), -1)
                     for p, pr in zip(em["pids"], em["prints"])])
    fin = np.isfinite(P_full)
    born = np.where(fin.any(axis=1), fin.argmax(axis=1), -1)
    has = rows >= 0
    young = np.isfinite(realized) & has
    young[has] &= born[rows[has]] == o_col
    if young.sum() < 20:
        print(f"[{game}/{target}/{h}@{origin}] young: only {int(young.sum())} "
              f"origin-born cards matured — skipped", flush=True)
        return
    traits = fp.card_traits(game)
    inv = {i: k for k, i in idx_full.items()}

    # Which sets are NEW at the origin: base cards born in the origin column.
    set_births = collections.Counter()
    for i in np.where(born == o_col)[0]:
        pid, pr = inv[i]
        if pr == "":
            set_births[(traits.get(pid) or {}).get("set_name") or "?"] += 1
    new_sets = {s for s, n in set_births.items() if n >= SET_MIN}

    # Rarity-cohort prior from launches that matured strictly before the origin.
    coh = collections.defaultdict(list)
    for i in np.where((born >= 0) & (born + 1 < o_col))[0]:
        pid, pr = inv[i]
        if pr != "":
            continue
        p0, p1 = P_full[i, born[i]], P_full[i, born[i] + 1]
        if np.isfinite(p0) and np.isfinite(p1) and p0 > 0 and p1 > 0:
            r = float(np.clip(np.log(p1 / p0), -fp.RET_CLIP, fp.RET_CLIP))
            coh[(traits.get(pid) or {}).get("rarity") or "?"].append(r)
            coh["__all__"].append(r)
    med = {k: float(np.median(v)) for k, v in coh.items() if len(v) >= PRIOR_MIN}

    ret_model, _, _, _, _ = variant_bands(d, "baseline")
    pid_y = em["pids"][young].astype(int)
    rar = [(traits.get(int(p)) or {}).get("rarity") or "?" for p in pid_y]
    sets = [(traits.get(int(p)) or {}).get("set_name") or "?" for p in pid_y]
    pred = {"model": ret_model[young],
            "flat": np.zeros(int(young.sum())),
            "prior": np.array([med.get(r, med.get("__all__", 0.0)) for r in rar])}
    real_y, base_y = realized[young], em["base"][young]
    in_launch = np.array([s in new_sets for s in sets])
    conn.executemany(
        "INSERT OR REPLACE INTO young_cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_tag, game, target, h, origin, int(p), s_, r_, int(l), float(b),
          float(pm), float(pp), float(rz))
         for p, s_, r_, l, b, pm, pp, rz in zip(pid_y, sets, rar, in_launch, base_y,
                                                 pred["model"], pred["prior"], real_y)])
    print(f"[{game}/{target}/{h}@{origin}] young: {int(young.sum())} origin-born cards, "
          f"{int(in_launch.sum())} in {len(new_sets)} new set(s) "
          f"{sorted(new_sets)[:4]}{'...' if len(new_sets) > 4 else ''}; "
          f"{len(med) - ('__all__' in med)} rarity priors", flush=True)
    for pop, mask in (("young", np.ones(len(real_y), bool)), ("launch", in_launch)):
        if mask.sum() < 20:
            continue
        r_, b_ = real_y[mask], base_y[mask]
        w = fp.price_weight(b_)
        n_sets = len({s for s, m in zip(sets, mask) if m})
        for v, p in pred.items():
            p_ = p[mask]
            err = p_ - r_
            top, bot, allr, prec = _decile_stats(p_, r_)
            row = (run_tag, game, target, h, origin, pop, v, int(mask.sum()), n_sets,
                   _spearman(p_, r_), float(np.average(np.abs(err), weights=w)),
                   float(np.mean(np.abs(err))),
                   float(np.mean(np.sign(p_) == np.sign(r_))), top, bot, allr, prec)
            conn.execute("INSERT OR REPLACE INTO results_young VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            print(f"    {pop:6s} {v:6s} n={row[7]:<5d} rho={row[9]:+.3f} "
                  f"maeW={row[10]:.3f} top10={top:+.3f} bot10={bot:+.3f} "
                  f"all={allr:+.3f} prec10={prec:.2f}", flush=True)
        conn.commit()


POOL_VARIANTS = ("pool", "pool_base", "blend")   # scored by run_pool, not here


def run_cell(conn, run_tag, game, target, origin, horizons, variants, cache,
             capture_frames=False, donor=False, frames_cap=None, need_frames=False,
             young=False):
    """need_frames: a pooled fit for this (target, origin) is still pending,
    so re-run the segment for its frames even when every scored variant is
    already in the results (a pooled failure must not strand the cell)."""
    hmap = {h: HK[h] for h in horizons}
    if donor:
        # Frames only: the game's samples feed the pooled fit, nothing of its
        # own is scored (its six model fits are the whole cost of a big game).
        debug = {"capture_frames": True, "frames_only": True, "frames_cap": frames_cap}
        fp.forecast_game_target(game, target, now=f"bt-{origin}", as_of=origin,
                                horizons=hmap, debug=debug)
        got = [h for h in horizons if h in debug and "X" in debug[h]]
        print(f"[donor {game}/{target}@{origin}] frames for {', '.join(got) or 'nothing'}"
              + (f" ({len(debug[got[0]]['y'])} rows)" if got else ""), flush=True)
        return debug
    done = set(conn.execute(
        "SELECT variant, horizon FROM results WHERE run=? AND game=? AND target=? AND origin=?",
        (run_tag, game, target, origin)).fetchall())
    todo = [v for v in variants
            if any((v, h) not in done for h in horizons)]
    if not todo and not need_frames:
        print(f"[skip] {game}/{target}@{origin} already done", flush=True)
        return None
    debug = {"capture_frames": capture_frames} if capture_frames else {}
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
            if v in POOL_VARIANTS or (v, h) in done:
                continue   # pooled runs handled by run_pool()
            if v == "flat":
                # Persistence: zero return, bands = the baseline's bands
                # re-centred on zero (same width, so coverage is comparable).
                ret_b, lo_b, hi_b, bias, seg_widen = variant_bands(d, "baseline")
                ret = np.zeros_like(ret_b)
                lo, hi = lo_b - ret_b, hi_b - ret_b
            else:
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
        if young:
            score_young(conn, run_tag, game, target, h, origin, d, realized,
                        idx_full, dates_full, P_full, n_trunc)
    return debug


def _cap_frame(d, cap, seed=42):
    """Uniform row subsample of a captured frame (keeps train/test mix)."""
    n = len(d["y"])
    if not cap or n <= cap:
        return d
    idx = np.random.default_rng(seed).choice(n, cap, replace=False)
    out = dict(d)
    out.update({"X": d["X"].iloc[idx].reset_index(drop=True), "y": d["y"][idx],
                "test": d["test"][idx], "w": d["w"][idx], "base_t": d["base_t"][idx]})
    return out


def run_pool(conn, run_tag, target, origin, horizons, cache, debugs,
             evaluate=None, pool_cap=0):
    """Pooled variants: one model over every captured frame (evaluated games
    + donors), game as a categorical; scored per EVALUATED game three ways
    (pool / pool_base / blend, see the header)."""
    for h in horizons:
        frames = []
        for game, dbg in debugs.items():
            d = dbg.get(h)
            if not d or "X" not in d:
                continue
            frames.append((game, _cap_frame(d, pool_cap)))
        if len(frames) < 2:
            print(f"[pool/{target}/{h}@{origin}] <2 games with frames — skipped", flush=True)
            continue
        Xp = fcal.concat_pooled([fcal.add_game_col(d["X"], g) for g, d in frames])
        for c in Xp.columns:   # halve memory; HGB bins to float32 anyway
            if Xp[c].dtype == np.float64:
                Xp[c] = Xp[c].astype(np.float32)
        # HGB caps a categorical at 255 levels; pooling Magic's 447 sets in
        # blows set_name past it (597-744 on 2026-10-17). Keep each game's
        # most frequent levels and fold the long tail into one bucket — set
        # identity doesn't transfer across games anyway; the set-level
        # numeric features (setret3/12, set size/age) carry what does.
        CAT_MAX = 254
        cat_remap = {}
        for c in Xp.columns:
            if not isinstance(Xp[c].dtype, pd.CategoricalDtype) or Xp[c].nunique() <= CAT_MAX:
                continue
            keep = Xp[c].value_counts().index[:CAT_MAX].tolist()
            cats = keep + ["__other__"]
            Xp[c] = pd.Categorical(np.where(Xp[c].isin(keep), Xp[c].astype(object), "__other__"),
                                   categories=cats)
            cat_remap[c] = (set(keep), cats)
            print(f"[pool/{target}/{h}@{origin}] {c}: folded to {CAT_MAX} levels + __other__",
                  flush=True)
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
              + ", ".join(f"{g} {len(d['y'])}" for g, d in frames), flush=True)
        m_oos = model_new().fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        q10t = model_quantile(0.10).fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        q90t = model_quantile(0.90).fit(Xp[~tp], yp[~tp], sample_weight=wp[~tp])
        mF = model_new().fit(Xp, yp, sample_weight=wp)
        q10F = model_quantile(0.10).fit(Xp, yp, sample_weight=wp)
        q90F = model_quantile(0.90).fit(Xp, yp, sample_weight=wp)
        for game, d in frames:
            if "emit" not in d or (evaluate and game not in evaluate):
                continue   # donor: contributed rows, nothing to score
            gt = tp & (gcol == game)
            if gt.sum() < 50:
                continue
            po = m_oos.predict(Xp[gt])
            yt, wt, bt = yp[gt], wp[gt], bp[gt]
            q10o, q90o = q10t.predict(Xp[gt]), q90t.predict(Xp[gt])
            em = d["emit"]
            Xn = fcal.add_game_col(em["Xnow"], game).reindex(columns=Xp.columns)
            # Recast every categorical to the POOLED categories: pandas codes
            # depend on the dtype's category list, so a per-game dtype would
            # silently misencode against the pooled fit.
            for c in Xp.columns:
                if isinstance(Xp[c].dtype, pd.CategoricalDtype):
                    if c in cat_remap:
                        keep, cats = cat_remap[c]
                        Xn[c] = pd.Categorical(
                            np.where(Xn[c].astype(object).isin(keep), Xn[c].astype(object), "__other__"),
                            categories=cats)
                    else:
                        Xn[c] = pd.Categorical(Xn[c], categories=Xp[c].cat.categories)
            p_now, q10n, q90n = mF.predict(Xn), q10F.predict(Xn), q90F.predict(Xn)
            idx_full, dates_full, P_full = full_matrix(cache, game, target)
            realized = realized_for(em, HK[h], idx_full, dates_full, P_full, 0)
            ok = np.isfinite(realized)
            if ok.sum() < 30:
                continue
            outs = []
            # pool: debias + per-price-bucket conformal (the 2026-08 recipe)
            bias = fcal.fit_debias(po, yt, wt)
            E = fcal.nonconformity(q10o, q90o, yt, bias)
            calib = fcal.fit_bucket_conformal(bt, E, fp.CONF_TARGET)
            ret, lo, hi = fcal.emit_bands(p_now, q10n, q90n,
                                          fcal.bucket_widen_for(em["base"], calib),
                                          bias, fp.RET_CLIP)
            outs.append(("pool", ret, lo, hi, bias, float(np.median(calib["widen"]))))
            # pool_base: the baseline's own calibration (scalar conformal, no
            # debias) on the pooled model — the like-for-like comparison.
            E0 = fcal.nonconformity(q10o, q90o, yt, 0.0)
            w0 = fcal.conformal_quantile(E0, fp.CONF_TARGET)
            ret, lo, hi = fcal.emit_bands(p_now, q10n, q90n, w0, 0.0, fp.RET_CLIP)
            outs.append(("pool_base", ret, lo, hi, 0.0, float(w0)))
            # blend: the segment's own point pulled toward the pooled point by
            # n/(n+K); bands = the segment's baseline bands re-centred.
            n_seg = int((~d["test"]).sum())
            wseg = n_seg / (n_seg + BLEND_K)
            ret_b, lo_b, hi_b, _, seg_w = variant_bands(d, "baseline")
            pb = np.clip(wseg * em["pred_now"] + (1 - wseg) * p_now, -fp.RET_CLIP, fp.RET_CLIP)
            outs.append(("blend", pb, pb + (lo_b - ret_b), pb + (hi_b - ret_b), 0.0, seg_w))
            for v, ret, lo, hi, b_, sw in outs:
                met = score(ret[ok], lo[ok], hi[ok], realized[ok], em["base"][ok])
                conn.execute(
                    "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_tag, game, target, h, origin, v, met["n"], met["mae_w"],
                     met["mae_raw"], met["med_ape"], met["dir_acc"], met["bias_mean"],
                     met["bias_med"], met["coverage"], met["med_width"], b_, sw))
                conn.commit()
                print(f"[{game}/{target}/{h}@{origin}] {v:9s} n={met['n']} "
                      f"maeW={met['mae_w']:.3f} dir={met['dir_acc']:.2f} "
                      f"bias={met['bias_med']:+.3f} cov={met['coverage']:.2f} "
                      f"width={met['med_width']:.2f}"
                      + (f" (w_seg={wseg:.2f})" if v == "blend" else ""), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="lorcana,digimon,gundam,starwars")
    ap.add_argument("--targets", default="ungraded,psa10")
    ap.add_argument("--horizons", default="6m")
    ap.add_argument("--origins", default="2024-09,2024-12,2025-03,2025-06,2025-09,2025-12")
    ap.add_argument("--variants", default="baseline,debias,cqr,both")
    ap.add_argument("--run", default="bt1", help="results namespace")
    ap.add_argument("--donors", default="",
                    help="comma games whose frames join the pool but are not scored "
                         "(frames-only capture, e.g. magic,pokemon,yugioh)")
    ap.add_argument("--pool-games", default=",".join(POOL_GAMES),
                    help="evaluated games whose frames enter the pool")
    ap.add_argument("--pool-cap", type=int, default=0,
                    help="max rows per game in the pooled fit (0 = no cap)")
    ap.add_argument("--young", action="store_true",
                    help="also score the origin-born (single-bucket) cards: rank "
                         "correlation + decile outcomes vs flat and a rarity prior")
    args = ap.parse_args()

    assert os.environ.get("TCG_FC_DEBIAS") != "1" and os.environ.get("TCG_FC_CQR") != "1", \
        "backtest computes variants itself — run with calibration flags OFF"

    games = args.games.split(",")
    targets = args.targets.split(",")
    horizons = args.horizons.split(",")
    origins = args.origins.split(",")
    variants = args.variants.split(",")
    donors = [g for g in args.donors.split(",") if g]
    pool_games = set(args.pool_games.split(","))
    want_pool = any(v in POOL_VARIANTS for v in variants)

    conn = sqlite3.connect(OUT_DB)
    ensure_out(conn)
    cache = {}
    for origin in origins:
        for target in targets:
            debugs = {}
            # Per HORIZON: a 1m run marking this origin done must not skip a
            # later 6m run's pooled fit (2026-10-17: the clean 6m@2025-12
            # cell got no pooled rows for exactly that reason).
            hp = ",".join("?" * len(horizons))
            pool_done = conn.execute(
                f"SELECT COUNT(DISTINCT horizon) FROM results WHERE run=? AND target=? "
                f"AND origin=? AND variant='pool' AND horizon IN ({hp})",
                (args.run, target, origin, *horizons)).fetchone()[0]
            pool_pending = want_pool and pool_done < len(horizons)
            for game in games:
                try:
                    dbg = run_cell(conn, args.run, game, target, origin, horizons,
                                   variants, cache,
                                   capture_frames=want_pool and game in pool_games,
                                   need_frames=pool_pending and game in pool_games,
                                   young=args.young)
                    if dbg:
                        debugs[game] = dbg
                except Exception as e:
                    print(f"[{game}/{target}@{origin}] FAILED: {type(e).__name__}: {e}",
                          flush=True)
            if want_pool and debugs:
                if pool_pending:
                    # Donors last (their frames are the big ones) and only
                    # when a pooled fit is actually pending for this cell.
                    for game in donors:
                        try:
                            dbg = run_cell(conn, args.run, game, target, origin, horizons,
                                           variants, cache, donor=True,
                                           frames_cap=args.pool_cap or None)
                            if dbg:
                                debugs[game] = dbg
                        except Exception as e:
                            print(f"[donor {game}/{target}@{origin}] FAILED: "
                                  f"{type(e).__name__}: {e}", flush=True)
                    try:
                        run_pool(conn, args.run, target, origin, horizons, cache, debugs,
                                 evaluate=set(games), pool_cap=args.pool_cap)
                    except Exception as e:
                        print(f"[pool/{target}@{origin}] FAILED: {type(e).__name__}: {e}",
                              flush=True)
            debugs.clear()
    conn.close()
    print("backtest complete", flush=True)


if __name__ == "__main__":
    main()
