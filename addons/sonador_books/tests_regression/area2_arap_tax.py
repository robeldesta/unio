# Acceptance checklist, area 2: receivables, payables and tax (items 2.1-2.10).
# Every check acts as a real user (clerk, accountant, manager) created here; the superuser only sets up. Rolled back at the end.
# Run on a copy of the books, never the live one:
#   cd ~/ecogreen-odoo19 && docker compose exec -T odoo sh -c 'odoo shell -d ecogreen_reg2 --no-http --db_host db \
#       --db_user odoo --db_password "$PASSWORD"' < ~/sonador-odoo/addons/sonador_books/tests_regression/area2_arap_tax.py
import time
import traceback
from collections import defaultdict
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError

assert env.cr.dbname not in ('ecogreen_test', 'ecogreen_web'), 'never on a live database'

results = defaultdict(list)   # item -> [bool]
item = None
started = {}


def section(no, title):
    global item
    if item:
        started[item] = time.time() - started[item]
    item = no
    started[no] = time.time()
    print('\n== %s %s' % (no, title))


def check(cond, msg):
    print('%s %s %s' % ('PASS' if cond else 'FAIL', item, msg))
    results[item].append(bool(cond))
    return bool(cond)


def refused(fn, msg, contains=None):
    try:
        with env.cr.savepoint():
            fn()
    except (AccessError, UserError, ValidationError) as e:
        env.invalidate_all()
        text = str(e).splitlines()[0]
        return check(contains is None or contains.lower() in str(e).lower(), '%s (refused: %s)' % (msg, text[:150]))
    except Exception as e:  # noqa: BLE001
        env.invalidate_all()
        return check(False, '%s (crashed instead of a clear refusal: %r)' % (msg, e))
    return check(False, '%s (was allowed)' % msg)


def works(fn, msg):
    try:
        with env.cr.savepoint():
            res = fn()
        check(True, msg)
        return res
    except Exception as e:  # noqa: BLE001
        env.invalidate_all()
        check(False, '%s (failed: %s)' % (msg, str(e).splitlines()[0][:200]))
        traceback.print_exc()
        return None


def near(a, b, tol=0.005):
    return abs((a or 0.0) - (b or 0.0)) <= tol


# ====================================================================== setup (superuser)
co = env.company
D = fields.Date.to_date
today = fields.Date.context_today(env['res.users'])
lock = co._get_user_fiscal_lock_date(env['account.journal'].search([('type', '=', 'general')], limit=1))
d0 = max(lock + timedelta(days=1), today.replace(day=1))           # this month, open
past = d0 - timedelta(days=1)                                       # end of last month
U = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True)
users = {}
for role in ('clerk', 'accountant', 'manager'):
    users[role] = U.create({'name': 'Reg2 %s' % role.title(), 'login': 'reg2_%s@unio.local' % role, 'company_id': co.id,
                            'company_ids': [Command.set([co.id])],
                            'group_ids': [Command.set([env.ref('base.group_user').id, env.ref('sonador_books.group_sb_%s' % role).id])]})
clerk, accountant, manager = users['clerk'], users['accountant'], users['manager']
A = env['account.account']


def acc(code):
    a = A.with_context(active_test=False).search([('code', '=', code), ('company_ids', 'in', co.id)], limit=1)
    if a and not a.active:
        a.active = True
    return a


bank = env['account.journal'].create({'name': 'Reg2 Test Bank', 'type': 'bank', 'code': 'R2TB'}).default_account_id
income = acc('4000-0001') or A.search([('account_type', '=', 'income'), ('company_ids', 'in', co.id)], limit=1)
expense = A.search([('account_type', '=', 'expense'), ('company_ids', 'in', co.id), ('active', '=', True)], limit=1)
P = env['res.partner']
cust = P.create({'name': 'Reg2 Customer A', 'customer_rank': 1, 'vat': '0011223344'})
cust_b = P.create({'name': 'Reg2 Customer B', 'customer_rank': 1, 'vat': '0011223355'})
wo = [P.create({'name': 'Reg2 Write-off Customer %d' % i, 'customer_rank': 1}) for i in range(6)]
vend = P.create({'name': 'Reg2 Supplier PLC', 'is_company': True, 'supplier_rank': 1, 'vat': '0099887711', 'sb_trade_license': 'AA/9/2020'})
both = P.create({'name': 'Reg2 Customer And Supplier', 'is_company': True, 'customer_rank': 1, 'supplier_rank': 1, 'vat': '0099887722',
                 'sb_trade_license': 'AA/8/2020'})
V = env['sb.voucher']
R = env['sb.report']
FS = iter(range(1, 999))


def fs():
    return 'FS%08d' % (99100000 + next(FS))


def as_(user):
    return V.with_user(user)


