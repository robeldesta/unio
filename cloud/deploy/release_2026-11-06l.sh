#!/bin/bash
# Release 6 Nov 2026 (l), Unio, sonador only: 2026/27 bank movements from the CBE and Dashen statements and the Telebirr
# top-ups (bank transfers as Bank Transfer vouchers). Backup first; no restart.
#     bash ~/unio-cloud/deploy/release_2026-11-06l.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-telebirr2027-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/2 Telebirr per its statement"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06l/telebirr_2027.py $H:/root/telebirr_2027_20261106l.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/telebirr_2027_20261106l.py 2>&1 | grep -E "^>|Error|assert" || true'
