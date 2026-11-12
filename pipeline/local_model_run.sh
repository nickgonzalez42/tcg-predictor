#!/bin/zsh
# LOCAL model block (2026-09-20): scorecard -> forecast -> launch tier ->
# weekly report, running on the Mac — replaces the AWS trainer under the
# twice-weekly cadence (Mon/Thu 8 PM). predictions.db is authoritative HERE
# now (pulled from the trainer's EBS 2026-09-20; the instance stays stopped
# as cold fallback). On success this pushes predictions.db to prod, ships the
# static report pages, and restarts the API (whose startup self-warmup
# recomputes the caches).
#
# Invoked by run_daily_refresh.sh after Phase A; safe to run by hand too.
#
#   local_model_run.sh --plan   say whether tonight's block is a LONG one
#                               (MODEL_PLAN=long|normal) and exit; touches
#                               nothing. The nightly publishes prices BEFORE
#                               a long block instead of after it.
#   TCG_MODEL_RESTART=0         push predictions.db but leave the API restart
#                               to the caller (the nightly's price publish
#                               follows and restarts once for both).
# Under the nightly (TCG_BATCH_DATE set) each finished step is noted against
# the batch and a second invocation for the same batch skips it, so a block
# that died or failed picks up where it stopped; forecast_predict keeps its
# own note per game. Hand runs carry no batch and always run every step.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
CARDS="$DIR/../dotnet/API/Data/cards"
SERVER_IP=${SERVER_IP:-35.168.177.31}
KEY=$HOME/.ssh/tcg-predictor.pem
SSH="ssh -i $KEY -o StrictHostKeyChecking=no"
# GNU rsync when present — openrsync's delta transfer corrupts multi-GB files.
RSYNC=$([ -x /opt/homebrew/bin/rsync ] && echo /opt/homebrew/bin/rsync || echo rsync)
export AWS_PROFILE=default

# Model flags (same set the trainer ran): CQR bands + rolling live
# recalibration at 1m; launch tier issuing early estimates silently.
export TCG_FC_CQR=1m
export TCG_FC_ROLLCAL=1m
export TCG_FC_LAUNCH=1
# Pre-release estimates (2026-10-10): trait-only launch prices for cards with
# no market price yet (upcoming sets). Non-fatal step; see forecast_prerelease.py.
export TCG_FC_PRERELEASE=1
# 6m/12m retrain on the FULL-REBUILD night only (2026-10-09, user decision):
# they stay unvalidated until their first cohorts mature in 2027 and barely
# move between runs; a 1m-only night cuts the model block roughly in half.
# The long horizons (6m/12m; 3m retired 2026-10-24) retrain MONTHLY (2026-10-18 evened week):
# on the crawl night (run_daily_refresh.sh exports TCG_CRAWL_NIGHT from the
# LOCAL weekday — the old UTC-Sunday test cannot see an 18:20 Saturday
# start) in the first week of the month, i.e. the month's first Saturday.
# They are unvalidated until 2027 and barely move between runs; that one
# night is ~27 h, every other night trains 1m only and keeps serving the
# standing 6m/12m rows (horizon-scoped delete in forecast_predict.py).
# Between retrains, forecast_predict's FILL pass issues 6m/12m rows for the
# cards that have none, from the models each retrain saves (2026-11-02).
# One-shot override: touch one-piece/ml_data/.long_horizons_tonight and the
# next model block trains every horizon whatever the night (used 2026-11-02,
# user: "make sure any that don't have a 6M and 1Y get them tonight").
FORCE_LONG="$DIR/../../one-piece/ml_data/.long_horizons_tonight"
PLAN_ONLY=0; [ "${1:-}" = "--plan" ] && PLAN_ONLY=1
BATCH="${TCG_BATCH_DATE:-}"
STATE_DIR="$DIR/../../one-piece/ml_data/refresh_state"
# The night's day of the month is the day the run STARTED (the batch label
# is the morning after), not the clock's: a long block that is resumed the
# next day must not change its mind about which horizons it trains.
NIGHT_DOM=$(date +%d)
[ -n "$BATCH" ] && NIGHT_DOM=$(date -j -v-1d -f %F "$BATCH" +%d 2>/dev/null || date +%d)
# The choice is made once per batch and kept (the one-shot file is gone by
# the time a resume looks for it).
HFILE=""; [ -n "$BATCH" ] && HFILE="$STATE_DIR/$BATCH.horizons"
if [ -n "$HFILE" ] && [ -s "$HFILE" ]; then
  LONG_H=$(cat "$HFILE")
