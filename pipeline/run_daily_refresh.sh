#!/bin/zsh
# Daily data refresh (launchd entry point): dated logs, 30-day retention.
# The pipeline itself skips cleanly if a previous run is still going.
set -u
cd "$(dirname "$0")"

LOG_DIR="$HOME/Library/Logs/tcg-predictor"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "refresh-*.log" -mtime +30 -delete 2>/dev/null

exec /Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python \
  weekly_refresh.py >> "$LOG_DIR/refresh-$(date +%Y-%m-%d).log" 2>&1
