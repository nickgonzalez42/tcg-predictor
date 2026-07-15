#!/bin/zsh
# Push DATA to the server: card DBs, predictions/pricecharting, images.
# Run after any pipeline pass you want live (the nightly, a backfill, etc).
# rsync writes each file to a temp name and renames — atomic per file, and the
# running API keeps its old handles until the restart at the end. store.db
# (users/portfolios) is NEVER pushed; it lives on the server only.
# Usage: deploy/push_data.sh <ip> [--skip-images]
set -euo pipefail
IP=${1:?usage: push_data.sh <server-ip> [--skip-images]}
SKIP_IMAGES=${2:-}
KEY=~/.ssh/tcg-predictor.pem
RS="rsync -az --partial --stats -e"
SSH_CMD="ssh -i $KEY"
DATA=/Users/nicholasgonzalez/Developer/Projects/parent/one-piece
API_DATA=/Users/nicholasgonzalez/Developer/Projects/parent/tcg-predictor/dotnet/API/Data/cards

# A -wal sidecar means a writer is mid-flight on that DB — push would ship a
# torn database. Finish or pause the pipeline step first. The (N) null-glob
# qualifier makes a no-match (the healthy case) expand to nothing instead of
# erroring out under `set -e`.
for f in $DATA/*_cards.db-wal(N) $API_DATA/*.db-wal(N); do
  [ -e "$f" ] && { echo "ABORT: $f exists (active writer)"; exit 1; }
done

echo "== card DBs =="
${=RS} "$SSH_CMD" $DATA/*_cards.db ubuntu@$IP:/srv/tcg/data/

echo "== predictions + pricecharting =="
${=RS} "$SSH_CMD" $API_DATA/predictions.db $API_DATA/pricecharting.db \
  ubuntu@$IP:/srv/tcg/data/cards/

if [ "$SKIP_IMAGES" != "--skip-images" ]; then
  echo "== images (delta only; resumable) =="
  for d in images images_pokemon images_yugioh images_magic images_lorcana images_digimon images_gundam; do
    [ -d "$DATA/$d" ] && ${=RS} "$SSH_CMD" "$DATA/$d/" ubuntu@$IP:/srv/tcg/data/$d/
  done
fi

echo "== restart API =="
$SSH_CMD ubuntu@$IP 'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api'
echo "data live on http://$IP"
