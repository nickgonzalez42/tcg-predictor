#!/bin/zsh
# Scheduled refresh (launchd entry point — see
# ~/Library/LaunchAgents/com.tcg-predictor.daily-refresh.plist: 19:05 on six
# nights, 18:20 on Saturday, the crawl night).
#
# One night, in this order (2026-11-12 redesign):
#   core     weekly_refresh.py --core ... --to art-comps: the crawls the
#            prices are built from, the price build, the ml prep.
#   model    local_model_run.sh: scorecard, forecasts, reports. Pushes
#            predictions.db to prod on success.
#   publish  weekly_refresh.py --core --from s3-upload (card art), then
#            deploy/push_data.sh: card DBs to prod, the API restart, warm-up,
#            prerender.
#   -- the night is FINISHED here: ledger row, completion email, S3 backup --
#   tail     weekly_refresh.py --tail: the slow rotation crawls, which no
#            page of the same night reads (see TAIL in weekly_refresh.py),
#            then the per-printing backfill and the match review.
#
# Which of model and publish goes first is decided per night and kept:
#   normal  model, then publish — ONE API restart brings the forecasts and
#           the prices live together (every restart is about a minute of
#           502s and ten of slow pages).
#   long    publish, then model with its own restart — on a night the model
#           block trains for hours (6m/12m night, or half the games or more
#           refit their 1m: local_model_run.sh --plan), the prices do not
#           wait for it. TCG_PUBLISH_FIRST=1 forces this order.
# A model failure never costs the night its prices: on a normal night the
# prices are published right after the failed attempt, and the model block
# gets its second try after that.
#
# Failure and restart handling:
#   - core and publish get one more try after RETRY_PAUSE; the model block
#     one more after the publish. Each picks up at the step that failed
#     (weekly_refresh.py --resume, the model block's step notes,
#     forecast_predict's note per game). A stage that fails twice marks the
#     night failed (ledger + SNS); the tail still runs.
#   - ml_data/refresh_state/<batch>.state records the night: night, flow,
#     "ok <stage>", "finished", "done". A run that died (reboot, crash) has
#     "started" without "done"; refresh_watchdog.sh sees that and starts
#     `run_daily_refresh.sh --resume <batch>`, which skips what is noted.
#   - one runner at a time (.daily_refresh.lock in one-piece/). A new night
#     that finds the night before still in its tail ends that tail and goes
#     on; if it is still in core/model/publish the new night is skipped and
#     says so over SNS.
#
#   run_daily_refresh.sh                    tonight's run (launchd)
#   run_daily_refresh.sh --resume [BATCH]   pick an unfinished night back up
#   run_daily_refresh.sh --retry-failed [BATCH]   same, and rerun the stages
#                                           that failed on a finished night
#   run_daily_refresh.sh --fresh            forget what tonight's batch noted
#   DRY_DIR=/some/scratch run_daily_refresh.sh --dry-run
#                                           print the night instead of running
#                                           it (state kept in DRY_DIR;
#                                           DRY_NIGHT=csv|crawl,
#                                           DRY_PLAN=normal|long,
#                                           DRY_FAIL / DRY_FAIL_ONCE = labels
#                                           among catalog core model art push
#                                           tail backfill)
#
# store.db (accounts/portfolios) is never pushed — user data lives on the
# server only.
set -u
SELF="${0:A}"
cd "${SELF:h}"

# Keep the SYSTEM awake for the duration (the display may still sleep and the
# screen may lock — the run continues underneath). caffeinate exits with the
# script, so normal sleep behavior resumes the moment the refresh finishes.
# NOTE: this does not survive a closed lid — lid-close forces sleep regardless.
if [[ -z "${CAFFEINATED:-}" && " $* " != *" --dry-run "* ]]; then
  export CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$SELF" "$@"
fi

RESUME=0; RETRY=0; FRESH=0; DRY=0; WANT_BATCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --resume|--retry-failed)
      RESUME=1; [ "$1" = "--retry-failed" ] && RETRY=1
      if [[ "${2:-}" == 20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] ]]; then WANT_BATCH="$2"; shift; fi ;;
    --fresh) FRESH=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "usage: run_daily_refresh.sh [--resume [BATCH] | --retry-failed [BATCH] | --fresh] [--dry-run]"; exit 2 ;;
  esac
  shift
