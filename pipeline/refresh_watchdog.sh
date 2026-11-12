#!/bin/zsh
# Watches the nightly refresh (launchd: com.tcg-predictor.refresh-watchdog,
# every 15 minutes, and once at login so a reboot mid-night is picked up).
#
# It reads the newest ml_data/refresh_state/<batch>.state that
# run_daily_refresh.sh keeps, and does one of two things:
#
#   resume  The night has "started" but no "done" and no runner is alive (a
#           reboot, a crash, a killed shell): start
#           `run_daily_refresh.sh --resume <batch>`, which skips every stage
#           and step already noted. At most MAX_RESUMES times a night, and
#           only until RESUME_UNTIL on the batch's own day — after that the
#           next night's start is close and takes over.
#   alert   A runner is alive but the night's log has not been written for
#           QUIET_MIN minutes: one SNS message per night. It never stops
#           anything — a slow night is not a hung one, and the 07:00 finish
#           is a goal, not a limit.
#
# To keep it from resuming a night stopped on purpose:
#   touch one-piece/ml_data/refresh_state/HOLD      (remove the file to undo)
#
#   refresh_watchdog.sh            one check
#   WD_DIR=/scratch WD_DRY=1 refresh_watchdog.sh    a check against scratch
#                                  state (WD_DIR holds the .state files and
#                                  .daily_refresh.lock); prints what it would do
set -u
SELF="${0:A}"
cd "${SELF:h}"

ONEPIECE=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
LOG_DIR="$HOME/Library/Logs/tcg-predictor"
WLOG="$LOG_DIR/refresh-watchdog.log"
AWS=/usr/local/bin/aws
export AWS_PROFILE=default
CARDSTOCK_ACCT=522029196375
SNS_TOPIC=arn:aws:sns:us-east-1:522029196375:cardstock-problem-reports
MAX_RESUMES=3
RESUME_UNTIL=1700
QUIET_MIN=${TCG_WD_QUIET_MIN:-240}

DRY=${WD_DRY:-0}
if [ -n "${WD_DIR:-}" ]; then
  STATE_DIR="$WD_DIR"; LOCK="$WD_DIR/.daily_refresh.lock"
else
  STATE_DIR="$ONEPIECE/ml_data/refresh_state"; LOCK="$ONEPIECE/.daily_refresh.lock"
fi
mkdir -p "$LOG_DIR"

say() {
  if [ "$DRY" = 1 ]; then echo "$*"; else echo "$(date '+%F %T') $*" >> "$WLOG"; fi
}

# Same account pin as the refresh itself: the wrong account gets no message.
sns() {
  if [ "$DRY" = 1 ]; then echo "DRY [sns] $1"; return 0; fi
  local acct
  acct=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null)
  if [ "$acct" != "$CARDSTOCK_ACCT" ]; then
    say "(no SNS: aws resolves to account '${acct:-none}')"; return 1
  fi
  $AWS sns publish --region us-east-1 --topic-arn "$SNS_TOPIC" --subject "$1" --message "$2" \
    >/dev/null 2>&1 || say "(SNS publish failed)"
}

[ -f "$STATE_DIR/HOLD" ] && exit 0
states=("$STATE_DIR"/*.state(N))
[ ${#states} -eq 0 ] && exit 0
STATE="${states[-1]}"
BATCH="${${STATE:t}%.state}"
grep -q '^started ' "$STATE" || exit 0
grep -q '^done ' "$STATE" && exit 0

# The runner holding the lock, if it is alive (run_daily_refresh.sh writes
# its pid on the lock's first line and empties the file when it ends).
HP=$(head -1 "$LOCK" 2>/dev/null)
if [ -n "$HP" ] && kill -0 "$HP" 2>/dev/null \
   && ps -o command= -p "$HP" 2>/dev/null | grep -q "run_daily_refresh"; then
  LOG="$LOG_DIR/refresh-$(sed -n 2p "$LOCK" 2>/dev/null).log"
  [ -f "$LOG" ] || exit 0
  quiet=$(( ( $(date +%s) - $(stat -f %m "$LOG") ) / 60 ))
  FLAG="$STATE_DIR/$BATCH.quiet"
  if [ $quiet -ge $QUIET_MIN ] && [ ! -s "$FLAG" ]; then
    echo "alerted $(date '+%F %T')" > "$FLAG"
    say "batch $BATCH: runner pid $HP alive, log quiet for $quiet min — alert sent"
    sns "cardstock refresh may be hung ($BATCH)" \
      "$(printf 'The %s refresh (pid %s) is alive but has written nothing to its log for %s minutes.\nNothing was stopped. See %s\n\nLast 15 log lines:\n\n%s' \
          "$BATCH" "$HP" "$quiet" "$LOG" "$(tail -15 "$LOG" 2>/dev/null)")"
  fi
  exit 0
fi

# No runner holds the lock. A runner in its first second (before the lock is
# written) or any other live one means there is nothing to bring back.
[ "$DRY" != 1 ] && pgrep -f "run_daily_refresh.sh" >/dev/null 2>&1 && exit 0

if [[ ! "$(date '+%F %H%M')" < "$BATCH $RESUME_UNTIL" ]]; then
  exit 0      # too late for this batch; tonight's start takes over
fi
resumes=$(grep -c '^resume ' "$STATE")
if [ "$resumes" -ge $MAX_RESUMES ]; then
  FLAG="$STATE_DIR/$BATCH.gaveup"
  if [ ! -s "$FLAG" ]; then
    echo "gave up $(date '+%F %T')" > "$FLAG"
    say "batch $BATCH: died again after $resumes resumes — not resuming any more"
    sns "cardstock refresh keeps dying ($BATCH)" \
      "$(printf 'The %s refresh stopped again after %s automatic resumes; the watchdog leaves it alone now.\nState: %s\nBy hand: pipeline/run_daily_refresh.sh --resume %s' \
          "$BATCH" "$resumes" "$(tr '\n' ';' < "$STATE")" "$BATCH")"
  fi
  exit 0
fi

at=$(grep -E '^(stage|ok|failed|finished) ' "$STATE" | tail -1)
say "batch $BATCH: started, not done, no runner alive (last note: ${at:-none}) — resuming (resume $(( resumes + 1 )) of $MAX_RESUMES)"
if [ "$DRY" = 1 ]; then
  echo "DRY [resume] /bin/zsh ${SELF:h}/run_daily_refresh.sh --resume $BATCH"
  exit 0
fi
nohup /bin/zsh "${SELF:h}/run_daily_refresh.sh" --resume "$BATCH" >/dev/null 2>&1 &!
sns "cardstock refresh resumed ($BATCH)" \
  "$(printf 'The %s refresh had stopped without finishing (last note: %s). The watchdog started it again at %s; it picks up at the step that was running.\nLog: %s' \
      "$BATCH" "${at:-none}" "$(date '+%F %T')" "$LOG_DIR/refresh-$BATCH.log")"
exit 0
