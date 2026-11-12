#!/bin/bash
# Release 5 Nov 2026 (b), Unio: Chart of Accounts has View on every account: Account Transactions (brought forward,
# every entry with its number, customer / vendor, debit, credit, running balance, carried forward; a double-click opens
# the voucher). Paying taxes, pension, salaries, a dividend or a loan: no withholding and no goods / services question. sonador: the accounts added on 5 Nov numbered
# like the rest of the chart (1104-0004 ...). Backup first; Unio restarts (a minute or two).
#     bash ~/unio-cloud/deploy/release_2026-11-05b.sh
set -euo pipefail
H=ghion
HERE=~/unio-cloud/deploy/release_2026-11-05b
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/4 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/4 code"
ssh $SSHO $H 'cd /opt/unio/addons/sonador_books && for f in models/account_code.py models/report_engine.py models/report_present.py models/voucher.py views/ifrs_views.xml views/voucher_views.xml; do cp $f /opt/unio/backups/$(basename $f).bak-20261105b; done'
for f in account_code report_engine report_present voucher; do rsync -c -e "ssh $SSHO" $HERE/$f.py $H:/opt/unio/addons/sonador_books/models/$f.py; done
for f in ifrs_views voucher_views; do rsync -c -e "ssh $SSHO" $HERE/$f.xml $H:/opt/unio/addons/sonador_books/views/$f.xml; done
rsync -c -e "ssh $SSHO" $HERE/code_style.py $H:/root/code_style_20261105b.py
ssh $SSHO $H 'chown 100:101 /opt/unio/addons/sonador_books/models/account_code.py /opt/unio/addons/sonador_books/models/report_engine.py /opt/unio/addons/sonador_books/models/report_present.py /opt/unio/addons/sonador_books/models/voucher.py /opt/unio/addons/sonador_books/views/ifrs_views.xml /opt/unio/addons/sonador_books/views/voucher_views.xml; chmod a+r /root/code_style_20261105b.py'
echo "3/4 update"
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
sleep 20
echo "4/4 sonador account numbers"
docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error < /root/code_style_20261105b.py 2>&1 | grep -E "^>|Error" || true
REMOTE
for u in https://sonador.unio.et https://my.unio.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
