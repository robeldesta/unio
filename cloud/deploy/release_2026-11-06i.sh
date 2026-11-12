#!/bin/bash
# Release 6 Nov 2026 (i), Unio, the sonador company only: the opening balances in their own place. OB/24-25/0001 corrected
# (shareholder 258,203.60, old withholding set off), a new opening voucher on Jul 7, 2026 (the real bank balances), the
# journals that carried them voided, depreciation and profit tax as one year-end journal. Backup first; no restart.
#     bash ~/unio-cloud/deploy/release_2026-11-06i.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-openingplace-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/2 opening balances to their place"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06i/opening_place.py $H:/root/opening_place_20261106i.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/opening_place_20261106i.py 2>&1 | grep -E "^>|Error|assert" || true'
