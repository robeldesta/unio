#!/bin/bash
# Release 6 Nov 2026 (m), Unio, sonador only: CSI0000108 Jobale corrected to 107,125.00 (received 104,330.43).
#     bash ~/unio-cloud/deploy/release_2026-11-06m.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-jobale108-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/2 CSI0000108 Jobale"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06m/jobale_fix.py $H:/root/jobale_fix_20261106m.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/jobale_fix_20261106m.py 2>&1 | grep -E "^>|Error|assert" || true'
