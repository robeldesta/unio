#!/bin/bash
# Release 9 Nov 2026 (b), Sonador books: BOA 800 to petty cash; petty cash 24,930.02 + shareholder 1,069.98 to Telebirr;
# Meskerem 2019 payroll; pension 10,080 and income tax 15,500 paid from Telebirr with the charges. Telebirr ends 442.10.
#     bash ~/unio-cloud/deploy/release_2026-11-09b.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/2 book"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-09b/nov9.py $H:/root/nov9_20261109b.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/nov9_20261109b.py 2>&1 | grep -E "^>|Error|assert" || true'
