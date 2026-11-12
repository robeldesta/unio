#!/bin/bash
# Release 9 Nov 2026 (a): "Time Off" is called "Leave Requests" everywhere people see it (menus, screens, Me), in every
# Unio company and Ghion Investment. Text only. Backup first; both restart (a minute or two each).
#     bash ~/unio-cloud/deploy/release_2026-11-09a.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1; cd /opt/afyasoft && ./backup.sh && ls -t backups | head -1'
echo "2/3 text"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-09a/rename.sh $H:/root/rename_20261109a.sh
ssh $SSHO $H 'bash /root/rename_20261109a.sh /opt/unio/addons; chown -R 100:101 /opt/unio/addons/sonador_*; bash /root/rename_20261109a.sh /opt/afyasoft/addons'
echo "3/3 upgrade the menus, restart"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
for D in sonador solyemtrading mahderb my; do
  o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -u sonador_timeoff --no-http 2>&1 </dev/null)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  $D FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "  $D"
done
docker compose up -d odoo </dev/null
cd /opt/afyasoft
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_timeoff --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  afyasoft FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
echo "  afyasoft"
docker compose up -d odoo </dev/null
REMOTE
sleep 25
for u in https://sonador.unio.et https://my.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
