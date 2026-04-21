#!/bin/bash
# Sync ADMIN_TOKEN from frontend .env.local to backend .env
set -euo pipefail

TOKEN=$(grep '^VITE_ADMIN_TOKEN=' /opt/ratebridge/frontend/.env.local | cut -d= -f2)
if [ -z "$TOKEN" ]; then
  echo "ERROR: no VITE_ADMIN_TOKEN in /opt/ratebridge/frontend/.env.local"
  exit 1
fi

# Remove any old ADMIN_TOKEN lines, then add fresh one
sed -i '/^ADMIN_TOKEN=/d' /opt/ratebridge/.env
printf '\nADMIN_TOKEN=%s\n' "$TOKEN" >> /opt/ratebridge/.env

echo "Synced token (length=${#TOKEN})"
echo "Restarting bot..."
systemctl restart ratebridge
echo "DONE"