def invoice(amount, day, partner=cust, user=None, due_days=30, post=True):
    v = as_(user or accountant).create({
        'voucher_type': 'sale', 'partner_id': partner.id, 'sb_pad_number': fs(), 'date': day, 'due_date': day + timedelta(days=due_days),
        'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]})
    if post:
        v.action_post()
    return v


def bill(amount, day, partner=vend, user=None, taxes=None, post=True):
    v = as_(user or accountant).create({
        'voucher_type': 'bill', 'partner_id': partner.id, 'date': day, 'due_date': day + timedelta(days=30),
        'line_ids': [Command.create({'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': amount,
                                     'tax_ids': [Command.set(taxes.ids if taxes else [])]})]})
    if post:
        v.action_post()
    return v


def open_line(doc):
    return doc.move_id.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))


def settle(vtype, partner, day, applies=(), lines=(), received=0.0, user=None, post=True, supply='goods', extra=None):
    """A receipt / payment as the form builds it (onchange: withholding, caps). applies: (open line, amount[, discount])."""
    rows = []
    for a in applies:
        r = {'line_id': a[0].id, 'amount_applied': a[1], 'pay': True}
        if len(a) > 2:
            r['discount'] = a[2]
        rows.append(Command.create(r))
    vals = {'voucher_type': vtype, 'partner_id': partner.id, 'date': day, 'received_amount': received,
            'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})], 'apply_ids': rows,
            'line_ids': [Command.create({'account_id': a.id, 'name': a.name, 'amount': x, 'partner_id': partner.id}) for a, x in lines]}
    if vtype == 'payment':
        vals['wht_supply'] = supply
    vals.update(extra or {})
    v = as_(user or accountant).create(vals)
    v._onchange_sync_single_deposit()
    if post:
        v.action_post()
    return v


def gl(accounts, day, partner=None):
    dom = [('account_id', 'in', accounts.ids), ('parent_state', '=', 'posted'), ('date', '<=', day), ('company_id', '=', co.id)]
    if partner:
        dom.append(('partner_id', '=', partner.id))
    return sum(env['account.move.line'].search(dom).mapped('balance'))


def total_row(doc, key='total'):
    return next((r for r in doc['rows'] if r['id'] == 'total'), {'values': {}})['values'].get(key, 0.0)


def partner_row(doc, partner):
    return next((r for r in doc['rows'] if r['id'] == 'p%d' % partner.id), {'values': {}})['values']


Rep = R.with_user(accountant)
cust_accounts = A.search(['|', ('sb_partner_side', '=', 'customer'), ('id', 'in', co.sb_customer_advance_account_id.ids)])
vend_accounts = A.search(['|', ('sb_partner_side', '=', 'vendor'), ('id', 'in', co.sb_vendor_advance_account_id.ids)])

# a little activity of our own on top of Eco Green's books: invoices last month and this month, part paid, an advance
inv_old = invoice(20000, past - timedelta(days=60), due_days=15)
inv_mid = invoice(12000, past - timedelta(days=5))
inv_new = invoice(8000, d0)
settle('receipt', cust, d0, applies=[(open_line(inv_old), 5000)])
settle('receipt', cust, d0, received=3000)                                   # an advance, not applied
b_old = bill(40000, past - timedelta(days=40))
settle('payment', vend, d0, applies=[(open_line(b_old), 10000)], supply='goods')

# ====================================================================== 2.1 ageing = ledgers = control accounts
section('2.1', 'Aged AR/AP (due and invoice date, any past date) = partner ledgers = control accounts in the GL')
for day in (past - timedelta(days=30), past, today):
    for kind, accounts, sign in (('customer', cust_accounts, 1), ('vendor', vend_accounts, -1)):
        due = Rep.get_report('aged', {'partner_type': kind, 'date_to': str(day)})
        inv = Rep.get_report('aged', {'partner_type': kind, 'date_to': str(day), 'aged_by': 'invoice'})
        led = Rep.get_report('partner_ledger', {'partner_type': kind, 'date_from': str(day.replace(day=1)), 'date_to': str(day)})
        g = sign * gl(accounts, day)
        a1, a2, l1 = total_row(due), total_row(inv), total_row(led, 'balance')
        check(near(a1, g, 0.01) and near(a2, g, 0.01) and near(l1, g, 0.01),
              '%s at %s: aged by due %s = by invoice %s = ledger %s = GL %s' % (kind, day, '{:,.2f}'.format(a1), '{:,.2f}'.format(a2),
                                                                                '{:,.2f}'.format(l1), '{:,.2f}'.format(g)))
