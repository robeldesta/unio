#!/bin/bash
# Release 21 Oct 2026 (c): "NOT A TAX INVOICE" on Ethiopian customer documents; the in-app nightly backup job is switched
# off in the cloud (the server backs every company up itself; the job could only fail there and showed as errors in HQ).
set -euo pipefail
H=ghion
echo "1/3 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in sonador_print sonador_saas; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d "${2:-postgres}" -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
NOBACKUP="UPDATE ir_cron SET active = false WHERE id IN (SELECT res_id FROM ir_model_data WHERE module = 'sonador_books' AND name = 'cron_sb_backup')"
docker compose stop odoo </dev/null
up -d my -u sonador_saas
psq "$NOBACKUP" my || true
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -u sonador_print
  psq "$NOBACKUP" $T
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -u sonador_print
  psq "$NOBACKUP" $D
  echo "  customer $D"
done
psq "UPDATE saas_server_event SET done = true WHERE kind = 'error' AND (message LIKE '%nightly backup%' OR message LIKE '%Database management functions blocked%')" my
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
curl -s -o /dev/null -w "HQ %{http_code}\n" https://my.unio.et/web/login
