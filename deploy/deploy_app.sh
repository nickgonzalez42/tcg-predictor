#!/bin/zsh
# Build + ship the APPLICATION (API binary + client bundle + configs).
# Data is pushed separately by push_data.sh. Usage: deploy/deploy_app.sh <ip>
set -euo pipefail
IP=${1:?usage: deploy_app.sh <server-ip>}
KEY=~/.ssh/tcg-predictor.pem
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP"
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
${=SSH} 'bash /tmp/setup_server.sh'

# macOS ships openrsync (not GNU rsync), which sporadically loses one temp
# file on many-file trees (exit 23, "stat .X.dll.??? failed") — a second
# pass always converges, so each sync gets one retry. The API is stopped
# during its sync anyway (rewriting a live .NET install risks a torn app),
# and the ERR trap brings it back up even on a mid-ship failure.
# (Root fix if the retry ever isn't enough: brew install rsync.)
sync_dir() { rsync -az --delete -e "ssh -i $KEY" "$1" "$2" || \
             { echo "rsync flaked (openrsync) — retrying $2"; \
               rsync -az --delete -e "ssh -i $KEY" "$1" "$2"; } }
trap '${=SSH} "sudo systemctl start tcg-api" || true' ERR
${=SSH} 'sudo systemctl stop tcg-api'
sync_dir "$ROOT/dotnet/API/bin/deploy-linux/" ubuntu@$IP:/srv/tcg/api/
sync_dir "$ROOT/client/dist/" ubuntu@$IP:/srv/tcg/client/

${=SSH} 'chmod +x /srv/tcg/api/API && sudo systemctl start tcg-api && sleep 2 && systemctl is-active tcg-api'
trap - ERR
echo "app deployed -> http://$IP"