# per partner, and the buckets add up to the total
due = Rep.get_report('aged', {'partner_type': 'customer', 'date_to': str(today)})
row = partner_row(due, cust)
bucket_sum = sum(v for k, v in row.items() if k not in ('partner', 'total', 'fc'))
check(near(row.get('total'), 20000 - 5000 + 12000 + 8000 - 3000) and near(bucket_sum, row.get('total')),
      'our customer: total %s = 32,000 open less 3,000 advance, and the columns add to it' % row.get('total'))
check(near(row.get('total'), gl(cust_accounts, today, cust)), 'our customer: ageing = their GL balance')
past_row = partner_row(Rep.get_report('aged', {'partner_type': 'customer', 'date_to': str(past)}), cust)
check(near(past_row.get('total'), 32000), 'at the end of last month the customer owed 32,000 (this month\'s receipts not yet counted): %s'
      % past_row.get('total'))
inv_row = partner_row(Rep.get_report('aged', {'partner_type': 'customer', 'date_to': str(today), 'aged_by': 'invoice'}), cust)
check(near(inv_row.get('total'), row.get('total')) and inv_row != row,
      'aged by invoice date: same total, different bands (%s)' % {k: v for k, v in inv_row.items() if isinstance(v, float) and v})
vrow = partner_row(Rep.get_report('aged', {'partner_type': 'vendor', 'date_to': str(today)}), vend)
check(near(vrow.get('total'), 30000), 'our supplier: owed 30,000 (40,000 bill less 10,000 paid): %s' % vrow.get('total'))

# ====================================================================== 2.2 statements and advances = ageing
section('2.2', 'Statements and the advances report agree with the ageing')
for kind, partner in (('customer', cust), ('vendor', vend)):
    st = Rep.get_report('partner_statement', {'partner_type': kind, 'date_from': str(d0.replace(month=1) if d0.month > 1 else d0),
                                                'date_to': str(today), 'partner_ids': [partner.id]})
    grp = next((r for r in st['rows'] if r['id'] == 'p%d' % partner.id), {'values': {}})['values']
    ag = partner_row(Rep.get_report('aged', {'partner_type': kind, 'date_to': str(today)}), partner)
    check(near(grp.get('balance'), ag.get('total')), '%s statement balance %s = ageing %s' % (kind, grp.get('balance'), ag.get('total')))
    note = next((r for r in st['rows'] if r['id'] == 'agep%d' % partner.id), None)
    check(bool(note) and 'Ageing' in note['values'].get('name', ''), '%s statement carries the ageing line: %s' % (kind, note and note['values']['name']))
for kind in ('customer', 'vendor'):
    for day in (past, today):
        adv = Rep.get_report('open_credits', {'partner_type': kind, 'date_to': str(day)})
        ag = Rep.get_report('aged', {'partner_type': kind, 'date_to': str(day)})
        check(near(total_row(adv, 'open'), abs(total_row(ag, 'credit')), 0.01),
              '%s advances at %s: %s = ageing "Advances" column %s' % (kind, day, total_row(adv, 'open'), total_row(ag, 'credit')))
adv_rows = [r for r in Rep.get_report('open_credits', {'partner_type': 'customer', 'date_to': str(today)})['rows']
            if r['kind'] == 'line' and r['values']['partner'] == cust.name]
check(len(adv_rows) == 1 and near(adv_rows[0]['values']['open'], 3000), 'our customer\'s 3,000 advance is listed')

# ====================================================================== 2.3 one write-off rule
section('2.3', 'Every route that clears a balance without cash obeys one write-off rule')
limit = co.sb_discount_limit
inv_w = invoice(10000, d0, partner=wo[0])
refused(lambda: settle('receipt', wo[0], d0, applies=[(open_line(inv_w), 9500, 500)], user=clerk,
                       extra={'sb_discount_reason': 'Agreed with the customer by phone'}),
        'clerk: a 500 discount on a receipt')
refused(lambda: settle('receipt', wo[0], d0, applies=[(open_line(inv_w), 9500, 500)], user=accountant),
        'accountant: a 500 discount without a reason', 'reason')
r_ok = works(lambda: settle('receipt', wo[0], d0, applies=[(open_line(inv_w), 9500, 500)], user=accountant,
                            extra={'sb_discount_reason': 'Agreed with the customer by phone'}),
             'accountant: a 500 discount with a reason (limit %s)' % limit) or V
check(any('Discount / write-off' in (m.body or '') and accountant.name in (m.body or '') for m in r_ok.message_ids),
      'the write-off is logged on the voucher with who gave it and the reason')
inv_w2 = invoice(10000, d0, partner=wo[1])
refused(lambda: settle('receipt', wo[1], d0, applies=[(open_line(inv_w2), 10000 - limit - 500, limit + 500)], user=accountant,
                       extra={'sb_discount_reason': 'Agreed with the customer by phone'}),
        'accountant: a discount above the %s limit' % limit, 'Manager')
