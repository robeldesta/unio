#!/bin/bash
# Release 5 Nov 2026 (d), Unio: the Farm app (poultry first) in the sonador company. The sign-up templates (locked) and
# other customers' companies are left as they are. Backup first; Unio restarts (a minute or two).
#     bash ~/unio-cloud/deploy/release_2026-11-05d.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_farm/ $H:/opt/unio/addons/sonador_farm/
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_farm'
echo "3/3 install"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
for D in sonador; do
  o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -i sonador_farm --no-http 2>&1 </dev/null)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "  $D"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 20
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
