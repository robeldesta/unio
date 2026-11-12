#!/bin/bash
# Release 10 Nov 2026 (j), AfyaSoft: one invoice print. The sales invoice (Print > Invoice on the sale) shows the bank to pay
# to (Stanbic 9030025359134, Ghion Investment and General Trading Limited); Odoo's own "Proforma Invoice" prints on the
# journal entry come off the Print menu. Backup first; AfyaSoft restarts (a minute).
#     bash ~/unio-cloud/deploy/release_2026-11-10j.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
R=~/unio-cloud/deploy/release_2026-11-10j
SH='cd /opt/afyasoft && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d afyasoft --no-http --log-level=error'
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/afyasoft && ./backup.sh >/dev/null && ls -t backups | head -1; cp -a addons/sonador_books/report/invoice.xml /root/invoice.xml.before-j'
echo "2/3 invoice print, upgrade"
scp -q $SSHO $R/invoice.xml $H:/opt/afyasoft/addons/sonador_books/report/invoice.xml
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/afyasoft
chown 100:101 addons/sonador_books/report/invoice.xml
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_books --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then
  echo "  FAILED, putting the old print back"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20
  cp -a /root/invoice.xml.before-j addons/sonador_books/report/invoice.xml
  docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_books --no-http >/dev/null 2>&1 </dev/null || true
  docker compose up -d odoo </dev/null; exit 1
fi
docker compose up -d odoo </dev/null
REMOTE
sleep 25
echo "3/3 one print, bank"
scp -q $SSHO $R/one_print.py $R/check.py $H:/root/
ssh $SSHO $H "$SH < /root/one_print.py 2>&1 | grep -E '^>|Error' || true; $SH < /root/check.py 2>&1 | grep -E '^>|Error|Traceback' || true"
curl -s -o /dev/null -m 30 -w "https://ghioninvestment.sonador.et %{http_code}\n" https://ghioninvestment.sonador.et/web/login
