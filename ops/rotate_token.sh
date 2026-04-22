#!/bin/bash
# Rotate ADMIN_TOKEN in backend .env and matching VITE_* in frontend .env.local,
# then rebuild frontend and restart bot.
set -euo pipefail

NEW=$(openssl rand -hex 48)

# Backend
sed -i "s|^ADMIN_TOKEN=.*|ADMIN_TOKEN=$NEW|" /opt/ratebridge/.env

# Frontend
cat > /opt/ratebridge/frontend/.env.local <<EOF
VITE_WS_TOKEN=$NEW
VITE_ADMIN_TOKEN=$NEW
VITE_READ_TOKEN=$NEW
VITE_COMMAND_TOKEN=$NEW
VITE_EMERGENCY_TOKEN=$NEW
VITE_TRADE_TOKEN=$NEW
VITE_CONFIG_TOKEN=$NEW
EOF
chmod 600 /opt/ratebridge/frontend/.env.local

echo "Rotated token length=${#NEW}"
echo "Rebuilding frontend..."
cd /opt/ratebridge/frontend
npm run build 2>&1 | tail -2

echo "Restarting bot (docker)..."
cd /opt/ratebridge
docker compose restart bot 2>&1 | tail -3
sleep 5
if docker ps --filter name=trinity-bot --filter status=running --format '{{.Names}}' | grep -q trinity-bot; then
  echo "Bot container running."
else
  echo "WARNING: bot container not running — check: docker logs trinity-bot"
fi
