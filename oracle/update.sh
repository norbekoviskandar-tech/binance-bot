#!/usr/bin/env bash
# Pull latest code, refresh deps, restart the dashboard (and the bot only if it is running).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
./venv/bin/pip install -r requirements.txt
sudo systemctl restart binance-dashboard
if systemctl is-active --quiet binance-bot; then sudo systemctl restart binance-bot; fi
echo "Updated."
