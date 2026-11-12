#!/bin/bash
# Release 10 Nov 2026 (i): Unio AI charges only answers that arrive. A question from a system on its own server carries an
# id; when the answer is lost on the way back, the system asks again with the same id and Unio gives the same answer
# without charging it twice. Gateway (Unio HQ) and the AfyaSoft client. Backup first; Unio and AfyaSoft restart (a minute).
#     bash ~/unio-cloud/deploy/release_2026-11-10i.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
R=~/unio-cloud/deploy/release_2026-11-10i
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
scp -q $SSHO $R/saas/ai_gateway.py $H:/opt/unio/addons/sonador_saas/models/ai_gateway.py
scp -q $SSHO $R/saas/ai.py $H:/opt/unio/addons/sonador_saas/controllers/ai.py
scp -q $SSHO $R/saas/ir.model.access.csv $H:/opt/unio/addons/sonador_saas/security/ir.model.access.csv
scp -q $SSHO $R/client/ai.py $H:/opt/unio/addons/sonador_saas_client/models/ai.py
scp -q $SSHO $R/client/ai.py $H:/opt/afyasoft/addons/sonador_saas_client/models/ai.py
echo "3/3 upgrade the gateway, restart"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
chown -R 100:101 /opt/unio/addons/sonador_saas /opt/unio/addons/sonador_saas_client /opt/afyasoft/addons/sonador_saas_client
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d my -u sonador_saas --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  my FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
echo "  my"
docker compose up -d odoo </dev/null
cd /opt/afyasoft && docker compose restart odoo </dev/null >/dev/null && echo "  afyasoft restarted"
REMOTE
sleep 25
for u in https://my.unio.et https://sonador.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
