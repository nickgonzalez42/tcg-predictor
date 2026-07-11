#!/bin/zsh
# One-time backfill for the games added 2026-07: yugioh, magic, lorcana,
# digimon, gundam. Catalogs + art first (smallest game first), then prices,
# the PriceCharting chart-page history crawl (the long pole — roughly two
# days of polite 1 req/s crawling), and a full model retrain.
#
# Everything is serial, rate-limited, and resumable: rerun this script after
# any interruption and it continues where it left off (per-set scan records,
# existing images, --resume on the graded crawl, weekly_refresh's own lock).
set -u
cd "$(dirname "$0")"
PY=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece/.venv/bin/python

# Patient mode: network failures make the crawl WAIT (retrying every 5 min)
# instead of skipping work — an internet outage pauses the backfill, nothing
# is lost or stranded.
export TCG_PATIENT=1

for g in gundam lorcana digimon yugioh magic; do
  echo "=== backfill catalog: $g — $(date '+%F %T') ==="
  "$PY" tcg_scraper.py --game "$g" --new-only --no-history || exit 1
done
"$PY" sync_local_images.py

echo "=== catalogs done — prices + graded history + model — $(date '+%F %T') ==="
# Retry loop: if a step still manages to fail, resume from the price download
# after 5 minutes (pc-graded-new resumes card-by-card, the rest are cheap).
until "$PY" weekly_refresh.py --from pc-download; do
  echo "=== refresh failed — resuming in 5 min — $(date '+%F %T') ==="
  sleep 300
done
