#!/bin/bash
# Release 29 Oct 2026 (i): Agenda (what is due, per person and role) on our own systems first: sonador and my.
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_agenda/ $H:/opt/unio/addons/sonador_agenda/
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_agenda'
echo "3/3 install on sonador and my"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
docker compose stop odoo </dev/null
for D in sonador my; do
  o="$(run -d $D -i sonador_agenda --no-http 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "d = env['agenda.engine'].with_user(env.ref('base.user_admin')).agenda(); print('  $D', len(d['items']), 'agenda items')" \
    | docker compose run --rm -T odoo odoo shell --db_host db --db_user odoo --db_password "$PW" -d $D --no-http 2>/dev/null | grep "agenda items" || true
done
docker compose up -d odoo </dev/null
REMOTE
sleep 15
curl -s -o /dev/null -m 30 -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
