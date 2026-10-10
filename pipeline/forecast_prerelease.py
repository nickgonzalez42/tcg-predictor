#!/usr/bin/env python3
"""
Pre-release price estimates (prerelease-v1): a predicted launch price for
cards that have NO market price yet — upcoming sets (TCGplayer presale
listings) and just-released cards whose first sales haven't posted.

The standard model forecasts a RETURN from an anchor price. These cards have
no anchor, so this is a different problem: predict the price LEVEL from what
the card IS. Per game, a gradient-boosted model learns log(launch price) from
trait-only features:

  * rarity, card type, every printed stat the scraper captured (numeric where
    it parses, categorical where the vocabulary is small),
  * set structure known before release: set size, how many cards share the
    rarity, the card's position in the numbering, promo/starter-style sets,
  * the character's history: the launch prices of earlier cards with the same
    name root ("Mickey Mouse", "Charizard"), prior sets only,
  * the era: how the same rarity launched in the game's recent sets,
  * the art: a PCA of the CLIP embedding plus the launch prices of the card's
    visually nearest earlier cards.

Label: median ungraded (base printing) price observed 1-10 weeks after the
card's own release (price_history_unified), for releases at least 30 days
after the game's price coverage began — a real launch price, never a
years-later first observation.

Validation is the live task itself: the newest labeled sets are held out and
predicted from older sets only. The typical miss is published inside every
estimate's reason text and stored in prerelease_validation.

Output (predictions.db):
  prerelease_estimates  one row per unpriced card, replaced each run
  prerelease_archive    the FIRST estimate issued per card, graded here once
                        the card trades (same label definition) — the public
                        track record for these calls
  prerelease_validation per-game holdout numbers for each run

Gated on TCG_FC_PRERELEASE=1 (exits 0 otherwise). Runs after forecast_launch;
never fails the model block.
"""

import argparse
import collections
import json
import math
import os
import re
import sqlite3
import sys
import warnings
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE   # noqa: E402
from games import GAMES, priced_games, db_path   # noqa: E402

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                     "pricecharting.db")
OUT_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards",
                      "predictions.db")
ML = os.path.join(BASE, "ml_data")

MODEL_VERSION = "prerelease-v1"
LABEL_LO_DAYS, LABEL_HI_DAYS = 7, 75    # launch-price window after release
COVERAGE_LEAD_DAYS = 30                 # release must postdate price coverage by this
CANDIDATE_LOOKBACK_DAYS = 45            # unpriced + released within this = still "pre-release"
MIN_LABELED = 400                       # below this a game gets no estimates at all
MIN_TRAIN_FOR_VALIDATION = 300
HOLDOUT_MIN_CARDS, HOLDOUT_MIN_SETS, HOLDOUT_MAX_SETS = 200, 2, 4
SET_MIN_LABELED = 20                    # sets smaller than this never anchor a holdout
IMG_PCA = 16
KNN_K = 8
ERA_LOOKBACK_DAYS = 240                 # "recent sets" window for the era priors
INNER_SPLIT_Q = 0.8                     # newest 20% of training releases calibrate drift + bands
MAX_CAT = 200                           # HGB max_bins is 255; cap vocabularies
MIN_HALF_WIDTH = 0.25                   # log-space floor on each side of the band
RNG = 42
# Cross-game fallback for a game with too little launch history of its own
# (a brand-new game): one model over every game's launches, on features that
# mean the same thing in any game. Validated by holding out whole games.
POOLED_VERSION = "prerelease-pooled-v1"
POOLED_CAP = 6000                       # newest labeled launches per game in the pool
POOLED_HOLDOUT_GAMES = 4                # leave-one-game-out covers the youngest games
# A pooled model cannot know a new game's overall price LEVEL (held-out games
# ran +0.6..+1.0 log too high), so it only publishes once the game has a few
# dozen priced launches of its own to calibrate that level with (the median
# residual on its earliest launches). Below that: no estimates, honestly.
POOLED_CALIB_MIN = 60
UNIVERSAL = ["set_size", "rarity_in_set", "rarity_share", "rarity_rank", "num_pos", "num_over",
             "promo_set", "starter_set", "year_frac", "desc_len", "art_knn_price", "art_knn_sim"]

JUNK_COLS = {
    "product_id", "name", "clean_name", "set_name", "set_url_name", "card_number",
    "card_type", "rarity", "description", "release_date", "product_url", "image_url",
    "image_path", "near_mint_price", "custom_attributes", "raw_json", "scraped_at",
    "printings", "base_printing", "market_price", "lowest_price", "lowest_price_ship",
    "total_listings", "history_fetched", "history_fetched_at", "flavor_text",
}
JUNK_ATTR = {
    "description", "text", "cardtext", "oracletext", "flavortext", "number",
    "releasedate", "detailnote", "raritydbname", "cardtype", "cardtypeb", "name",
    "characterversion", "productname", "setname", "url", "image", "attack1",
    "attack2", "attack3", "attack4", "subtypes",
}
PROMO_RE = re.compile(r"promo|prize|championship|league|event|gift|box topper|judge", re.I)
STARTER_RE = re.compile(r"starter|structure|deck|illumineer's quest|collection|precon|commander", re.I)
NAME_SPLIT_RE = re.compile(r"\s+-\s+|\s*\(|\s*\[|\s+#|\s+//\s+")
NAME_SUFFIX_RE = re.compile(r"\s+(ex|v|vmax|vstar|gx|tag team|break|prime|lv\.?\s*x|leader|sp)$", re.I)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


def parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def name_root(name):
    """'Mickey Mouse - Best in Town' -> 'mickey mouse'; 'Charizard ex' -> 'charizard'."""
    if not name:
        return ""
    root = NAME_SPLIT_RE.split(str(name), maxsplit=1)[0].strip().lower()
    root = NAME_SUFFIX_RE.sub("", root).strip()
    return root


def card_num(s, set_size):
    """Position of the card in its set's numbering (last integer group,
    before any '/total'); None when the number doesn't carry one."""
    if not s:
        return None
    s = str(s).split("/")[0]
    m = re.findall(r"\d+", s)
    if not m:
        return None
    n = int(m[-1])
    return n if 0 < n < 10_000 else None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.match(r"^\s*[-+]?\d+(?:[.,]\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _sval(v):
    if v is None:
        return None
    if isinstance(v, list):
        parts = sorted(str(x).strip() for x in v if x is not None and str(x).strip())
        return ";".join(parts) if parts else None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "null") else None


# ----------------------------------------------------------------------------
# data loading
# ----------------------------------------------------------------------------

