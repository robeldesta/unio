#!/bin/bash
# Release 6 Nov 2026 (n), Unio: ledgers leave out voided entries and their same-day reversals, and show money in before
# money out within a day (one report file, Unio restarts, under a minute). sonador only: salaries on the CPVs from Dashen on
# the statement's days, CSI0000111 into Dashen; the four correcting journals voided.
#     bash ~/unio-cloud/deploy/release_2026-11-06n.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
F=/opt/unio/addons/sonador_books/models/report_engine.py
echo "1/4 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-dashen2027-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/4 report file"
ssh $SSHO $H "md5sum $F | grep -q edc7df6226638f7c1bffc4b685dd4336 || md5sum $F | grep -q $(md5sum < ~/unio-cloud/deploy/release_2026-11-06n/report_engine.py | cut -c1-32) || { echo 'report_engine.py on the server is not the expected one: stopped'; exit 1; }"
ssh $SSHO $H "cp $F /opt/unio/backups/report_engine.py.before-n"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06n/report_engine.py $H:$F
ssh $SSHO $H "chown 100:101 $F"
echo "3/4 restart"
ssh $SSHO $H 'cd /opt/unio && docker compose restart odoo >/dev/null </dev/null'
sleep 20
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
echo "4/4 Dashen per its statement"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06n/dashen_fix.py $H:/root/dashen_fix_20261106n.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/dashen_fix_20261106n.py 2>&1 | grep -E "^>|Error|assert" || true'
