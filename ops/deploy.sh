#!/bin/bash
# RateBridge deploy — pull, rebuild frontend, restart bot
set -euo pipefail
cd /opt/ratebridge

echo "-- 1/5 Pulling latest code --"
git pull --ff-only origin v3-live

echo "-- 2/5 Updating Python deps --"
.venv/bin/pip install -q -r requirements.txt

echo "-- 3/5 Rebuilding frontend --"
cd frontend
npm install --silent
npm run build 2>&1 | tail -3
cd ..

echo "-- 4/5 Precompiling Python --"
.venv/bin/python -m compileall -q src api main.py

echo "-- 5/5 Restarting bot --"
systemctl restart ratebridge
sleep 3
if systemctl is-active ratebridge > /dev/null; then
  echo "Bot active."
else
  echo "Bot failed - check logs with: /opt/ratebridge/ops/logs.sh"
fi

echo ""
echo "Deploy complete."
