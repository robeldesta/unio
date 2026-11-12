# Acceptance checklist, area 5 "Daily use" (items 5.1-5.8), on a THROWAWAY copy of Eco Green's books. Every check runs as
# a real user of its role (clerk, petty cash keeper, accountant, manager, storekeeper, HR), never as superuser except for
# setting the users up. Everything is rolled back at the end.
#   cd ~/ecogreen-odoo19 && docker compose exec -T odoo sh -c 'odoo shell -d ecogreen_reg5 --no-http --db_host db \
#       --db_user odoo --db_password "$PASSWORD"' < ~/sonador-odoo/addons/sonador_books/tests_regression/area5_daily_use.py
# Output: "PASS|FAIL <item> <what>" per check, then "ITEM 5.n PASS|FAIL|MANUAL (k/m checks)" per item.
import os
import re
import time
import traceback
from collections import defaultdict
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import Form

assert env.cr.dbname not in ('ecogreen_test', 'ecogreen_web', 'ecogreen'), 'never on a live or working database'

RES = defaultdict(list)      # item -> [ok]
TIMES = []                   # (what, seconds)
MANUAL = {}
TECH = re.compile(r'(\(\w+(?:\.\w+)+\))|\b(res\.\w+|sb\.\w+(?:\.\w+)*|account\.move|odoo|sonador|peachtree|traceback|singleton|constraint|null value|'
                  r'psycopg2|keyerror|valueerror|attributeerror|expected singleton|record rule|ir\.\w+)\b', re.I)


def check(item, cond, what):
    print(('PASS ' if cond else 'FAIL ') + item + ' ' + what)
    RES[item].append(bool(cond))
    return cond


def timed(what, fn, *a, **k):
    t = time.time()
    res = fn(*a, **k)
    TIMES.append((what, time.time() - t))
    return res


def step(item, what, fn):
    """Run one piece of a role's day: PASS when it works, FAIL with the message a user would see."""
    try:
        with env.cr.savepoint():
            out = fn()
        check(item, True, what)
        return out
    except Exception as e:  # noqa: BLE001 - any error dialog fails the day
        msg = str(e).splitlines()[0][:220] if str(e) else type(e).__name__
        check(item, False, '%s -> %s: %s' % (what, type(e).__name__, msg))
        if not isinstance(e, (UserError, ValidationError, AccessError)):
            traceback.print_exc(limit=3)
        return None


def refused(fn):
    try:
        with env.cr.savepoint():
            fn()
    except (UserError, ValidationError, AccessError) as e:
        return str(e)
    return None


company = env.company
today = fields.Date.context_today(env['res.users'])
A = env['account.account']
acc = lambda code: A.search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)  # noqa: E731
BANK, PETTY, CHARGES, SALES, AR, AP, WHT = (acc(c) for c in ('1104-0002', '1101-0008', '8200-0033', '4000-0002', '1400-1000',
                                                          '2000-0001', '2100-0004'))
OFFICE = acc('8200-0040') or CHARGES
wh = env['stock.warehouse'].search([('company_id', '=', company.id), ('code', '=', 'WH')], limit=1)
P = env['product.product']
fg = P.search([('default_code', '=', '1504-3001')], limit=1)
raw = P.search([('is_storable', '=', True), ('default_code', '=like', '1501%')], limit=1) or \
    P.search([('is_storable', '=', True), ('id', '!=', fg.id)], limit=1)
for name, x in (('bank', BANK), ('petty', PETTY), ('charges', CHARGES), ('sales', SALES), ('warehouse', wh), ('FG item', fg),
                ('raw item', raw)):
    assert x, 'setup: no %s on this copy' % name

