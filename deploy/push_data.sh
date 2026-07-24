#!/bin/zsh
# Push DATA to the server: card DBs, predictions/pricecharting.
# Run after any pipeline pass you want live (the nightly, a backfill, etc).
# rsync writes each file to a temp name and renames — atomic per file, and the
# running API keeps its old handles until the restart at the end. store.db
# (users/portfolios) is NEVER pushed; it lives on the server only. Card art
# doesn't ride this push either — it lives in S3 (s3_upload_images.py) and is
# served through CloudFront.
# Usage: deploy/push_data.sh <ip>
set -euo pipefail
IP=${1:?usage: push_data.sh <server-ip>}
KEY=~/.ssh/tcg-predictor.pem
RS="rsync -az --partial --stats -e"
SSH_CMD="ssh -i $KEY"
DATA=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
API_DATA=/Users/nicholasgonzalez/Developer/Projects/parent/tcg-predictor/dotnet/API/Data/cards

# A -wal or -journal sidecar means a writer is mid-flight on that DB — push
# would ship a torn database. Finish or pause the pipeline step first. (The
# pipeline's sqlite connections use the default rollback journal, so -journal
# is the sidecar that actually appears; -wal is kept in case a step ever opts
# into WAL.) The (N) null-glob qualifier makes a no-match (the healthy case)
# expand to nothing instead of erroring out under `set -e`.
for f in $DATA/*_cards.db-wal(N) $DATA/*_cards.db-journal(N) \
         $API_DATA/*.db-wal(N) $API_DATA/*.db-journal(N); do
  [ -e "$f" ] && { echo "ABORT: $f exists (active writer)"; exit 1; }
done

echo "== card DBs =="
${=RS} "$SSH_CMD" $DATA/*_cards.db ubuntu@$IP:/srv/tcg/data/

echo "== predictions + pricecharting =="
${=RS} "$SSH_CMD" $API_DATA/predictions.db $API_DATA/pricecharting.db \
  ubuntu@$IP:/srv/tcg/data/cards/

echo "== restart API =="
# ${=SSH_CMD} forces zsh word-splitting so "ssh -i <key>" isn't run as one word.
${=SSH_CMD} ubuntu@$IP 'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api'
echo "data live on http://$IP"
