# Unio: read this first

Unio is an ERP built by Sonador Systems on **Odoo 19 Community**: accounting-first apps that all post into one ledger and one stock engine. It is sold as SaaS at **unio.et** (control room my.unio.et, one database per customer at `<company>.unio.et`) and also run on-premise for larger clients (Eco Green in Ethiopia, AfyaSoft in Uganda). The owner is Robel (Sonador Systems).

The company-specific code for Eco Green lives in the separate repo `robeldesta/unio_ecogreen`; this repo is the product.

## Layout

| Path | What |
|---|---|
| `addons/sonador_*` | Our modules (the product). Technical names keep `sonador_`; users never see "Sonador" or "Odoo". |
| `addons/om_*`, `muk_*`, `accounting_pdf_reports` | Third-party modules vendored unchanged. |
| `cloud/` | The SaaS stack: `docker-compose.yml`, `site/` (unio.et static site and sign-up), `server/hq_agent.py`, `storefront/` (Next.js shop front), `deploy/release_*.sh` (every production release as a script). |
| `docs_modules.md` | One line per module: app name, what it does, dependencies. |
| `docs/` | How we work (conventions, product rules, deploy). |

Each `addons/<module>/tests_manual/` holds scripts run in `odoo shell` against a local database.

## Run it locally

Docker, image `odoo:19.0` plus PostgreSQL 17 (see `cloud/docker-compose.yml`). Addons path: `addons/`. Make `cloud/config/odoo.conf` from `docs/odoo.conf.example`; secrets are never in git. Keep one database per local stack: a second one makes Odoo show a database picker and breaks login tests. A fresh Ethiopian company is provisioned by `sonador_saas` templates (`tpl_et`, `tpl_ug`, `tpl_ke`), which set country, currency, chart and taxes.

## The rules that matter most

Read `docs/conventions.md` before changing anything. In short:

1. **No "Sonador", no "Odoo" in anything a user sees.** Apps are plain names: Accounting, People, Payroll, Sales, Inventory…
2. **Screens are OWL client actions** with PDF and Excel export, Ethiopian calendar periods, and no wizard dialogs. Forms follow the layout rules in `docs/conventions.md`.
3. **Production is never changed by hand.** Every change ships as a script (a "release" here, a "kit" for on-premise clients) that backs up first, the owner runs it with sudo, and a script is never edited after it is handed over: issue a new one.
4. **Messages to people read like a person wrote them**: greeting, a summary with real numbers, grouped items, document numbers, a button.
5. **Ethiopian tax and labour rules** come from the owner's rulebook (income tax brackets, pension 7% / 11%, transport exemption 600 / 2,200, one-third deduction limit, Labour Proclamation 1156/2019).

## Where things are documented

- `docs/conventions.md`: product, UI, form, message and code rules.
- `docs/deploy.md`: how releases reach unio.et and how client kits work.
- `docs_modules.md`: what every module does.
