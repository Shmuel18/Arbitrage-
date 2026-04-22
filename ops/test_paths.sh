#!/bin/bash
TOKEN=$(grep '^ADMIN_TOKEN=' /opt/ratebridge/.env | cut -d= -f2)
echo "Token len: ${#TOKEN}"
echo ""
echo "1. Direct to docker-proxy on 127.0.0.1:8000"
curl -s -H "X-Read-Token: $TOKEN" http://127.0.0.1:8000/api/health
echo ""
echo ""
echo "2. Via nginx HTTP"
curl -s -H "X-Read-Token: $TOKEN" http://localhost/api/health
echo ""
echo ""
echo "3. Via nginx HTTPS (ratebridge.live)"
curl -s -H "X-Read-Token: $TOKEN" https://ratebridge.live/api/health
echo ""
