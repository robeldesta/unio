#!/bin/bash
# Release 10 Nov 2026 (f), Sonador books: Telebirr 400 to Dashen, then Dashen 400 to petty cash. Seconds; nothing restarts.
#     bash ~/unio-cloud/deploy/release_2026-11-10f.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-10f/nov10.py $H:/root/nov10_20261110f.py
ssh $SSHO $H 'cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/nov10_20261110f.py 2>&1 | grep -E "^>|Error" || true'