refused(lambda: settle('receipt', wo[1], d0, applies=[(open_line(inv_w2), 10000 - limit - 500, limit + 500)], user=manager),
        'manager: a discount without a reason', 'reason')
works(lambda: settle('receipt', wo[1], d0, applies=[(open_line(inv_w2), 10000 - limit - 500, limit + 500)], user=manager,
                     extra={'sb_discount_reason': 'Agreed with the customer by phone'}),
      'manager: the same discount above the limit, with a reason')
# negative GL line on a receipt (lowers what is banked: a write-off)
inv_n = invoice(5000, d0, partner=wo[2])
refused(lambda: settle('receipt', wo[2], d0, applies=[(open_line(inv_n), 5000)], lines=[(expense, -300)], user=clerk),
        'clerk: a negative GL line (-300) on a receipt')
works(lambda: settle('receipt', wo[2], d0, applies=[(open_line(inv_n), 5000)], lines=[(expense, -300)], user=accountant,
                     extra={'sb_discount_reason': 'Bank charges taken by the customer'}),
      'accountant: the same negative line with a reason')
# a journal voucher that lowers a customer's balance
recv = open_line(inv_n).account_id


def jv(user, amount, reason=None, who=None):
    v = as_(user).create({'voucher_type': 'journal', 'date': d0, 'memo': 'Write off', 'sb_discount_reason': reason or False,
                          'line_ids': [Command.create({'account_id': expense.id, 'name': 'Bad debt', 'debit': amount}),
                                       Command.create({'account_id': recv.id, 'partner_id': (who or wo[3]).id, 'name': 'Bad debt', 'credit': amount})]})
    v.action_post()
    return v


refused(lambda: jv(clerk, 200, 'Customer closed the shop'), 'clerk: a journal voucher lowering a customer by 200')
refused(lambda: jv(accountant, limit + 100, 'Customer closed the shop'), 'accountant: a journal write-off above the limit', 'Manager')
works(lambda: jv(accountant, 200, 'Customer closed the shop'), 'accountant: a journal write-off of 200 with a reason')
works(lambda: jv(manager, limit + 100, 'Customer closed the shop', wo[4]), 'manager: a journal write-off above the limit with a reason')
refused(lambda: jv(manager, 300, None, wo[5]), 'manager: a journal write-off without a reason', 'reason')
# a credit note against no document is the accountant's (and capped, 2.5)
refused(lambda: as_(clerk).create({'voucher_type': 'sale_return', 'partner_id': cust.id, 'date': d0, 'sb_pad_number': '98999',
                                   'line_ids': [Command.create({'account_id': income.id, 'name': 'Goodwill', 'quantity': 1, 'price_unit': 100,
                                                                'tax_ids': []})]}).action_post(),
        'clerk: a credit note against no document')
# the limit is not split: per item and per customer per day
inv_s = invoice(10000, d0, partner=cust_b)
settle('receipt', cust_b, d0, applies=[(open_line(inv_s), 5000 - limit * 0.6, limit * 0.6)], user=accountant,
       extra={'sb_discount_reason': 'Agreed with the customer by phone'})
refused(lambda: settle('receipt', cust_b, d0, applies=[(open_line(inv_s), 5000 - limit * 0.6, limit * 0.6)], user=accountant,
                       extra={'sb_discount_reason': 'Agreed with the customer by phone'}),
        'accountant: a second discount on the same invoice that takes it over the limit', 'already has')
# withholding above the rule on a receipt = a write-off
inv_wh = invoice(30000, d0)
refused(lambda: settle('receipt', cust, d0, applies=[(open_line(inv_wh), 30000)], user=clerk,
                       extra={'withholding': True, 'wht_amount': 3000, 'wht_certificate_no': 'R2-WH-9001'}),
        'clerk: a customer withholding of 3,000 on 30,000 (2%% rule = 600)')

# ====================================================================== 2.4 own partner's items; set-off
section('2.4', 'A receipt or payment applies only its own partner\'s items; set-off needs an accountant and is logged')
inv_b = invoice(4000, d0, partner=cust_b)
refused(lambda: settle('receipt', cust, d0, applies=[(open_line(inv_b), 4000)], user=accountant),
        'receipt for customer A applying customer B\'s invoice', 'belong to')
refused(lambda: settle('receipt', cust, d0, applies=[(open_line(inv_b), 4000)], user=manager),
        'even a manager cannot apply another customer\'s invoice', 'belong to')
b_v = bill(3000, d0)
refused(lambda: settle('payment', cust, d0, applies=[(open_line(b_v), 3000)], user=accountant),
        'payment to a customer applying a supplier\'s bill', 'belong to')
inv_both = invoice(6000, d0, partner=both)
bill_both = bill(2000, d0, partner=both)
refused(lambda: settle('receipt', both, d0, applies=[(open_line(inv_both), 6000), (open_line(bill_both), 2000)], user=accountant),
        'invoice and bill of the same partner on one receipt without "Set off"', 'set-off')
