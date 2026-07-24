#!/bin/zsh
# Live progress view for the daily price + model refresh.
# Open it anytime (double-click, or: zsh pipeline/watch_refresh.sh). Closing
# this window does NOT stop the run — it's just a tail of the log.
LOG=$(ls -t "$HOME/Library/Logs/tcg-predictor"/refresh-*.log 2>/dev/null | head -1)
if [[ -z "$LOG" ]]; then
  echo "No refresh log yet — the daily job runs at 1:00 AM."
  echo "(Safe to close this window.)"
else
  echo "=== watching: $LOG"
  echo "=== close this window anytime; the refresh keeps running in the background"
  echo
  tail -n 30 -f "$LOG"
fi