def load_cards(game):
    """Every card with art: traits as a DataFrame + the raw attribute dicts."""
    conn = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    rows = conn.execute("SELECT * FROM cards WHERE image_path IS NOT NULL AND image_path <> ''").fetchall()
    conn.close()
    has_rel = "release_date" in cols
    recs, attrs = [], {}
    for r in rows:
        d = dict(r)
        try:
            ca = json.loads(d.get("custom_attributes") or "{}") or {}
        except (TypeError, ValueError):
            ca = {}
        rel = parse_date(d.get("release_date")) if has_rel else None
        if rel is None:
            rel = parse_date(ca.get("releaseDate"))
        # typed stat columns (pokemon / one piece) ride along as attributes
        a = {}
        for k, v in ca.items():
            if k.lower() in JUNK_ATTR:
                continue
            a[k] = v
        for c in cols:
            if c in JUNK_COLS or c in a:
                continue
            a[c] = d.get(c)
        attrs[int(d["product_id"])] = a
        recs.append({
            "pid": int(d["product_id"]), "name": d.get("name") or "",
            "set_name": d.get("set_name") or "?", "rarity": d.get("rarity") or "?",
            "card_type": _sval(d.get("card_type")) or "?",
            "card_number": d.get("card_number"), "rel": rel,
            "nm": d.get("near_mint_price"),
            "desc_len": len(d.get("description") or ""),
        })
    return pd.DataFrame(recs), attrs


def load_points(pc, game):
    pts = collections.defaultdict(list)
    for pid, d, p in pc.execute(
            "SELECT product_id, date, price FROM price_history_unified "
            "WHERE game=? AND grade='ungraded' AND printing='' AND price > 0", (game,)):
        pts[pid].append((d, p))
    for v in pts.values():
        v.sort()
    cov = pc.execute(
        "SELECT MIN(date) FROM price_history_unified WHERE game=? AND grade='ungraded' "
        "AND printing=''", (game,)).fetchone()[0]
    return pts, parse_date(cov)


def launch_price(points, rel, today=None):
    """Median price in [rel+7d, rel+75d] (capped at today); None if no point."""
    lo = (rel + timedelta(days=LABEL_LO_DAYS)).isoformat()
    hi = rel + timedelta(days=LABEL_HI_DAYS)
    if today is not None and today < hi:
        hi = today
    hi = hi.isoformat()
    vals = [p for d, p in points if lo <= d <= hi]
    return float(np.median(vals)) if vals else None


def load_boxes(game):
    """set_name -> (log box price, source) from sealed_prices.py's CSV: the
    PriceCharting launch price where history exists, else TCGplayer's live
    (presale) market price — the only box price an unreleased set has."""
    path = os.path.join(ML, "sealed_boxes.csv")
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        import csv
        for r in csv.DictReader(f):
            if r.get("game") != game:
                continue
            for col, src in (("box_launch", "pc"), ("tcg_box_price", "tcg")):
                v = _num(r.get(col))
                if v and v > 0:
                    out[r["set_name"]] = (math.log(v), src)
                    break
    return out


def box_features(df, boxes):
    """Per-card set box price (log) and its gap to the game's prior sets'
    boxes (same 400-day window the era priors use) — relative demand for
    the set, which is what moves every card in it."""
    sets = (df.groupby("set_name")["rel_ord"].min().reset_index())
    sets["box"] = sets["set_name"].map(lambda s: boxes.get(s, (np.nan, None))[0])
    known = sets[sets["box"].notna()].sort_values("rel_ord")
    rels, logs = known["rel_ord"].tolist(), known["box"].tolist()
    rel_gap = {}
    for s, r, b in zip(sets["set_name"], sets["rel_ord"], sets["box"]):
        if not np.isfinite(b):
            rel_gap[s] = np.nan
            continue
        lo, hi = bisect_left(rels, r - 400), bisect_left(rels, r)
        prior = logs[lo:hi]
        rel_gap[s] = b - float(np.median(prior)) if prior else np.nan
    df["box_log"] = df["set_name"].map(dict(zip(sets["set_name"], sets["box"])))
    df["box_rel"] = df["set_name"].map(rel_gap)
    return df


def load_embeddings(game):
    path = os.path.join(ML, f"{game}_img_emb.npz")
    if not os.path.exists(path):
        return None, None
    z = np.load(path)
    emb = z["emb"].astype(np.float32)
    emb /= np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-6)
    index = {int(p): i for i, p in enumerate(z["product_id"])}
    return emb, index


# ----------------------------------------------------------------------------
# features
# ----------------------------------------------------------------------------

def attribute_frame(attrs, pids):
    """Typed attribute columns: numeric where most values parse, categorical
    where the vocabulary is small, dropped otherwise."""
    keys = collections.Counter()
    for pid in pids:
        keys.update(k for k, v in attrs.get(pid, {}).items() if _sval(v) is not None)
    n = max(1, len(pids))
    out = {}
    for k, cnt in keys.items():
        if cnt < 0.05 * n:
            continue
        vals = [attrs.get(pid, {}).get(k) for pid in pids]
        nums = [_num(v) for v in vals]
        present = [v for v in vals if _sval(v) is not None]
        num_ok = sum(1 for v, x in zip(vals, nums) if _sval(v) is not None and x is not None)
        if present and num_ok >= 0.6 * len(present):
            out[f"a_{k}"] = pd.Series(nums, dtype="float64")
        else:
            svals = [_sval(v) for v in vals]
            if len(set(s for s in svals if s is not None)) <= 60:
                out[f"a_{k}"] = pd.Series(svals, dtype="object")
    return pd.DataFrame(out)


def prior_stats(sorted_pairs, rel, lookback_days=None):
    """(median, max, n) of labels released strictly before `rel` (optionally
    only within lookback_days). sorted_pairs: [(rel_ordinal, label), ...]."""
    if not sorted_pairs or rel is None:
        return (np.nan, np.nan, 0)
    keys = sorted_pairs[0]
    hi = bisect_left(keys, rel.toordinal())
    lo = 0
    if lookback_days is not None:
        lo = bisect_left(keys, rel.toordinal() - lookback_days)
    if hi <= lo:
        return (np.nan, np.nan, 0)
    seg = sorted_pairs[1][lo:hi]
    return (float(np.median(seg)), float(np.max(seg)), hi - lo)


def build_prior_index(df_lab):
    """Per key -> ([release ordinals ascending], [labels in that order])."""
    idx = {}
    for key, grp in df_lab.groupby("key"):
        g = grp.sort_values("rel_ord")
        idx[key] = (g["rel_ord"].tolist(), g["y"].tolist())
    return idx


def build_set_index(df_lab, min_n=8):
    """rarity -> ([set release ordinals asc], [that set's median label]) for
    sets with at least min_n labeled cards of the rarity."""
    idx = {}
    g = (df_lab.groupby(["rarity", "set_name"])
         .agg(rel=("rel_ord", "min"), med=("y", "median"), n=("y", "count"))
         .reset_index())
    g = g[g["n"] >= min_n].sort_values("rel")
    for rar, grp in g.groupby("rarity"):
        idx[rar] = (grp["rel"].tolist(), grp["med"].tolist())
    return idx


def last_set_median(idx, rel):
    if not idx or rel is None:
        return np.nan
    i = bisect_left(idx[0], rel.toordinal())
    return float(idx[1][i - 1]) if i > 0 else np.nan


