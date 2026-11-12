#!/bin/bash
# Release 9 Nov 2026 (c), Sonador books: the default "Bank" account 1104-0003 (used for Chapa) is dropped; its entries
# (balance 265.00) move to 1102-0001 Bank Suspense Account; the account and its "Bank" journal are archived.
#     bash ~/unio-cloud/deploy/release_2026-11-09c.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/2 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/2 move to Bank Suspense"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-09c/suspense.py $H:/root/suspense_20261109c.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/suspense_20261109c.py 2>&1 | grep -E "^>|Error|assert" || true'
