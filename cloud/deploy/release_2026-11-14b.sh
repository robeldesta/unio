#!/bin/bash
# Release 14 Nov 2026 (b), unio.et: the whole product. Replaces 12a and 14a (not run for real).
# unio.et is far behind the repo (14 Nov comparison: Accounting 117 files, Inventory 34, Me 21, Sales 15 ...; 10 modules
# not on the server). Shipping a few modules mixed new code with old code it was never run with. 14b ships every module
# of the repo's addons/ and updates, in each company, every one of them it has installed, together: the code the tests
# ran on. A module an updated one now depends on is installed by that update; the run lists them.
# What people will notice, besides the unreleased work since unio.et's last update:
#   Everyone   sign-in fields at once; untouched "New" forms leave no empty drafts; switched-off apps stay off.
#   Owners     "Set up your company" checklist on Me; "Add me to People".
#   Purchase   Purchase Voucher (not Bill); paid first: CPV books advance + VAT (on the VAT return as local purchases),
#              GRNs use the order's advance; on credit: GRN -> Purchase Voucher -> CPV; withholding before VAT.
#   Store      expected order -> GRN in one click; GIVs for a production order (Work in Progress); no finance buttons.
#   Production Recipes in Production; MO/ numbers; Finish and post -> FPRR from WIP; shortages, cost and analytics
#              follow it; floor screens in plain words; tours shown to the roles they are for.
#   Clean-up   empty drafts older than a day (never numbered, nothing typed, no lines) are removed and listed.
# Dry run first (copies of the databases, the new code in a separate folder, nothing live changes), then the real run:
#     SRC=~/unio bash ~/unio/cloud/deploy/release_2026-11-14b.sh --dry-run
#     SRC=~/unio bash ~/unio/cloud/deploy/release_2026-11-14b.sh
# On-premise clients (Eco Green, AfyaSoft) get a kit, not this.
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
SRC="${SRC:-$HOME/unio}"
R="$(cd "$(dirname "$0")" && pwd)/release_2026-11-14b"
# every module of the repo (Unio's and the vendored ones): the product as it was tested
MODS="$(cd "$SRC/addons" && for d in */; do [ -f "${d}__manifest__.py" ] && printf '%s ' "${d%/}"; done)"
[ -n "$MODS" ] || { echo "No modules found in $SRC/addons: stop."; exit 1; }

# the checkout must hold the whole branch
git -C "$SRC" merge-base --is-ancestor 0a5a60b HEAD 2>/dev/null || { echo "$SRC does not contain 0a5a60b (main with the set-up safety fix): stop."; exit 1; }
[ -z "$(git -C "$SRC" status --porcelain -- addons)" ] || { echo "$SRC/addons has uncommitted changes: stop."; exit 1; }

echo "1/6 code to the server: $(wc -w <<<"$MODS") modules ($([ $DRY = 1 ] && echo 'separate folder, dry run' || echo 'live; the whole addons folder kept for rollback'))"
DEST=$([ $DRY = 1 ] && echo /opt/unio/addons-14b || echo /opt/unio/addons)
if [ $DRY = 1 ]; then ssh $SSHO $H "rm -rf $DEST && cp -a /opt/unio/addons $DEST"; else ssh $SSHO $H "rm -rf /root/rollback-14b && cp -a /opt/unio/addons /root/rollback-14b"; fi
for M in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" "$SRC/addons/$M/" $H:$DEST/$M/
done
ssh $SSHO $H "chown -R 100:101 $DEST"
scp -q $SSHO "$R/remember_apps.sql" "$R/cleanup_empty_drafts.py" $H:/root/

