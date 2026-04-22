#!/bin/bash
# Test API inside bot container with its own token
TOKEN=$(printenv ADMIN_TOKEN)
echo "Token length: ${#TOKEN}"
echo "First 10 chars: ${TOKEN:0:10}"
echo "Last 10 chars: ${TOKEN: -10}"
echo "--- GET /api/health ---"
curl -s -H "X-Read-Token: $TOKEN" http://127.0.0.1:8000/api/health
echo
echo "--- POST /api/health ---"
curl -s -X POST -H "X-Read-Token: $TOKEN" http://127.0.0.1:8000/api/health
echo
