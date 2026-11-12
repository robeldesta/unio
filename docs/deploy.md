# Deploying

## unio.et (SaaS)

- Server stack: `/opt/unio` on the host, `docker compose` (`cloud/docker-compose.yml`), Odoo on 127.0.0.1:8569 behind nginx, `dbfilter ^%d$` so every customer database answers on its own sub-domain.
- Platform database `my` runs `sonador_saas` (the "Cloud" app: tenants, sign-up, billing, referrals, HQ dashboard, help articles). Template databases `tpl_et`, `tpl_ug`, `tpl_ke` are locked and copied for each new customer; `sonador_saas_client` in each customer database shows the trial banner and locks the database read-only when a trial ends.
- Per-tenant HTTPS: the platform drops a request in a queue folder; a systemd path unit runs certbot and writes the nginx site.
- Every production change is a script in `cloud/deploy/release_<date><letter>.sh`: it backs up, copies the changed modules, updates them, warms assets and restarts. Run a dry run with rollback on the live data before handing it over. The owner runs it.

## On-premise clients ("kits")

Clients such as Eco Green run their own server. Changes go as a kit folder `kit-<date><letter>/` with `deploy.sh` (back up with `sudo -u postgres pg_dump`, install files, `-u` if fields changed, `odoo shell` steps, warm assets, start) plus the files and step scripts it needs. Copy it with `COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs` so macOS `._*` files never reach the server. Once handed over, a kit is never edited: the next change gets the next letter.

## Secrets

Credentials live only in `CREDENTIALS*` files and `odoo.conf` on each machine, never in git. `docs/odoo.conf.example` shows the keys.
