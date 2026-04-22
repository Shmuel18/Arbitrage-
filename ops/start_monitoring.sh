#!/bin/bash
# Start Prometheus + Grafana monitoring stack.
set -euo pipefail
cd /opt/ratebridge

# Ensure Grafana admin password is set in .env
if ! grep -q '^GRAFANA_ADMIN_PASSWORD=' .env; then
  NEW_PW=$(openssl rand -hex 16)
  printf '\nGRAFANA_ADMIN_PASSWORD=%s\n' "$NEW_PW" >> .env
  echo "Generated new Grafana admin password."
else
  echo "Grafana admin password already in .env."
fi

# Pull images + start monitoring services
docker compose pull prometheus node_exporter cadvisor redis_exporter grafana 2>&1 | tail -3
docker compose up -d prometheus node_exporter cadvisor redis_exporter grafana 2>&1 | tail -8

echo
echo "Waiting for Grafana to be ready..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf http://127.0.0.1:3000/api/health > /dev/null 2>&1; then
    echo "Grafana up after ${i}0s."
    break
  fi
  sleep 10
done

echo
echo "Containers running:"
docker ps --format '{{.Names}}: {{.Status}}' | grep trinity-