refused(lambda: settle('receipt', both, d0, applies=[(open_line(inv_both), 6000), (open_line(bill_both), 2000)], user=clerk,
                       extra={'sb_setoff': True}),
        'clerk ticks "Set off"')
so = works(lambda: settle('receipt', both, d0, applies=[(open_line(inv_both), 6000), (open_line(bill_both), 2000)], user=accountant,
                          extra={'sb_setoff': True}),
           'accountant sets off 6,000 receivable against 2,000 payable') or V
check(near(so.amount_cash, 4000), 'set-off receipt banks the 4,000 difference: %s' % so.amount_cash)
check(any('Set-off' in (m.body or '') and accountant.name in (m.body or '') for m in so.message_ids), 'the set-off is logged with who did it')
check(inv_both.move_id.payment_state in ('paid', 'in_payment') and bill_both.move_id.payment_state in ('paid', 'in_payment'),
      'both items settled')

# ====================================================================== 2.5 credit and debit notes
section('2.5', 'Credit and debit notes are capped at their origin (or recent invoicing), right accounts and numbering')
inv_c = invoice(10000, d0)


CN = iter(range(1, 999))


def cn(amount, origin=None, partner=cust, user=None, account=None, post=True):
    v = as_(user or accountant).create({'voucher_type': 'sale_return', 'partner_id': partner.id, 'date': d0,
                                        'sb_pad_number': str(99000 + next(CN)),
                                        'origin_id': origin.id if origin else False,
                                        'line_ids': [Command.create({'account_id': (account or income).id, 'name': 'Return', 'quantity': 1,
                                                                     'price_unit': amount, 'tax_ids': []})]})
    if post:
        v.action_post()
    return v


refused(lambda: cn(12000, inv_c), 'credit note of 12,000 against a 10,000 invoice', 'at most')
c1 = works(lambda: cn(6000, inv_c, user=clerk), 'clerk: credit note of 6,000 against the invoice') or V
refused(lambda: cn(5000, inv_c), 'a second credit note of 5,000 (6,000 already credited)', 'already credited')
refused(lambda: cn(1000, inv_c, partner=cust_b), 'credit note for customer B against customer A\'s invoice', 'not')
refused(lambda: cn(1000, inv_c, account=expense), 'credit note booked to an expense account')
check((c1.sb_pad_number or '').upper().startswith('CN'), 'credit note carries a credit-note register number: %s (%s)' % (c1.sb_pad_number, c1.name))
refused(lambda: cn(100, inv_c, post=True) if False else as_(accountant).create({
    'voucher_type': 'sale_return', 'partner_id': cust.id, 'date': d0, 'origin_id': inv_c.id, 'sb_pad_number': fs(),
    'line_ids': [Command.create({'account_id': income.id, 'name': 'Return', 'quantity': 1, 'price_unit': 100, 'tax_ids': []})]}).action_post(),
    'a credit note with a fiscal receipt (FS) number instead of a credit-note number')
inv_room = invoice(3000, d0, partner=cust_b)
invoiced, given = V._sb_note_room(co, cust_b, d0, 'customer')
refused(lambda: cn(invoiced - given + 1000, None, partner=cust_b), 'credit note against no document above 12 months\' invoicing less notes (%s)'
        % '{:,.2f}'.format(invoiced - given), 'at most')
works(lambda: cn(500, None, partner=cust_b), 'accountant: a 500 credit note against no document, within recent invoicing')
check(near(open_line(c1).balance, -6000) and open_line(c1).account_id == open_line(inv_c).account_id,
      'the credit note credits the invoice\'s receivable account (%s)' % open_line(c1).account_id.code)
b_d = bill(9000, d0)


def dn(amount, origin=None, account=None, post=True):
    v = as_(accountant).create({'voucher_type': 'purchase_return', 'partner_id': vend.id, 'date': d0, 'origin_id': origin.id if origin else False,
                                'line_ids': [Command.create({'account_id': (account or expense).id, 'name': 'Return', 'quantity': 1,
                                                             'price_unit': amount, 'tax_ids': []})]})
    if post:
        v.action_post()
    return v


refused(lambda: dn(9500, b_d), 'debit note of 9,500 against a 9,000 bill', 'at most')
d1 = dn(4000, b_d)
check(bool(d1.name) and d1.name.upper().startswith('DN'), 'debit note numbered in its series: %s' % d1.name)
refused(lambda: dn(1000, b_d, account=income), 'debit note booked to a sales account')

