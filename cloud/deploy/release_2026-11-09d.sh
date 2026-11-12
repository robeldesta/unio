#!/bin/bash
# Release 9 Nov 2026 (d), Sonador books: Chapa's fees (with VAT) off Bank Suspense, which ends at 208.37. Run after c.
#     bash ~/unio-cloud/deploy/release_2026-11-09d.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-09d/chapa_fees.py $H:/root/chapa_fees_20261109d.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/chapa_fees_20261109d.py 2>&1 | grep -E "^>|Error|assert" || true'
