#!/bin/bash
# Release 10 Nov 2026 (h), Sonador books: Bank Suspense 1102-0001 (the Chapa money) becomes a bank / cash account, so it shows on Business status. Seconds; nothing restarts.
#     bash ~/unio-cloud/deploy/release_2026-11-10h.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-10h/suspense_cash.py $H:/root/suspense_cash_20261110h.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/suspense_cash_20261110h.py 2>&1 | grep -E "^>|Error" || true'