# ====================================================================== 2.6 withholding
section('2.6', 'Withholding: thresholds per bill, 30% without TIN, advances, refunds; unique receipt numbers; register = GL')
b30 = bill(30000, d0)
p1 = settle('payment', vend, d0, applies=[(open_line(b30), 15000)], user=accountant)
check(p1.withholding and near(p1.wht_amount, 450), 'first 15,000 of a 30,000 goods bill: 450 withheld (%s)' % p1.wht_amount)
p2 = settle('payment', vend, d0, applies=[(open_line(b30), 15000)], user=accountant)
check(p2.withholding and near(p2.wht_amount, 450) and b30.move_id.payment_state in ('paid', 'in_payment'),
      'second 15,000: 450 again (900 = 3% of 30,000); the bill is settled')
b15 = bill(15000, d0)
p3 = settle('payment', vend, d0, applies=[(open_line(b15), 15000)], user=accountant)
check(not p3.withholding, 'a 15,000 goods bill (below the threshold) is paid gross')
notin = P.create({'name': 'Reg2 Supplier Without TIN', 'is_company': True, 'supplier_rank': 1})
b_nt = bill(30000, d0, partner=notin)
p_nt = settle('payment', notin, d0, applies=[(open_line(b_nt), 30000)], user=accountant, post=False)
check(p_nt.withholding and near(p_nt.wht_amount, 9000) and 'Withheld at 30%' in (p_nt.sb_wht_warning or ''),
      'no TIN: 30%% = 9,000 withheld, with a warning (%s)' % p_nt.wht_amount)
vend_a = P.create({'name': 'Reg2 Supplier Advance PLC', 'is_company': True, 'supplier_rank': 1, 'vat': '0099887733', 'sb_trade_license': 'AA/7/2020'})
adv = settle('payment', vend_a, d0, received=50000, user=accountant)
check(adv.withholding and near(adv.wht_amount, 1500), 'goods advance of 50,000: 1,500 withheld as it is paid (%s)' % adv.wht_amount)
b_a = bill(50000, d0, partner=vend_a)
adv_line = adv.move_id.line_ids.filtered(lambda l: l.account_id == co.sb_vendor_advance_account_id)
p_a = settle('payment', vend_a, d0, applies=[(open_line(b_a), 50000), (adv_line, 50000)], user=accountant)
check(not p_a.withholding and near(p_a.wht_amount, 0) and near(p_a.amount_cash, 0),
      'the bill settled by the withheld advance: no second withholding, no cash')
# refund of an advance: the supplier gives the money back
vend_r = P.create({'name': 'Reg2 Supplier Refund PLC', 'is_company': True, 'supplier_rank': 1, 'vat': '0099887799', 'sb_trade_license': 'AA/6/2020'})
adv_r = settle('payment', vend_r, d0, received=40000, user=accountant)
adv_r_line = adv_r.move_id.line_ids.filtered(lambda l: l.account_id == co.sb_vendor_advance_account_id)
refund = works(lambda: settle('receipt', vend_r, d0, applies=[(adv_r_line, 40000)], user=accountant),
               'the supplier refunds the 40,000 advance on a receipt')
wht_acc = co.sb_wht_payable_account_id
left = -sum(env['account.move.line'].search([('account_id', '=', wht_acc.id), ('partner_id', '=', vend_r.id),
                                              ('parent_state', '=', 'posted')]).mapped('balance'))
check(near(left, 1200), 'the 1,200 withheld on the refunded advance stays owed to the tax office for this supplier (%s)' % left)
# unique receipt numbers
p1.write({'wht_certificate_no': 'R2-WHT-0777'})
refused(lambda: p2.with_user(accountant).write({'wht_certificate_no': 'r2 wht 777'}),
        'the same withholding receipt number (written differently) on a second payment', 'already entered')
check((p3.wht_certificate_no or '') == '' and (adv.wht_certificate_no or '').strip() != '', 'a withholding payment gets a receipt number (%s)'
      % adv.wht_certificate_no)
# register = GL for the month
reg = Rep.get_report('wht_register', {'date_from': str(d0), 'date_to': str(today), 'side': 'payable', 'mode': 'month'})
regtot = next(r for r in reg['rows'] if r['id'] == 'total')['values']['amount']
gl_wht = -sum(env['account.move.line'].search([('account_id', '=', wht_acc.id), ('parent_state', '=', 'posted'), ('date', '>=', d0),
                                                ('date', '<=', today), ('credit', '>', 0)]).mapped('balance'))
check(near(regtot, gl_wht, 0.01), 'withholding payable register %s = withholding credited in the GL this month %s' % (regtot, gl_wht))

