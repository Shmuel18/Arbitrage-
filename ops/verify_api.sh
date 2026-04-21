#!/bin/bash
# Quick API verification — reads token from .env and hits endpoints
set -u
TOKEN=$(grep '^ADMIN_TOKEN=' /opt/ratebridge/.env | cut -d= -f2)
if [ -z "$TOKEN" ]; then
  echo "ERROR: ADMIN_TOKEN missing from .env"
  exit 1
fi

echo "=== /api/health ==="
curl -s -H "X-Read-Token: $TOKEN" -H "X-Admin-Token: $TOKEN" http://localhost/api/health
echo
echo
echo "=== /api/opportunities (truncated) ==="
curl -s -H "X-Read-Token: $TOKEN" http://localhost/api/opportunities | head -c 500
echo
echo
echo "=== /api/balances (truncated) ==="
curl -s -H "X-Read-Token: $TOKEN" http://localhost/api/balances | head -c 500
echo
echo
echo "=== Port 8000 listening ==="
ss -tlnp | grep 8000 || echo "NOT LISTENING"
