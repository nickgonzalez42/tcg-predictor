#!/bin/zsh
# Scheduled refresh (launchd entry point, 10:00 PM daily — see
# ~/Library/LaunchAgents/com.tcg-predictor.daily-refresh.plist).
#
#   Phase A (Mac): crawls + price build + ml prep (weekly_refresh --to art-comps;
#                  Sunday also does the TCGplayer catalog scrape first).
#   Launch (AWS):  start the trainer, ship it the fresh inputs, and kick the
#                  model block off DETACHED (launch_model_on_aws). The trainer
#                  owns the endgame: it pushes predictions.db to prod, restarts
#                  the API there, reports failures over SNS, and powers itself
#                  off (see aws_model_run.sh). The Mac does NOT wait ~3h for it.
#   Phase C (Mac): s3 art + link suggest, then push the other DBs + restart —
#                  run IMMEDIATELY after the launch (nothing in it depends on
#                  the model output), so the Mac's night ends here (~6 AM).
# Card art uploads to S3 inside the refresh itself (s3-upload step); the push
# only ships databases. The API restart in Phase C serves fresh CRAWL prices
# against the prior night's forecasts; the trainer's own restart ~3h later
# brings the new forecasts live.
#
# store.db (accounts/portfolios) is never pushed — user data lives on the
# server only. Every step is resumable: on failure the log names the step;
# rerun weekly_refresh.py --from <step>, then deploy/push_data.sh manually.
# weekly_refresh has its own lock, so an overlapping run skips cleanly.
set -u
cd "$(dirname "$0")"

# Keep the SYSTEM awake for the duration (the display may still sleep and the
# screen may lock — the run continues underneath). caffeinate exits with the
# script, so normal sleep behavior resumes the moment the refresh finishes.
# NOTE: this does not survive a closed lid — lid-close forces sleep regardless.
if [[ -z "${CAFFEINATED:-}" ]]; then
  export CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$0" "$@"
fi

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
SERVER_IP=35.168.177.31
REPO=/Users/nicholasgonzalez/Developer/Projects/parent/tcg-predictor
ONEPIECE=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
API_DATA="$REPO/dotnet/API/Data/cards"
# AWS trainer: the MODEL BLOCK (scorecard/forecast/report) runs here since
# 2026-08-03 — the 8GB Mac swap-thrashed ~5h on it. Detached since 2026-08-05:
# started nightly, then left to run/push/self-stop on its own (the trainer also
# self-stops 4.5h after boot as a runaway backstop). predictions.db is produced
# + pushed to prod BY the trainer over the private VPC; it never returns here.
AWS=/usr/local/bin/aws
# Two AWS profiles exist on this Mac (default = cardstock 522029196375,
# client = 870397520032). Pin every aws/boto call this pipeline makes to the
# cardstock account and REFUSE to run the AWS legs otherwise — a leaked
# AWS_PROFILE from an interactive shell must never point the pipeline at the
# other account. (2026-08-14)
export AWS_PROFILE=default
CARDSTOCK_ACCT=522029196375
for _try in 1 2 3; do
  ACCT=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null) && break
  sleep 10
done
if [ "$ACCT" != "$CARDSTOCK_ACCT" ]; then
  echo "ABORT: aws resolves to account '${ACCT:-none}', not cardstock ($CARDSTOCK_ACCT) — check ~/.aws profiles"
  exit 1
fi
TRAINER=i-0a21c69c5a842c7ad
TKEY=/Users/nicholasgonzalez/.ssh/tcg-predictor.pem
TSSH="ssh -i $TKEY -o StrictHostKeyChecking=no"
# GNU rsync (brew) — macOS's openrsync corrupts multi-GB delta transfers
# (pricecharting.db, exit 23 on 2026-08-05 push and 2026-08-06 input sync).
RSYNC=$([ -x /opt/homebrew/bin/rsync ] && echo /opt/homebrew/bin/rsync || echo rsync)
SNS_TOPIC=arn:aws:sns:us-east-1:522029196375:cardstock-problem-reports