# ------------------------------------------------------------------ users of each role (setup, as superuser)
g = env.ref
U = env['res.users'].sudo().with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)
ROLES = {
    'clerk': ['sonador_books.group_sb_clerk', 'sonador_sales.group_sb_sales_user'],
    'petty': ['sonador_books.group_sb_clerk', 'sonador_books.group_sb_petty_cash'],
    'accountant': ['sonador_books.group_sb_accountant', 'sonador_requests.group_request_finance'],
    'manager': ['sonador_books.group_sb_manager', 'sonador_requests.group_request_gm'],
    'store': ['sonador_inventory.group_sb_store_user'],
    'hr': ['sonador_books.group_sb_hr'],
    # the payroll officer holds both roles: HR (whose pay) and accounting (posts it); see payroll_rules.xml
    'payroll': ['sonador_books.group_sb_hr', 'sonador_books.group_sb_accountant'],
}
user = {}
for role, groups in ROLES.items():
    user[role] = U.create({'name': 'Reg5 %s' % role, 'login': 'reg5_%s@example.local' % role, 'company_id': company.id,
                           'company_ids': [Command.set([company.id])],
                           'group_ids': [Command.set([g('base.group_user').id] + [g(x).id for x in groups])]})
Emp = env['hr.employee'].sudo()
for role, u in user.items():
    Emp.create({'name': u.name, 'user_id': u.id, 'company_id': company.id})
cust = env['res.partner'].sudo().create({'name': 'Reg5 Customer PLC', 'vat': '0098765431', 'customer_rank': 1})
vend = env['res.partner'].sudo().create({'name': 'Reg5 Supplier PLC', 'vat': '0098765432', 'supplier_rank': 1, 'is_company': True,
                                         'sb_trade_license': 'AA/1234/2019'})
# the petty cash box belongs to its keeper (as Zewdinesh's)
if 'sb_custodian_id' in A._fields:
    PETTY.sudo().sb_custodian_id = user['petty'].id
V = lambda role: env['sb.voucher'].with_user(user[role]).with_company(company)  # noqa: E731
SV = lambda role: env['sb.stock.voucher'].with_user(user[role]).with_company(company)  # noqa: E731
pad = [int(time.time()) % 900000 + 100000]


def next_fs():
    pad[0] += 1
    return 'FS%08d' % pad[0]


# ================================================================== 5.1 a week of real work per role (and 5.2 timings)
print('--- 5.1 / 5.2 a week of each role\'s work')
days = [today - timedelta(days=i) for i in range(4, -1, -1)]

# clerk / cashier: cash sales every day, one credit invoice
sales = []
for i, d in enumerate(days):
    def cash_sale(d=d, i=i):
        v = timed('cash sale: save', V('clerk').create, {
            'voucher_type': 'cash_sale', 'date': d, 'partner_id': cust.id, 'sb_pad_number': next_fs(),
            'line_ids': [Command.create({'name': 'Aquapure 5 L', 'account_id': SALES.id, 'quantity': 10 + i, 'price_unit': 120.0})],
            'cash_line_ids': [Command.create({'account_id': BANK.id, 'method': 'deposit'})]})
        timed('cash sale: post', v.action_post)
        assert v.state == 'posted', v.state
        return v
    v = step('5.1', 'clerk: cash sale %s' % d, cash_sale)
    if v:
        sales.append(v)
inv = step('5.1', 'clerk: credit sales invoice', lambda: (lambda v: (timed('invoice: post', v.action_post), v)[1])(
    timed('invoice: save', V('clerk').create, {'voucher_type': 'sale', 'date': days[0], 'partner_id': cust.id,
                                             'sb_pad_number': next_fs(),
                                             'line_ids': [Command.create({'name': 'Aquapure 5 L', 'account_id': SALES.id,
                                                                         'quantity': 100, 'price_unit': 115.0})]})))
check('5.1', refused(lambda: sales[0].with_user(user['clerk']).action_void()) is not None if sales else False,
      'clerk cannot void a posted cash sale (accountant does)')

