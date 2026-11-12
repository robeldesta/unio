#!/bin/bash
# Release 20 Oct 2026 on the server (run from the Mac): print designer, Studio, the Apps screen and industry presets,
# sign-up protections, sign-up confirmed by email (Brevo, port 2525: DigitalOcean blocks 25/465/587).
# Touches only /opt/unio, /var/www/unio and nginx's my.unio.et site (never Eco Green or AfyaSoft).
set -euo pipefail
H=ghion
ADDONS=~/sonador-odoo/addons
echo "1/7 backup of every Unio database"
ssh $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -4'
echo "2/7 code: sonador_saas, sonador_saas_client, sonador_print, sonador_studio"
for m in sonador_saas sonador_saas_client sonador_print sonador_studio; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude tests_manual "$ADDONS/$m/" "$H:/opt/unio/addons/$m/"
done
ssh $H 'chown -R 100:101 /opt/unio/addons'
echo "3/7 databases"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
RUN="docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password $PW --stop-after-init"
run() { $RUN "$@" </dev/null; }
psq() { docker compose exec -T db psql -U odoo -d postgres -qtA -c "$1" </dev/null; }
PSQL="docker compose exec -T db psql -U odoo -d postgres -qtA"
docker compose stop odoo </dev/null
run -d my -u sonador_saas 2>&1 | grep -E "ERROR|CRITICAL" && exit 1 || true
for T in $(psq "SELECT datname FROM pg_database WHERE datname LIKE 'tpl\_%'"); do
  psq "ALTER DATABASE $T WITH IS_TEMPLATE false ALLOW_CONNECTIONS true"
  run -d $T -u sonador_saas_client -i sonador_print,sonador_studio 2>&1 | grep -E "ERROR|CRITICAL" && exit 1 || true
  psq "ALTER DATABASE $T WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
  echo "  template $T"
done
for D in $(psq "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres','my') AND datname NOT LIKE 'tpl\_%'"); do
  run -d $D -u sonador_saas_client -i sonador_print,sonador_studio 2>&1 | grep -E "ERROR|CRITICAL" && exit 1 || true
  echo "  customer $D"
done
echo "4/7 settings and each customer's industry preset"
docker compose run --rm -T odoo odoo shell -d my --no-http --db_host db --db_user odoo --db_password $PW 2>/dev/null <<'PY' | grep "^>"
P = env['ir.config_parameter']
P.set_param('saas.site_url', 'https://unio.et')
P.set_param('saas.mail_from', 'Unio <noreply@unio.et>')
P.set_param('saas.confirm_email', 'True')
env.cr.commit()  # an odoo shell does not save on its own
print('> settings set')
from odoo.addons.sonador_saas.models.provision import _tenant_env
for t in env['saas.tenant'].search([('state', 'in', ('trial', 'active', 'overdue', 'suspended'))]):
    cr, tenv = _tenant_env(t.subdomain)
    try:
        tenv['saas.apps'].apply_industry(t.industry or 'other'); cr.commit()
        print('> %s: apps for %s' % (t.subdomain, t.industry))
    finally:
        cr.close()
PY
docker compose up -d odoo </dev/null
REMOTE
echo "4b/7 outgoing mail through Brevo and the Turnstile robot check (keys read from brevo.txt and turnstile.txt, never printed)"
{ printf "KEY = %s\n" "$(python3 -c 'import json,sys; print(json.dumps(open(sys.argv[1]).read().strip()))' ~/unio-cloud/brevo.txt)"
  python3 -c 'import json,sys; v=[l.split(":",1)[1].strip() for l in open(sys.argv[1]) if ":" in l]; print("SITE, SECRET = %s, %s" % (json.dumps(v[0]), json.dumps(v[1])))' ~/unio-cloud/turnstile.txt
  cat <<'PY'
P = env['ir.config_parameter']
P.set_param('saas.turnstile_site_key', SITE)
P.set_param('saas.turnstile_secret', SECRET)
print('> robot check (Turnstile) on')
S = env['ir.mail_server']
vals = {'name': 'Brevo', 'smtp_host': 'smtp-relay.brevo.com', 'smtp_port': 2525, 'smtp_encryption': 'starttls',
        'smtp_user': 'bf2033001@smtp-brevo.com', 'smtp_pass': KEY, 'from_filter': 'unio.et', 'sequence': 1}
s = S.search([('name', '=', 'Brevo')], limit=1)
s.write(vals) if s else S.create(vals)
env.cr.commit()
print('> mail server set')
PY
} | ssh $H 'cd /opt/unio && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)" && docker compose exec -T odoo odoo shell -d my --no-http --db_host db --db_user odoo --db_password "$PW" 2>/dev/null | grep "^>"'
echo "5/7 website"
rsync -rc --exclude .DS_Store ~/unio-cloud/site/ $H:/var/www/unio/
echo "6/7 nginx: request limit on the sign-up endpoints of my.unio.et"
ssh $H 'bash -s' <<'REMOTE'
set -euo pipefail
cat > /etc/nginx/conf.d/unio-limits.conf <<'NG'
# Unio sign-up endpoints: at most 10 requests a minute per address (bursts of 5)
limit_req_zone $binary_remote_addr zone=unio_signup:10m rate=10r/m;
NG
F=/etc/nginx/sites-enabled/unio-tenant-my
if ! grep -q unio_signup $F; then
  python3 - "$F" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
block = '''    location ~ ^/unio/(signup|resend|verify)$ {
        limit_req zone=unio_signup burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8569;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
    location / {'''
s = s.replace('    location / {', block, 1)
open(p, 'w').write(s)
PY
fi
nginx -t && systemctl reload nginx
REMOTE
echo "7/7 checks"
curl -s https://my.unio.et/unio/form | head -c 120; echo
curl -s -o /dev/null -w "site %{http_code}\n" https://unio.et/verify.html
