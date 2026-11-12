#!/bin/bash
# Release 28 Oct 2026 (a): HQ keeps no books. Payments go into Sonador Systems' own books (sonador.unio.et) as receipt +
# draft invoice + gateway fee, tagged Unio; last month's AI cost as a draft payment; HQ's test entries removed; the real
# Ethio Telecom bill moved to the company books; Sonador Systems free and active; Accounting hidden in HQ.
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/4 backup"
ssh $SSHO $H 'cd /opt/unio && for D in my sonador; do docker compose exec -T db pg_dump -U odoo -Fc $D > backups/$D-prebooks-$(date +%Y%m%d-%H%M).dump; done; ls backups | grep prebooks'
echo "2/4 code"
rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_saas/ $H:/opt/unio/addons/sonador_saas/
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons'
echo "3/4 upgrade, move, clean"
scp $SSHO ~/unio-cloud/deploy/books/move_to_books.py ~/unio-cloud/deploy/books/clean_hq.py $H:/tmp/
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
docker compose stop odoo </dev/null >/dev/null 2>&1
docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d my -u sonador_saas </dev/null 2>&1 | grep -E "ERROR|CRITICAL|Modules loaded"
docker compose up -d odoo </dev/null >/dev/null 2>&1
sleep 10
sh_() { docker compose exec -T odoo odoo shell -d $1 --no-http --db_host db --db_user odoo --db_password "$PW" 2>&1 < $2 | grep -E "^(MOVED|LEFT|SONADOR|ACCOUNTING)|Error|error" || true; }
sh_ sonador /tmp/move_to_books.py
sh_ my /tmp/clean_hq.py
rm -f /tmp/move_to_books.py /tmp/clean_hq.py
REMOTE
echo "4/4 check"
curl -s -o /dev/null -m 30 -w "my %{http_code}\n" https://my.unio.et/web/login
curl -s -o /dev/null -m 30 -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
