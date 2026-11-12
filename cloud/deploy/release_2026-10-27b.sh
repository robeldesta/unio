#!/bin/bash
# Release 27 Oct 2026 (b): fixed assets - country tax classes on their own accounts, Ethiopian periods, automatic monthly or
# yearly depreciation, expensed items on the register, control against the ledger, books vs tax with deferred tax,
# capitalising past expenses; two new reports. One shared SSH connection (the server limits new ones).
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
MODS="sonador_books sonador_assets"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual -e "ssh $SSHO" ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d ${2:-postgres} -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
mods() { if [ "$(psq "SELECT state FROM ir_module_module WHERE name='sonador_assets'" $1)" = "installed" ]; then echo sonador_books,sonador_assets; else echo sonador_books; fi; }
docker compose stop odoo </dev/null
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  M=$(mods $T); up -d $T -u $M
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T ($M)"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres') AND datname NOT LIKE 'tpl\_%'"); do
  M=$(mods $D); up -d $D -u $M
  echo "  $D ($M)"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 15
curl -s -o /dev/null -m 30 -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
