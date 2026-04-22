#!/bin/bash
# Add /grafana/ location to the HTTPS server block (missed by v1 script).
set -euo pipefail

CONF=/etc/nginx/sites-available/ratebridge

python3 <<'PYEOF'
path = "/etc/nginx/sites-available/ratebridge"
with open(path) as f:
    content = f.read()

block = '''    # Grafana monitoring UI (HTTPS server block)
    location /grafana/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

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

# Count existing occurrences — should be 1 (http block) so we need to add 1 more (https)
count = content.count("location /grafana/ {")
print(f"existing /grafana/ blocks: {count}")

if count >= 2:
    print("both_already_configured")
else:
    # Find the 2nd occurrence of "location / {" (SPA fallback in HTTPS server block)
    # Insert grafana block right before it.
    first_spa = content.find("location / {")
    if first_spa == -1:
        print("no_spa_block_found")
    else:
        second_spa = content.find("location / {", first_spa + 1)
        if second_spa == -1:
            print("only_one_spa_block_no_https")
        else:
            # Need to find the start of that line (preceding whitespace)
            line_start = content.rfind("\n", 0, second_spa) + 1
            # Determine indentation — find start of line with indentation
            # Add our block followed by original indentation
            new_content = content[:line_start] + block + "    " + content[line_start + 4:]
            # Actually just insert block before the 4-space indent
            indent_start = line_start
            new_content = content[:indent_start] + block + content[indent_start:]
            with open(path, "w") as f:
                f.write(new_content)
            print("added_to_https")
PYEOF

nginx -t
systemctl reload nginx
echo "DONE"