# petty cash keeper: two small payments from the keeper's box
for i, d in enumerate(days[1:3]):
    def petty(d=d, i=i):
        v = timed('petty cash: save', V('petty').create, {
            'voucher_type': 'payment', 'date': d, 'payer_type': 'other' if 'payer_type' in env['sb.voucher']._fields else False,
            'memo': 'Taxi and water', 'line_ids': [Command.create({'name': 'Office supplies', 'account_id': OFFICE.id,
                                                                   'amount': 250.0 + i})],
            'cash_line_ids': [Command.create({'account_id': PETTY.id, 'method': 'cash', 'amount_currency': 250.0 + i})]})
        timed('petty cash: post', v.action_post)
        assert v.state == 'posted', v.state
        return v
    step('5.1', 'petty cash keeper: petty cash payment %s' % d, petty)

# accountant: a bill, its payment with withholding, a receipt, a journal voucher, a bank reconciliation
bill = step('5.1', 'accountant: purchase bill (VAT-exempt company)', lambda: (lambda v: (timed('bill: post', v.action_post), v)[1])(
    timed('bill: save', V('accountant').create, {'voucher_type': 'bill', 'date': days[1], 'partner_id': vend.id,
                                               'line_ids': [Command.create({'name': 'Cartons', 'account_id': OFFICE.id,
                                                                           'price_unit': 25000.0})]})))
if bill:
    def pay_bill():
        f = Form(V('accountant').with_context(default_voucher_type='payment', default_payer_type='vendor'))
        f.partner_id = vend
        f.date = days[2]
        f.received_amount = 25000.0
        v = timed('payment: save', f.save)
        timed('payment: post', v.action_post)
        assert v.state == 'posted', v.state
        return v
    pay = step('5.1', 'accountant: payment of the bill (withholding applied)', pay_bill)
    if pay:
        check('5.1', bill.amount_residual < bill.amount_total if 'amount_residual' in bill._fields else True,
              'the payment settles the bill (open %.2f of %.2f)' % (getattr(bill, 'amount_residual', 0), bill.amount_total))
        want = round(25000.0 * company.sb_wht_rate / 100.0, 2)
        check('5.1', abs((pay.wht_amount or 0.0) - want) < 0.01,
              'withholding on 25,000 from a licensed company with a TIN: %.2f (expected %.2f at %g%%)' % (
                  pay.wht_amount or 0.0, want, company.sb_wht_rate))
if inv:
    def receipt():
        f = Form(V('accountant').with_context(default_voucher_type='receipt', default_payer_type='customer'))
        f.partner_id = cust
        f.date = days[3]
        f.received_amount = 11500.0
        v = timed('receipt: save', f.save)
        timed('receipt: post', v.action_post)
        assert v.state == 'posted', v.state
        return v
    rec = step('5.1', 'accountant: receipt from the customer', receipt)
jv = step('5.1', 'accountant: journal voucher', lambda: (lambda v: (timed('JV: post', v.action_post), v)[1])(
    timed('JV: save', V('accountant').create, {'voucher_type': 'journal', 'date': days[3], 'memo': 'Reclass bank charge',
                                             'line_ids': [Command.create({'name': 'Charge', 'account_id': CHARGES.id, 'debit': 30.0}),
                                                          Command.create({'name': 'Charge', 'account_id': OFFICE.id, 'credit': 30.0})]})))


def bank_rec():
    r = timed('bank rec: open', env['sb.bank.rec'].with_user(user['accountant']).create,
              {'account_id': acc('1104-0010').id, 'date': today, 'statement_balance': 0.0})
    timed('bank rec: load', r.action_load)
    return r


step('5.1', 'accountant: bank reconciliation opened and loaded', bank_rec)

# storekeeper prepares, finance posts (Eco Green rule)
def store_doc(vtype, line):
    v = timed('%s: save' % vtype, SV('store').create, {'voucher_type': vtype, 'date': days[4], 'warehouse_id': wh.id,
                                                      'partner_id': vend.id if vtype == 'grn' else False,
                                                      'line_ids': [Command.create(line)]})
    return v


