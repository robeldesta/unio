#!/bin/bash
# Release 29 Oct 2026 (c): receipts / payments: "Amounts include VAT" (VAT split out of the totals typed); mobile money
# payment method. Unio (all systems and templates) and AfyaSoft.
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1; cd /opt/afyasoft && ./backup.sh >/dev/null && echo afyasoft backed up'
echo "2/3 code"
for S in unio afyasoft; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_books/ $H:/opt/$S/addons/sonador_books/
done
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_books; chown -R 501:50 /opt/afyasoft/addons/sonador_books'
echo "3/3 databases"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
for S in unio afyasoft; do
cd /opt/$S
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d ${2:-postgres} -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
has() { psq "SELECT count(*) FROM ir_module_module WHERE state='installed' AND name='sonador_books'" $1; }
docker compose stop odoo </dev/null
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  [ "$(has $T)" = 1 ] && up -d $T -u sonador_books
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  $S template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres') AND datname NOT LIKE 'tpl\_%'"); do
  [ "$(has $D)" = 1 ] && up -d $D -u sonador_books && echo "  $S $D" || echo "  $S $D (no books)"
done
docker compose up -d odoo </dev/null
done
REMOTE
sleep 20
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
