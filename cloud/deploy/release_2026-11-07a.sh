#!/bin/bash
# Release 7 Nov 2026 (a): Fixed Assets. Changing the cost or depreciation terms of an asset that already has
# depreciation posted no longer refuses: the asset takes the change, its depreciation is worked out again from the
# start and one catch-up journal voucher posts the difference. Every Unio company and Ghion Investment.
# Backup first; both restart (under a minute each).
#     bash ~/unio-cloud/deploy/release_2026-11-07a.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
HERE=~/unio-cloud/deploy/release_2026-11-07a
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1; cd /opt/afyasoft && ./backup.sh && ls -t backups | head -1'
echo "2/3 code"
for T in unio afyasoft; do
  M=/opt/$T/addons/sonador_assets/models
  ssh $SSHO $H "md5sum $M/service.py | grep -q f425ca3951e54fc3d250e1f26c08e1b9 || md5sum $M/service.py | grep -q $(md5sum < $HERE/service.py | cut -c1-32) || { echo '$T: service.py is not the expected one, stopped'; exit 1; }"
  scp -q $SSHO $HERE/service.py $HERE/redo.py $HERE/__init__.py $H:$M/
  ssh $SSHO $H "chown --reference=$M/asset.py $M/service.py $M/redo.py $M/__init__.py; chmod 644 $M/service.py $M/redo.py $M/__init__.py"
done
echo "3/3 restart"
ssh $SSHO $H 'cd /opt/unio && docker compose restart odoo >/dev/null </dev/null; cd /opt/afyasoft && docker compose restart odoo >/dev/null </dev/null'
sleep 25
for u in https://sonador.unio.et https://my.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