def knn_prior(emb, index, pool_pids, pool_rel_ord, pool_y, query_pids, query_rel_ord, k=KNN_K):
    """Mean label + mean similarity of each query's k nearest POOL cards
    released before the query (cosine on CLIP). NaN without an embedding."""
    out_y = np.full(len(query_pids), np.nan)
    out_s = np.full(len(query_pids), np.nan)
    if emb is None:
        return out_y, out_s
    pool_rows = np.array([index.get(p, -1) for p in pool_pids])
    keep = pool_rows >= 0
    if keep.sum() < k:
        return out_y, out_s
    P = emb[pool_rows[keep]]
    p_rel = np.asarray(pool_rel_ord)[keep]
    p_y = np.asarray(pool_y)[keep]
    q_rows = np.array([index.get(p, -1) for p in query_pids])
    q_rel = np.asarray(query_rel_ord)
    CH = 1500
    for s in range(0, len(query_pids), CH):
        sl = slice(s, s + CH)
        rows = q_rows[sl]
        ok = rows >= 0
        if not ok.any():
            continue
        sims = emb[rows[ok]] @ P.T                       # (m, n_pool)
        # a query may only look at cards released strictly before it
        mask = p_rel[None, :] >= q_rel[sl][ok][:, None]
        sims[mask] = -np.inf
        kk = min(k, sims.shape[1])
        top = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        ts = np.take_along_axis(sims, top, axis=1)
        ty = p_y[top]
        valid = np.isfinite(ts)
        cnt = valid.sum(axis=1)
        ty = np.where(valid, ty, 0.0)
        ts0 = np.where(valid, ts, 0.0)
        ys = np.where(cnt > 0, ty.sum(axis=1) / np.maximum(cnt, 1), np.nan)
        ss = np.where(cnt > 0, ts0.sum(axis=1) / np.maximum(cnt, 1), np.nan)
        idxs = np.arange(len(query_pids))[sl][ok]
        out_y[idxs] = ys
        out_s[idxs] = ss
    return out_y, out_s


def feature_table(df, attrs, emb, index, pool, pcs):
    """Trait-only features for the rows of `df`, using `pool` (labeled cards
    released before each row) for the character / era / art priors."""
    f = pd.DataFrame(index=df.index)
    f["rarity"] = df["rarity"].astype("object")
    f["card_type"] = df["card_type"].astype("object")
    f["set_size"] = df["set_size"].astype(float)
    f["rarity_in_set"] = df["rarity_in_set"].astype(float)
    f["rarity_share"] = (df["rarity_in_set"] / df["set_size"].clip(lower=1)).astype(float)
    f["num_pos"] = df["num_pos"].astype(float)
    f["num_over"] = df["num_over"].astype(float)
    f["promo_set"] = df["set_name"].str.contains(PROMO_RE).astype(float)
    f["starter_set"] = df["set_name"].str.contains(STARTER_RE).astype(float)
    f["year_frac"] = df["year_frac"].astype(float)
    f["desc_len"] = df["desc_len"].astype(float)
    # set-level demand: the booster box's price and how it compares with the
    # game's recent sets (sealed_prices.py; NaN when no box is known)
    f["box_log"] = df["box_log"].astype(float)
    f["box_rel"] = df["box_rel"].astype(float)

    # character / era priors from strictly earlier labeled cards. The era
    # block is what carries level drift (Lorcana commons launched at $1.20 in
    # 2024 and $0.05 in 2026): the same rarity's recent median, the whole
    # game's recent median, and the same rarity in the single latest set.
    root_idx, rar_idx, all_idx, rar_set_idx = (pool["root_idx"], pool["rar_idx"],
                                               pool["all_idx"], pool["rar_set_idx"])
    rm, rx, rn, em, en, am, lm = [], [], [], [], [], [], []
    for root, rar, rel in zip(df["root"], df["rarity"], df["rel"]):
        m, x, n = prior_stats(root_idx.get(root), rel)
        rm.append(m); rx.append(x); rn.append(n)
        m2, _, n2 = prior_stats(rar_idx.get(rar), rel, lookback_days=ERA_LOOKBACK_DAYS)
        em.append(m2); en.append(n2)
        m3, _, _ = prior_stats(all_idx, rel, lookback_days=ERA_LOOKBACK_DAYS)
        am.append(m3)
        lm.append(last_set_median(rar_set_idx.get(rar), rel))
    f["root_prior_med"], f["root_prior_max"], f["root_prior_n"] = rm, rx, rn
    f["era_rarity_med"], f["era_rarity_n"] = em, en
    f["era_all_med"], f["era_rarity_last"] = am, lm

    # art: PCA block + nearest earlier cards' launch prices
    rows = np.array([index.get(p, -1) if index else -1 for p in df["pid"]])
    pc_block = np.full((len(df), pcs.shape[1] if pcs is not None else 0), np.nan)
    if pcs is not None:
        ok = rows >= 0
        pc_block[ok] = pcs[rows[ok]]
    for i in range(pc_block.shape[1]):
        f[f"img{i}"] = pc_block[:, i]
    ky, ks = knn_prior(emb, index, pool["pids"], pool["rel_ord"], pool["y"],
                       df["pid"].tolist(), df["rel_ord"].tolist())
    f["art_knn_price"], f["art_knn_sim"] = ky, ks

    a = attribute_frame(attrs, df["pid"].tolist())
    a.index = df.index
    for c in a.columns:
        f[c] = a[c]
    return f


def to_matrix(f, cats=None):
    """Object columns -> pandas categoricals with a shared, capped vocabulary
    (sklearn >= 1.4 reads them via categorical_features='from_dtype')."""
    f = f.copy()
    if cats is None:
        cats = {}
        for c in f.columns:
            if f[c].dtype == object:
                keep = f[c].value_counts().head(MAX_CAT).index.tolist()
                cats[c] = keep
    for c, keep in cats.items():
        if c not in f.columns:
            f[c] = None
        s = f[c].astype("object")
        s = s.where(s.isin(keep) | s.isna(), "OTHER")
        f[c] = pd.Categorical(s, categories=list(keep) + ["OTHER"])
    # any leftover object column (unseen at fit) is dropped
    drop = [c for c in f.columns if f[c].dtype == object]
    return f.drop(columns=drop), cats


# ----------------------------------------------------------------------------
# model
# ----------------------------------------------------------------------------

HGB_PARAMS = dict(max_iter=450, learning_rate=0.05, max_leaf_nodes=31,
                  min_samples_leaf=20, l2_regularization=1.0,
                  categorical_features="from_dtype", random_state=RNG)


def usable_columns(X):
    """Columns with at least two distinct non-missing values (an all-NaN or
    constant column crashes HGB's binning on small games)."""
    return [c for c in X.columns if X[c].nunique(dropna=True) >= 2]


def base_level(X, y_ref):
    """The naive anchor every estimate starts from: the same rarity's median
    launch price in the game's recent sets (falls back to the game's recent
    median, then to the training median)."""
    b = X["era_rarity_med"].to_numpy(dtype=float)
    alt = X["era_all_med"].to_numpy(dtype=float)
    b = np.where(np.isfinite(b), b, alt)
    return np.where(np.isfinite(b), b, float(np.nanmedian(y_ref)))