[ $DRY = 1 ] || { echo "2/6 backup"; ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'; }
[ $DRY = 0 ] || echo "2/6 backup: not needed, the dry run works on copies"

ssh $SSHO $H "DRY=$DRY DEST=$DEST MODS='$MODS' bash -s" <<'REMOTE'
set -euo pipefail
cd /opt/unio
PW="$(sed -n "s/^POSTGRES_PASSWORD=//p" .env)"
PSQL() { docker compose exec -T db psql -U odoo "$@" </dev/null; }
PSQLF() { docker compose exec -T db psql -U odoo "$@"; }   # reads SQL from stdin
DBS=$(PSQL -d postgres -Atc "select datname from pg_database where not datistemplate and datname <> 'postgres' order by datname")
RUN() {  # odoo with the new code (the dry run mounts the separate folder)
  if [ "$DRY" = 1 ]; then docker compose run --rm -T -v "$DEST:/mnt/extra-addons" odoo odoo --db_host db --db_user odoo --db_password "$PW" "$@" </dev/null
  else docker compose run --rm -T odoo odoo --db_host db --db_user odoo --db_password "$PW" "$@" </dev/null; fi
}
[ "$DRY" = 1 ] || docker compose stop odoo </dev/null
# the dry run must really run the new code: the separate folder has to be what Odoo sees
if [ "$DRY" = 1 ]; then
  seen=$(docker compose run --rm -T -v "$DEST:/mnt/extra-addons" --entrypoint sh odoo -c 'ls /mnt/extra-addons/sonador_factory/models/wip.py 2>/dev/null' </dev/null || true)
  [ -n "$seen" ] || { echo "The dry run cannot see the new code in $DEST: stop."; exit 1; }
  echo "  dry run uses the new code ($DEST)"
fi
failed=""
INLIST="'$(sed "s/ *$//; s/ /','/g" <<<"$MODS")'"
for D in $DBS; do
  has=$(PSQL -d "$D" -Atc "select count(*) from ir_module_module where name='sonador_books' and state='installed'" 2>/dev/null || echo 0)
  [ "$has" = 1 ] || { echo "  $D: not a Unio company, skipped"; continue; }
  T="$D"
  if [ "$DRY" = 1 ]; then
    # a copy while the live server keeps using the database (a template copy needs it idle)
    T="${D}_dry14b"; PSQL -d postgres -qc "drop database if exists \"$T\"" >/dev/null 2>&1; PSQL -d postgres -qc "create database \"$T\"" >/dev/null
    docker compose exec -T db sh -c "pg_dump -U odoo -Fc '$D' | pg_restore -U odoo --no-owner -d '$T'" </dev/null >/dev/null 2>&1 || true
    # the copy must never act: the live server's scheduled jobs would otherwise run on it too (mail queue, reminders,
    # digests: real emails to real customers from a copy). Jobs and mail servers off at once, then Odoo's neutralize.
    PSQL -d "$T" -qc "update ir_cron set active = false; update ir_mail_server set active = false;" >/dev/null 2>&1 || true
    docker compose run --rm -T -v "$DEST:/mnt/extra-addons" odoo odoo neutralize --db_host db --db_user odoo --db_password "$PW" -d "$T" </dev/null >/dev/null 2>&1 || { echo "  $D: the copy could not be neutralized: stop."; PSQL -d postgres -qc "drop database \"$T\"" >/dev/null; exit 1; }
  fi
  echo "3/6 $D: apps switched off, recorded before the update"
  PSQLF -d "$T" -At < /root/remember_apps.sql 2>/dev/null | grep '^>' | sed 's/^/    /' || echo "    (no Apps screen in this database)"
  # every repo module this company has installed, updated together
  UPD=$(PSQL -d "$T" -Atc "select string_agg(name, ',' order by name) from ir_module_module where state = 'installed' and name in ($INLIST)")
  echo "4/6 $D: update ($(tr ',' '\n' <<<"$UPD" | grep -c .) modules)"
  o="$(RUN --stop-after-init -d "$T" -u "$UPD" --no-http 2>&1)" || true
  if grep -qE " ERROR | CRITICAL " <<<"$o"; then
    echo "    FAILED:"; sed -n '/Traceback/,$p' <<<"$o" | grep -vE '^[[:space:]]+[~^]+[[:space:]]*$' | tail -16 | sed 's/^/    /'
    failed="$failed $D"
    [ "$DRY" = 1 ] && { PSQL -d postgres -c "drop database \"$T\"" >/dev/null; continue; }
    break
  fi
  grep -E "UNIO SETUP" <<<"$o" | sed 's/^.*UNIO SETUP: /    note: /' | head -10 || true
  after=$(PSQL -d "$T" -Atc "select string_agg(name, ',' order by name) from ir_module_module where state = 'installed' and name in ($INLIST)")
  added=$(comm -13 <(tr ',' '\n' <<<"$UPD" | sort) <(tr ',' '\n' <<<"$after" | sort) | tr '\n' ' ')
  [ -z "${added// }" ] || echo "    installed by the update (needed by updated modules): $added"
  echo "5/6 $D: empty drafts"
  if [ "$DRY" = 1 ]; then
    docker compose run --rm -T -e DRY_RUN=1 -v "$DEST:/mnt/extra-addons" odoo odoo shell --db_host db --db_user odoo --db_password "$PW" -d "$T" --no-http --log-level=error < /root/cleanup_empty_drafts.py 2>&1 | grep -E '^>' | sed 's/^/    /' || true
    PSQL -d postgres -c "drop database \"$T\"" >/dev/null
  else
    docker compose run --rm -T odoo odoo shell --db_host db --db_user odoo --db_password "$PW" -d "$T" --no-http --log-level=error < /root/cleanup_empty_drafts.py 2>&1 | grep -E '^>' | sed 's/^/    /' || true
  fi
done
if [ "$DRY" = 1 ]; then
  rm -rf "$DEST"
  [ -z "$failed" ] && echo "DRY RUN OK: every database updates cleanly. Nothing live was changed." || { echo "DRY RUN FAILED for:$failed"; exit 1; }
  exit 0
fi
if [ -n "$failed" ]; then
  echo "FAILED on$failed: putting the whole old addons folder back. Databases updated before it run the new code's data:"
  echo "restore them from the backup taken in step 2."
  rm -rf addons && cp -a /root/rollback-14b addons
  chown -R 100:101 addons
  docker compose up -d odoo </dev/null; exit 1
fi
echo "6/6 start, warm the screens"
docker compose up -d odoo </dev/null
REMOTE

[ $DRY = 1 ] && exit 0
sleep 30
for u in https://my.unio.et https://sonador.unio.et; do curl -s -o /dev/null -m 60 -w "$u %{http_code}\n" "$u/web/login"; done
echo "Done. In the browser: sign in (fields at once), Purchase > an order (Purchase Voucher button), Inventory home."
