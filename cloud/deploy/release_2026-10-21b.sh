#!/bin/bash
# Release 21 Oct 2026 (b): Ethiopia, credit notes not printed either (fiscal register / Ministry e-invoice only).
set -euo pipefail
H=ghion
echo "1/3 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in sonador_books sonador_print; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -u sonador_books,sonador_print
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -u sonador_books,sonador_print
  echo "  customer $D"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
curl -s -o /dev/null -w "HQ %{http_code}\n" https://my.unio.et/web/login
