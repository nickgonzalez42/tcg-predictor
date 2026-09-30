#!/bin/bash
# Runs the MODEL BLOCK (scorecard -> forecast -> report) on the AWS trainer and
# pushes the resulting predictions.db to prod over the private VPC (same-AZ = free).
#
# DETACHED since 2026-08-05: the Mac ships fresh inputs, launches this with
# --auto-stop via nohup (>> ~/model_run.log), and its night ends there — so
# this script owns the endgame. On success it pushes predictions.db AND
# restarts the prod API (the Mac's push_data.sh restart happened hours
# earlier, and the API holds its old predictions.db inode until restarted).
# On failure it reports over SNS (instance role tcg-trainer grants
# sns:Publish). With --auto-stop it powers the instance off either way
# (instance shutdown behavior is 'stop', so billing ends); the 4.5h-after-boot
# cron shutdown remains the hang backstop.
#
# predictions.db is authoritative HERE — it persists on the trainer's EBS and
# is the next run's prior, so it is NOT copied back to the Mac.
#
# OpenBLAS is capped to the vCPU count — uncapped it spawns 64 threads on 4
# cores and thrashes (the forecast crawled from ~1min to >5min).
#
#   aws_model_run.sh                # run + push + restart prod API
#   aws_model_run.sh --no-push      # run only (validation, no deploy)
#   aws_model_run.sh --auto-stop    # nightly: also power off when done
set -u
PROD_IP="${PROD_IP:-172.31.24.13}"
PUSH=1; AUTO_STOP=0
for a in "$@"; do
  case "$a" in
    --no-push)   PUSH=0 ;;
    --auto-stop) AUTO_STOP=1 ;;
  esac
done
H=/home/ubuntu
PY=$H/parent/one-piece/.venv/bin/python
PIPE=$H/parent/tcg-predictor/pipeline
CARDS=$H/parent/tcg-predictor/dotnet/API/Data/cards
PROD_SSH="ssh -i $H/.ssh/tcg-predictor.pem -o StrictHostKeyChecking=no"
export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
# Bucketed (Mondrian) conformal bands at 1m only — backtest-validated 2026-08-13
# (btA: tighter bands at equal coverage, closer-to-target in 23/45 cells; btB
# showed 6m gains are regime-limited, so 6m/12m keep the scalar widening).
# Debias (TCG_FC_DEBIAS) stays OFF: measured nightly in the logs, but both
# backtests showed applying it does not help. Rollback = delete this export.
export TCG_FC_CQR=1m
# 2026-09-13 (telemetry-reviewed): rolling live recalibration ON at 1m —
# measured widens were +0.00-0.02 on calibrated segments and +0.07-0.18 only
# on young-game ungraded tiers where live coverage genuinely fell short.
export TCG_FC_ROLLCAL=1m
# Launch tier ON: early estimates issue + grade silently (horizon launch1m is
# not served by the API's trend windows); its self-healing record accrues
# until the labeled UI ships.
export TCG_FC_LAUNCH=1
cd "$H/parent/one-piece"

notify_failure() {  # $1 = failed step
  "$PY" - "$1" <<'EOF' || echo "(SNS notify failed — check the tcg-trainer instance role)"
import sys
import boto3
step = sys.argv[1]
try:
    with open("/home/ubuntu/model_run.log") as f:
        tail = f.read()[-3000:]
except OSError:
    tail = "(no model_run.log)"
boto3.client("sns", region_name="us-east-1").publish(
    TopicArn="arn:aws:sns:us-east-1:522029196375:cardstock-problem-reports",
    Subject=f"cardstock AWS model block FAILED ({step})",
    Message=(f"Model block failed at {step} on the trainer.\n"
             "predictions.db was NOT pushed; prod keeps its prior forecasts.\n"
             "Log tail:\n\n" + tail))
EOF
}

finish() {  # $1 = exit code
  if [ "$AUTO_STOP" = "1" ]; then
    echo "=== auto-stop: powering off $(date -u '+%T UTC') ==="
    sudo shutdown -h now
  fi
  exit "$1"
}

