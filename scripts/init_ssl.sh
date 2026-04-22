#!/usr/bin/env bash
#
# One-time SSL bootstrap for Trinity Arbitrage.
#
# Flow:
#   1. Render HTTP-only nginx config (only ACME challenge + placeholder).
#   2. Start nginx alone so certbot can reach /.well-known/acme-challenge/.
#   3. Request a Let's Encrypt cert via webroot challenge.
#   4. Render full HTTPS nginx config and reload nginx.
#
# Run from project root on the VPS:  bash scripts/init_ssl.sh
#
# Required env (loaded from .env):  DOMAIN, LETSENCRYPT_EMAIL

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.production.template to .env and fill it in."
    exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${DOMAIN:?DOMAIN not set in .env}"
: "${LETSENCRYPT_EMAIL:?LETSENCRYPT_EMAIL not set in .env}"

echo ">> Bootstrapping SSL for ${DOMAIN}"

mkdir -p nginx/conf.d certbot/conf certbot/www nginx/logs logs

render_template() {
    local src="$1"
    local dst="$2"
    DOMAIN="$DOMAIN" envsubst '${DOMAIN}' < "$src" > "$dst"
}

echo ">> Step 1/4: Rendering HTTP-only nginx config"
rm -f nginx/conf.d/*.conf
render_template nginx/templates/app.http-only.conf.template nginx/conf.d/app.conf

echo ">> Step 2/4: Starting nginx (HTTP only) for ACME challenge"
docker compose up -d nginx
sleep 3

echo ">> Step 3/4: Requesting certificate from Let's Encrypt"
docker compose run --rm --entrypoint "" certbot \
    certbot certonly --webroot -w /var/www/certbot \
    -d "${DOMAIN}" \
    --email "${LETSENCRYPT_EMAIL}" \
    --agree-tos --no-eff-email --non-interactive

echo ">> Step 4/4: Switching to full HTTPS config"
render_template nginx/templates/app.https.conf.template nginx/conf.d/app.conf
docker compose exec nginx nginx -s reload

echo
echo ">> Done. Verify with:  curl -I https://${DOMAIN}"
echo ">> Certificate auto-renewal runs via the certbot service every 12h."