# ====================================================================== 2.7 / 2.8 VAT return, registers, GL, filed months
section('2.7', 'VAT return and the sales / purchase registers agree with each other and the GL, including voids after filing')
co.sb_vat_registered = True
vat_s, vat_p = co.account_sale_tax_id, co.account_purchase_tax_id
out_acc = vat_s.invoice_repartition_line_ids.filtered(lambda r: r.repartition_type == 'tax').account_id
in_acc = vat_p.invoice_repartition_line_ids.filtered(lambda r: r.repartition_type == 'tax').account_id
last_m = past.replace(day=1)
sale_old = as_(accountant).create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': last_m + timedelta(days=3),
                                   'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1,
                                                                'price_unit': 10000, 'tax_ids': [Command.set(vat_s.ids)]})]})
sale_old.action_post()
sale_now = as_(accountant).create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': d0,
                                   'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1,
                                                                'price_unit': 4000, 'tax_ids': [Command.set(vat_s.ids)]})]})
sale_now.action_post()
bill_vat = bill(2000, last_m + timedelta(days=4), taxes=vat_p)


def vat_month(df, dt):
    ret = Rep.get_report('vat_return', {'date_from': str(df), 'date_to': str(dt)})
    s = next(r for r in ret['rows'] if r['id'] == 'sale')['values']
    p = next(r for r in ret['rows'] if r['id'] == 'purchase')['values']
    rs = total_row(Rep.get_report('vat_register', {'date_from': str(df), 'date_to': str(dt), 'side': 'sale'}), 'tax')
    rp = total_row(Rep.get_report('vat_register', {'date_from': str(df), 'date_to': str(dt), 'side': 'purchase'}), 'tax')
    dom = [('parent_state', '=', 'posted'), ('date', '>=', df), ('date', '<=', dt), ('company_id', '=', co.id)]
    gs = -sum(env['account.move.line'].search(dom + [('account_id', 'in', out_acc.ids)]).mapped('balance'))
    gp = sum(env['account.move.line'].search(dom + [('account_id', 'in', in_acc.ids)]).mapped('balance'))
    return s.get('tax', 0.0), rs, gs, p.get('tax', 0.0), rp, gp


def vat_ties(df, dt, label):
    s, rs, gs, p, rp, gp = vat_month(df, dt)
    check(near(s, rs, 0.01) and near(s, gs, 0.01), '%s output VAT: return %s = sales register %s = GL %s' % (label, s, rs, gs))
    check(near(p, rp, 0.01) and near(p, gp, 0.01), '%s input VAT: return %s = purchase register %s = GL %s' % (label, p, rp, gp))
    return s, p


s_last, p_last = vat_ties(last_m, past, 'last month')
check(near(s_last - vat_month(last_m, past)[2], 0) and s_last >= 1500 - 0.01, 'last month\'s output VAT includes our 1,500')
vat_ties(d0, today, 'this month')
for back in (2, 3):   # the months before, on Eco Green's own books
    m0 = (last_m - timedelta(days=28 * (back - 1))).replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    vat_ties(m0, m1, m0.strftime('%b %Y'))
# file last month, then void the sale: the void lands this month, last month's return does not move
co.tax_lock_date = past
before = vat_month(last_m, past)
works(lambda: sale_old.with_user(manager).with_context(sb_void_reason='Wrong customer, reissued').action_void(),
      'manager voids last month\'s VAT invoice after the month is filed')
rev = sale_old.move_id.reversal_move_ids[:1]
check(sale_old.state == 'void' and rev and rev.date > past, 'the void is posted in the first open date: %s' % (rev and rev.date))
check(near(vat_month(last_m, past)[0], before[0]), 'the filed month\'s output VAT is unchanged (%s)' % before[0])
s_now, *_ = vat_ties(d0, today, 'this month after the void')
check(near(s_now, 600 - 1500), 'this month\'s output VAT carries the reversal: 600 - 1,500 = %s' % s_now)

section('2.8', 'Filed VAT months cannot change; a void is posted in the first open date')
refused(lambda: as_(accountant).create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': last_m + timedelta(days=10),
                                        'line_ids': [Command.create({'account_id': income.id, 'name': 'Late', 'quantity': 1, 'price_unit': 100,
                                                                     'tax_ids': []})]}).action_post(),
        'post a sales invoice into the filed month (even without VAT)')
refused(lambda: bill(500, last_m + timedelta(days=10), taxes=vat_p), 'post a bill into the filed month')
refused(lambda: bill_vat.with_user(manager).action_edit(), 'manager edits a bill of the filed month')
refused(lambda: as_(manager).create({'voucher_type': 'journal', 'date': last_m + timedelta(days=10), 'memo': 'Move VAT',
                                     'line_ids': [Command.create({'account_id': out_acc.id, 'name': 'x', 'debit': 100}),
                                                  Command.create({'account_id': expense.id, 'name': 'x', 'credit': 100})]}).action_post(),
        'manager: a journal voucher on the VAT account in the filed month')
works(lambda: bill_vat.with_user(manager).with_context(sb_void_reason='Duplicate supplier invoice').action_void(),
      'manager voids a bill of the filed month')
