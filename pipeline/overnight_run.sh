#!/bin/zsh
# Overnight chain (2026-07-13): wait for the gundam crawl + unify (PID $1),
# then run the rest of the pipeline through the model, then push data live.
# Fully detached — no interaction needed. Log: pipeline/overnight.log
set -u
cd "$(dirname "$0")"
PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python

echo "=== overnight: waiting for crawl+unify chain (PID $1) — $(date '+%F %T') ==="
while kill -0 "$1" 2>/dev/null; do sleep 120; done
echo "=== chain done — starting nm-price → … → forecast — $(date '+%F %T') ==="

if "$PY" weekly_refresh.py --from nm-price; then
  echo "=== model pass complete — pushing data to server — $(date '+%F %T') ==="
  ../deploy/push_data.sh 35.168.177.31 --skip-images
  echo "=== overnight complete — $(date '+%F %T') ==="
else
  echo "=== weekly_refresh FAILED — resume with --from <step> (see above) — $(date '+%F %T') ==="
fi
