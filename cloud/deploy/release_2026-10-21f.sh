#!/bin/bash
# Release 21 Oct 2026 (f): the Ethiopian chart of accounts layout (as Eco Green: 1101 cash, 1104 banks, 1400 trade debtors,
# 2000 payables, 3000 equity, 4000 sales, 5000 cost of sales, 8200 administration ..., codes like 1104-0001).
# Ethiopian template and Ethiopian companies are renumbered (balances and entries unchanged); UG / KE untouched.
set -euo pipefail
H=ghion
MODS="sonador_books sonador_inventory sonador_mrp sonador_assets sonador_payroll sonador_project sonador_saas"
echo "1/3 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
echo "2/3 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual ~/sonador-odoo/addons/$m/ $H:/opt/unio/addons/$m/
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/3 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d "${2:-postgres}" -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
renumber() {
  docker compose run --rm -T odoo odoo shell -d "$1" --no-http --db_host db --db_user odoo --db_password "$PW" 2>/dev/null <<'PY' | grep "^>"
c = env['res.company'].search([], limit=1)
if (c.account_fiscal_country_id or c.country_id).code == 'ET':
    A = env['account.account']
    env.cr.execute("select account_id, round(sum(balance),2) from account_move_line where parent_state='posted' group by 1")
    before = dict(env.cr.fetchall())
    n = A.sb_renumber_et(c)
    env.cr.execute("select account_id, round(sum(balance),2) from account_move_line where parent_state='posted' group by 1")
    assert dict(env.cr.fetchall()) == before, 'balances changed'
    env.cr.commit()
    print('>', env.cr.dbname, 'renumbered', n)
else:
    print('>', env.cr.dbname, 'not Ethiopian, unchanged')
PY
}
docker compose stop odoo </dev/null
up -d my -u sonador_saas
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -u sonador_books,sonador_inventory,sonador_mrp,sonador_assets,sonador_payroll,sonador_project
  renumber $T
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -u sonador_books,sonador_inventory,sonador_mrp,sonador_assets,sonador_payroll,sonador_project
  renumber $D
done
docker compose up -d odoo </dev/null
REMOTE
sleep 10
curl -s -o /dev/null -w "sonador %{http_code}\n" https://sonador.unio.et/web/login
