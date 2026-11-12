#!/bin/bash
# Release 20 Oct 2026 (b): the Unio look everywhere (loader, tab icon and title, sign-in, Powered by, app icon,
# sidebar), AutoBot without Odoo's name, the subscribe page on unio.et. Touches only /opt/unio, /var/www/unio and
# nginx's my.unio.et site.
set -euo pipefail
H=ghion
ADDONS=~/sonador-odoo/addons
MODS="sonador_saas sonador_saas_client sonador_print sonador_studio sonador_unio_brand sonador_brand"
echo "1/5 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -2'
echo "2/5 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual "$ADDONS/$m/" "$H:/opt/unio/addons/$m/"
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/5 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
# upgrade one database; on an error show it, restart the server and stop
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
up -d my -u sonador_saas -i sonador_unio_brand
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -i sonador_unio_brand -u sonador_saas_client,sonador_print,sonador_brand
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -i sonador_unio_brand -u sonador_saas_client,sonador_print,sonador_brand
  echo "  customer $D"
done
echo "  payment settings and the invoicing company"
docker compose run --rm -T odoo odoo shell -d my --no-http --db_host db --db_user odoo --db_password "$PW" 2>/dev/null <<'PY' | grep "^>"
P = env['ir.config_parameter']
P.set_param('saas.telebirr_qr', '00020101021128580032a663fcd3cb714c58a495d9cf817e71620108TELEBIRR020693809052048451530323054039995802ET5930SONADOR SYSTEMS ONE MEMBER PLC6011Addis Ababa623007064728910816Pay for merchant8016Pay for merchant850701031126304B60D')
P.set_param('saas.bank_details', 'Commercial Bank of Ethiopia 1000609571668, Sonador Systems One Member PLC')
P.set_param('saas.sales_email', 'sales@unio.et')
c = env.ref('base.main_company')
c.write({'name': 'Sonador Systems One Member PLC', 'vat': '0088565182'})
env.cr.commit()
print('> telebirr QR, bank and company set:', c.name, c.vat)
PY
docker compose up -d odoo </dev/null
REMOTE
echo "4/5 website"
rsync -rc --exclude .DS_Store ~/unio-cloud/site/ $H:/var/www/unio/
echo "5/5 nginx: the subscribe request under the same limit"
ssh $H "sed -i 's#location ~ ^/unio/(signup|resend|verify)\\\$#location ~ ^/unio/(signup|resend|verify|subscribe|paid)\\\$#' /etc/nginx/sites-enabled/unio-tenant-my && grep -n 'location ~' /etc/nginx/sites-enabled/unio-tenant-my && nginx -t 2>&1 | tail -1 && systemctl reload nginx"
curl -s -o /dev/null -w "subscribe page %{http_code}\n" https://unio.et/subscribe.html
curl -s -o /dev/null -w "loader %{http_code}\n" https://my.unio.et/sonador_unio_brand/static/img/unio_loader.svg
