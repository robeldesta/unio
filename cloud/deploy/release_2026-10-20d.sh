#!/bin/bash
# Release 20 Oct 2026 (d): referrals, Unio HQ, in-app help and support, Unio AI (dormant until the Anthropic key is set),
# Chapa (dormant until live keys), payment-return fix, Unio name in every email, HQ server agent, yearly billing
# (two months free), guided tours.
set -euo pipefail
H=ghion
ADDONS=~/sonador-odoo/addons
MODS="sonador_saas sonador_saas_client sonador_print sonador_studio sonador_unio_brand sonador_brand sonador_ai sonador_tours"
echo "1/6 backup"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -2'
echo "2/6 code"
for m in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual "$ADDONS/$m/" "$H:/opt/unio/addons/$m/"
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/6 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
run() { docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
up() { local o; o="$(run "$@" 2>&1)" || true
  if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -30; docker compose up -d odoo </dev/null; exit 1; fi; }
docker compose stop odoo </dev/null
up -d my -u sonador_saas,sonador_unio_brand
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  up -d $T -i sonador_ai,sonador_tours -u sonador_saas_client,sonador_print,sonador_studio,sonador_unio_brand,sonador_brand
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  up -d $D -i sonador_ai,sonador_tours -u sonador_saas_client,sonador_print,sonador_studio,sonador_unio_brand,sonador_brand
  # AutoBot's first message, sent before the Unio wording existed
  docker compose exec -T db psql -U odoo -d "$D" -qtA -c "UPDATE mail_message SET body = replace(body, 'Odoo''s chat helps employees collaborate efficiently. I''m here to help you discover its features.', 'The chat helps your team work together. I''m AutoBot, here to help you discover it.') WHERE body LIKE '%Odoo''s chat helps%'" </dev/null
  echo "  customer $D"
done
docker compose run --rm -T odoo odoo shell -d my --no-http --db_host db --db_user odoo --db_password "$PW" 2>/dev/null <<'PY' | grep "^>"
P = env['ir.config_parameter']
P.set_param('saas.support_email', P.get_param('saas.support_email') or 'sales@unio.et')
env['saas.tenant']._cron_push_referrals()   # codes, platform address and Unio names into every company
env['saas.hq'].measure()
env.cr.commit()
print('> referral codes, help and HQ measures in place')
PY
docker compose up -d odoo </dev/null
REMOTE
echo "4/6 HQ server agent (every 5 minutes)"
scp -q ~/unio-cloud/server/hq_agent.py $H:/opt/unio/hq_agent.py
ssh $H 'chmod 700 /opt/unio/hq_agent.py && mkdir -p /opt/unio/queue/hq && chown 100:101 /opt/unio/queue/hq && \
  (crontab -l 2>/dev/null | grep -v hq_agent.py; echo "*/5 * * * * /usr/bin/python3 /opt/unio/hq_agent.py >/dev/null 2>&1") | crontab - && \
  /usr/bin/python3 /opt/unio/hq_agent.py && ls /opt/unio/queue/hq'
echo "5/6 website"
rsync -rc --exclude .DS_Store ~/unio-cloud/site/ $H:/var/www/unio/
echo "6/6 checks"
sleep 10
curl -s -o /dev/null -w "HQ app %{http_code}\n" https://my.unio.et/web/login
curl -s "https://my.unio.et/unio/ref?code=NOPE"; echo
