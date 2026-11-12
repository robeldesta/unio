#!/bin/bash
# Release 27 Oct 2026 (a): no Odoo anywhere. Unio in every message the system shows ("Unio Server Error"...), Odoo's
# help/support/odoo.com links gone from the user menu, Settings sidebar and About in Unio names and icons, Unio icons
# for Discuss and Settings, Odoo's own apps (Invoicing, Manufacturing, Employees, Odoo's module list...) never shown.
set -euo pipefail
H=ghion
MODS="sonador_books sonador_unio_brand sonador_saas_client"
echo "1/3 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d ${2:-postgres} -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
brand() {  # install the Unio look where it is missing, upgrade it where it is there
  if [ "$(psq "SELECT state FROM ir_module_module WHERE name='sonador_unio_brand'" $1)" = "installed" ]; then
    up -d $1 -u sonador_books,sonador_unio_brand
  else
    up -d $1 -i sonador_unio_brand -u sonador_books
  fi; }
docker compose stop odoo </dev/null
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  brand $T
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres') AND datname NOT LIKE 'tpl\_%'"); do
  brand $D
  echo "  $D"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
