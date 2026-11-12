#!/bin/bash
# Release 10 Nov 2026 (k), AfyaSoft: the sales invoice redesigned (clean layout, no grid lines, AfyaSoft teal; number and dates
# top right; Bill to / Pay to side by side; light EFRIS box). Backup first; AfyaSoft restarts (a minute); the old print comes
# back by itself if the upgrade fails.
#     bash ~/unio-cloud/deploy/release_2026-11-10k.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
R=~/unio-cloud/deploy/release_2026-11-10k
SH='cd /opt/afyasoft && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d afyasoft --no-http --log-level=error'
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/afyasoft && ./backup.sh >/dev/null && ls -t backups | head -1; cp -a addons/sonador_books/report/invoice.xml /root/books_invoice.xml.before-k; cp -a addons/sonador_efris/views/invoice.xml /root/efris_invoice.xml.before-k'
echo "2/3 invoice design, upgrade"
scp -q $SSHO $R/books_invoice.xml $H:/opt/afyasoft/addons/sonador_books/report/invoice.xml
scp -q $SSHO $R/efris_invoice.xml $H:/opt/afyasoft/addons/sonador_efris/views/invoice.xml
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/afyasoft
chown 100:101 addons/sonador_books/report/invoice.xml addons/sonador_efris/views/invoice.xml
docker compose stop odoo </dev/null
o="$(docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_books,sonador_efris --no-http 2>&1 </dev/null)" || true
if grep -qE "ERROR|CRITICAL" <<<"$o"; then
  echo "  FAILED, putting the old print back"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20
  cp -a /root/books_invoice.xml.before-k addons/sonador_books/report/invoice.xml; cp -a /root/efris_invoice.xml.before-k addons/sonador_efris/views/invoice.xml
  docker compose run --rm -T odoo odoo -c /etc/odoo/odoo.conf --stop-after-init -d afyasoft -u sonador_books,sonador_efris --no-http >/dev/null 2>&1 </dev/null || true
  docker compose up -d odoo </dev/null; exit 1
fi
docker compose up -d odoo </dev/null
REMOTE
sleep 25
echo "3/3 colour, check"
scp -q $SSHO $R/colour.py $R/check.py $H:/root/
ssh $SSHO $H "$SH < /root/colour.py 2>&1 | grep -E '^>|Error' || true; $SH < /root/check.py 2>&1 | grep -E '^>|Error|Traceback' || true"
curl -s -o /dev/null -m 30 -w "https://ghioninvestment.sonador.et %{http_code}\n" https://ghioninvestment.sonador.et/web/login