notify_failure() {
  local rc=$1
  $AWS sns publish --region us-east-1 --topic-arn "$SNS_TOPIC" \
    --subject "cardstock daily refresh FAILED ($(date +%F), exit $rc)" \
    --message "$(printf 'Daily refresh failed (exit %s) at %s.\nSee %s\n\nLast 30 log lines:\n\n%s' \
        "$rc" "$(date '+%F %T')" "$LOG" "$(tail -30 "$LOG")")" \
    >/dev/null 2>&1 || echo "(SNS notify itself failed — check aws cli/creds)"
}

# Start the trainer, ship it the fresh forecast inputs (NOT predictions.db — it
# owns that), and launch the model block DETACHED with --auto-stop: the trainer
# pushes predictions.db + restarts the prod API on success, reports failures
# over SNS, and powers itself off either way. Returns non-zero only for
# start/sync/launch failures (model failures are the trainer's to report).
launch_model_on_aws() {
  echo "=== AWS model handoff: start trainer $(date '+%T') ==="
  $AWS ec2 start-instances --region us-east-1 --instance-ids "$TRAINER" >/dev/null 2>&1
  $AWS ec2 wait instance-running --region us-east-1 --instance-ids "$TRAINER" 2>/dev/null
  local ip i
  ip=$($AWS ec2 describe-instances --region us-east-1 --instance-ids "$TRAINER" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null)
  for i in $(seq 1 30); do
    ${=TSSH} -o ConnectTimeout=8 ubuntu@$ip 'echo ok' 2>/dev/null | grep -q ok && break
    sleep 8
  done
  echo "trainer at $ip — syncing inputs $(date '+%T')"
  # --inplace: update the trainer's copy directly instead of staging a full
  # temp copy — the trainer's 29G root can't hold 2x this file (ENOSPC killed
  # the 2026-08-11 sync). Safe here: the model only reads it AFTER the sync
  # succeeds, and an interrupted transfer just re-syncs next launch.
  $RSYNC -az --inplace --partial -e "$TSSH" "$API_DATA/pricecharting.db" ubuntu@$ip:parent/tcg-predictor/dotnet/API/Data/cards/ &&
  $RSYNC -az -e "$TSSH" $ONEPIECE/*_cards.db ubuntu@$ip:parent/one-piece/ &&
  $RSYNC -az -e "$TSSH" "$ONEPIECE/ml_data/" ubuntu@$ip:parent/one-piece/ml_data/ &&
  $RSYNC -az -e "$TSSH" "$REPO/pipeline/" ubuntu@$ip:parent/tcg-predictor/pipeline/
  local sync_rc=$?
  if [ $sync_rc -ne 0 ]; then
    echo "=== AWS input sync FAILED rc=$sync_rc — stopping trainer ==="
    $AWS ec2 stop-instances --region us-east-1 --instance-ids "$TRAINER" >/dev/null 2>&1
    return $sync_rc
  fi
  ${=TSSH} ubuntu@$ip \
    'nohup bash ~/parent/tcg-predictor/pipeline/aws_model_run.sh --auto-stop >> ~/model_run.log 2>&1 & disown; echo "model launched (pid $!)"'
  local launch_rc=$?
  if [ $launch_rc -ne 0 ]; then
    echo "=== model LAUNCH FAILED rc=$launch_rc — stopping trainer ==="
    $AWS ec2 stop-instances --region us-east-1 --instance-ids "$TRAINER" >/dev/null 2>&1
    return $launch_rc
  fi
  echo "=== model block launched detached $(date '+%T') — trainer pushes + restarts API + powers off on its own ==="
  TRAINER_IP=$ip   # Phase C ships the Mac summary here for the completion email
  return 0
}

LOG_DIR="$HOME/Library/Logs/tcg-predictor"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "refresh-*.log" -mtime +30 -delete 2>/dev/null
# UTC day basis (2026-08-14): an evening start crosses midnight UTC, so the
# run's UTC date — the log name, the weekly gates, and every batch label the
# python side writes — is the NEXT calendar day, matching the morning the
# data goes live. Twice-weekly cadence (2026-09-28, Sun/Thu 8 PM local):
# the SUNDAY run's UTC day is Monday (=1) and carries the full rebuild +
# market watch (Tuesday =2 also accepted, covering the final Monday-local
# run of the old cadence and any manual Monday-evening kick); the THURSDAY
# run's UTC day is Friday, which still gates the weekly report, and Friday
# (=5) gates the S3 backup.
LOG="$LOG_DIR/refresh-$(date -u +%Y-%m-%d).log"

{
  # Phase A (Mac): crawls + price build + ml prep, through art-comps.
  if [ "$(date -u +%u)" = "1" ] || [ "$(date -u +%u)" = "2" ]; then
    echo "=== WEEKLY full refresh — $(date '+%F %T') ==="
    "$PY" weekly_refresh.py --to art-comps
  else
    echo "=== daily refresh — $(date '+%F %T') ==="
    "$PY" weekly_refresh.py --from tcg-nm-prices --to art-comps
  fi
  phaseA=$?
  if [ $phaseA -ne 0 ]; then
    echo "=== FAILED (Phase A, exit $phaseA): resume weekly_refresh.py --from <step> --to art-comps — $(date '+%F %T') ==="
    notify_failure "$phaseA"
  else
    # Phase B (2026-09-20): model block runs LOCALLY — no trainer, no handoff.
    # launch_model_on_aws stays defined above as a dormant fallback.
    /bin/zsh local_model_run.sh
    launch_rc=$?
    if [ $launch_rc -ne 0 ]; then
      echo "=== FAILED (local model block, exit $launch_rc) — prod predictions.db unchanged — $(date '+%F %T') ==="
      notify_failure "$launch_rc"
    else
      # Phase C (Mac): s3 art + link suggest, then push the OTHER DBs + restart.
      # predictions.db already pushed by the local model block above.
      "$PY" weekly_refresh.py --from s3-upload && ../deploy/push_data.sh "$SERVER_IP"
      rc=$?
      if [ $rc -eq 0 ]; then
        echo "=== refresh complete (local model) — $(date '+%F %T') ==="
        # Completion email (2026-09-20): the model ran locally and finished
        # BEFORE this point, so one email at true completion replaces the old
        # handoff + trainer-completion pair.
        $AWS sns publish --region us-east-1 \
          --topic-arn "arn:aws:sns:us-east-1:522029196375:cardstock-refresh-reports" \
          --subject "cardstock refresh done ($(date -u +%F), local model)" \
          --message "$(printf 'Full refresh finished %s — crawls, local model block, and prod push all complete.\n\nSteps:\n%s\n\nModel block:\n%s' \
              "$(date '+%F %T')" \
              "$(grep -E '^  (ok|FAIL) |^Done\. [0-9]+ Near Mint|^total budget' "$LOG" | head -40)" \
              "$(grep -E '^--- (forecast_|market_report).* rc=|rows -> |Wrote report|forecast_launch: |predictions.db pushed' "$LOG" | tail -14)")" \
          >/dev/null 2>&1 || echo "(completion SNS failed — non-fatal)"
        # Best-effort S3 backups (nightly CSVs; Saturdays add the big dumps) —
        # never fails the refresh.
        /bin/zsh backup_to_s3.sh || echo "(backup_to_s3 failed — non-fatal)"
        # Per-printing PC history for variants the ingest discovered tonight
        # (Phase 2 maintenance): bounded, labeled-namespace-only, and AFTER
        # everything that matters — a failure here cannot touch the refresh.
        "$PY" pc_printing_backfill.py --limit 600 || echo "(pc_printing_backfill failed — non-fatal)"
        "$PY" match_review.py --if-pending >> "$LOG_DIR/match_review.log" 2>&1 &
      else
        echo "=== FAILED (Phase C/push, exit $rc): weekly_refresh.py --from s3-upload, then deploy/push_data.sh $SERVER_IP — $(date '+%F %T') ==="
        notify_failure "$rc"
      fi
    fi
  fi
} >> "$LOG" 2>&1