class Ensemble:
    """Point model = mean of a raw-level learner and a deviation-from-baseline
    learner (both median regressors). Neither wins everywhere on its own —
    the raw learner carries games whose rarity levels jump between sets, the
    residual learner carries games with steady level drift — and the average
    is never far from the better of the two."""

    def __init__(self, X, y, w):
        from sklearn.ensemble import HistGradientBoostingRegressor as HGB
        self.cols = usable_columns(X)
        Xf = X[self.cols]
        self.y_ref = y
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.raw = HGB(loss="absolute_error", **HGB_PARAMS).fit(Xf, y, sample_weight=w)
            self.res = HGB(loss="absolute_error", **HGB_PARAMS).fit(
                Xf, y - base_level(X, y), sample_weight=w)

    def predict(self, X):
        Xf = X.reindex(columns=self.cols)
        return 0.5 * (self.raw.predict(Xf) + base_level(X, self.y_ref) + self.res.predict(Xf))


MAX_HALF_WIDTH = math.log(6.0)          # a range wider than ÷6…×6 says nothing useful
BAND_MIN_N = 40                         # rows a stratum needs for its own band


def band_from(res):
    """10th/90th residual quantiles -> additive log band, floored and capped
    on each side. Residuals are y - prediction, so the band keeps whatever
    skew the misses actually have (launch misses skew upward: chase cards
    blow out)."""
    q10, q90 = np.quantile(res, [0.10, 0.90])
    lo = float(np.clip(q10, -MAX_HALF_WIDTH, -MIN_HALF_WIDTH))
    hi = float(np.clip(q90, MIN_HALF_WIDTH, MAX_HALF_WIDTH))
    return lo, hi


def band_strata(p, res):
    """Band per predicted-price tercile: misses on the cheap end of a set are
    far wider (in log terms) than on its chase cards, and one global band
    would hand a $1,700 estimate a $25,000 ceiling. Returns (edges, bands)
    with a global fallback for thin strata."""
    glob = band_from(res)
    if len(p) < 3 * BAND_MIN_N:
        return [], [glob]
    edges = [float(x) for x in np.quantile(p, [1 / 3, 2 / 3])]
    bands = []
    for s in range(3):
        m = np.searchsorted(edges, p) == s
        bands.append(band_from(res[m]) if m.sum() >= BAND_MIN_N else glob)
    return edges, bands


def band_for(p, edges, bands):
    idx = np.searchsorted(edges, p) if edges else np.zeros(len(p), dtype=int)
    lo = np.array([bands[i][0] for i in idx])
    hi = np.array([bands[i][1] for i in idx])
    return p + lo, p + hi


def inner_band(tr, attrs, emb, index, pcs):
    """Band from an INNER temporal split of the training rows (fit on the
    older 80% of releases, predict the newest 20%). Used to measure honest
    holdout coverage; the production band comes from the holdout itself.
    A level SHIFT from the same split was tried (2026-10-10) and rejected: it
    tracked one recent set's quirks and made three of five games far worse."""
    cut2 = float(np.quantile(tr["rel_ord"], INNER_SPLIT_Q))
    a, b = tr[tr["rel_ord"] < cut2], tr[tr["rel_ord"] >= cut2]
    if len(a) < 150 or len(b) < 40:
        return [], [(-1.2, 1.2)]
    pool = pool_from(a)
    Xa, cats = to_matrix(feature_table(a, attrs, emb, index, pool, pcs))
    Xb, _ = to_matrix(feature_table(b, attrs, emb, index, pool, pcs), cats)
    Xb = Xb.reindex(columns=Xa.columns)
    model = Ensemble(Xa, a["y"].to_numpy(), price_weight(a["y"].to_numpy()))
    pb = model.predict(Xb)
    return band_strata(pb, b["y"].to_numpy() - pb)


def price_weight(y):
    p = np.exp(y)
    return np.clip(2.0 ** (np.log10(np.maximum(p, 0.01)) - 1.0), 0.5, 3.0)


def spearman(a, b):
    if len(a) < 5:
        return float("nan")
    return float(pd.Series(a).corr(pd.Series(b), method="spearman"))


# ----------------------------------------------------------------------------
# per game
# ----------------------------------------------------------------------------

def prepare_game(game, pc, today):
    df, attrs = load_cards(game)
    if df.empty:
        return None
    pts, cov = load_points(pc, game)
    df = df[df["rel"].notna()].copy()
    if df.empty or cov is None:
        return None
    df["rel_ord"] = df["rel"].map(lambda d: d.toordinal())
    df["year_frac"] = df["rel"].map(lambda d: d.year + (d.timetuple().tm_yday - 1) / 365.0)
    df["root"] = df["name"].map(name_root)
    sizes = df.groupby("set_name")["pid"].transform("count")
    df["set_size"] = sizes
    df["rarity_in_set"] = df.groupby(["set_name", "rarity"])["pid"].transform("count")
    # Scarcity rank of the card's rarity within its set (0 = rarest, 1 = most
    # common): a game-agnostic stand-in for rarity names in the cross-game pool.
    uniq = (df.assign(share=df["rarity_in_set"] / df["set_size"].clip(lower=1))
            .drop_duplicates(["set_name", "rarity"])[["set_name", "rarity", "share"]])
    uniq["rank"] = uniq.groupby("set_name")["share"].rank(method="dense") - 1
    uniq["k"] = uniq.groupby("set_name")["rank"].transform("max")
    uniq["rarity_rank"] = np.where(uniq["k"] > 0, uniq["rank"] / uniq["k"].replace(0, 1), 0.5)
    df = df.merge(uniq[["set_name", "rarity", "rarity_rank"]], on=["set_name", "rarity"], how="left")
    nums = [card_num(n, s) for n, s in zip(df["card_number"], df["set_size"])]
    df["num_pos"] = nums
    maxnum = df.assign(n=nums).groupby("set_name")["n"].transform("max")
    df["num_over"] = [(n / m) if (n is not None and m and m > 0) else np.nan
                      for n, m in zip(nums, maxnum)]
    # Booster-box features are OFF by default (2026-10-10 A/B on the held-out
    # sets: Pokémon slightly better, Magic/Yu-Gi-Oh slightly worse, Lorcana
    # flat — all within noise, and 2-3 holdout sets cannot validate a per-SET
    # feature). sealed_prices.py keeps collecting so the live presale box
    # price can be re-tested once a few pre-release cohorts have graded.
    boxes = load_boxes(game) if os.environ.get("TCG_PRE_BOX", "0") == "1" else {}
    df = box_features(df, boxes)
    if boxes:
        print(f"[{game}] booster-box prices for {len(boxes)} sets "
              f"({sum(1 for v in boxes.values() if v[1] == 'tcg')} live TCGplayer)")

    # labels: launch price, only where the window could have been observed
    cutoff = cov + timedelta(days=COVERAGE_LEAD_DAYS)
    ys = []
    for pid, rel in zip(df["pid"], df["rel"]):
        if rel < cutoff or rel + timedelta(days=LABEL_LO_DAYS) > today:
            ys.append(np.nan)
            continue
        lp = launch_price(pts.get(pid, []), rel, today)
        ys.append(math.log(lp) if lp and lp > 0 else np.nan)
    df["y"] = ys

    # candidates: art, no market price, upcoming or just released
    look = today - timedelta(days=CANDIDATE_LOOKBACK_DAYS)
    cand = df[(df["nm"].isna()) & (df["rel"] >= look)].copy()
    # a card that already has ANY recent unified price belongs to the regular/launch models
    recent = (today - timedelta(days=60)).isoformat()
    cand = cand[[not any(d >= recent for d, _ in pts.get(pid, [])) for pid in cand["pid"]]]
    return df, attrs, cand, pts


