#!/bin/bash
# Runs ON the EC2 box (as ubuntu, via ssh) once after provisioning:
# creates the tcg user + directory layout, installs Caddy + rsync,
# installs the systemd unit and Caddyfile (pushed alongside this script).
set -euo pipefail

sudo apt-get update -q
sudo apt-get install -y -q rsync debian-keyring debian-archive-keyring apt-transport-https curl

# Caddy (official repo)
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update -q && sudo apt-get install -y -q caddy
fi

# Layout. Everything owned by ubuntu — the ssh/rsync user and the service user.
sudo mkdir -p /srv/tcg/{api,client,data/cards}
sudo chown -R ubuntu:ubuntu /srv/tcg
sudo chmod -R g+rX,o+rX /srv/tcg              # caddy reads /srv/tcg/client

# systemd unit + Caddyfile (pushed to /tmp by deploy_app.sh)
sudo mv /tmp/tcg-api.service /etc/systemd/system/tcg-api.service
sudo mv /tmp/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable tcg-api
sudo systemctl restart caddy

echo "server setup complete"
