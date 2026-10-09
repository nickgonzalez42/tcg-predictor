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
# 6m/12m retrain on the FULL-REBUILD night only (2026-10-09, user decision):
# they stay unvalidated until their first cohorts mature in 2027 and barely
# move between runs; a 1m-only night cuts the model block roughly in half.
# Sunday local = Mon UTC (1); the legacy Monday kick = Tue UTC (2). Other
# nights keep serving Sunday's standing 6m/12m rows (horizon-scoped delete
# in forecast_predict.py).
u=$(date -u +%u)
if [ "$u" != "1" ] && [ "$u" != "2" ]; then
  export TCG_FC_HORIZONS=1m
fi

echo "=== LOCAL model block — $(date '+%F %T') ==="
for step in forecast_scorecard forecast_predict forecast_launch market_report; do
  echo "--- $step $(date '+%T') ---"
  t0=$SECONDS
  "$PY" "$DIR/$step.py"
  rc=$?
  echo "--- $step rc=$rc in $(( (SECONDS - t0) / 60 )) min ---"
  if [ $rc -ne 0 ]; then
    echo "=== MODEL BLOCK FAILED at $step (rc=$rc) — predictions.db NOT pushed ==="
    exit 1
  fi
done

echo "=== push predictions.db -> prod ($SERVER_IP) ==="
$RSYNC -az --partial -e "$SSH" "$CARDS/predictions.db" \
  "ubuntu@$SERVER_IP:/srv/tcg/data/cards/" \
  || { echo "=== predictions.db PUSH FAILED ==="; exit 1; }

# Static report pages (crawler-visible content) ride every model run.
"$PY" "$DIR/report_pages.py" --db --out "$HOME/tcg-backups/report_pages" \
  && $RSYNC -az -e "$SSH" "$HOME/tcg-backups/report_pages/" \
       "ubuntu@$SERVER_IP:/srv/tcg/static/reports/" \
  && echo "static report pages shipped" \
  || echo "(static report pages ship failed — non-fatal)"

${=SSH} "ubuntu@$SERVER_IP" \
  'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api' \
  || { echo "=== prod API RESTART FAILED ==="; exit 1; }
echo "predictions.db pushed + prod API restarted (startup self-warmup handles caches)"