def pool_from(df_lab):
    lab = df_lab.copy()
    lab["key"] = lab["root"]
    root_idx = build_prior_index(lab)
    lab["key"] = lab["rarity"]
    rar_idx = build_prior_index(lab)
    lab["key"] = "all"
    all_idx = build_prior_index(lab).get("all")
    return {"pids": lab["pid"].tolist(), "rel_ord": lab["rel_ord"].tolist(),
            "y": lab["y"].tolist(), "root_idx": root_idx, "rar_idx": rar_idx,
            "all_idx": all_idx, "rar_set_idx": build_set_index(lab)}


def pick_holdout(df_lab):
    """Newest labeled sets (big enough to mean something), oldest-first cutoff."""
    sets = (df_lab.groupby("set_name")
            .agg(n=("pid", "count"), rel=("rel_ord", "min"))
            .query("n >= @SET_MIN_LABELED")
            .sort_values("rel", ascending=False))
    chosen, n = [], 0
    for s, row in sets.iterrows():
        chosen.append(s)
        n += int(row["n"])
        if len(chosen) >= HOLDOUT_MAX_SETS or (n >= HOLDOUT_MIN_CARDS and len(chosen) >= HOLDOUT_MIN_SETS):
            break
    if len(chosen) < HOLDOUT_MIN_SETS:
        return [], None
    cut = int(sets.loc[chosen, "rel"].min())
    return chosen, cut


# ----------------------------------------------------------------------------
# cross-game pool (new games)
# ----------------------------------------------------------------------------

def universal_frame(df):
    f = pd.DataFrame(index=df.index)
    for c in ("set_size", "rarity_in_set", "rarity_rank", "num_pos", "num_over", "year_frac", "desc_len"):
        f[c] = df[c].astype(float)
    f["rarity_share"] = (df["rarity_in_set"] / df["set_size"].clip(lower=1)).astype(float)
    f["promo_set"] = df["set_name"].str.contains(PROMO_RE).astype(float)
    f["starter_set"] = df["set_name"].str.contains(STARTER_RE).astype(float)
    return f


def query_matrix(df, emb, index, dim=512):
    """Embedding rows aligned to df (zeros + flag where a card has none)."""
    rows = np.array([index.get(int(pid), -1) if index else -1 for pid in df["pid"]])
    ok = rows >= 0
    q = np.zeros((len(df), emb.shape[1] if emb is not None else dim), dtype=np.float32)
    if emb is not None and ok.any():
        q[ok] = emb[rows[ok]]
    return q, ok


def pooled_rows(prep, exclude=None):
    """The cross-game labeled pool: each game's newest POOLED_CAP launches
    (so Magic's 23k don't drown the rest) with their embeddings."""
    parts, embs = [], []
    for g, p in prep.items():
        if p is None or g == exclude or p["emb"] is None:
            continue
        lab = p["df"][p["df"]["y"].notna()].sort_values("rel_ord").tail(POOLED_CAP)
        q, ok = query_matrix(lab, p["emb"], p["index"])
        if not ok.any():
            continue
        parts.append(lab[ok].assign(game=g))
        embs.append(q[ok])
    if not parts:
        return None, None
    return pd.concat(parts, ignore_index=True), np.vstack(embs)


def knn_cross(qE, q_rel, pE, p_rel, p_y, k=KNN_K):
    """knn_prior with explicit matrices: mean label + similarity of each
    query's k nearest pool cards released strictly before it."""
    out_y, out_s = np.full(len(qE), np.nan), np.full(len(qE), np.nan)
    if pE is None or len(pE) < k:
        return out_y, out_s
    CH = 1500
    for s in range(0, len(qE), CH):
        sims = qE[s:s + CH] @ pE.T
        sims[p_rel[None, :] >= q_rel[s:s + CH][:, None]] = -np.inf
        kk = min(k, sims.shape[1])
        top = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        ts, ty = np.take_along_axis(sims, top, axis=1), p_y[top]
        valid = np.isfinite(ts)
        cnt = valid.sum(axis=1)
        out_y[s:s + CH] = np.where(cnt > 0, np.where(valid, ty, 0.0).sum(1) / np.maximum(cnt, 1), np.nan)
        out_s[s:s + CH] = np.where(cnt > 0, np.where(valid, ts, 0.0).sum(1) / np.maximum(cnt, 1), np.nan)
    return out_y, out_s


def pooled_features(df, qE, ok, pool, pE):
    f = universal_frame(df)
    ky, ks = knn_cross(qE, df["rel_ord"].to_numpy(), pE, pool["rel_ord"].to_numpy(), pool["y"].to_numpy())
    ky[~ok], ks[~ok] = np.nan, np.nan
    f["art_knn_price"], f["art_knn_sim"] = ky, ks
    return f[UNIVERSAL]


def fit_pooled(pool, pE):
    from sklearn.ensemble import HistGradientBoostingRegressor as HGB
    X = pooled_features(pool, pE, np.ones(len(pool), dtype=bool), pool, pE)
    y = pool["y"].to_numpy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return HGB(loss="absolute_error", **HGB_PARAMS).fit(X, y, sample_weight=price_weight(y))


def validate_pooled(prep, out, today, dry_run=False):
    """Leave-one-game-out over the youngest games: train the pool without a
    game, predict every one of its labeled launches. That is exactly what a
    brand-new game faces. Returns (edges, bands) for the live band plus the
    headline numbers quoted in reasons."""
    labeled = {g: int(p["df"]["y"].notna().sum()) for g, p in prep.items() if p is not None}
    games = [g for g, n in sorted(labeled.items(), key=lambda kv: kv[1])
             if n >= 2 * POOLED_CALIB_MIN][:POOLED_HOLDOUT_GAMES]
    allp, ally = [], []
    for g in games:
        pool, pE = pooled_rows(prep, exclude=g)
        if pool is None:
            continue
        model = fit_pooled(pool, pE)
        lab_g = prep[g]["df"][prep[g]["df"]["y"].notna()].sort_values("rel_ord")
        # the game's EARLIEST launches calibrate the level (what a new game
        # would have after its first set); everything later is the test
        cal, te = lab_g.iloc[:POOLED_CALIB_MIN], lab_g.iloc[POOLED_CALIB_MIN:]
        qc, okc = query_matrix(cal, prep[g]["emb"], prep[g]["index"])
        shift = float(np.median(cal["y"].to_numpy() - model.predict(pooled_features(cal, qc, okc, pool, pE))))
        q, ok = query_matrix(te, prep[g]["emb"], prep[g]["index"])
        p_raw = model.predict(pooled_features(te, q, ok, pool, pE))
        p = p_raw + shift
        y = te["y"].to_numpy()
        err, err_raw = np.abs(p - y), np.abs(p_raw - y)
        print(f"[pooled ⟂ {g}] {len(te):,} launches from {len(pool):,} other-game launches, level set by "
              f"its first {len(cal)} ({shift:+.2f}): typical miss {100 * (math.exp(np.median(err)) - 1):.0f}% "
              f"(uncalibrated {100 * (math.exp(np.median(err_raw)) - 1):.0f}%) · within ±50%: "
              f"{100 * np.mean(err <= math.log(1.5)):.0f}% · rank corr {spearman(p, y):.2f} · bias {np.mean(p - y):+.2f}")
        if not dry_run:
            store_validation(out, f"pooled:{g}", today, {
                "n_train": int(len(pool)), "n_test": int(len(te)), "sets": [g],
                "mae_log": float(err.mean()), "med_log": float(np.median(err)),
                "within50": float(np.mean(err <= math.log(1.5))), "within2x": float(np.mean(err <= math.log(2.0))),
                "coverage": float("nan"), "spearman": spearman(p, y),
                "baseline_mae_log": float("nan"), "baseline_spearman": float("nan"),
                "band": band_strata(p, y - p)}, version=POOLED_VERSION)
        allp.append(p); ally.append(y)
    if not allp:
        return None
    p, y = np.concatenate(allp), np.concatenate(ally)
    err = np.abs(p - y)
    summary = {"n_games": len(games), "games": games, "med_log": float(np.median(err)),
               "within50": float(np.mean(err <= math.log(1.5))), "spearman": spearman(p, y),
               "band": band_strata(p, y - p)}
    print(f"[pooled] across {len(games)} held-out games: typical miss {100 * (math.exp(summary['med_log']) - 1):.0f}% "
          f"· within ±50%: {100 * summary['within50']:.0f}% · rank corr {summary['spearman']:.2f}")
    return summary


