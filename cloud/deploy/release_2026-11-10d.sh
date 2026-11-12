#!/bin/bash
# Release 10 Nov 2026 (d), AfyaSoft: address Kool Industry Park, Nyamitanga Division, Mbarara City South (replaces the
# Kampala one of b); sales@ghioninvestment.com. Seconds; nothing restarts. Before or after c.
#     bash ~/unio-cloud/deploy/release_2026-11-10d.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-10d/ghion_address.py $H:/root/ghion_address_20261110d.py
ssh $SSHO $H 'cd /opt/afyasoft && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d afyasoft --no-http --log-level=error < /root/ghion_address_20261110d.py 2>&1 | grep -E "^>|Error" || true'
