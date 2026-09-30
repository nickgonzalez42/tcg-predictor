#!/bin/zsh
# Supervised Star Wars Unlimited onboarding: catalog + art only, COEXISTING
# with the 1:00 AM daily refresh (same pattern as magic_backfill.sh). Every
# phase is resumable, and the run is pause-aware: whenever the daily refresh
# (or a manual weekly_refresh / data push) is active, the current phase is
# interrupted and restarted once it finishes — the daily pull always wins.
#
# No PriceCharting phase: no bulk category slug exists for this game yet
# (see games.py) — a console-page price scraper (gundam-style) is a separate
# follow-up once the catalog is in. Until then the site hides these cards
# (unpriced = filtered out automatically).
#
# Run it in a terminal or with nohup; rerun after ANY interruption and it
# continues where it left off. 27 sets / ~7,900 products — expect well under
# a day at the polite rate limit.
set -u
cd "$(dirname "$0")"

if [[ -z "${CAFFEINATED:-}" ]]; then
  export CAFFEINATED=1
  exec caffeinate -i /bin/zsh "$0" "$@"
fi

PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python
export TCG_PATIENT=1   # network outages pause the crawl instead of failing it

daily_running() {
  pgrep -f "run_daily_refresh.sh|weekly_refresh.py|push_data.sh" > /dev/null 2>&1
}

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
        kill -TERM $child 2>/dev/null
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

phase() {
  local name=$1; shift
  echo "=== $name — $(date '+%F %T') ==="
  until run_with_pauses "$@"; do
    echo "=== $name failed — retrying in 5 min — $(date '+%F %T') ==="
    sleep 300
  done
}

phase "catalog + art (27 sets)" \
  "$PY" tcg_scraper.py --game starwars --new-only --no-history
phase "CLIP-embed new art" \
  "$PY" embed_images.py
phase "upload art to S3" \
  "$PY" s3_upload_images.py --no-prune
phase "art-sync (image_path from the bucket)" \
  "$PY" sync_local_images.py

echo "=== STAR WARS UNLIMITED CATALOG BACKFILL COMPLETE — $(date '+%F %T') ==="
echo "No prices yet — cards are cataloged but hidden site-wide until a"
echo "PriceCharting price source is built (see games.py comment)."