def run_pooled_game(game, prep, pooled, out, today, dry_run=False):
    """Estimates for a game whose own launch history is too thin: the
    cross-game model, clearly labeled as such in every reason."""
    p_g = prep[game]
    cand, n_lab = p_g["cand"], int(p_g["df"]["y"].notna().sum())
    if cand.empty:
        return 0
    if n_lab < POOLED_CALIB_MIN:
        print(f"[{game}] only {n_lab} priced launches of its own — fewer than the {POOLED_CALIB_MIN} "
              f"needed to set the cross-game model's price level; no estimates yet")
        return 0
    pool, pE = pooled_rows(prep, exclude=game)
    if pool is None:
        return 0
    model = fit_pooled(pool, pE)
    own = p_g["df"][p_g["df"]["y"].notna()]
    qo, oko = query_matrix(own, p_g["emb"], p_g["index"])
    shift = float(np.median(own["y"].to_numpy() - model.predict(pooled_features(own, qo, oko, pool, pE))))
    q, ok = query_matrix(cand, p_g["emb"], p_g["index"])
    X = pooled_features(cand, q, ok, pool, pE)
    p = model.predict(X) + shift
    print(f"[{game}] cross-game level calibrated on its {n_lab} own launches: {shift:+.2f} log")
    val = pooled["summary"]
    edges, bands = val["band"] if val else ([], [(-1.5, 1.5)])
    lo, hi = band_for(p, edges, bands)
    miss = round(100 * (math.exp(val["med_log"]) - 1)) if val else None
    within = round(100 * val["within50"]) if val else None
    label = GAMES[game]["label"]
    n_games = len({g for g in pool["game"]})
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for i, (_, c) in enumerate(cand.iterrows()):
        x = X.iloc[i]
        bits = [f"{c['rarity']} in a {int(c['set_size'])}-card set ({int(c['rarity_in_set'])} cards at that rarity)"]
        if np.isfinite(x["art_knn_sim"]) and x["art_knn_sim"] >= 0.7:
            bits.append(f"the cards its art most resembles, across games, launched near ${math.exp(x['art_knn_price']):,.2f}")
        txt = (f"Cross-game estimate: {label} has only {n_lab} priced launches of its own, so this "
               f"comes from {len(pool):,} launches across {n_games} other games — " + "; ".join(bits) + ". ")
        if val:
            txt += (f"Tested by holding out whole games, the method's typical miss was about {miss}%, "
                    f"with {within}% of cards within ±50% — a rough first read. ")
        txt += "It updates nightly and hands off to this game's own model once it has enough launches."
        pred = max(0.10, math.exp(p[i]))
        rows.append((game, int(c["pid"]), "", _iso(c["rel"]), today.isoformat(), round(pred, 2),
                     round(max(0.05, math.exp(lo[i])), 2), round(max(pred, math.exp(hi[i])), 2), "low",
                     txt, POOLED_VERSION, int(len(pool)), val["n_games"] if val else 0, miss, within, now_iso))
    if dry_run:
        for r in sorted(rows, key=lambda r: -r[5])[:6]:
            nm = cand.loc[cand["pid"] == r[1], "name"].iloc[0]
            print(f"    {nm[:38]:38s} {r[3]}  ${r[5]:>8.2f}  [{r[6]:.2f}–{r[7]:.2f}]  (pooled)")
        print(f"[{game}] dry run: {len(rows)} cross-game estimates (not written)")
        return len(rows)
    write_rows(out, game, rows)
    print(f"[{game}] {len(rows)} cross-game pre-release estimates written (own launch history: {n_lab})")
    return len(rows)


