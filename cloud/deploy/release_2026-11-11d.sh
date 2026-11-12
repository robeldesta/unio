#!/bin/bash
# Release 11 Nov 2026 (d), Sonador books: the two Chapa payments become cash sales with 15% VAT (so the VAT declaration
# and the sales register count them): 115 on 22 Oct and 1,150 on 25 Oct, both receipted to Worthy Trading.
# JV/26-27/0001 and 0002 are voided. Give the two receipt numbers (FS No. or e-invoice reference), 115 first:
#     bash ~/unio-cloud/deploy/release_2026-11-11d.sh <receipt for 115> <receipt for 1,150>
set -euo pipefail
[ $# -eq 2 ] || { echo "Usage: bash $0 <receipt no. for 115> <receipt no. for 1,150>"; exit 1; }
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-11d/chapa_sales.py $H:/root/chapa_sales_20261111d.py
ssh $SSHO $H "cd /opt/unio && docker compose exec -T -e FS_115='$1' -e FS_1150='$2' odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/chapa_sales_20261111d.py 2>&1 | grep -E '^>|Error|assert' || true"
