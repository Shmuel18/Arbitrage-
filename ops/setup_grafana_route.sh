#!/bin/bash
# Add /grafana/ location block to nginx config and reload.
set -euo pipefail

CONF=/etc/nginx/sites-available/ratebridge

if grep -q 'location /grafana/' "$CONF"; then
  echo "already_configured"
  exit 0
fi

# Insert /grafana/ block BEFORE the SPA catchall "location /"
python3 <<'PYEOF'
path = "/etc/nginx/sites-available/ratebridge"
with open(path) as f:
    content = f.read()

block = '''    # Grafana monitoring UI (password-protected by Grafana itself)
    location /grafana/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # Grafana WebSocket (live dashboards)
    location /grafana/api/live/ {
        proxy_pass http://127.0.0.1:3000/api/live/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
    }

'''

marker = "    # Main SPA route"
if marker in content and "location /grafana/" not in content:
    content = content.replace(marker, block + marker, 1)
    with open(path, "w") as f:
        f.write(content)
    print("added")
else:
    print("marker_not_found_or_already_exists")
PYEOF

nginx -t
systemctl reload nginx
echo "DONE"