brev = bill_vat.move_id.reversal_move_ids[:1]
check(bill_vat.state == 'void' and brev and brev.date > past, 'voiding a filed month\'s bill posts the reversal on %s' % (brev and brev.date))
check(near(vat_month(last_m, past)[3], before[3]), 'the filed month\'s input VAT is unchanged (%s)' % before[3])
refused(lambda: co.with_user(accountant).write({'tax_lock_date': False}), 'accountant re-opens the filed VAT month')
co.tax_lock_date = False
co.sb_vat_registered = False

# ====================================================================== 2.9 not registered for VAT
section('2.9', 'A company not registered for VAT cannot charge VAT; VAT on purchases goes to cost')
check(not co.sb_vat_registered, 'Eco Green is not registered for VAT (agricultural inputs)')
s_nv = as_(accountant).create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': d0,
                               'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1, 'price_unit': 1000})]})
check(not s_nv.line_ids.tax_ids, 'a sales line gets no VAT by default')
refused(lambda: as_(accountant).create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': d0,
                                        'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1,
                                                                     'price_unit': 1000, 'tax_ids': [Command.set(vat_s.ids)]})]}).action_post(),
        'a sales invoice with 15% VAT typed in', 'not registered')
refused(lambda: as_(manager).create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': d0,
                                     'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})],
                                     'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1,
                                                                  'price_unit': 1000, 'tax_ids': [Command.set(vat_s.ids)]})]}).action_post(),
        'manager: a cash sale with VAT', 'not registered')
b_nv = bill(1000, d0, taxes=vat_p)
lines = b_nv.move_id.line_ids
check(near(sum(lines.filtered(lambda l: l.account_id == expense).mapped('balance')), 1150) and not lines.filtered('tax_line_id'),
      'a bill with 15%% VAT: 1,150 to the expense (VAT in cost), nothing to VAT receivable (%s)' % lines.mapped(lambda l: (l.account_id.code, l.balance)))
check(any('not claimable' in (m.body or '') for m in b_nv.message_ids), 'the bill says its VAT went to cost')

# ====================================================================== 2.10 bank reconciliation
section('2.10', 'Bank reconciliation: cleared items are locked; a finished reconciliation reopens only by its button')
pay = settle('payment', vend, d0, lines=[(expense, 1000)], supply='service', user=accountant)
Rec = env['sb.bank.rec'].with_user(accountant)
rec = Rec.create({'account_id': bank.id, 'date': today})
rec.action_load()
rec.line_ids.write({'cleared': False})
rec.line_ids.filtered(lambda l: l.move_line_id.move_id == pay.move_id).write({'cleared': True})
rec.statement_balance = rec.cleared_balance
rec.action_finish()
check(rec.state == 'done', 'accountant finishes the reconciliation')
for who, u in (('accountant', accountant), ('manager', manager)):
    pv = pay.with_user(u)
    refused(lambda: pv.action_edit(), '%s: edit a cleared payment' % who, 'reconcil')
    refused(lambda: pv.with_context(sb_void_reason='test void').action_void(), '%s: void a cleared payment' % who, 'reconcil')
    refused(lambda: pv.move_id.button_draft(), '%s: reset the cleared entry to draft' % who)
    refused(lambda: rec.with_user(u).line_ids[:1].write({'cleared': False}), '%s: untick a cleared item' % who, 'reopen')
    refused(lambda: rec.with_user(u).write({'statement_balance': 1}), '%s: change the statement balance of the finished one' % who, 'reopen')
    refused(lambda: rec.with_user(u).write({'state': 'draft'}), '%s: write the state back to draft behind the screen' % who)
    refused(lambda: rec.with_user(u).unlink(), '%s: delete the finished reconciliation' % who)
refused(lambda: rec.with_user(clerk).action_reopen(), 'clerk: reopen the reconciliation')
works(lambda: rec.action_reopen(), 'accountant: Reopen button')
check(rec.state != 'done', 'reopened: %s' % rec.state)
works(lambda: (pay.with_user(accountant).action_edit(), pay.line_ids.write({'amount': 1200}), pay.action_post()),
      'after Reopen the payment can be changed')

# ====================================================================== summary
started[item] = time.time() - started[item]
print('\n== SUMMARY')
for no in sorted(results, key=lambda k: [int(x) for x in k.split('.')]):
    r = results[no]
    print('ITEM %s %s (%d/%d checks, %.1f s)' % (no, 'PASS' if all(r) else 'FAIL', sum(r), len(r), started.get(no, 0)))
print('AREA 2: %d of %d items pass, %d of %d checks' % (sum(all(r) for r in results.values()), len(results),
                                                      sum(sum(r) for r in results.values()), sum(len(r) for r in results.values())))
env.cr.rollback()
