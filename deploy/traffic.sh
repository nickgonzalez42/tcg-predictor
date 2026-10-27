#!/bin/zsh
# Visitor + page report from the server's access log.
#   deploy/traffic.sh        last 24 hours
#   deploy/traffic.sh 7      last 7 days
set -u
DAYS=${1:-1}
IP=${2:-35.168.177.31}
KEY=$HOME/.ssh/tcg-predictor.pem
scp -q -i $KEY -o StrictHostKeyChecking=no "$(dirname "$0")/traffic_report.py" ubuntu@$IP:/srv/tcg/traffic_report.py
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@$IP "sudo python3 /srv/tcg/traffic_report.py --days $DAYS"
