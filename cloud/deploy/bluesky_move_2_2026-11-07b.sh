#!/bin/bash
# Move Bluesky CEO Pulse, step 2 of 2 (after the DNS of ceo.bluesky.et points to 157.245.91.235): certificate on ghion,
# https site, check. The Eco Green copy is then stopped with kit-20261107ad on that server.
#     bash ~/unio-cloud/deploy/bluesky_move_2_2026-11-07b.sh
set -euo pipefail
ip=$(dig +short @ns2.siteground.net ceo.bluesky.et | tail -1)  # the DNS host itself: public caches may still hold the old address
[ "$ip" = 157.245.91.235 ] || { echo "ceo.bluesky.et still points to $ip: change the DNS first (it can take a few minutes)"; exit 1; }
ssh ghion 'bash -s' <<'REMOTE'
set -euo pipefail
certbot certonly --webroot -w /var/www/acme -d ceo.bluesky.et --non-interactive --agree-tos 2>&1 | tail -2
cat > /etc/nginx/sites-available/ceo.bluesky.et <<'SITE'
server {
    listen 80;
    server_name ceo.bluesky.et;
    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://$host$request_uri; }
}
server {
    listen 443 ssl;
    server_name ceo.bluesky.et;
    ssl_certificate /etc/letsencrypt/live/ceo.bluesky.et/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ceo.bluesky.et/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8788;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
SITE
nginx -t && systemctl reload nginx
REMOTE
echo "https://ceo.bluesky.et on ghion answers: $(curl -s -o /dev/null -m 20 -w "%{http_code}" --resolve ceo.bluesky.et:443:157.245.91.235 https://ceo.bluesky.et/)"
echo "Leave the old copy on the Eco Green server running until tomorrow (public DNS caches hold the old address up to 24 h), then: sudo bash ~/unio-refresh/kit-20261107ad/deploy.sh"
