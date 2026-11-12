#!/bin/bash
# Release 29 Oct 2026 (a): Unio AI checks proposed values (choices, fields, lines) before showing a card; Create never crashes.
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
for D in /opt/unio /opt/afyasoft; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_ai/ $H:$D/addons/sonador_ai/
done
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_ai; chown -R 501:50 /opt/afyasoft/addons/sonador_ai; cd /opt/unio && docker compose restart odoo </dev/null >/dev/null 2>&1; cd /opt/afyasoft && docker compose restart odoo </dev/null >/dev/null 2>&1; sleep 15; md5sum /opt/unio/addons/sonador_ai/models/assistant.py /opt/afyasoft/addons/sonador_ai/models/assistant.py'
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
