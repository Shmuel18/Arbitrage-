#!/bin/bash
# RateBridge nightly backup.
#
# Snapshots the Redis data volume + the .env file, stores timestamped
# tarballs under /var/backups/ratebridge, and rotates out anything older
# than 14 days. Runs from a systemd timer (see ratebridge-backup.timer).
#
# If anything fails the script exits non-zero so systemd marks the unit
# failed, and — when Telegram creds are present — sends an alert.
#
# NOTE: this is a LOCAL backup. To survive a VPS disaster you also need
# off-host sync (rclone to B2/S3, rsync to another host, etc.). See the
# TODO at the bottom.

set -euo pipefail

BACKUP_DIR=/var/backups/ratebridge
RETENTION_DAYS=14
REDIS_CONTAINER=trinity-redis
VOLUME_NAME=ratebridge_redis_data
ENV_FILE=/opt/ratebridge/.env
TS=$(date -u +%Y%m%dT%H%M%SZ)

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
fail() {
    log "ERROR: $*"
    # Best-effort Telegram alert.
    if [ -f "$ENV_FILE" ]; then
        set -a; source "$ENV_FILE" 2>/dev/null || true; set +a
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
            curl -s -o /dev/null \
                "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
                --data-urlencode "parse_mode=HTML" \
                --data-urlencode "text=🚨 <b>RateBridge backup failed</b>%0A<code>$*</code>"
        fi
    fi
    exit 1
}

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# ── 1. Force Redis to flush a fresh RDB snapshot ─────────────────────
log "Triggering Redis BGSAVE…"
LAST_SAVE_BEFORE=$(docker exec "$REDIS_CONTAINER" redis-cli LASTSAVE) \
    || fail "could not read LASTSAVE from $REDIS_CONTAINER"
docker exec "$REDIS_CONTAINER" redis-cli BGSAVE >/dev/null \
    || fail "BGSAVE dispatch failed"

# Wait up to 120 s for LASTSAVE to advance, which tells us the new RDB
# is on disk and safe to snapshot.
for _ in $(seq 1 120); do
    NOW_SAVE=$(docker exec "$REDIS_CONTAINER" redis-cli LASTSAVE)
    if [ "$NOW_SAVE" != "$LAST_SAVE_BEFORE" ]; then
        log "BGSAVE completed (LASTSAVE: $LAST_SAVE_BEFORE → $NOW_SAVE)"
        break
    fi
    sleep 1
done
if [ "${NOW_SAVE:-}" = "$LAST_SAVE_BEFORE" ]; then
    fail "BGSAVE did not finish within 120 s"
fi

# ── 2. Snapshot the named volume ─────────────────────────────────────
REDIS_OUT="$BACKUP_DIR/redis-$TS.tar.gz"
log "Archiving volume $VOLUME_NAME → $REDIS_OUT"
docker run --rm \
    -v "$VOLUME_NAME:/data:ro" \
    -v "$BACKUP_DIR:/backup" \
    alpine:3 \
    tar czf "/backup/$(basename "$REDIS_OUT")" -C /data . \
    || fail "tar of $VOLUME_NAME failed"

# Sanity check: file must be non-empty and at least 1 KB.
SIZE=$(stat -c %s "$REDIS_OUT" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 1024 ]; then
    fail "backup file $REDIS_OUT is suspiciously small ($SIZE bytes)"
fi
log "Redis backup: $(du -h "$REDIS_OUT" | cut -f1)"

# ── 3. Back up .env (root-only, no encryption on local disk) ─────────
if [ -f "$ENV_FILE" ]; then
    ENV_OUT="$BACKUP_DIR/env-$TS.env"
    cp "$ENV_FILE" "$ENV_OUT"
    chmod 600 "$ENV_OUT"
    log ".env backup: $ENV_OUT"
fi

# ── 4. Rotate: delete backups older than RETENTION_DAYS ──────────────
DELETED=$(find "$BACKUP_DIR" -type f \( -name 'redis-*.tar.gz' -o -name 'env-*.env' \) \
    -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
log "Rotated $DELETED file(s) older than $RETENTION_DAYS days"

log "Backup complete."

# TODO(off-host): add rclone sync here so a single VPS loss doesn't
# take the backups with it, e.g.:
#   rclone sync "$BACKUP_DIR" b2:ratebridge-backups --min-age 1h
