#!/bin/bash
# Release 6 Nov 2026 (e), Unio: the Apps screen also lists Farm, Agenda and Controller. An app a company has never had is
# added the first time its admin switches it on. Code only (no database change); backup first; Unio restarts (under a minute).
#     bash ~/unio-cloud/deploy/release_2026-11-06e.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_saas_client/ $H:/opt/unio/addons/sonador_saas_client/
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_saas_client'
echo "3/3 restart"
ssh $SSHO $H 'cd /opt/unio && docker compose restart odoo >/dev/null </dev/null'
sleep 20
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
