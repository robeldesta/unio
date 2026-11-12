#!/bin/bash
# Release 20 Oct 2026 (e): the Website app and online shop. Installs sonador_site in the templates and every company,
# and starts the storefront (Next.js) next to Odoo. The web server routing for company addresses is changed separately,
# by hand, after this has run (see the report).
set -euo pipefail
H=ghion
ADDONS=~/sonador-odoo/addons
MODS="sonador_site sonador_saas sonador_saas_client sonador_tours"
echo "1/5 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -2'
echo "2/5 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual "$ADDONS/$m/" "$H:/opt/unio/addons/$m/"
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
rsync -rc --delete --exclude node_modules --exclude .next ~/unio-cloud/storefront/ $H:/opt/unio/storefront/
echo "3/5 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
up -d my -u sonador_saas
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -i sonador_site -u sonador_saas_client,sonador_tours
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -i sonador_site -u sonador_saas_client,sonador_tours
  echo "  customer $D"
done
docker compose up -d odoo </dev/null
REMOTE
echo "4/5 storefront"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio/storefront
docker build -q -t unio-storefront . >/dev/null
NET=$(docker inspect "$(cd /opt/unio && docker compose ps -q odoo)" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker rm -f unio-storefront >/dev/null 2>&1 || true
docker run -d --name unio-storefront --restart unless-stopped --network "$NET" -p 127.0.0.1:3100:3000 \
  -e ODOO_URL=http://odoo:8069 --memory 384m unio-storefront >/dev/null
sleep 4
docker ps --filter name=unio-storefront --format '{{.Names}} {{.Status}}'
REMOTE
echo "5/5 checks"
sleep 8
curl -s -o /dev/null -w "HQ %{http_code}\n" https://my.unio.et/web/login
ssh $H 'curl -s -o /dev/null -w "storefront %{http_code}\n" -H "Host: sonador.unio.et" http://127.0.0.1:3100/robots.txt'
