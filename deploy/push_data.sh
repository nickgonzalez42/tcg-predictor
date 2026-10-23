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
# GNU rsync (brew) when present: macOS's bundled openrsync corrupted the
# 4.4GB pricecharting.db delta on 2026-08-05 ("failed verification -- update
# discarded", exit 23) — big-file delta transfer is exactly its weak spot.
# --partial-dir, never bare --partial (2026-10-13): when the server's disk
# filled mid-push, --partial renamed the truncated temp file OVER the live
# pricecharting.db, corrupting prod. --partial-dir keeps the resumable piece
# in a side directory instead, so the destination only ever changes via the
# atomic rename of a COMPLETED transfer.
RSYNC=$([ -x /opt/homebrew/bin/rsync ] && echo /opt/homebrew/bin/rsync || echo rsync)
RS="$RSYNC -az --partial-dir=.rsync-partial --stats -e"
# Keepalive (2026-10-20): the Monday run hung 9.5 h on the prerender ssh
# AFTER the server side had finished — the Mac slept (caffeinate -i does
# not survive a closed lid), the TCP session died silently, and with no
# keepalive the client blocked on read forever; launchd would have skipped
# Tuesday's run behind it. A dead peer is now detected within ~3 min.
# (macOS ships no `timeout`, so this is the fix rather than a wrapper.)
SSH_CMD="ssh -i $KEY -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
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

# Free-space preflight (2026-10-13): pushing onto a 97%-full disk hit ENOSPC
# mid-transfer and truncated pricecharting.db on prod. The running API holds
# every replaced DB's old inode open until the restart at the end, so during
# a push each shipped file briefly exists twice — demand room for all of them
# plus a 1GB margin before sending a byte. (Delta nights need less, but a
# failed delta falls back to the full push, so require full-push room always.)
need_kb=$(du -ck $DATA/*_cards.db "$API_DATA/pricecharting.db" | tail -1 | cut -f1)
need_kb=$(( need_kb + 1048576 ))
free_kb=$(${=SSH_CMD} ubuntu@$IP "df -Pk / | awk 'NR==2 {print \$4}'")
if [ "$free_kb" -lt "$need_kb" ]; then
  echo "ABORT: prod has $(( free_kb / 1048576 ))GB free; need $(( need_kb / 1048576 ))GB" \
       "(each pushed DB exists twice until the restart frees the API's old handles)"
  exit 1
fi
echo "preflight: prod free $(( free_kb / 1048576 ))GB >= needed $(( need_kb / 1048576 ))GB"

echo "== card DBs =="
${=RS} "$SSH_CMD" $DATA/*_cards.db ubuntu@$IP:/srv/tcg/data/

# predictions.db is NOT pushed here — the AWS trainer produces it (scorecard/
# forecast/report offloaded 2026-08-03) and pushes it to prod directly over the
# private VPC. Pushing the Mac's now-stale copy would clobber the fresh one. The
# restart below still picks up the trainer's predictions.db alongside these DBs.

# pricecharting.db: on incremental nights build_unified_history.py stages the
# day's exact table changes in unified_delta.db (~15MB) — ship + apply THAT
# instead of the multi-GB file. Prod verifies its resulting row count against
# the delta's expectation; any failure or divergence falls back to the full
# push (which restores byte parity). Sundays / full rebuilds delete the delta
# file, so the full path runs automatically.
DELTA=$DATA/ml_data/unified_delta.db
push_pc_full() {
  echo "== pricecharting (full) =="
  ${=RS} "$SSH_CMD" $API_DATA/pricecharting.db ubuntu@$IP:/srv/tcg/data/cards/
}
# The delta is tonight's when its meta date is the night's batch label
# (TCG_BATCH_DATE, exported by run_daily_refresh.sh; UTC day for hand runs).
if [ -f "$DELTA" ] && [ "$(sqlite3 "$DELTA" 'SELECT date FROM meta' 2>/dev/null)" = "${TCG_BATCH_DATE:-$(date -u +%F)}" ]; then
  echo "== pricecharting (delta, $(du -h "$DELTA" | cut -f1)) =="
  scp -i $KEY -o ServerAliveInterval=30 -o ServerAliveCountMax=6 "$DELTA" ubuntu@$IP:/tmp/unified_delta.db
  if ${=SSH_CMD} ubuntu@$IP 'sqlite3 -bail /srv/tcg/data/cards/pricecharting.db' <<'SQL' | tail -1 | grep -qx 0
ATTACH '/tmp/unified_delta.db' AS d;
PRAGMA busy_timeout = 15000;
BEGIN IMMEDIATE;
DELETE FROM price_history_unified WHERE (game, product_id, printing, grade, date)
  IN (SELECT game, product_id, printing, grade, date FROM d.del);
INSERT OR REPLACE INTO price_history_unified SELECT * FROM d.ins;
INSERT OR REPLACE INTO pricecharting SELECT * FROM d.match_rows;
COMMIT;
SELECT (SELECT COUNT(*) FROM price_history_unified) -
       (SELECT expected_unified_rows FROM d.meta);
SQL
  then
    echo "delta applied cleanly"
    ${=SSH_CMD} ubuntu@$IP 'rm -f /tmp/unified_delta.db'
  else
    echo "DELTA APPLY FAILED or row counts diverged — falling back to full push"
    push_pc_full
  fi
else
  push_pc_full
fi

echo "== restart API =="
# ${=SSH_CMD} forces zsh word-splitting so "ssh -i <key>" isn't run as one word.
${=SSH_CMD} ubuntu@$IP 'sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api'
# Warm the hot paths so the first visitor after a restart doesn't pay the
# cold-cache cost (2026-08-14: homepage movers took ages on a cold API —
# ranking recompute + cold page cache over the multi-GB DBs).
echo "== warmup =="
for ep in \
  "api/cards/movers?count=24&horizon=1m&trend=6m" \
  "api/cards/movers?horizon=mix&perGame=4" \
  "api/cards/movers?game=all&count=10" \
  "api/cards?game=pokemon&pageSize=24"; do
  t0=$(date +%s)
  curl -s -o /dev/null --max-time 120 "https://cardstock.guide/$ep" || true
  echo "  warmed $ep in $(( $(date +%s) - t0 ))s"
done
# Regenerate the crawler prerenders from the fresh data (2026-09-26): ~200k
# card stubs + home/prose pages, built on the box in minutes. Non-fatal.
echo "== prerender for crawlers =="
${=SSH_CMD} ubuntu@$IP 'python3 /srv/tcg/prerender_pages.py' | tail -1 \
  || echo "(prerender failed — non-fatal)"
echo "data live on http://$IP"