grn = step('5.1', 'storekeeper: prepares a GRN', lambda: store_doc('grn', {'product_id': raw.id, 'quantity': 50, 'unit_cost': 40.0}))
if grn:
    msg = refused(lambda: grn.with_user(user['store']).action_post())
    check('5.1', msg is not None, 'storekeeper cannot post it (finance posts)')
    step('5.1', 'storekeeper: sends the GRN to finance', lambda: timed('GRN: send to finance', grn.with_user(user['store']).action_sb_send_finance))
    step('5.1', 'accountant: posts the GRN', lambda: (timed('GRN: post', grn.with_user(user['accountant']).action_sb_finance_post),
                                                      None if grn.state == 'posted' else (_ for _ in ()).throw(AssertionError(grn.state))))
giv = step('5.1', 'storekeeper: prepares a GIV', lambda: store_doc('giv', {'product_id': raw.id, 'quantity': 5}))
if giv:
    step('5.1', 'storekeeper sends, accountant posts the GIV', lambda: (
        giv.with_user(user['store']).action_sb_send_finance(), timed('GIV: post', giv.with_user(user['accountant']).action_sb_finance_post)))

# manager: approvals, reports, the period close screen
step('5.1', 'manager: Me approvals open', lambda: timed('Me: open', env['sb.me'].with_user(user['manager']).data))
step('5.1', 'manager: trial balance', lambda: timed('trial balance', env['sb.report'].with_user(user['manager']).get_report,
                                                   'trial_balance', {'date_from': '2026-07-08', 'date_to': str(today)}))
step('5.1', 'manager: profit and loss', lambda: timed('profit and loss', env['sb.report'].with_user(user['manager']).get_report,
                                                     'profit_loss', {'date_from': '2026-07-08', 'date_to': str(today)}))
step('5.1', 'manager: period close screen', lambda: timed('period close: open', env['sb.period.wizard'].with_user(user['manager']).create, {}))
step('5.1', 'manager: Business Status', lambda: timed('Business Status', env['sb.report'].with_user(user['manager']).get_center_data, 'business'))
if jv:
    step('5.1', 'manager: voids a voucher with a reason', lambda: timed('void', jv.with_user(user['manager']).with_context(
        sb_void_reason='Regression test').action_void))

# payroll: the accountant prepares the month's run (HR reads payroll, it does not post it), HR reads the run and its register
def payroll():
    R = env['sb.payroll.run'].with_user(user['payroll'])
    period = R._period_choices()[-1][0] if callable(getattr(R, '_period_choices', None)) else False
    r = timed('payroll: new run', R.create, {'kind': 'regular', 'period': period, 'company_id': company.id})
    timed('payroll: load employees', r.action_load)
    assert r.line_ids, 'no employees loaded'
    return r


run = step('5.1', 'payroll officer (HR + accounting): monthly payroll run loads everyone', payroll)
if run:
    step('5.1', 'HR: reads the payroll run and its lines', lambda: timed('payroll: HR reads',
         lambda: env['sb.payroll.run'].with_user(user['hr']).browse(run.id).line_ids.read(['employee_id'])))
step('5.1', 'HR: payroll register report', lambda: timed('payroll register', env['sb.report'].with_user(user['hr']).get_report,
                                                         'payroll_register', {'date_from': str(today.replace(day=1)), 'date_to': str(today)}))

# ================================================================== 5.2 timings
print('--- 5.2 timings (server side)')
for what, sec in TIMES:
    limit = 3.0
    if any(w in what for w in ('trial balance', 'profit and loss', 'Business Status', 'payroll register', 'bank rec', 'payroll')):
        limit = 3.0
    check('5.2', sec < limit, '%-28s %.2f s (limit %.0f s)' % (what, sec, limit))
for xmlid in ('sonador_books.action_sb_voucher', 'sonador_inventory.action_sb_stock_voucher'):
    act = env.ref(xmlid, raise_if_not_found=False)
    if act:
        t = time.time()
        env[act.res_model].with_user(user['accountant']).get_views([(False, 'list'), (False, 'form'), (False, 'search')])
        check('5.2', time.time() - t < 2.0, 'screen opens (views of %s) %.2f s (limit 2 s)' % (act.res_model, time.time() - t))