# "Full refresh done" email (topic cardstock-refresh-reports, 2026-08-14):
# the model block is the refresh's last leg, so success here = whole night
# done. Folds in the Mac-side summary shipped to ~/mac_refresh_summary.txt
# during Phase C, this run's step timings + forecast volume, and the weekly
# report outcome (generated on Friday-night runs, skipped otherwise).
notify_success() {
  "$PY" - <<'EOF' || echo "(SNS success notify failed — email skipped)"
import datetime
import os
import boto3

try:
    lines = open("/home/ubuntu/model_run.log").read().splitlines()
except OSError:
    lines = []
start = 0
for i, l in enumerate(lines):
    if l.startswith("=== AWS model run ") and "push=" in l:
        start = i
run = lines[start:]

steps = [l for l in run if l.startswith("--- ") and " rc=" in l]
wrote = [l for l in run if "rows ->" in l and "predictions.db" in l]
if any("Not Friday" in l for l in run):
    report = "weekly report: skipped (not a Friday run)"
else:
    sec = [l for l in run if "market_report" in l or "report" in l.lower()][:6]
    report = "weekly report: GENERATED (Friday run)\n  " + "\n  ".join(sec)

mac = "(no Mac summary received — check refresh log on the Mac)"
p = "/home/ubuntu/mac_refresh_summary.txt"
try:
    if (datetime.datetime.now().timestamp() - os.path.getmtime(p)) < 20 * 3600:
        mac = open(p).read().strip()
except OSError:
    pass

today = datetime.date.today().isoformat()
msg = (f"Full nightly refresh complete ({run[0] if run else today}).\n\n"
       f"== Mac: crawls, price build, prod data push ==\n{mac}\n\n"
       f"== Trainer: model block ==\n" + "\n".join(steps + wrote)
       + f"\n{report}\n\npredictions.db pushed + prod API restarted.\n")
boto3.client("sns", region_name="us-east-1").publish(
    TopicArn="arn:aws:sns:us-east-1:522029196375:cardstock-refresh-reports",
    Subject=f"cardstock nightly refresh done ({today})",
    Message=msg)
EOF
}

echo "=== AWS model run $(date -u '+%F %T UTC') (push=$PUSH auto-stop=$AUTO_STOP) ==="
for step in forecast_scorecard forecast_predict forecast_launch market_report; do
  echo "--- $step $(date -u '+%T') ---"
  t0=$(date +%s)
  "$PY" "$PIPE/$step.py"; rc=$?
  echo "--- $step rc=$rc in $(( ($(date +%s)-t0)/60 )) min ---"
  if [ $rc -ne 0 ]; then
    echo "=== MODEL BLOCK FAILED at $step (rc=$rc) — predictions.db NOT pushed ==="
    notify_failure "$step"
    finish $rc
  fi
done

if [ $PUSH -eq 1 ]; then
  echo "=== push predictions.db -> prod ($PROD_IP), same-AZ ==="
  rsync -az -e "$PROD_SSH" \
    "$CARDS/predictions.db" "ubuntu@$PROD_IP:/srv/tcg/data/cards/" \
    || { echo "=== predictions.db PUSH FAILED ==="; notify_failure "push"; finish 1; }
  ${PROD_SSH} "ubuntu@$PROD_IP" \
    'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api' \
    || { echo "=== prod API RESTART FAILED ==="; notify_failure "api-restart"; finish 1; }
  echo "predictions.db pushed + prod API restarted"
  # Static report pages (2026-08-29, crawler-visible content): render every
  # report in the DB as standalone HTML and ship to Caddy's static root.
  # Cheap and idempotent nightly; each Friday's new report rides along.
  "$PY" "$H/parent/tcg-predictor/pipeline/report_pages.py" --db --out "$H/report_pages" \
    && rsync -az -e "$PROD_SSH" "$H/report_pages/" "ubuntu@$PROD_IP:/srv/tcg/static/reports/" \
    && echo "static report pages shipped" \
    || echo "(static report pages ship failed — non-fatal)"
  # Warm the homepage's hot paths so the morning's first visitor doesn't pay
  # the cold-cache recompute (see deploy/push_data.sh, 2026-08-14).
  for ep in \
    "api/cards/movers?count=24&horizon=1m&trend=6m" \
    "api/cards/movers?horizon=mix&perGame=4" \
    "api/cards/movers?game=all&count=10"; do
    curl -s -o /dev/null --max-time 120 "https://cardstock.guide/$ep" || true
  done
  echo "warmup done"
fi
echo "=== AWS model run done $(date -u '+%F %T UTC') ==="
notify_success
finish 0
