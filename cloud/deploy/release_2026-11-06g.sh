#!/bin/bash
# Release 6 Nov 2026 (g), Unio, the sonador company only: opening balances of the year from 8 Jul 2026 (replaces f).
# 7 Jul 2025: 428,796.40 of the shareholder receivable was dividends paid. 7 Jul 2026: depreciation and profit tax, and one journal on
# 7 Jul 2026 moves into Telebirr what it received during the year (from the banks down to their real balances, all the
# cash on hand, the rest from the shareholder). Backup of the sonador database first; no restart.
#     bash ~/unio-cloud/deploy/release_2026-11-06g.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T -e PGPASSWORD="$PW" db pg_dump -U odoo -Fc sonador > /opt/unio/backups/sonador-before-opening2027-$(date +%Y%m%d-%H%M%S).dump && ls -t /opt/unio/backups | head -1'
echo "2/2 opening balances"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06g/opening_2027.py $H:/root/opening_2027_20261106g.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/opening_2027_20261106g.py 2>&1 | grep -E "^>|Error|assert" || true'
