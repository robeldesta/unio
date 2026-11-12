#!/bin/bash
# Release 21 Oct 2026 (a): Ethiopia, no printing of sales invoices or cash sales (fiscal register / Ministry e-invoice only).
set -euo pipefail
H=ghion
echo "1/3 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in sonador_books sonador_print sonador_tours sonador_saas; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
up -d my -u sonador_saas
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -u sonador_books,sonador_print,sonador_tours
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -u sonador_books,sonador_print,sonador_tours
  echo "  customer $D"
done
docker compose run --rm -T odoo odoo shell -d my --no-http --db_host db --db_user odoo --db_password "$PW" 2>/dev/null <<'PY' | grep "^>"
old = "<li>Use <b>Print</b> to get the PDF in your company's design.</li>"
new = ("<li>In Ethiopia the customer's invoice itself is issued from your fiscal cash register or as the Ministry of Revenue's "
       "QR-coded e-invoice: Unio records the sale but does not print invoices. Elsewhere, <b>Print</b> gives the PDF in your company's design.</li>")
a = env.ref('sonador_saas.article_01')
a.body = str(a.body).replace(old, new)
a14 = env.ref('sonador_saas.article_14')
a14.body = str(a14.body).replace("pick a document (invoice, receipt, payment voucher, delivery note…)",
    "pick a document (receipt, payment voucher, delivery note, quotation…; in Ethiopia sales invoices are not printed: they come from the fiscal cash register or the Ministry's e-invoice)")
env.cr.commit()
print('> help articles updated', 'fiscal' in str(a.body), 'fiscal' in str(a14.body))
PY
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
curl -s -o /dev/null -w "HQ %{http_code}\n" https://my.unio.et/web/login
