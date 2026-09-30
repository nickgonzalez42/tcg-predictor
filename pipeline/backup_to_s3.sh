#!/bin/zsh
# S3 backups of the irreplaceable pipeline data (backlog item, done 2026-08-06).
# The three-way Mac/prod/trainer replication is NOT a backup — all copies sync
# within a day, so corruption or a bad pipeline write propagates everywhere
# before anyone notices. This puts point-in-time snapshots out of reach:
#
#   nightly            curated ml_data CSVs (price corrections, match overrides,
#                      tcg_nm_only — hand-maintained, tiny, priceless)
#   Saturdays/--weekly graded_price_history + tcg_nm_history + match table
#                      (the post-subscription-unrecoverable crawl record) and
#                      the card catalogs
#
# Retention is by bucket lifecycle (pipeline/nightly/ 30d, pipeline/weekly/ 90d),
# so this script only ever uploads dated objects. store.db and the forecast
# archive are backed up FROM PROD (cardstock-backup-store.sh cron) — they live
# there, not here. Called at the end of the nightly refresh, best-effort (a
# backup failure never fails the refresh).
set -u
AWS=/usr/local/bin/aws
# Pin to the cardstock account (see run_daily_refresh.sh): never let a leaked
# AWS_PROFILE aim these uploads at the client (870397520032) account.
export AWS_PROFILE=default
ACCT=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null)
[ "$ACCT" = "522029196375" ] || { echo "ABORT: wrong AWS account '${ACCT:-none}'"; exit 1 }
DEST=s3://cardstock-backups/pipeline
D=$(date -u +%F)   # UTC day basis (2026-08-14) — matches batch labels + log names
ONEPIECE=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
PC_DB=/Users/nicholasgonzalez/Developer/Projects/parent/tcg-predictor/dotnet/API/Data/cards/pricecharting.db
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "=== backup_to_s3 $D $(date '+%T') ==="
tar -czf "$TMP/ml_csvs-$D.tar.gz" -C "$ONEPIECE/ml_data" \
  $(cd "$ONEPIECE/ml_data" && ls *.csv 2>/dev/null | tr '\n' ' ')
$AWS s3 cp "$TMP/ml_csvs-$D.tar.gz" "$DEST/nightly/" --only-show-errors || exit 1

# -v-12H: the run's nominal day (10 PM start crosses midnight)
# Friday UTC = the Thursday-evening run (2026-09-20 twice-weekly cadence).
if [ "$(date -u +%u)" = "5" ] || [ "${1:-}" = "--weekly" ]; then
  echo "--- weekly: crawl history + catalogs ---"
  sqlite3 "$PC_DB" "
    ATTACH '$TMP/pc_crawl-$D.db' AS b;
    CREATE TABLE b.graded_price_history AS SELECT * FROM graded_price_history;
    CREATE TABLE b.tcg_nm_history       AS SELECT * FROM tcg_nm_history;
    CREATE TABLE b.pricecharting        AS SELECT * FROM pricecharting;
    DETACH b;" || exit 1
  gzip "$TMP/pc_crawl-$D.db"
  $AWS s3 cp "$TMP/pc_crawl-$D.db.gz" "$DEST/weekly/" --only-show-errors || exit 1
  for f in $ONEPIECE/*_cards.db; do
    gzip -c "$f" > "$TMP/$(basename "$f" .db)-$D.db.gz"
    $AWS s3 cp "$TMP/$(basename "$f" .db)-$D.db.gz" "$DEST/weekly/" --only-show-errors || exit 1
    rm -f "$TMP/$(basename "$f" .db)-$D.db.gz"
  done
fi
echo "=== backup_to_s3 done $(date '+%T') ==="
