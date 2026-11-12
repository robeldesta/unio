#!/bin/bash
# unio cloud hotfix: a sale edited with another fiscal receipt number takes that number (sonador: CSI0000108 -> CSI0000109).
# Backup first; unio restarts (about 20 seconds).
#     ssh ghion bash /root/unio-hotfix-20261105-fsno/deploy.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd /opt/unio
F=addons/sonador_books/models/voucher.py
TS=$(date +%Y%m%d-%H%M%S)
echo "1/4 backup"
cp $F backups/voucher.py.bak-$TS
docker compose exec -T db pg_dump -U odoo -Fc sonador > backups/sonador-$TS-pre-fsno.dump
echo "2/4 code"
install -m "$(stat -c %a $F)" -o "$(stat -c %u $F)" -g "$(stat -c %g $F)" "$HERE/voucher.py" $F
echo "3/4 restart"
docker compose restart odoo >/dev/null; sleep 20
curl -s -o /dev/null -w "   sonador.unio.et %{http_code}\n" https://sonador.unio.et/web/login
echo "4/4 CSI0000108 -> CSI0000109"
docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < "$HERE/renumber_108.py" 2>&1 | grep -E "^>|Error" || true
echo "Done. To go back: cp backups/voucher.py.bak-$TS $F && docker compose restart odoo"
