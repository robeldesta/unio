# Conventions

These are decisions the owner (Robel) made and repeated. Follow them without asking again.

## Product and wording

- The product is **Unio**. Never show "Sonador" or "Odoo" in menus, labels, messages, groups, report titles or manifest names. Only technical module names (`sonador_*`) and the manifest author keep it.
- Apps have plain names: Accounting, People, Payroll, Attendance, Sales, Purchase, Inventory, Production, Projects, Fleet, CRM, Planning, Requests, Me.
- Branding is global and modern; nothing country-themed. Keep IFRS names ("Statement of Financial Position").
- Write for the person on the screen, in plain words: "Send to accounting", not "Submit"; errors say what happened and what to do.

## Screens

- Reports and working screens are **OWL client actions** with Excel and PDF export and Ethiopian-calendar periods (Meskerem … Pagume, fiscal year from 8 July). No wizard dialogs.
- Peachtree-like flow: navigation centres, a report centre, drawers; the user never needs the browser Back button.
- Show each person only the buttons of their own step (Submit / Check / Approve / Authorize). After someone signs and approves, their button disappears.
- Design is part of the work: plain or "ugly" screens are rejected. Check every screen in the browser, light and dark, before calling it done.

## Forms (approved 3 Nov 2026)

1. Top left = who (party, their reference, salesperson, terms); top right = when, number, status. Balanced columns.
2. Everything else in titled blocks (for example DELIVERY); nothing stacked alone on one side.
3. Lines show only what people enter or read (item, description, quantity, price, tax, amount); accounting columns are optional and hidden by default.
4. Each figure once: no duplicate totals or fields that repeat a smart button.
5. Conditional fields only when they apply (cheque fields only for cheques). Quantities without trailing zeros.

## Messages people receive

Telegram, email, the Me inbox, digests and reminders: greet by first name, say in one sentence what matters (how many, the oldest wait, the money), group by kind with totals, name documents by their real number, add a button. Never send a raw list.

## Approvals

- Nobody approves their own request: it goes one level up.
- The general manager approves payment requests; HR sets terms before the GM for staff loans; finance clears exits only when the staff debtor and loans are nil.
- Signed approvals: the signature replaces the password; a signature given once can be reused for the next step of the same document.

## Payroll (Ethiopia)

- Income tax: monthly brackets 0 / 15 / 20 / 25 / 30 / 35 % (Proclamation 979/2016 as amended). Payments on leaving are taxed month by month (Regulation 410).
- Pension: employee 7 %, employer 11 %, on the full basic; on the salary for the days worked in the month someone joins or leaves; none while on probation (pension exempted on the person).
- Allowances: transport and taxable allowances on days paid; house and position in full when any day is paid. Transport is tax-free up to 600 (commute) or ¼ of salary capped at 2,200 (field).
- Overtime: hourly rate = basic ÷ 240; 1.5 day, 1.75 night (22:00–06:00), 2 rest day, 2.5 public holiday. Only approved overtime is paid.
- Staff loans: no interest; all loans together at most one third of the monthly wage (Labour Proclamation 1156/2019, art. 59); repayable by payroll or by receipt (CRV).

## Code

- Each module is self-contained: a model file per concept, an OWL client action for the screen, `tests_manual/` scripts for `odoo shell`.
- New fields need a module update (`-u`); a release that only changes code and assets does not.
- After any upgrade, warm the asset bundles before the service starts (`odoo shell` script that requests the backend bundles), so the first user does not wait two minutes.
- Never put passwords, tokens or customer data in code, commits or messages.
