#!/bin/bash
# Release 20 Oct 2026 (g): web orders confirmed, invoiced and made ready for delivery on their own; Chapa at checkout
# with each shop's own key (checked with Chapa first). Unio's own payment accounts are never used for shops.
set -euo pipefail
H=ghion
echo "1/4 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/4 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git ~/sonador-odoo/addons/sonador_site/ $H:/opt/unio/addons/sonador_site/
ssh $H 'chown -R 100:101 /opt/unio/addons'
rsync -rc --delete --exclude node_modules --exclude .next ~/unio-cloud/storefront/ $H:/opt/unio/storefront/
echo "3/4 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d "${2:-postgres}" -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -u sonador_site
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -u sonador_site
  psq "UPDATE unio_site SET confirm_orders = true" "$D" || true
  echo "  customer $D"
done
docker compose up -d odoo </dev/null
REMOTE
echo "4/4 storefront"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio/storefront
docker build -q -t unio-storefront . >/dev/null 2>&1
NET=$(docker inspect "$(cd /opt/unio && docker compose ps -q odoo)" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker rm -f unio-storefront >/dev/null 2>&1 || true
docker run -d --name unio-storefront --restart unless-stopped --network "$NET" -p 127.0.0.1:3100:3000 \
  -e ODOO_URL=http://odoo:8069 --memory 384m unio-storefront >/dev/null
sleep 5; docker ps --filter name=unio-storefront --format '{{.Names}} {{.Status}}'
REMOTE
sleep 8
for u in / /web/login; do printf "%-12s " $u; curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://sonador.unio.et$u; done