done

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
SERVER_IP=35.168.177.31
REPO=/Users/nicholasgonzalez/Developer/Projects/parent/tcg-predictor
ONEPIECE=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
API_DATA="$REPO/dotnet/API/Data/cards"
# AWS trainer (DORMANT since 2026-09-20, when the model block moved back to
# the Mac; launch_model_on_aws below is kept as the cold fallback):
AWS=/usr/local/bin/aws
# Two AWS profiles exist on this Mac (default = cardstock 522029196375,
# client = 870397520032). Pin every aws/boto call this pipeline makes to the
# cardstock account and REFUSE to run the AWS legs otherwise — a leaked
# AWS_PROFILE from an interactive shell must never point the pipeline at the
# other account. (2026-08-14)
export AWS_PROFILE=default
CARDSTOCK_ACCT=522029196375
# (The account check itself runs under the night's log, further down.)
TRAINER=i-0a21c69c5a842c7ad
TKEY=/Users/nicholasgonzalez/.ssh/tcg-predictor.pem
TSSH="ssh -i $TKEY -o StrictHostKeyChecking=no"
# GNU rsync (brew) — macOS's openrsync corrupts multi-GB delta transfers
# (pricecharting.db, exit 23 on 2026-08-05 push and 2026-08-06 input sync).
RSYNC=$([ -x /opt/homebrew/bin/rsync ] && echo /opt/homebrew/bin/rsync || echo rsync)
SNS_TOPIC=arn:aws:sns:us-east-1:522029196375:cardstock-problem-reports

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
if [ "$DRY" = 1 ]; then
  STATE_DIR="${DRY_DIR:?--dry-run needs DRY_DIR, a scratch folder for its state}"
  LOCK="$DRY_DIR/.daily_refresh.lock"
  RETRY_PAUSE=0
else
  STATE_DIR="$ONEPIECE/ml_data/refresh_state"
  LOCK="$ONEPIECE/.daily_refresh.lock"
  RETRY_PAUSE=${TCG_RETRY_PAUSE:-300}
fi
mkdir -p "$STATE_DIR"
# Warn (never stop) when the data volume has less than this free: unify and
# the forecast publish both write multi-GB files.
MIN_FREE_GB=10

# ONE label per night (2026-10-23): the local calendar date the batch goes
# LIVE — the morning after the start. Exported as TCG_BATCH_DATE and read by
# every step that stamps a day (export/import file names, price rows, the
# unified delta's meta date, push_data's delta check), by the ledger and by
# the state files, so a night is one key everywhere. (UTC-at-the-moment
# labels made Tuesday append to Monday's log, 2026-10-20.) macOS date: -v+1d.
# A resume keeps the label of the night it resumes, whatever the clock says.
if [ -n "$WANT_BATCH" ]; then
  BATCH="$WANT_BATCH"
