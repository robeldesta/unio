#!/bin/bash
# Release 14 Nov 2026 (a), unio.et: replaces 12a (its dry run stopped every database at an update-time set-up step).
# Ships main as of PRs #2, #4 and #5 plus the set-up safety fix. What people will notice:
#   Everyone   sign-in fields appear at once; a "New" form left untouched no longer leaves an empty draft behind;
#              apps a company switched off stay off after releases (each company's choice is recorded first).
#   Owners     "Set up your company" checklist on Me (by kind of business); "Add me to People".
#   Purchase   no more "Bill": it is the Purchase Voucher. Paid first: the CPV books the advance and the input VAT,
#              and GRNs use that order's advance. On credit: GRN -> Purchase Voucher -> CPV. The order shows
#              Invoiced / Not invoiced.
#   Store      an expected order opens as a GRN filled from it; GIVs can be for a production order (lines from its
#              recipe, charged to Work in Progress); store clerks no longer see finance buttons.
#   Production Recipes inside Production; production orders numbered MO/; "Finish and post" receives the finished
#              goods on an FPRR from Work in Progress (materials are no longer taken from stock a second time).
#              Companies with Production get: WIP account 241100, FPRR cost from WIP, Raw Materials issued to WIP.
#   Accounting VAT paid ahead on a CPV is on the VAT return as local purchases (with its base); withholding on a
#              payment against an order is worked out before VAT. Shortages, posted cost and production analytics
#              follow the GIV / FPRR flow; production-floor screens and tours fixed (tours were hidden from non-admins).
#   Clean-up   empty drafts older than a day (never numbered, nothing typed, no lines) are removed and listed.
#
# Run the dry run first: everything on copies of the databases, with the new code in a separate folder; nothing live
# changes, the copies are dropped. Then the real run (backs up first).
#     SRC=~/unio bash ~/unio/cloud/deploy/release_2026-11-14a.sh --dry-run
#     SRC=~/unio bash ~/unio/cloud/deploy/release_2026-11-14a.sh
# SRC is the checkout with the branch (default ~/unio). On-premise clients (Eco Green, AfyaSoft) get a kit, not this.
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
H=ghion
SSHO="-o ControlMaster=auto -o ControlPath=/tmp/ssh-ghion-%r -o ControlPersist=1800 -o ConnectTimeout=40"
SRC="${SRC:-$HOME/unio}"
R="$(cd "$(dirname "$0")" && pwd)/release_2026-11-14a"
MODS="sonador_assets sonador_books sonador_factory sonador_inventory sonador_me sonador_purchase sonador_saas sonador_saas_client sonador_sales sonador_tours sonador_unio_brand"
UPD="sonador_sales,sonador_books,sonador_inventory,sonador_purchase,sonador_factory,sonador_me,sonador_assets,sonador_saas_client,sonador_saas,sonador_tours,sonador_unio_brand"

# the checkout must hold the whole branch
git -C "$SRC" merge-base --is-ancestor 0a5a60b HEAD 2>/dev/null || { echo "$SRC does not contain 0a5a60b (main with the set-up safety fix): stop."; exit 1; }
[ -z "$(git -C "$SRC" status --porcelain -- addons)" ] || { echo "$SRC/addons has uncommitted changes: stop."; exit 1; }

echo "1/6 code to the server ($([ $DRY = 1 ] && echo 'separate folder, dry run' || echo 'live, old modules kept for rollback'))"
DEST=$([ $DRY = 1 ] && echo /opt/unio/addons-14a || echo /opt/unio/addons)
if [ $DRY = 1 ]; then ssh $SSHO $H "rm -rf $DEST && cp -a /opt/unio/addons $DEST"; else ssh $SSHO $H "rm -rf /root/rollback-14a && mkdir -p /root/rollback-14a && cd /opt/unio/addons && cp -a $MODS /root/rollback-14a/"; fi
for M in $MODS; do
  rsync -rc --delete --exclude __pycache__ --exclude '*.pyc' --exclude .git -e "ssh $SSHO" "$SRC/addons/$M/" $H:$DEST/$M/
done
ssh $SSHO $H "chown -R 100:101 $DEST"
scp -q $SSHO "$R/remember_apps.sql" "$R/cleanup_empty_drafts.py" $H:/root/

[ $DRY = 1 ] || { echo "2/6 backup"; ssh $SSHO $H 'cd /opt/unio && ./backup.sh >/dev/null && ls -t backups/nightly | head -1'; }
[ $DRY = 0 ] || echo "2/6 backup: not needed, the dry run works on copies"

ssh $SSHO $H "DRY=$DRY DEST=$DEST UPD=$UPD MODS='$MODS' bash -s" <<'REMOTE'
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
for D in $DBS; do
  has=$(PSQL -d "$D" -Atc "select count(*) from ir_module_module where name='sonador_books' and state='installed'" 2>/dev/null || echo 0)
  [ "$has" = 1 ] || { echo "  $D: not a Unio company, skipped"; continue; }
  T="$D"
  if [ "$DRY" = 1 ]; then
    # a copy while the live server keeps using the database (a template copy needs it idle)
    T="${D}_dry14a"; PSQL -d postgres -qc "drop database if exists \"$T\"" >/dev/null 2>&1; PSQL -d postgres -qc "create database \"$T\"" >/dev/null
    docker compose exec -T db sh -c "pg_dump -U odoo -Fc '$D' | pg_restore -U odoo --no-owner -d '$T'" </dev/null >/dev/null 2>&1 || true
  fi
  echo "3/6 $D: apps switched off, recorded before the update"
  PSQLF -d "$T" -At < /root/remember_apps.sql 2>/dev/null | grep '^>' | sed 's/^/    /' || echo "    (no Apps screen in this database)"
  echo "4/6 $D: update"
  o="$(RUN --stop-after-init -d "$T" -u "$UPD" --no-http 2>&1)" || true
  if grep -qE " ERROR | CRITICAL " <<<"$o"; then
    echo "    FAILED:"; sed -n '/Traceback/,$p' <<<"$o" | grep -vE '^[[:space:]]+[~^]+[[:space:]]*$' | tail -16 | sed 's/^/    /'
    failed="$failed $D"
    [ "$DRY" = 1 ] && { PSQL -d postgres -c "drop database \"$T\"" >/dev/null; continue; }
    break
  fi
  grep -E "UNIO SETUP" <<<"$o" | sed 's/^.*UNIO SETUP: /    note: /' | head -10 || true
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
  echo "FAILED on$failed: putting the old modules back. Databases updated before it run the new code's data: restore"
  echo "them from the backup taken in step 2 if anything looks wrong."
  for M in $MODS; do rm -rf "addons/$M"; cp -a "/root/rollback-14a/$M" "addons/$M"; done
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
