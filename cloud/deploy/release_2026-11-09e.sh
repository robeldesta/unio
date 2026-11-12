#!/bin/bash
# Release 9 Nov 2026 (e). Runs c and d too if not run yet (each is safe to repeat).
# Books: bank and transfer charges include VAT 15%; the VAT part now goes to VAT receivable on its own (setting in Accounting
# settings, on for Sonador only). The charges booked before: their VAT (171.31) moved out today in one journal.
#     bash ~/unio-cloud/deploy/release_2026-11-09e.sh
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
R=~/unio-cloud/deploy
SH='cd /opt/unio && docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d sonador --no-http --log-level=error'
echo "1/5 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/5 old Bank account to Bank Suspense (c)"
scp -q $SSHO $R/release_2026-11-09c/suspense.py $H:/root/suspense_20261109c.py
ssh $SSHO $H "$SH < /root/suspense_20261109c.py 2>&1 | grep -E '^>|Error|assert' || true"
echo "3/5 Chapa fees (d)"
scp -q $SSHO $R/release_2026-11-09d/chapa_fees.py $H:/root/chapa_fees_20261109d.py
ssh $SSHO $H "$SH < /root/chapa_fees_20261109d.py 2>&1 | grep -E '^>|Error|assert' || true"
echo "4/5 VAT in bank charges: code, upgrade"
scp -q $SSHO $R/release_2026-11-09e/files/voucher.py $R/release_2026-11-09e/files/company.py $H:/opt/unio/addons/sonador_books/models/
scp -q $SSHO $R/release_2026-11-09e/files/company_views.xml $H:/opt/unio/addons/sonador_books/views/
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
chown -R 100:101 addons/sonador_books
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null
for D in sonador solyemtrading mahderb my; do
  o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -u sonador_books --no-http 2>&1 </dev/null)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then echo "  $D FAILED"; grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
  echo "  $D"
done
docker compose up -d odoo </dev/null
REMOTE
sleep 25
echo "5/5 VAT out of the charges booked before"
scp -q $SSHO $R/release_2026-11-09e/charges_vat.py $H:/root/charges_vat_20261109e.py
ssh $SSHO $H "$SH < /root/charges_vat_20261109e.py 2>&1 | grep -E '^>|Error|assert' || true"
curl -s -o /dev/null -m 30 -w "https://sonador.unio.et %{http_code}\n" https://sonador.unio.et/web/login
