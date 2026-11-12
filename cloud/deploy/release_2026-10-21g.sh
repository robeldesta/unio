#!/bin/bash
# Release 21 Oct 2026 (g): the School app (students and admissions, classes, attendance, marks with rank, Amharic and
# English report cards, fees billed and collected on the books) and "School" as a business type at sign-up.
# Installed everywhere (all apps are in the price); existing companies keep it hidden until they switch it on in Apps.
set -euo pipefail
H=ghion
echo "1/4 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/4 code"
for m in sonador_school sonador_saas sonador_saas_client; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/4 website (sign-up: School)"
rsync -rc --exclude .DS_Store ~/unio-cloud/site/ $H:/var/www/unio/
echo "4/4 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d "${2:-postgres}" -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
hide() { psq "UPDATE ir_ui_menu SET active = false WHERE id = (SELECT res_id FROM ir_model_data WHERE module = 'sonador_school' AND name = 'menu_school_root')" "$1"; }
docker compose stop odoo </dev/null
up -d my -u sonador_saas
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -i sonador_school -u sonador_saas_client
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -i sonador_school -u sonador_saas_client
  hide $D
  echo "  customer $D (School installed, hidden)"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
curl -s https://unio.et/signup.html | grep -c 'value="school"'