elif [ -f "$FORCE_LONG" ]; then
  LONG_H=all
  [ "$PLAN_ONLY" = 1 ] || echo "=== .long_horizons_tonight present: training 6m/12m tonight (one-shot) ==="
elif [ "${TCG_CRAWL_NIGHT:-0}" = "1" ] && [ "$NIGHT_DOM" -le 7 ]; then
  LONG_H=all
else
  LONG_H=1m
fi
[ "$LONG_H" = "1m" ] && export TCG_FC_HORIZONS=1m
# 1m: score from each segment's saved fit, refit each game on its own night
# of the week (2026-11-12; see SCORE_1M in forecast_predict.py).
# TCG_FC_1M_SCORE=0 in the environment refits every segment for one run.
export TCG_FC_1M_SCORE=${TCG_FC_1M_SCORE:-1}

if [ "$PLAN_ONLY" = 1 ]; then
  # Long = every horizon trains, or at least half the games fit their 1m
  # (the first night under score mode, or after a model-version bump).
  if [ "$LONG_H" = "all" ]; then
    echo "MODEL_PLAN=long (6m/12m train tonight)"
  else
    line=$("$PY" "$DIR/forecast_predict.py" --plan 2>/dev/null | grep 'plan-1m' | tail -1)
    n_fit=$(echo "$line" | sed -n 's/.*fit_games=\([0-9]*\) of \([0-9]*\).*/\1/p')
    n_all=$(echo "$line" | sed -n 's/.*fit_games=\([0-9]*\) of \([0-9]*\).*/\2/p')
    if [ -z "$n_fit" ] || [ -z "$n_all" ]; then
      echo "MODEL_PLAN=long (the 1m plan could not be read)"
    elif [ $(( n_fit * 2 )) -ge "$n_all" ]; then
      echo "MODEL_PLAN=long ($line)"
    else
      echo "MODEL_PLAN=normal ($line)"
    fi
  fi
  exit 0
fi
[ -f "$FORCE_LONG" ] && rm -f "$FORCE_LONG"
if [ -n "$HFILE" ]; then
  mkdir -p "$STATE_DIR" && echo "$LONG_H" > "$HFILE"
fi
STEPS_FILE=""; [ -n "$BATCH" ] && STEPS_FILE="$STATE_DIR/$BATCH.model_steps"

# Card attributes the model reads beyond price history (2026-11-01, user:
# name, text, art, stat line, set, printing, price, sales volume, rarity,
# age, track record, supply). Sales volume and listing supply arrive as
# monthly signal files from build_market_signals.py; the flags let
# forecast_deep read them. Backtest 2026-11-01 (pokemon + onepiece, 1m,
# origins 2026-07/08): volume 8 of 8 cells better, mean -0.7% error; card
# text neutral (mean +0.2%, inside the noise floor) and kept on the same
# footing as the art block, a product decision; supply has history only
# from 2026-10-16, so it is inert in training until those months mature.
# Re-backtest all three around 2027-02 (backtest_forecast.py, runs attr_*).
export TCG_FC_FEAT_VOL=1
export TCG_FC_FEAT_SUPPLY=1
export TCG_FC_FEAT_TXT=1

