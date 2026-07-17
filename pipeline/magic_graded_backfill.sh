#!/bin/zsh
# Graded-history head start for Magic, run WHILE magic_backfill.sh is still
# crawling the TCGplayer catalog. The two hit different sites (catalog ->
# TCGplayer, graded history -> PriceCharting), so their rate limits don't
# stack — same trick as the Yu-Gi-Oh onboarding.
#
# Behavior:
#   - Crawls graded history for every Magic product matched so far; new
#     matches land with each 2:00 AM refresh, so when caught up it naps and
#     re-checks every 30 minutes.
#   - Pauses (SIGTERM, resumable) whenever the daily refresh, a data push, or
#     the main backfill's PriceCharting phases are running — one polite
#     crawler per site, and no writer contention on pricecharting.db.
#   - Exits for good the moment the main backfill starts its own graded
#     phase: that phase takes over the identical resumable crawl.
set -u
cd "$(dirname "$0")"

if [[ -z "${CAFFEINATED:-}" ]]; then
  export CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$0" "$@"
fi

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
export TCG_PATIENT=1   # network outages pause the crawl instead of failing it

child=""

# Anything that crawls PriceCharting or writes pricecharting.db.
must_pause() {
  pgrep -f "run_daily_refresh.sh|weekly_refresh.py|push_data.sh|download_pricecharting.py|build_pricecharting.py" > /dev/null 2>&1
}

# A scrape_graded_history for magic that isn't our child = the main
# backfill's graded phase has begun.
handoff_due() {
  pgrep -f "scrape_graded_history.py --game magic" 2>/dev/null | grep -qvx "${child:-0}"
}

hand_off() {
  echo "=== main backfill graded phase detected — handing off — $(date '+%F %T') ==="
  exit 0
}

while :; do
  handoff_due && hand_off
  while must_pause; do sleep 120; done

  "$PY" scrape_graded_history.py --game magic --resume --workers 1 --delay 1.0 &
  child=$!
  paused=0
  while kill -0 $child 2>/dev/null; do
    if handoff_due; then
      kill -TERM $child 2>/dev/null
      wait $child 2>/dev/null
      hand_off
    fi
    if must_pause; then
      paused=1
      echo "--- pause condition detected: pausing — $(date '+%F %T') ---"
      kill -TERM $child 2>/dev/null
      wait $child 2>/dev/null
      break
    fi
    sleep 60
  done
  if (( paused )); then
    child=""
    while must_pause; do sleep 120; done
    echo "--- pause over: resuming — $(date '+%F %T') ---"
    continue
  fi
  wait $child 2>/dev/null
  rc=$?
  child=""
  if (( rc != 0 )); then
    echo "=== crawl exited rc=$rc — retrying in 5 min — $(date '+%F %T') ==="
    sleep 300
    continue
  fi
  echo "--- caught up with matched products — next check in 30 min — $(date '+%F %T') ---"
  sleep 1800
done