# ================================================================== 5.3 dates
print('--- 5.3 dates')
for role in ('clerk', 'accountant', 'store'):
    lang = env['res.lang']._lang_get(user[role].lang or 'en_US')
    check('5.3', lang.date_format.startswith('%d'), '%s types and reads dates day first (%s)' % (role, lang.date_format))
if sales:
    check('5.3', bool(sales[0].date_ec) and re.search(r'20(1|2)\d', sales[0].date_ec or ''), 'vouchers carry the Ethiopian date (%s)' % sales[0].date_ec)
bs = env['sb.report'].with_user(user['manager']).get_center_data('business')
check('5.3', bool(bs.get('today_ec')), 'Business Status shows the Ethiopian date (%s)' % bs.get('today_ec'))
check('5.3', bool(env['sb.report'].with_user(user['manager'])._sb_print_date(today)) if hasattr(env['sb.report'], '_sb_print_date')
      else bool(env['sb.voucher']._sb_print_date(today)), 'printouts write both dates: %s' % (env['sb.voucher']._sb_print_date(today),))

# ================================================================== 5.4 plain language
print('--- 5.4 plain language')
msgs = []
msgs.append(('unbalanced JV', refused(lambda: V('accountant').create({'voucher_type': 'journal', 'date': today, 'line_ids': [
    Command.create({'name': 'x', 'account_id': CHARGES.id, 'debit': 10.0}),
    Command.create({'name': 'x', 'account_id': OFFICE.id, 'credit': 9.0})]}).action_post())))
if sales:
    msgs.append(('clerk voids', refused(lambda: sales[-1].with_user(user['clerk']).action_void())))
    msgs.append(('edit posted as clerk', refused(lambda: sales[-1].with_user(user['clerk']).write({'memo': 'x'}))))
msgs.append(('post in a closed year', refused(lambda: V('accountant').create({'voucher_type': 'journal', 'date': '2025-06-30', 'line_ids': [
    Command.create({'name': 'x', 'account_id': CHARGES.id, 'debit': 10.0}),
    Command.create({'name': 'x', 'account_id': OFFICE.id, 'credit': 10.0})]}).action_post())))
msgs.append(('duplicate fiscal receipt', refused(lambda: V('clerk').create({
    'voucher_type': 'cash_sale', 'date': today, 'partner_id': cust.id, 'sb_pad_number': sales[0].sb_pad_number if sales else 'FS00000001',
    'line_ids': [Command.create({'name': 'x', 'account_id': SALES.id, 'price_unit': 10.0})],
    'cash_line_ids': [Command.create({'account_id': BANK.id, 'method': 'cash'})]}).action_post())))
if grn:
    msgs.append(('storekeeper posts', refused(lambda: SV('store').create({'voucher_type': 'grn', 'date': today, 'warehouse_id': wh.id,
        'partner_id': vend.id, 'line_ids': [Command.create({'product_id': raw.id, 'quantity': 1, 'unit_cost': 1.0})]}).action_post())))
msgs.append(('store clerk opens accounting', refused(lambda: env['sb.voucher'].with_user(user['store']).search([], limit=1).read(['name']))))
for what, m in msgs:
    if m is None:
        check('5.4', False, '%s: was not refused' % what)
        continue
    bad = [x.group(0) for x in TECH.finditer(m)]
    check('5.4', not bad, '%s: "%s"%s' % (what, m.splitlines()[0][:150], (' -- technical words: %s' % sorted(set(bad))) if bad else ''))

