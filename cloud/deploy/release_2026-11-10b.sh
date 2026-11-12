#!/bin/bash
# Release 10 Nov 2026 (b), AfyaSoft: the seller's TIN 1047587863, BRN and Kampala address on invoices (from the URA
# e-receipt); the footer names Ghion Investment and General Trading Limited. Seconds; nothing restarts.
#     bash ~/unio-cloud/deploy/release_2026-11-10b.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-10b/ghion_tin.py $H:/root/ghion_tin_20261110b.py
ssh $SSHO $H 'cd /opt/afyasoft && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d afyasoft --no-http --log-level=error < /root/ghion_tin_20261110b.py 2>&1 | grep -E "^>|Error" || true'
