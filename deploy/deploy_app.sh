#!/bin/zsh
# Build + ship the APPLICATION (API binary + client bundle + configs).
# Data is pushed separately by push_data.sh. Usage: deploy/deploy_app.sh <ip>
set -euo pipefail
IP=${1:?usage: deploy_app.sh <server-ip>}
KEY=~/.ssh/tcg-predictor.pem
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=6 ubuntu@$IP"
# Same keepalive on the rsync transport — see push_data.sh (2026-10-20).
SSH_E="ssh -i $KEY -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- API: self-contained linux publish (no runtime install on the box) ------
cd "$ROOT/dotnet/API"
dotnet publish -c Release -r linux-x64 --self-contained true -o bin/deploy-linux

# --- client: production bundle (same-origin /api) ----------------------------
cd "$ROOT/client"
npm run build   # .env.production pins VITE_API_URL=/api

# --- ship ---------------------------------------------------------------------
scp -i $KEY "$ROOT/deploy/tcg-api.service" "$ROOT/deploy/Caddyfile" ubuntu@$IP:/tmp/
scp -i $KEY "$ROOT/deploy/setup_server.sh" ubuntu@$IP:/tmp/
scp -i $KEY "$ROOT/pipeline/prerender_pages.py" ubuntu@$IP:/srv/tcg/
${=SSH} 'bash /tmp/setup_server.sh'

# macOS ships openrsync (not GNU rsync), which sporadically loses one temp
# file on many-file trees (exit 23, "stat .X.dll.??? failed") — a second
# pass always converges, so each sync gets one retry. The API is stopped
# during its sync anyway (rewriting a live .NET install risks a torn app),
# and the ERR trap brings it back up even on a mid-ship failure.
# (Root fix if the retry ever isn't enough: brew install rsync.)
RSYNC=$([ -x /opt/homebrew/bin/rsync ] && echo /opt/homebrew/bin/rsync || echo rsync)
sync_dir() { $RSYNC -az --delete -e "$SSH_E" "$1" "$2" || \
             { echo "rsync flaked — retrying $2"; \
               $RSYNC -az --delete -e "$SSH_E" "$1" "$2"; } }
trap '${=SSH} "sudo systemctl start tcg-api" || true' ERR
${=SSH} 'sudo systemctl stop tcg-api'
sync_dir "$ROOT/dotnet/API/bin/deploy-linux/" ubuntu@$IP:/srv/tcg/api/
# Normalize modes before shipping: rsync -a PRESERVES them, and Caddy runs as
# its own user, so one owner-only file (an asset copied in from a 600 source
# — riftbound-back.png, 2026-10-20) is unreadable to the server. The SPA
# fallback then answers that URL with index.html: HTTP 200, text/html, a
# silently broken image rather than a 404 anyone would notice.
chmod -R a+rX "$ROOT/client/dist"
sync_dir "$ROOT/client/dist/" ubuntu@$IP:/srv/tcg/client/
# Static crawler-visible pages (reports backfill + guides). NO --delete: the
# trainer drops each new Friday report into the same tree; the repo copy only
# grows it.
$RSYNC -az -e "$SSH_E" "$ROOT/static-content/" ubuntu@$IP:/srv/tcg/static/

${=SSH} 'chmod +x /srv/tcg/api/API && sudo systemctl start tcg-api && sleep 2 && systemctl is-active tcg-api'
trap - ERR
# The API takes 10-30 s to come up (migrations, the scanner artifacts); a
# warm-up fired before that only collects 502s from Caddy (2026-10-25). Wait
# for a real answer first, then prime what the startup self-warmup covers as
# well — belt and braces. The per-game facets are the slow ones.
wait_for_api() {
  for i in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://cardstock.guide/api/home/stats" || true)
    [ "$code" = "200" ] && { echo "api up after $((i*5))s"; return 0; }
    sleep 5
  done
  echo "api not answering after 300s — warming anyway"
}
wait_for_api
# Prime the process-lifetime caches the home page reads (movers, at-a-glance
# stats) so the first visitor after a deploy does not watch them load — the
# same list push_data.sh warms after its own restart.
for ep in "api/cards/movers?count=24&horizon=1m&trend=6m" \
          "api/cards/movers?horizon=mix&perGame=2" \
          "api/cards/movers?game=all&count=10" \
          "api/home/stats" \
          $(for g in pokemon magic yugioh onepiece lorcana digimon gundam starwars fleshandblood sorcery riftbound cyberpunk; do echo "api/cards/filters?game=$g"; done); do
  curl -s -o /dev/null --max-time 900 "https://cardstock.guide/$ep" || true
done
echo "app deployed -> http://$IP"
