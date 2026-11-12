#!/bin/bash
# Release 5 Nov 2026 (c), Unio: "Goods or services?" on payments has a fourth choice, Government (taxes, duties, fees):
# never withheld. A supplier's last choice is offered again next time. Backup first; Unio restarts (a minute or two).
#     bash ~/unio-cloud/deploy/release_2026-11-05c.sh
set -euo pipefail
H=ghion
HERE=~/unio-cloud/deploy/release_2026-11-05c
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1 && cp addons/sonador_books/models/voucher.py backups/voucher.py.bak-20261105c'
echo "2/3 code"
rsync -c -e "ssh $SSHO" $HERE/voucher.py $H:/opt/unio/addons/sonador_books/models/voucher.py
ssh $SSHO $H 'chown 100:101 /opt/unio/addons/sonador_books/models/voucher.py'
echo "3/3 update"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
for D in tpl_et tpl_ke tpl_ug my sonador solyemtrading mahderb; do
  installed=$(docker compose exec -T db psql -U odoo -d $D -Atc "select state from ir_module_module where name='sonador_books'" </dev/null || true)
  [ "$installed" = installed ] || { echo "  $D: books not installed, skipped"; continue; }
  o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -u sonador_books --no-http 2>&1 </dev/null)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "  $D"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 20
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