def write_rows(out, game, rows):
    out.execute("DELETE FROM prerelease_estimates WHERE game=?", (game,))
    out.executemany(
        "INSERT OR REPLACE INTO prerelease_estimates (game, product_id, printing, release_date, "
        "as_of, predicted, low, high, confidence, reason, model_version, n_train, val_sets, "
        "val_miss_pct, val_within50, scored_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    out.executemany(
        "INSERT OR IGNORE INTO prerelease_archive (game, product_id, printing, release_date, "
        "as_of, predicted, low, high, model_version, scored_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[10], r[15]) for r in rows])
    out.commit()


def run_game(game, prep, pc, today, out, dry_run=False):
    p_g = prep.get(game)
    if p_g is None:
        print(f"[{game}] no cards with release dates — skipped")
        return 0
    df, attrs, cand, emb, index = p_g["df"], p_g["attrs"], p_g["cand"], p_g["emb"], p_g["index"]
    lab = df[df["y"].notna()].copy()
    print(f"[{game}] {len(df):,} cards with art+release · {len(lab):,} labeled launch prices "
          f"· {len(cand):,} unpriced pre-release candidates")
    if len(lab) < MIN_LABELED:
        print(f"[{game}] fewer than {MIN_LABELED} labeled launches of its own — cross-game model")
        return None   # caller routes to the pooled model
    pcs = None
    if emb is not None:
        from sklearn.decomposition import PCA
        pcs = PCA(n_components=min(IMG_PCA, emb.shape[1]), random_state=RNG).fit_transform(emb)

    # ---- validation: newest sets predicted from older ones only -------------
    # Everything quoted to users comes from here: the model is fit on releases
    # before the holdout sets, the band it is judged with comes from an inner
    # split of that same training data, and nothing is tuned on the holdout.
    val = None
    holdout, cut = pick_holdout(lab)
    if holdout and (lab["rel_ord"] < cut).sum() >= MIN_TRAIN_FOR_VALIDATION:
        tr = lab[lab["rel_ord"] < cut]
        te = lab[lab["set_name"].isin(holdout)]
        pool = pool_from(tr)
        Xtr, cats = to_matrix(feature_table(tr, attrs, emb, index, pool, pcs))
        Xte, _ = to_matrix(feature_table(te, attrs, emb, index, pool, pcs), cats)
        Xte = Xte.reindex(columns=Xtr.columns)
        ytr, y = tr["y"].to_numpy(), te["y"].to_numpy()
        p = Ensemble(Xtr, ytr, price_weight(ytr)).predict(Xte)
        i_edges, i_bands = inner_band(tr, attrs, emb, index, pcs)
        lo_i, hi_i = band_for(p, i_edges, i_bands)
        err = np.abs(p - y)
        base = base_level(Xte, ytr)
        res = y - p
        big = np.exp(y) >= 5
        val = {
            "n_train": int(len(tr)), "n_test": int(len(te)), "sets": holdout,
            "mae_log": float(err.mean()), "med_log": float(np.median(err)),
            "within50": float(np.mean(err <= math.log(1.5))),
            "within2x": float(np.mean(err <= math.log(2.0))),
            "coverage": float(np.mean((y >= lo_i) & (y <= hi_i))),
            "spearman": spearman(p, y),
            "spearman_5": spearman(p[big], y[big]) if big.sum() >= 10 else float("nan"),
            "baseline_mae_log": float(np.mean(np.abs(base - y))),
            "baseline_spearman": spearman(base, y),
            # production band: the holdout IS the newest era, so its residual
            # spread (per predicted-price tercile) is the most relevant
            # calibration for the next set
            "band": band_strata(p, res),
        }
        bands_txt = " ".join(f"[{lo:+.2f},{hi:+.2f}]" for lo, hi in val["band"][1])
        print(f"[{game}] holdout {len(te):,} cards / {len(holdout)} sets ({', '.join(holdout)}): "
              f"typical miss {100 * (math.exp(val['med_log']) - 1):.0f}% (mean log {val['mae_log']:.3f} "
              f"vs rarity-era baseline {val['baseline_mae_log']:.3f}) · within ±50%: "
              f"{100 * val['within50']:.0f}% · rank corr {val['spearman']:.2f} "
              f"(baseline {val['baseline_spearman']:.2f}; $5+ cards {val['spearman_5']:.2f}) · inner band "
              f"covered {100 * val['coverage']:.0f}% · live bands by tercile {bands_txt}")
    else:
        print(f"[{game}] not enough history for a held-out set validation yet")

    if cand.empty:
        if val and not dry_run:
            store_validation(out, game, today, val)
        return 0

    # ---- final fit on everything labeled, predict the candidates ------------
    pool = pool_from(lab)
    Xall, cats = to_matrix(feature_table(lab, attrs, emb, index, pool, pcs))
    Xc, _ = to_matrix(feature_table(cand, attrs, emb, index, pool, pcs), cats)
    Xc = Xc.reindex(columns=Xall.columns)
    yall = lab["y"].to_numpy()
    p = Ensemble(Xall, yall, price_weight(yall)).predict(Xc)
    edges, bands = val["band"] if val else inner_band(lab, attrs, emb, index, pcs)
    lo, hi = band_for(p, edges, bands)

    miss_pct = round(100 * (math.exp(val["med_log"]) - 1)) if val else None
    within = round(100 * val["within50"]) if val else None
    conf = "med" if (val and val["within50"] >= 0.65 and val["spearman"] >= 0.6) else "low"
    label = GAMES[game]["label"]
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for i, (_, c) in enumerate(cand.iterrows()):
        pred = max(0.10, math.exp(p[i]))
        low = max(0.05, math.exp(lo[i]))
        high = max(pred, math.exp(hi[i]))
        rows.append((game, int(c["pid"]), "", _iso(c["rel"]), today.isoformat(),
                     round(pred, 2), round(low, 2), round(high, 2), conf,
                     reason_text(c, Xc.iloc[i], label, len(lab), val, miss_pct, within),
                     MODEL_VERSION, int(len(lab)), len(val["sets"]) if val else 0,
                     miss_pct, within, now_iso))
    if dry_run:
        show = sorted(rows, key=lambda r: -r[5])[:8]
        for r in show:
            nm = cand.loc[cand["pid"] == r[1], "name"].iloc[0]
            print(f"    {nm[:38]:38s} {r[3]}  ${r[5]:>8.2f}  [{r[6]:.2f}–{r[7]:.2f}]")
        print(f"[{game}] dry run: {len(rows)} estimates (not written)")
        return len(rows)

    write_rows(out, game, rows)
    if val:
        store_validation(out, game, today, val)
    print(f"[{game}] {len(rows)} pre-release estimates written "
          f"({sum(1 for r in rows if r[3] > today.isoformat())} for sets not yet released)")
    return len(rows)


def reason_text(c, x, label, n_train, val, miss_pct, within):
    bits = [f"{c['rarity']} in a {int(c['set_size'])}-card set"]
    rn = x.get("root_prior_n")
    if rn is not None and np.isfinite(rn) and rn >= 3 and np.isfinite(x.get("root_prior_med", np.nan)):
        who = str(c["root"]).title()
        bits.append(f"earlier {who} cards launched around ${math.exp(x['root_prior_med']):,.2f}")
    if np.isfinite(x.get("era_rarity_med", np.nan)) and (x.get("era_rarity_n") or 0) >= 10:
        bits.append(f"recent {label} {c['rarity']}s launched near ${math.exp(x['era_rarity_med']):,.2f}")
    if np.isfinite(x.get("art_knn_sim", np.nan)) and x["art_knn_sim"] >= 0.72 \
            and np.isfinite(x.get("art_knn_price", np.nan)):
        bits.append(f"the cards its art most resembles launched near ${math.exp(x['art_knn_price']):,.2f}")
    if np.isfinite(x.get("box_log", np.nan)):
        gap = x.get("box_rel", np.nan)
        how = ("" if not np.isfinite(gap) or abs(gap) < 0.1
               else f", {abs(gap) * 100:.0f}% {'above' if gap > 0 else 'below'} the game's recent sets")
        bits.append(f"the set's booster box trades around ${math.exp(x['box_log']):,.0f}{how}")
    txt = ("Pre-release estimate from the card itself (no sales yet): " + "; ".join(bits) + ". ")
    if val:
        txt += (f"Tested on the last {len(val['sets'])} {label} sets, this method's typical miss "
                f"was about {miss_pct}%, with {within}% of cards landing within ±50%. ")
    else:
        txt += "This game's history is still too short to validate the method against past launches. "
    txt += "It updates nightly and hands off to the regular forecast once the card starts trading."
    return txt


# ----------------------------------------------------------------------------
# storage + grading
# ----------------------------------------------------------------------------

def ensure_tables(out):
    out.executescript("""
    CREATE TABLE IF NOT EXISTS prerelease_estimates (
        game TEXT NOT NULL, product_id INTEGER NOT NULL, printing TEXT NOT NULL DEFAULT '',
        release_date TEXT, as_of TEXT NOT NULL, predicted REAL NOT NULL, low REAL, high REAL,
        confidence TEXT, reason TEXT, model_version TEXT, n_train INTEGER, val_sets INTEGER,
        val_miss_pct REAL, val_within50 REAL, scored_at TEXT,
        PRIMARY KEY (game, product_id, printing));
    CREATE TABLE IF NOT EXISTS prerelease_archive (
        game TEXT NOT NULL, product_id INTEGER NOT NULL, printing TEXT NOT NULL DEFAULT '',
        release_date TEXT, as_of TEXT NOT NULL, predicted REAL NOT NULL, low REAL, high REAL,
        model_version TEXT, scored_at TEXT,
        realized_price REAL, realized_at TEXT, graded_at TEXT,
        PRIMARY KEY (game, product_id, printing));
    CREATE TABLE IF NOT EXISTS prerelease_validation (
        game TEXT NOT NULL, as_of TEXT NOT NULL, n_train INTEGER, n_test INTEGER, test_sets TEXT,
        mae_log REAL, med_log REAL, within50 REAL, within2x REAL, coverage REAL, spearman REAL,
        baseline_mae_log REAL, baseline_spearman REAL, band_lo REAL, band_hi REAL,
        model_version TEXT,
        PRIMARY KEY (game, as_of));
    """)


def store_validation(out, game, today, v, version=MODEL_VERSION):
    out.execute(
        "INSERT OR REPLACE INTO prerelease_validation (game, as_of, n_train, n_test, test_sets, "
        "mae_log, med_log, within50, within2x, coverage, spearman, baseline_mae_log, "
        "baseline_spearman, band_lo, band_hi, model_version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (game, today.isoformat(), v["n_train"], v["n_test"], json.dumps(v["sets"]),
         v["mae_log"], v["med_log"], v["within50"], v["within2x"], v["coverage"], v["spearman"],
         v["baseline_mae_log"], v["baseline_spearman"],
         min(b[0] for b in v["band"][1]), max(b[1] for b in v["band"][1]), version))


