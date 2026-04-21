#!/bin/bash
# RateBridge healthcheck — runs from cron every 5 min.
# Alerts via Telegram on state changes (down/up).

STATE_FILE=/var/lib/ratebridge/health.state
mkdir -p /var/lib/ratebridge

# Read telegram creds from .env
set -a
source /opt/ratebridge/.env 2>/dev/null || exit 0
set +a

alert() {
  local msg="$1"
  [ -z "$TELEGRAM_BOT_TOKEN" ] && return
  [ -z "$TELEGRAM_CHAT_ID" ] && return
  curl -s -o /dev/null \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "parse_mode=HTML" \
    --data-urlencode "text=${msg}"
}

# Collect component status
BOT=$(systemctl is-active ratebridge)
REDIS=$(docker inspect -f '{{.State.Status}}' trinity-redis 2>/dev/null || echo "missing")
NGINX=$(systemctl is-active nginx)
DISK_USED=$(df / | awk 'NR==2 {gsub("%","",$5); print $5}')

# Disk flag
if [ -n "$DISK_USED" ] && [ "$DISK_USED" -lt 90 ] 2>/dev/null; then
  DISK_STATE="ok"
else
  DISK_STATE="full"
fi

CURRENT="${BOT}|${REDIS}|${NGINX}|${DISK_STATE}"
PREVIOUS=$(cat "$STATE_FILE" 2>/dev/null || echo "")

# Only alert on state change
if [ "$CURRENT" != "$PREVIOUS" ]; then
  if [[ "$CURRENT" == *"inactive"* ]] || [[ "$CURRENT" == *"failed"* ]] || [[ "$CURRENT" == *"missing"* ]] || [[ "$CURRENT" == *"full"* ]]; then
    MSG="🚨 <b>RateBridge Health Alert</b>

bot: <code>${BOT}</code>
redis: <code>${REDIS}</code>
nginx: <code>${NGINX}</code>
disk: <code>${DISK_USED}% (${DISK_STATE})</code>

<i>Host: ratebridge-bot (Tokyo)</i>"
    alert "$MSG"
  elif [ -n "$PREVIOUS" ]; then
    MSG="✅ <b>RateBridge recovered</b>

bot: <code>${BOT}</code>
redis: <code>${REDIS}</code>
nginx: <code>${NGINX}</code>"
    alert "$MSG"
  fi
fi

echo "$CURRENT" > "$STATE_FILE"