echo "=== LOCAL model block — $(date '+%F %T') ==="
# build_market_signals (2026-11-01) turns the sales-volume and listing-depth
# tables into the monthly signal CSVs forecast_predict reads under
# TCG_FC_FEAT_VOL / TCG_FC_FEAT_SUPPLY; it runs after the scorecard so
# the forecast step sees every signal file fresh.
# game_articles (2026-11-06): two per-game articles a day on a Mon-Sat
# rotation (schedule in game_articles.py); never fatal on its own.
# pick_showcase (2026-11-09): the home page's featured (past moves) and
# expected (forecast) cards, each checked for recent sales with a fresh
# TCGplayer fetch (the daytime crawler is paused during the refresh, so the
# step fetches directly); writes the showcase table; never fatal on its own.
for step in forecast_scorecard build_market_signals forecast_predict forecast_launch sealed_prices forecast_prerelease market_report game_articles pick_showcase; do
  if [ -n "$STEPS_FILE" ] && grep -qx "$step" "$STEPS_FILE" 2>/dev/null; then
    echo "--- $step already done for batch $BATCH, skipped ---"
    continue
  fi
  echo "--- $step $(date '+%T') ---"
  t0=$SECONDS
  "$PY" "$DIR/$step.py"
  rc=$?
  echo "--- $step rc=$rc in $(( (SECONDS - t0) / 60 )) min ---"
  if [ $rc -ne 0 ]; then
    echo "=== MODEL BLOCK FAILED at $step (rc=$rc) — predictions.db NOT pushed ==="
    exit 1
  fi
  [ -n "$STEPS_FILE" ] && echo "$step" >> "$STEPS_FILE"
done

# One-off job hook: a self-contained pipeline/oneoff_model_job.sh (never
# committed; .gitignore) runs once after the model block and is deleted on
# success — surgical re-runs that must not collide with Phase A's crawls.
# Non-fatal: a failing one-off neither blocks the push nor deletes itself.
if [ -x "$DIR/oneoff_model_job.sh" ]; then
  echo "--- oneoff_model_job $(date '+%T') ---"
  /bin/zsh "$DIR/oneoff_model_job.sh" && rm -f "$DIR/oneoff_model_job.sh"
  echo "--- oneoff_model_job done $(date '+%T') ---"
fi

echo "=== push predictions.db -> prod ($SERVER_IP) ==="
# Publish-retry (2026-10-09, after the 10-02 offline-at-publish failure):
# the model block can finish with the laptop offline — retry every 15 min
# for ~4 h instead of dying on the first attempt. A closed lid suspends the
# sleep and resumes on wake, so this naturally waits out "offline until the
# user opens the laptop somewhere with wifi".
push_ok=0
for attempt in {1..16}; do
  if $RSYNC -az --partial -e "$SSH" "$CARDS/predictions.db" \
       "ubuntu@$SERVER_IP:/srv/tcg/data/cards/"; then
    push_ok=1; break
  fi
  echo "=== push attempt $attempt failed (offline?) — retrying in 15 min — $(date '+%F %T') ==="
  sleep 900
done
[ "$push_ok" = 1 ] || { echo "=== predictions.db PUSH FAILED (all retries) ==="; exit 1; }

# Static report pages (crawler-visible content) ride every model run.
"$PY" "$DIR/report_pages.py" --db --out "$HOME/tcg-backups/report_pages" \
  && $RSYNC -az -e "$SSH" "$HOME/tcg-backups/report_pages/" \
       "ubuntu@$SERVER_IP:/srv/tcg/static/reports/" \
  && echo "static report pages shipped" \
  || echo "(static report pages ship failed — non-fatal)"

# Per-game article pages (2026-11-06) ride along the same way.
"$PY" "$DIR/article_pages.py" --db --out "$HOME/tcg-backups/article_pages" \
  && $RSYNC -az -e "$SSH" "$HOME/tcg-backups/article_pages/" \
       "ubuntu@$SERVER_IP:/srv/tcg/static/articles/" \
  && echo "article pages shipped" \
  || echo "(article pages ship failed — non-fatal)"

# Per-set SEO pages: fresh prices + forecasts every run; the manifest the
# API's /sitemap-sets.xml reads ships with them.
"$PY" "$DIR/set_pages.py" --out "$HOME/tcg-backups/set_pages" \
  && $RSYNC -az --delete -e "$SSH" "$HOME/tcg-backups/set_pages/" \
       "ubuntu@$SERVER_IP:/srv/tcg/static/sets/" \
  && echo "set pages shipped" \
  || echo "(set pages ship failed — non-fatal)"

if [ "${TCG_MODEL_RESTART:-1}" != "1" ]; then
  echo "predictions.db pushed; the API restart is left to the price publish that follows"
  exit 0
fi
${=SSH} "ubuntu@$SERVER_IP" \
  'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api' \
  || { echo "=== prod API RESTART FAILED ==="; exit 1; }
echo "predictions.db pushed + prod API restarted (startup self-warmup handles caches)"
