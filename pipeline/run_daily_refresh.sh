#!/bin/zsh
# Scheduled refresh (launchd entry point, 8:00 AM daily — see
# ~/Library/LaunchAgents/com.tcg-predictor.daily-refresh.plist).
#
#   Mon–Sat: prices + model  (weekly_refresh from pc-download), pushed to
#            production WITHOUT images.
#   Sunday:  full run — TCGplayer catalog scrape for new cards + art first,
#            then the same chain; the push includes new images.
#
# store.db (accounts/portfolios) is never pushed — user data lives on the
# server only. Every step is resumable: on failure the log names the step;
# rerun weekly_refresh.py --from <step>, then deploy/push_data.sh manually.
# weekly_refresh has its own lock, so an overlapping run skips cleanly.
set -u
cd "$(dirname "$0")"

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
SERVER_IP=35.168.177.31

LOG_DIR="$HOME/Library/Logs/tcg-predictor"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "refresh-*.log" -mtime +30 -delete 2>/dev/null
LOG="$LOG_DIR/refresh-$(date +%Y-%m-%d).log"

{
  if [ "$(date +%u)" = "7" ]; then
    echo "=== SUNDAY full refresh (new cards + prices + model) — $(date '+%F %T') ==="
    "$PY" weekly_refresh.py && ../deploy/push_data.sh "$SERVER_IP"
  else
    echo "=== daily refresh (prices + model) — $(date '+%F %T') ==="
    "$PY" weekly_refresh.py --from pc-download && ../deploy/push_data.sh "$SERVER_IP" --skip-images
  fi
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "=== refresh + push complete — $(date '+%F %T') ==="
  else
    echo "=== FAILED (exit $rc): fix, resume with weekly_refresh.py --from <step>, then deploy/push_data.sh $SERVER_IP — $(date '+%F %T') ==="
  fi
} >> "$LOG" 2>&1