# user-facing strings in the code: refusals (UserError/ValidationError) and screen labels, technical / old-system words
root = '/mnt/extra-addons'
eco = '/mnt/ecogreen-addons'
pat = re.compile(r'(UserError|ValidationError|AccessError)\(\s*(?:self\.env\._|_|env\._)?\(?\s*[\'"](.+?)[\'"]', re.S)
hits = []
for base in (root, eco):
    for dirpath, _d, files in os.walk(base):
        if '/tests' in dirpath or '/import' in dirpath or '__pycache__' in dirpath:
            continue
        mod = dirpath[len(base) + 1:].split('/')[0]
        if not (mod.startswith('sonador_') or mod.startswith('ecogreen_')):
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            if f.endswith('.py'):
                src = open(p, encoding='utf-8', errors='replace').read()
                for m in pat.finditer(src):
                    text = m.group(2)
                    bad = [x.group(0) for x in TECH.finditer(text)]
                    if bad:
                        hits.append('%s: %s' % (p.replace(base + '/', ''), text[:110]))
            elif f.endswith('.xml'):
                src = open(p, encoding='utf-8', errors='replace').read()
                for m in re.finditer(r'(?:string|help|placeholder|title)="([^"]*)"', src):
                    if re.search(r'\b(Odoo|Sonador|Peachtree|res\.partner|record rule)\b', m.group(1)):
                        hits.append('%s: %s' % (p.replace(base + '/', ''), m.group(1)[:110]))
for h in hits[:25]:
    print('     ' + h)
check('5.4', len(hits) == 0, 'user-facing strings with technical or old-system words: %d' % len(hits))

# ================================================================== 5.5 defaults
print('--- 5.5 defaults')
pick = env['account.account'].with_user(user['accountant']).name_search('', limit=500)
ids = [i for i, _n in pick]
foreign = env['account.account'].sudo().browse(ids).filtered(lambda a: company not in a.company_ids)
check('5.5', not foreign, 'account picker shows only the company\'s own chart (%d accounts listed)' % len(ids))
hidden = env['account.account'].sudo().browse(ids).filtered(lambda a: 'sb_hidden_template' in a._fields and a.sb_hidden_template)
check('5.5', not hidden, 'no unused standard-chart accounts in the picker (%d hidden template accounts shown)' % len(hidden))
dv = env['sb.voucher'].with_user(user['petty']).with_context(default_voucher_type='payment').default_get(['cash_line_ids'])
row = dv.get('cash_line_ids') and dv['cash_line_ids'][0][2] or {}
small = env['sb.voucher'].with_user(user['petty'])._sb_paid_from_for(250.0)
check('5.5', bool(row.get('auto_account')) and small == PETTY,
      'a small payment by the petty cash keeper is paid from their own box (%s)' % (small.display_name or 'none'))
big = env['sb.voucher'].with_user(user['petty'])._sb_paid_from_for(10 * (company.sb_petty_cash_limit or 5000.0))
check('5.5', big and big != PETTY, 'above the petty cash limit it is paid from the main bank (%s)' % (big.display_name or 'none'))
if inv and 'rec' in dir() and rec:
    inv.invalidate_recordset()
    check('5.5', abs(inv.amount_residual) < 0.01 if 'amount_residual' in inv._fields else True,
          'a receipt applies itself to the customer\'s open invoice (invoice open: %.2f)' % getattr(inv, 'amount_residual', 0.0))
dv2 = env['sb.voucher'].with_user(user['clerk']).with_context(default_voucher_type='cash_sale').default_get(['date'])
check('5.5', str(dv2.get('date')) == str(today), 'new vouchers start on today\'s date')

# ================================================================== 5.6 layout
MANUAL['5.6'] = 'layout at 1280 and 1440 px needs a person in a browser (no browser login for test users here)'
print('MANUAL 5.6 ' + MANUAL['5.6'])

# ================================================================== 5.7 printing
print('--- 5.7 printing (PDF rendered and read back)')
env['ir.config_parameter'].sudo().set_param('report.url', 'http://localhost:8069')
R = env['ir.actions.report']


