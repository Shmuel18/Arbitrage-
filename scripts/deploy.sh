#!/usr/bin/env bash
#
# Pull latest code, rebuild bot image, restart stack.
# Run from project root on the VPS:  bash scripts/deploy.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found."
    exit 1
fi

if [[ ! -f nginx/conf.d/app.conf ]]; then
    echo "ERROR: nginx config not rendered. Run scripts/init_ssl.sh first."
    exit 1
fi

echo ">> Pulling latest code"
git pull --ff-only

echo ">> Rebuilding bot image"
docker compose build bot

echo ">> Restarting services"
docker compose up -d

echo ">> Status:"
docker compose ps

echo
echo ">> Tail logs with:  docker compose logs -f bot"
