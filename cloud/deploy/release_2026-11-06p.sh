#!/bin/bash
# Release 6 Nov 2026 (p), replaces (o): the Audit Room in every Unio company (sonador, solyemtrading, mahderb, my) and in
# Ghion Investment (ghioninvestment.sonador.et). Internal auditor visits, findings, answers, visit reports; auditors see
# only Accounting and the Audit Room. Backup first; both restart (a minute or two each).
#     bash ~/unio-cloud/deploy/release_2026-11-06p.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/4 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1; cd /opt/afyasoft && ./backup.sh && ls -t backups | head -1'
echo "2/4 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude .gitignore -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_audit/ $H:/opt/unio/addons/sonador_audit/
rsync -c -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_saas_client/models/apps.py $H:/opt/unio/addons/sonador_saas_client/models/apps.py
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_audit /opt/unio/addons/sonador_saas_client/models/apps.py'
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude .gitignore -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_audit/ $H:/opt/afyasoft/addons/sonador_audit/
ssh $SSHO $H 'chown -R 0:0 /opt/afyasoft/addons/sonador_audit && chmod -R a+rX /opt/afyasoft/addons/sonador_audit'
echo "3/4 install: Unio"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
for D in sonador solyemtrading mahderb my; do
  o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -i sonador_audit --no-http 2>&1 </dev/null)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  $D FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "  $D"
done
docker compose up -d odoo </dev/null
REMOTE
echo "4/4 install: Ghion Investment"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/afyasoft
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -i sonador_audit --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  afyasoft FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
echo "  afyasoft"
docker compose up -d odoo </dev/null
REMOTE
sleep 25
for u in https://sonador.unio.et https://my.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