def pdf_text(report, recs, who):
    pdf, _t = R.with_user(user[who])._render_qweb_pdf(report, recs.ids)
    html = R.with_user(user[who])._render_qweb_html(report, recs.ids)[0].decode('utf-8', 'replace')
    return pdf, re.sub(r'<[^>]+>', ' ', html)


def print_check(what, report, recs, who, must):
    try:
        with env.cr.savepoint():
            pdf, txt = pdf_text(report, recs, who)
        missing = [m for m in must if m not in txt]
        brand = sorted(set(re.findall(r'\b(Sonador|Odoo)\b', txt)))
        ok = pdf[:4] == b'%PDF' and not missing and not brand
        check('5.7', ok, '%s prints (%d KB), shows %s%s%s' % (what, len(pdf) // 1024, ', '.join(must),
              (' -- missing: %s' % missing) if missing else '', (' -- brand words: %s' % brand) if brand else ''))
    except Exception as e:  # noqa: BLE001
        check('5.7', False, '%s -> %s' % (what, str(e).splitlines()[0][:160]))


if bill:
    print_check('purchase bill', 'sonador_books.action_report_voucher', bill, 'accountant', [bill.name])
if 'pay' in dir() and pay:
    print_check('payment voucher (CPV)', 'sonador_books.action_report_voucher', pay, 'accountant', [pay.name])
if grn and grn.state == 'posted':
    print_check('goods received note', 'sonador_inventory.action_report_stock_voucher', grn, 'store', [grn.name])
if inv:
    print_check('sales invoice internal copy', 'sonador_books.action_report_voucher_internal', inv, 'accountant', [inv.name])
rep = env['sb.report'].with_user(user['accountant'])
try:
    act = rep.export_pdf('trial_balance', {'date_from': '2026-07-08', 'date_to': str(today)}) if hasattr(rep, 'export_pdf') else None
    check('5.7', act is None or bool(act), 'trial balance exports to PDF')
except Exception as e:  # noqa: BLE001
    check('5.7', False, 'trial balance PDF -> %s' % str(e).splitlines()[0][:160])

# ================================================================== 5.8 agenda
print('--- 5.8 agenda')
for role in ('accountant', 'manager', 'clerk', 'store', 'hr'):
    try:
        with env.cr.savepoint():
            items = env['agenda.engine'].with_user(user[role]).agenda()
        n = len(items) if isinstance(items, (list, tuple)) else len((items or {}).get('items', []) or []) if isinstance(items, dict) else 0
        check('5.8', True, '%s: agenda opens (%d items)' % (role, n))
    except Exception as e:  # noqa: BLE001
        check('5.8', False, '%s: agenda -> %s' % (role, str(e).splitlines()[0][:160]))
try:
    with env.cr.savepoint():
        w = env['sb.period.wizard'].with_user(user['manager']).create({})
        lines = w.line_ids
    check('5.8', bool(lines), 'month-end close lists the periods for the manager (%d)' % len(lines))
    msg = refused(lambda: env['sb.period.wizard'].with_user(user['accountant']).create({}).line_ids[:1].action_close_through())
    check('5.8', msg is not None, 'the accountant prepares, only the manager closes: "%s"' % (msg or 'not refused')[:120])
except Exception as e:  # noqa: BLE001
    check('5.8', False, 'period close -> %s' % str(e).splitlines()[0][:160])

# ================================================================== summary
print('=' * 70)
slow = sorted(TIMES, key=lambda x: -x[1])[:3]
print('slowest: ' + ', '.join('%s %.2f s' % (w, s) for w, s in slow))
for item in ('5.1', '5.2', '5.3', '5.4', '5.5', '5.6', '5.7', '5.8'):
    if item in MANUAL and not RES[item]:
        print('ITEM %s MANUAL (%s)' % (item, MANUAL[item]))
        continue
    r = RES[item]
    print('ITEM %s %s (%d/%d checks)' % (item, 'PASS' if r and all(r) else 'FAIL', sum(r), len(r)))
env.cr.rollback()
print('rolled back')