elif [ "$RESUME" = 1 ]; then
  states=("$STATE_DIR"/*.state(N))
  if [ ${#states} -eq 0 ]; then echo "no night on record to resume"; exit 1; fi
  BATCH="${${states[-1]:t}%.state}"
else
  BATCH=$(date -v+1d +%F)
fi
export TCG_BATCH_DATE="$BATCH"
LOG="$LOG_DIR/refresh-$BATCH.log"
[ "$DRY" = 1 ] && LOG=/dev/stdout
STATE="$STATE_DIR/$BATCH.state"

# The night's record. note "ok core" appends "ok core <time>"; has "ok core"
# asks whether that line is there.
note() { echo "$* $(date '+%F %T')" >> "$STATE"; }
has()  { grep -q "^$1 " "$STATE" 2>/dev/null; }

# Every command of the night goes through run, so --dry-run can print the
# night (and fail a labelled command on request) instead of running it.
run() {
  local label=$1; shift
  if [ "$DRY" = 1 ]; then
    echo "DRY [$label] $*"
    [[ " ${DRY_FAIL:-} " == *" $label "* ]] && return 1
    if [[ " ${DRY_FAIL_ONCE:-} " == *" $label "* ]] && [ ! -f "$DRY_DIR/.failed_once_$label" ]; then
      : > "$DRY_DIR/.failed_once_$label"; return 1
    fi
    return 0
  fi
  "$@"
}

# Run ledger (refresh_ledger.py -> ml_data/refresh_runs.csv): start, phase
# boundaries, finish, and how the night landed against TCG_REFRESH_DEADLINE.
# Never fatal — a ledger hiccup must not touch the refresh.
ledger() {
  if [ "$DRY" = 1 ]; then echo "DRY [ledger] $*"; return 0; fi
  "$PY" refresh_ledger.py record --run "$BATCH" "$@" || echo "(ledger record failed — non-fatal)"
}

# sns TOPIC SUBJECT MESSAGE (subjects: ASCII, under 100 characters)
sns() {
  if [ "$DRY" = 1 ]; then echo "DRY [sns] $2"; return 0; fi
  $AWS sns publish --region us-east-1 --topic-arn "$1" --subject "$2" --message "$3" \
    >/dev/null 2>&1 || echo "(SNS notify itself failed — check aws cli/creds)"
}

notify_failure() {
  sns "$SNS_TOPIC" "cardstock daily refresh FAILED ($BATCH)" \
    "$(printf 'Daily refresh failed at %s:%s\nSee %s\n\nLast 30 log lines:\n\n%s' \
        "$(date '+%F %T')" "$1" "$LOG" "$(tail -30 "$LOG" 2>/dev/null)")"
}

# pid of a live runner holding the lock, if there is one. An empty lock, a
# lock left by a dead run, or a pid that is something else now, is free.
lock_holder() {
  local hp
  hp=$(head -1 "$LOCK" 2>/dev/null)
  [ -n "$hp" ] && [ "$hp" != "$$" ] && kill -0 "$hp" 2>/dev/null \
    && ps -o command= -p "$hp" 2>/dev/null | grep -q "run_daily_refresh" && echo "$hp"
}
# Released by emptying it (the file itself stays).
trap '[ "$(head -1 "$LOCK" 2>/dev/null)" = "$$" ] && : > "$LOCK"' EXIT
trap 'exit 143' TERM INT

# ---- the stages -------------------------------------------------------------
do_core() {
  if [ "$TCG_CRAWL_NIGHT" = "1" ]; then
    run core "$PY" weekly_refresh.py --core --resume --to art-comps
  else
    # Catalog crawl on the daily night too (2026-10-10): --new-only makes it
    # a single request per game on a quiet night, and it is what gets a set
    # TCGplayer lists mid-week (presale) onto the site with pre-release
    # estimates by Friday instead of the following Monday. Non-fatal: a
    # crawl hiccup must not cost the price pipeline its night.
    run catalog "$PY" weekly_refresh.py --core --resume --to catalog \
      || echo "(daily catalog crawl failed — continuing with prices)"
    # Prices: Seller Portal CSV export + import (the search crawl runs only
    # for a game whose CSV didn't land), then the price build and ml prep.
    run core "$PY" weekly_refresh.py --core --resume --from tcg-csv-export --to art-comps
  fi
}

# $1 = 1 when the block restarts the API itself, 0 when the publish that
# follows does it for both.
do_model() {
  run model env TCG_MODEL_RESTART=$1 /bin/zsh local_model_run.sh || return $?
  # With its own restart the block ran apart from the publish, whose
  # prerender carried the forecasts of the night before: write the crawler
  # pages again (about 6 min on the server, reads the DBs, never fatal).
  if [ "$1" = 1 ]; then
    run prerender ssh -i "$TKEY" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=6 "ubuntu@$SERVER_IP" 'python3 /srv/tcg/prerender_pages.py' | tail -1 \
      || echo "(prerender failed — non-fatal)"
  fi
  return 0
}

do_publish() {
  run art "$PY" weekly_refresh.py --core --resume --from s3-upload \
    && run push ../deploy/push_data.sh "$SERVER_IP"
}

# attempt STAGE FN [ARGS]: run the stage; if it fails, wait and run it once
# more. Notes "ok STAGE" or adds the stage to FAILED.
FAILED=""
FLOW=""
attempt() {
  local stage=$1 rc; shift
  note "stage $stage"
  "$@"; rc=$?
  if [ $rc -ne 0 ]; then
    echo "=== $stage failed (exit $rc) — one more try in $(( RETRY_PAUSE / 60 )) min — $(date '+%F %T') ==="
    sleep $RETRY_PAUSE
    "$@"; rc=$?
  fi
  if [ $rc -eq 0 ]; then
    note "ok $stage"
  else
    note "failed $stage"
    FAILED="$FAILED $stage (exit $rc)"
    echo "=== FAILED ($stage, exit $rc, after a retry) — $(date '+%F %T') ==="
  fi
  return $rc
}

stage_publish() {
  has "ok publish" && return 0
  attempt publish do_publish && ledger --event deploy
}

main_stages() {
  if ! has "ok core"; then
    attempt core do_core || return 1
    ledger --event phase_a
  fi

  # Which order tonight. Decided once per batch: after a partial model run
  # the plan would read differently.
  FLOW=$(awk '$1=="flow"{f=$2} END{print f}' "$STATE")
  if [ -z "$FLOW" ]; then
    local plan="TCG_PUBLISH_FIRST=1"
    if [ "${TCG_PUBLISH_FIRST:-0}" = "1" ]; then
      FLOW=long
    elif [ "$DRY" = 1 ]; then
      FLOW=${DRY_PLAN:-normal}; plan="DRY_PLAN"
    else
      plan=$(/bin/zsh local_model_run.sh --plan 2>&1 | grep '^MODEL_PLAN=' | tail -1)
      # Unreadable plan -> long: the order in which the prices never wait.
      case "$plan" in MODEL_PLAN=normal*) FLOW=normal ;; *) FLOW=long ;; esac
    fi
    note "flow $FLOW"
    echo "=== tonight's order: $([ "$FLOW" = long ] && echo 'publish, then model' || echo 'model, then publish') (${plan:-no plan read}) — $(date '+%F %T') ==="
  fi

  if [ "$FLOW" = "long" ]; then
    stage_publish
    if ! has "ok model"; then attempt model do_model 1 && ledger --event model; fi
    return 0
  fi

  # normal: the model block first, one restart for both at the publish.
  local own_restart=0 rc=0 again=0
  if ! has "ok model"; then
    has "ok publish" && own_restart=1     # a resume after the prices went out
    note "stage model"
    do_model $own_restart; rc=$?
    if [ $rc -eq 0 ]; then
      note "ok model"; ledger --event model
    else
      again=1
      echo "=== model block failed (exit $rc) — publishing the prices now, then one more try — $(date '+%F %T') ==="
    fi
  else
    own_restart=1
  fi
  stage_publish
  if [ $again = 1 ]; then
    do_model 1; rc=$?
    if [ $rc -eq 0 ]; then
      note "ok model"; ledger --event model
    else
      note "failed model"
      FAILED="$FAILED model (exit $rc)"
      echo "=== FAILED (model, exit $rc, after a retry) — prod predictions.db unchanged — $(date '+%F %T') ==="
    fi
  elif [ $own_restart = 0 ] && ! has "ok publish"; then
    # The forecasts reached the server but the restart that would have
    # served them was the failed publish's.
    echo "=== publish failed after a good model block — restarting the API so the new forecasts are served ==="
    run restart ssh -i "$TKEY" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 "ubuntu@$SERVER_IP" \
      'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api' \
      || echo "(API restart failed — non-fatal)"
  fi
  return 0
}

{
  # ---- one runner at a time ----
  HP=$(lock_holder)
  if [ -n "$HP" ]; then
    HB=$(sed -n 2p "$LOCK" 2>/dev/null)
    if [ "$RESUME" = 1 ] || [ "$HB" = "$BATCH" ]; then
      echo "(runner pid $HP is already on batch ${HB:-?} — nothing to start, $(date '+%F %T'))"
      exit 0
    fi
    # A new night while the one before is still going. Past its "finished"
    # line it is only running the tail: end that and take over.
    if grep -q '^finished ' "$STATE_DIR/$HB.state" 2>/dev/null; then
      echo "=== batch $HB (pid $HP) is still in its tail — stopping it for tonight's run $(date '+%F %T') ==="
      run stoptail pkill -TERM -f "weekly_refresh.py --tail"
      for _w in $(seq 1 90); do
        [ -z "$(lock_holder)" ] && break
        [ "$DRY" = 1 ] && break
        sleep 20
      done
    fi
    HP=$(lock_holder)
    if [ -n "$HP" ]; then
      echo "=== batch $BATCH NOT started: batch $HB (pid $HP) is still running — $(date '+%F %T') ==="
      sns "$SNS_TOPIC" "cardstock refresh SKIPPED ($BATCH)" \
        "$(printf 'The %s refresh did not start at %s: the %s run (pid %s) is still going.\nSee %s' \
            "$BATCH" "$(date '+%F %T')" "$HB" "$HP" "$LOG_DIR/refresh-$HB.log")"
      exit 1
    fi
  fi
  printf '%s\n%s\n' "$$" "$BATCH" > "$LOCK"

  # ---- fresh night or resume ----
  if [ "$FRESH" = 1 ]; then
    # Forgotten by emptying the batch's notes (an empty note file reads as
    # none everywhere).
    for _f in "$STATE" "$STATE_DIR/$BATCH.steps" "$STATE_DIR/$BATCH.model_steps" "$STATE_DIR/$BATCH.horizons"; do
      [ -f "$_f" ] && : > "$_f"
    done
    [ "$DRY" != 1 ] && [ -f "$ONEPIECE/ml_data/forecast_progress.json" ] && : > "$ONEPIECE/ml_data/forecast_progress.json"
  fi
  if has started; then
    if has "done" && [ "$RETRY" = 0 ]; then
      echo "(batch $BATCH already ran to its end — --retry-failed reruns failed stages, --fresh starts it over; $(date '+%F %T'))"
      exit 0
    fi
    MODE=resume
    NIGHT=$(awk '$1=="night"{n=$2} END{print n}' "$STATE")
    note "resume"
    echo "=== RESUMING batch $BATCH (${NIGHT} night) at $(date '+%F %T') — noted so far: $(grep -E '^(ok|failed|finished) ' "$STATE" | cut -d' ' -f1,2 | tr '\n' ',') ==="
  elif [ "$RESUME" = 1 ]; then
    echo "(no started night $BATCH to resume)"
    exit 1
  else
    MODE=fresh
    # Which night this is, from the LOCAL weekday (2026-10-18): Saturday is
    # the crawl + full-rebuild night; every other night is a CSV night.
    if [ "$DRY" = 1 ] && [ -n "${DRY_NIGHT:-}" ]; then NIGHT=$DRY_NIGHT
    elif [ "$(date +%u)" = "6" ]; then NIGHT=crawl
    else NIGHT=csv
    fi
    note "night $NIGHT"
    note "started"
    ledger --event started --kind "$NIGHT"
    if [ "$NIGHT" = crawl ]; then
      echo "=== WEEKLY full refresh (crawl night) — $(date '+%F %T') ==="
    else
      echo "=== daily refresh (CSV night) — $(date '+%F %T') ==="
    fi
  fi
  # Exported for weekly_refresh.py (CRAWL_NIGHT), build_unified_history.py
  # (full rebuild) and local_model_run.sh (long horizons).
  if [ "$NIGHT" = crawl ]; then export TCG_CRAWL_NIGHT=1; else export TCG_CRAWL_NIGHT=0; fi

  # ---- the right AWS account, or nothing (see the note above) ----
  if [ "$DRY" != 1 ]; then
    ACCT=""
    for _try in $(seq 1 10); do
      ACCT=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null) && break
      sleep 30
    done
    if [ "$ACCT" != "$CARDSTOCK_ACCT" ]; then
      # No "done" is noted, so the watchdog brings the night back up.
      echo "ABORT: aws resolves to account '${ACCT:-none}', not cardstock ($CARDSTOCK_ACCT) — check ~/.aws profiles and the network ($(date '+%F %T'))"
      exit 1
    fi
  fi

  # ---- the machine ----
  FREE_GB=$(df -g "$ONEPIECE" 2>/dev/null | awk 'NR==2{print $4}')
  echo "machine: ${FREE_GB:-?} GB free on the data volume; swap $(sysctl -n vm.swapusage 2>/dev/null | sed 's/  */ /g')"
  if [ "$MODE" = fresh ] && [ "${FREE_GB:-99}" -lt "$MIN_FREE_GB" ]; then
    echo "=== LOW DISK: ${FREE_GB} GB free (under $MIN_FREE_GB) — the night goes on ==="
    sns "$SNS_TOPIC" "cardstock refresh: low disk (${FREE_GB} GB free)" \
      "$(printf 'The %s refresh started with %s GB free on the data volume (warning line: %s GB).\nunify and the forecast publish write multi-GB files; a full disk fails the night.' \
          "$BATCH" "$FREE_GB" "$MIN_FREE_GB")"
  fi

  # ---- core, model, publish ----
  if ! has finished || [ "$RETRY" = 1 ]; then
    main_stages
    if [ -z "$FAILED" ]; then
      ledger --event finished --status ok
      note "finished ok"
      echo "=== refresh complete ($FLOW order) — $(date '+%F %T') ==="
      sns "arn:aws:sns:us-east-1:522029196375:cardstock-refresh-reports" \
        "cardstock refresh done ($BATCH)" \
        "$(printf 'Refresh finished %s — prices, forecasts and the prod push are all in. The rotation crawls run on from here.\n\nRecent nights (vs the 06:00 target):\n%s\n\nSteps:\n%s\n\nModel block:\n%s' \
            "$(date '+%F %T')" \
            "$("$PY" refresh_ledger.py report --last 7 2>/dev/null)" \
            "$(grep -E '^  (ok|FAIL) |^Done\. [0-9]+ Near Mint|^total budget' "$LOG" 2>/dev/null | head -40)" \
            "$(grep -E '^--- (forecast_|market_report).* rc=|rows -> |Wrote report|forecast_launch: |forecast_prerelease: |predictions.db pushed' "$LOG" 2>/dev/null | tail -14)")"
      # Best-effort S3 backups (nightly CSVs; Saturdays add the big dumps) —
      # never fails the refresh.
      run backup /bin/zsh backup_to_s3.sh || echo "(backup_to_s3 failed — non-fatal)"
    else
      ledger --event finished --status failed --notes "${FAILED# }"
      note "finished failed"
      notify_failure "$FAILED"
    fi
  fi

  # ---- the tail: rotation crawls nothing tonight waited for ----
  trc=0
  if ! has "ok tail"; then
    note "stage tail"
    echo "=== the tail (rotation crawls) — $(date '+%F %T') ==="
    run tail "$PY" weekly_refresh.py --tail --resume; trc=$?
    [ $trc -eq 0 ] && note "ok tail"
  fi
  # Per-printing PC history for variants the ingest discovered (bounded,
  # labeled-namespace-only), then the match review for what pc-link-suggest
  # queued. Both follow the tail steps that feed them. Skipped when the next
  # night ended the tail (143) or it is late on the batch's own day.
  if [ $trc -ne 143 ] && ! has "ok post" && [[ "$(date '+%F %H%M')" < "$BATCH 1730" ]]; then
    run backfill "$PY" pc_printing_backfill.py --limit 600 || echo "(pc_printing_backfill failed — non-fatal)"
    if [ "$DRY" = 1 ]; then echo "DRY [review] match_review.py --if-pending"
    else "$PY" match_review.py --if-pending >> "$LOG_DIR/match_review.log" 2>&1 &!
    fi
    note "ok post"
  fi
  note "done"
  echo "=== night $BATCH closed — $(date '+%F %T') ==="
} >> "$LOG" 2>&1