def grade_archive(out, pc, today):
    """Fill realized_price on archived estimates whose card has now traded:
    same label definition (median price 1-10 weeks after release), once at
    least 45 days have passed so the window is reasonably populated."""
    pending = out.execute(
        "SELECT game, product_id, release_date FROM prerelease_archive "
        "WHERE realized_price IS NULL AND release_date IS NOT NULL").fetchall()
    due = [(g, pid, parse_date(rd)) for g, pid, rd in pending
           if parse_date(rd) and today >= parse_date(rd) + timedelta(days=45)]
    if not due:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updates = []
    by_game = collections.defaultdict(list)
    for g, pid, rd in due:
        by_game[g].append((pid, rd))
    for g, items in by_game.items():
        pts, _ = load_points(pc, g)
        for pid, rd in items:
            lp = launch_price(pts.get(pid, []), rd, today)
            if lp:
                hi = min(rd + timedelta(days=LABEL_HI_DAYS), today)
                updates.append((round(lp, 2), hi.isoformat(), now_iso, g, pid))
    out.executemany(
        "UPDATE prerelease_archive SET realized_price=?, realized_at=?, graded_at=? "
        "WHERE game=? AND product_id=? AND printing=''", updates)
    out.commit()
    rec = out.execute(
        "SELECT COUNT(*), AVG(ABS(LN(realized_price / predicted))), "
        "AVG(ABS(LN(realized_price / predicted)) <= LN(1.5)), "
        "AVG(realized_price BETWEEN low AND high) FROM prerelease_archive "
        "WHERE realized_price IS NOT NULL AND predicted > 0").fetchone()
    if rec and rec[0]:
        print(f"prerelease track record: {rec[0]} graded · mean log miss {rec[1]:.3f} · "
              f"within ±50%: {100 * rec[2]:.0f}% · band coverage {100 * rec[3]:.0f}%")
    return len(updates)


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Trait-only launch-price estimates for unpriced cards.")
    ap.add_argument("--game", action="append", help="limit to game(s) (default: all priced)")
    ap.add_argument("--db", default=OUT_DB, help="predictions db to write (default: production copy)")
    ap.add_argument("--dry-run", action="store_true", help="validate + print, write nothing")
    ap.add_argument("--force", action="store_true", help="run even without TCG_FC_PRERELEASE=1")
    ap.add_argument("--validate-pooled", action="store_true",
                    help="also run the leave-one-game-out validation of the cross-game model")
    args = ap.parse_args()
    if os.environ.get("TCG_FC_PRERELEASE") != "1" and not args.force:
        print("forecast_prerelease: TCG_FC_PRERELEASE not set — skipped")
        return
    today = datetime.now(timezone.utc).date()
    pc = sqlite3.connect(f"file:{PC_DB}?mode=ro", uri=True, timeout=120)
    out = sqlite3.connect(args.db, timeout=120)
    if not args.dry_run:
        ensure_tables(out)

    # Prepare every game first: a game with too little history of its own
    # borrows the others' launches (pooled model), so the pool needs them all.
    games = args.game or priced_games()
    prep = {}
    for g in set(games) | set(priced_games()):
        try:
            p = prepare_game(g, pc, today)
            if p is None:
                prep[g] = None
                continue
            df, attrs, cand, pts = p
            emb, index = load_embeddings(g)
            prep[g] = {"df": df, "attrs": attrs, "cand": cand, "emb": emb, "index": index}
        except Exception as e:                      # noqa: BLE001
            print(f"[{g}] prepare failed: {type(e).__name__}: {e}", file=sys.stderr)
            prep[g] = None

    total, needs_pool = 0, []
    for g in games:
        try:
            n = run_game(g, prep, pc, today, out, dry_run=args.dry_run)
            if n is None:
                needs_pool.append(g)
            else:
                total += n
        except Exception as e:                      # noqa: BLE001 — one game never sinks the rest
            print(f"[{g}] prerelease failed: {type(e).__name__}: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()

    # Cross-game fallback is OFF by default (TCG_PRE_POOLED=1 enables it).
    # Leave-one-game-out verdict 2026-10-10: typical miss ~250% (digimon 500%+,
    # lorcana 147%), rank corr ~0.5, and an own-level calibration on the game's
    # first 60 launches helped two games and wrecked another. A dollar figure
    # that rough would mislead; a new game waits for MIN_LABELED of its own
    # launches (about one set plus two months) and gets the per-game model.
    pooled = {"summary": None}
    if needs_pool and os.environ.get("TCG_PRE_POOLED", "0") != "1":
        for g in needs_pool:
            print(f"[{g}] no estimates yet: needs {MIN_LABELED} priced launches of its own "
                  f"(cross-game model is off — held-out games missed by ~250%)")
        needs_pool = []
    if needs_pool or args.validate_pooled:
        try:
            pooled["summary"] = validate_pooled(prep, out, today, dry_run=args.dry_run)
        except Exception as e:                      # noqa: BLE001
            print(f"[pooled] validation failed: {type(e).__name__}: {e}", file=sys.stderr)
    for g in needs_pool:
        try:
            total += run_pooled_game(g, prep, pooled, out, today, dry_run=args.dry_run)
        except Exception as e:                      # noqa: BLE001
            print(f"[{g}] pooled prerelease failed: {type(e).__name__}: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
    if not args.dry_run:
        n = grade_archive(out, pc, today)
        print(f"forecast_prerelease: {total} estimates live · {n} archived estimates graded")
    out.close()
    pc.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                          # noqa: BLE001
        print(f"forecast_prerelease: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
