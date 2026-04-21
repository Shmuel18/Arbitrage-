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

echo "Restarting bot..."
systemctl restart ratebridge
sleep 3
if systemctl is-active ratebridge > /dev/null; then
  echo "Bot active."
else
  echo "WARNING: bot not active - check logs"
fi
