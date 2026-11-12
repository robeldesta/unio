#!/bin/bash
# Release 6 Nov 2026 (j), Unio, sonador only: OB/25-26/0001 reads the opening balance of Jul 8, 2026 account by account
# (338,859.47); a closing voucher of 2018 on the same day. Balances unchanged. Backup first; no restart.
#     bash ~/unio-cloud/deploy/release_2026-11-06j.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-openinglist-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/2 opening list"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06j/opening_list.py $H:/root/opening_list_20261106j.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/opening_list_20261106j.py 2>&1 | grep -E "^>|Error|assert" || true'
