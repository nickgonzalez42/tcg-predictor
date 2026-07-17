#!/bin/zsh
# Supervised Magic onboarding: catalog + art + prices + graded history,
# COEXISTING with the 3:00 AM daily refresh. Every phase is resumable, and
# the whole run is pause-aware: whenever the daily refresh (or a manual
# weekly_refresh / data push) is active, the current phase is interrupted
# and restarted once it finishes — the daily pull always wins.
#
# Run it in a terminal or with nohup; rerun after ANY interruption and it
# continues where it left off. Expect the catalog (436 sets, ~112k products)
# to take a couple of days at the polite rate limit, and the graded-history
# crawl about as long again. Needs ~12 GB free disk for staged art (pruned
# automatically a week after it reaches S3).
set -u
cd "$(dirname "$0")"

# Hold off system sleep for the duration (display may still sleep). Does not
# survive a closed lid.
if [[ -z "${CAFFEINATED:-}" ]]; then
  export CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$0" "$@"
fi

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
export TCG_PATIENT=1   # network outages pause the crawl instead of failing it

daily_running() {
  pgrep -f "run_daily_refresh.sh|weekly_refresh.py|push_data.sh" > /dev/null 2>&1
}

# Run a resumable command, yielding to the daily refresh: if one starts, the
# child gets SIGINT (safe — per-set / per-card commits) and is restarted from
# where it stopped once the refresh (and its data push) finish.
run_with_pauses() {
  while :; do
    while daily_running; do sleep 120; done
    "$@" &
    local child=$!
    local paused=0
    while kill -0 $child 2>/dev/null; do
      if daily_running; then
        paused=1
        echo "--- daily refresh detected: pausing — $(date '+%F %T') ---"
        kill -INT $child 2>/dev/null
        wait $child 2>/dev/null
        break
      fi
      sleep 60
    done
    if (( paused )); then
      while daily_running; do sleep 120; done
      echo "--- daily refresh done: resuming — $(date '+%F %T') ---"
      continue
    fi
    wait $child 2>/dev/null
    return $?
  done
}

# A phase retries until it succeeds (transient failures wait 5 minutes).
phase() {
  local name=$1; shift
  echo "=== $name — $(date '+%F %T') ==="
  until run_with_pauses "$@"; do
    echo "=== $name failed — retrying in 5 min — $(date '+%F %T') ==="
    sleep 300
  done
}

phase "catalog + art (436 sets, the first long pole)" \
  "$PY" tcg_scraper.py --game magic --new-only --no-history
phase "CLIP-embed new art (before upload, like the daily order)" \
  "$PY" embed_images.py
phase "upload art to S3" \
  "$PY" s3_upload_images.py --no-prune
phase "PriceCharting download" "$PY" download_pricecharting.py
phase "PriceCharting match"    "$PY" build_pricecharting.py
phase "graded history crawl (the second long pole)" \
  "$PY" scrape_graded_history.py --game magic --resume --workers 1 --delay 1.0
phase "unify price history"    "$PY" build_unified_history.py
phase "near-mint price column" "$PY" backfill_nm_price.py
phase "art-sync (image_path from the bucket)" "$PY" sync_local_images.py

echo "=== MAGIC BACKFILL COMPLETE — $(date '+%F %T') ==="
echo "Magic goes live with the next daily refresh + push (forecasts retrain"
echo "then too). Remember the site copy: 'six TCGs' becomes seven, and the"
echo "meta descriptions gain Magic."
