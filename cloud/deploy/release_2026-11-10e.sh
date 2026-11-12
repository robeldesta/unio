#!/bin/bash
# Release 10 Nov 2026 (e), AfyaSoft: replaces c (do not run c: it would put back b's Kampala address over d's).
# EFRIS "Issued in the URA portal": our print is titled Invoice and carries the EFRIS fiscal document no. and verification
# code typed on the posted invoice; the simulation's TEST numbers come off the 18 invoices. Backup first; AfyaSoft restarts
# (a minute).
#     bash ~/unio-cloud/deploy/release_2026-11-10e.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
R=~/unio-cloud/deploy
SH='cd /opt/afyasoft && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d afyasoft --no-http --log-level=error'
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/afyasoft && ./backup.sh >/dev/null && ls -t backups | head -1'
echo "2/3 EFRIS code, upgrade"
scp -q $SSHO $R/release_2026-11-10c/files/efris.py $H:/opt/afyasoft/addons/sonador_efris/models/efris.py
scp -q $SSHO $R/release_2026-11-10c/files/views.xml $R/release_2026-11-10c/files/invoice.xml $H:/opt/afyasoft/addons/sonador_efris/views/
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/afyasoft
chown -R 100:101 addons/sonador_efris
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_efris --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  afyasoft FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
docker compose up -d odoo </dev/null
REMOTE
sleep 25
echo "3/3 EFRIS: issued in the URA portal"
scp -q $SSHO $R/release_2026-11-10c/efris_manual.py $H:/root/efris_manual_20261110c.py
ssh $SSHO $H "$SH < /root/efris_manual_20261110c.py 2>&1 | grep -E '^>|Error' || true"
curl -s -o /dev/null -m 30 -w "https://ghioninvestment.sonador.et %{http_code}\n" https://ghioninvestment.sonador.et/web/login
