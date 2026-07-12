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

rsync -az --delete -e "ssh -i $KEY" "$ROOT/dotnet/API/bin/deploy-linux/" ubuntu@$IP:/srv/tcg/api/
rsync -az --delete -e "ssh -i $KEY" "$ROOT/client/dist/" ubuntu@$IP:/srv/tcg/client/

${=SSH} 'chmod +x /srv/tcg/api/API && sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api'
echo "app deployed -> http://$IP"
