#!/bin/bash
# Add no-cache headers for index.html so new frontend builds show up
# immediately without users having to hard-refresh.
set -euo pipefail

CONF=/etc/nginx/sites-available/ratebridge

if grep -q 'location = /index.html' "$CONF"; then
  echo "already_configured"
  exit 0
fi

cp "$CONF" "$CONF.bak"

# Insert a no-cache block for index.html right before the SPA fallback.
python3 <<'PYEOF'
path = "/etc/nginx/sites-available/ratebridge"
with open(path) as f:
    content = f.read()

block = '''    # Never cache index.html so new deployments surface immediately.
    # (Hashed /assets/* files remain cached for 1y — they change names on rebuild.)
    location = /index.html {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma "no-cache";
        expires 0;
    }

'''

marker = "    # Main SPA route"
if marker in content and "location = /index.html" not in content:
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
