#!/bin/bash
set -u
echo "==============================================="
echo "  RateBridge Status - $(date +"%Y-%m-%d %H:%M:%S %Z")"
echo "==============================================="
echo ""
echo "-- Bot (systemd) --"
if systemctl is-active ratebridge > /dev/null; then
  echo "  status    : ACTIVE"
else
  echo "  status    : DOWN"
fi
echo "  uptime    : $(systemctl show ratebridge --value -p ActiveEnterTimestamp | xargs -I{} date -d "{}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "N/A")"
echo "  restarts  : $(systemctl show ratebridge --value -p NRestarts)"
MEM_BYTES=$(systemctl show ratebridge --value -p MemoryCurrent)
if [ "$MEM_BYTES" != "[not set]" ] && [ -n "$MEM_BYTES" ]; then
  echo "  memory    : $(echo $MEM_BYTES | numfmt --to=iec 2>/dev/null || echo $MEM_BYTES)"
fi
echo ""
echo "-- Redis (docker) --"
docker ps --filter name=trinity-redis --format "  status    : {{.Status}}" 2>/dev/null || echo "  status    : MISSING"
echo ""
echo "-- Nginx --"
if systemctl is-active nginx > /dev/null; then
  echo "  status    : ACTIVE"
else
  echo "  status    : DOWN"
fi
echo ""
echo "-- Resources --"
echo "  load-avg  : $(uptime | awk -F"load average:" '{print $2}' | sed 's/^ //')"
free -h | awk '/^Mem:/ { printf "  memory    : %s used / %s total\n", $3, $2 }'
df -h / | awk 'NR==2 { printf "  disk      : %s used / %s total (%s full)\n", $3, $2, $5 }'
echo ""
echo "-- Time sync --"
chronyc tracking 2>/dev/null | grep -E "Reference|System time" | sed 's/^/  /'
echo ""
echo "-- Exchanges connected --"
LAST_VERIFIED=$(journalctl -u ratebridge --since "15 min ago" --no-pager -o cat 2>/dev/null | grep -oP 'Verified \d+ exchanges: \[[^]]+\]' | tail -1)
if [ -n "$LAST_VERIFIED" ]; then
  echo "  $LAST_VERIFIED"
else
  echo "  (check journalctl for details)"
fi
echo ""
echo "-- API health --"
TOKEN=$(grep '^ADMIN_TOKEN=' /opt/ratebridge/.env | cut -d= -f2)
if [ -n "$TOKEN" ]; then
  curl -s -H "X-Read-Token: $TOKEN" http://localhost/api/health 2>/dev/null | sed 's/^/  /' || echo "  API unreachable"
else
  echo "  ADMIN_TOKEN not set"
fi
echo ""
echo "==============================================="
