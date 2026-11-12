#!/bin/bash
# Release 30 Oct 2026 (a): the AI button and help switches come with the page again. On 28 Oct the new AI button
# (sonador_ai) went out without the client module part that tells the page AI is on (sonador_saas_client
# models/http.py), so the button stayed hidden for everyone. This ships the current sonador_saas_client to Unio
# (all databases) and AfyaSoft (both databases).
set -euo pipefail
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800"
echo "1/3 backup"
ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'
ssh $SSHO $H 'cd /opt/afyasoft && PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"; mkdir -p backups; for D in afyasoft afyasoft_sim_20261025; do docker compose exec -T db pg_dump -U odoo -Fc $D > backups/$D-pre-20261030a.dump; done; echo afyasoft backed up'
echo "2/3 code"
for T in /opt/unio /opt/afyasoft; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" ~/sonador-odoo/addons/sonador_saas_client/ $H:$T/addons/sonador_saas_client/
done
ssh $SSHO $H 'chown -R 100:101 /opt/unio/addons/sonador_saas_client; chown -R 501:50 /opt/afyasoft/addons/sonador_saas_client'
echo "3/3 update"
ssh $SSHO $H 'bash -s' <<'REMOTE'
set -euo pipefail
for T in unio afyasoft; do
  cd /opt/$T
  PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
  DBS=$( [ $T = unio ] && echo "tpl_et tpl_ke tpl_ug my sonador solyemtrading mahderb" || echo "afyasoft_sim_20261025 afyasoft" )
  docker compose stop odoo </dev/null
  for D in $DBS; do
    installed=$(docker compose exec -T db psql -U odoo -d $D -Atc "select state from ir_module_module where name='sonador_saas_client'" </dev/null || true)
    [ "$installed" = installed ] || { echo "  $T $D: client module not installed, skipped"; continue; }
    o="$(docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" --stop-after-init -d $D -u sonador_saas_client --no-http 2>&1 </dev/null)" || true
    if grep -qE "ERROR|CRITICAL" <<<"$o"; then grep -E "ERROR|CRITICAL" -A6 <<<"$o" | head -20; docker compose up -d odoo </dev/null; exit 1; fi
    echo "  $T $D"
  done
  docker compose up -d odoo </dev/null
done
REMOTE
sleep 15
for u in https://sonador.unio.et https://my.unio.et https://ghioninvestment.sonador.et; do curl -s -o /dev/null -m 30 -w "$u %{http_code}\n" $u/web/login; done
