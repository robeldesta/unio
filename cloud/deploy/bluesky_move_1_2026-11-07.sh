#!/bin/bash
# Move Bluesky CEO Pulse (ceo.bluesky.et, a Telegram mini app over Bluesky's Odoo 16 database) from the Eco Green server
# to ghion. Step 1 of 2: copy the app (with its .env) straight from server to server, install it on ghion as its own
# service on 127.0.0.1:8788 and add the nginx site. The Eco Green copy keeps running until step 2, so nothing stops.
#     bash ~/unio-cloud/deploy/bluesky_move_1_2026-11-07.sh
# Then point the DNS A record of ceo.bluesky.et to 157.245.91.235 and run step 2.
set -euo pipefail
echo "1/4 copy the app"
ssh ecogreen-server "tar czf - -C /mnt/opt --exclude=.venv --exclude=__pycache__ bluesky-pulse" | ssh ghion "mkdir -p /opt && rm -rf /opt/bluesky-pulse && tar xzf - -C /opt"
ssh ghion 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/bluesky-pulse
echo "2/4 install"
id bluesky >/dev/null 2>&1 || useradd --system --home /opt/bluesky-pulse --shell /usr/sbin/nologin bluesky
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chown -R bluesky:bluesky /opt/bluesky-pulse; chmod 600 .env
echo "3/4 service"
cat > /etc/systemd/system/bluesky-pulse.service <<UNIT
[Unit]
Description=Bluesky CEO Pulse (ceo.bluesky.et)
After=network.target

[Service]
User=bluesky
Group=bluesky
WorkingDirectory=/opt/bluesky-pulse
EnvironmentFile=/opt/bluesky-pulse/.env
ExecStart=/opt/bluesky-pulse/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8788
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload; systemctl enable --now bluesky-pulse
sleep 4
echo "   app answers: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/)"
echo "4/4 nginx site (plain http until the certificate in step 2)"
cat > /etc/nginx/sites-available/ceo.bluesky.et <<'SITE'
server {
    listen 80;
    server_name ceo.bluesky.et;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / {
        proxy_pass http://127.0.0.1:8788;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
SITE
mkdir -p /var/www/acme
ln -sf /etc/nginx/sites-available/ceo.bluesky.et /etc/nginx/sites-enabled/ceo.bluesky.et
nginx -t && systemctl reload nginx
REMOTE
echo "Done. Now point ceo.bluesky.et (A record) to 157.245.91.235, then run step 2."
