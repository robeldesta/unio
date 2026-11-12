#!/bin/bash
# Release 6 Nov 2026 (q): journal vouchers and opening balances must balance (debits = credits) on saving and on posting,
# and no line may take a negative amount. Nothing is balanced behind the user's back any more. Every Unio company and
# Ghion Investment. One new file in Accounting; backup first; both restart (under a minute each).
#     bash ~/unio-cloud/deploy/release_2026-11-06q.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1; cd /opt/afyasoft && ./backup.sh && ls -t backups | head -1'
echo "2/3 code"
for T in unio afyasoft; do
  scp -q $SSHO ~/unio-cloud/deploy/release_2026-11-06q/balance_guard.py $H:/opt/$T/addons/sonador_books/models/balance_guard.py
  ssh $SSHO $H "f=/opt/$T/addons/sonador_books/models/__init__.py; grep -q '^from . import balance_guard' \$f || echo 'from . import balance_guard' >> \$f; chown --reference=\$f /opt/$T/addons/sonador_books/models/balance_guard.py; chmod 644 /opt/$T/addons/sonador_books/models/balance_guard.py"
done
echo "3/3 restart"
ssh $SSHO $H 'cd /opt/unio && docker compose restart odoo >/dev/null </dev/null; cd /opt/afyasoft && docker compose restart odoo >/dev/null </dev/null'
sleep 25
for u in https://sonador.unio.et https://my.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
