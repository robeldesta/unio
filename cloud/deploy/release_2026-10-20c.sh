#!/bin/bash
# Release 20 Oct 2026 (c): telebirr C2B Web Checkout (switched on later by its live keys). Platform database only.
set -euo pipefail
H=ghion
echo "1/4 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -2'
echo "2/4 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/sonador_saas/ $H:/opt/unio/addons/sonador_saas/
ssh $H 'chown -R 100:101 /opt/unio/addons/sonador_saas'
echo "3/4 platform database"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d my -u sonador_saas </dev/null 2>&1)" || true
docker compose up -d odoo </dev/null
if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; exit 1; fi
echo "  my upgraded"
sed -i 's#location ~ ^/unio/(signup|resend|verify|subscribe|paid)\$#location ~ ^/unio/(signup|resend|verify|subscribe|paid|checkout)\$#' /etc/nginx/sites-enabled/unio-tenant-my
grep -n 'location ~' /etc/nginx/sites-enabled/unio-tenant-my
nginx -t 2>&1 | tail -1 && systemctl reload nginx
REMOTE
echo "4/4 website"
rsync -rc --exclude .DS_Store ~/unio-cloud/site/ $H:/var/www/unio/
sleep 8
curl -s "https://my.unio.et/unio/pay?c=sonador&u=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('pay:', d.get('total'), d.get('currency'), 'button' if d.get('checkout') else 'no button (no live keys yet)', 'qr' if d.get('qr') else '')"
curl -s -X POST -H "Content-Type: application/json" -d '{"merch_order_id":"NOPE"}' https://my.unio.et/unio/telebirr/notify; echo
