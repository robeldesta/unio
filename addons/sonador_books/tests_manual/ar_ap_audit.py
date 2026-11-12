# Receivables, payables, cash / bank and tax: scenarios from the October 2026 audit, with the expected numbers.
# Rolled back at the end. Run: ~/ecogreen-odoo19/sync/local_shell.sh < addons/sonador_books/tests_manual/ar_ap_audit.py
from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError

V = env['sb.voucher']
P = env['res.partner']
R = env['sb.report']
co = env.company
D = fields.Date.from_string
def acc(c):
    # a standard-chart account the company never used is archived (hidden): the tests use it, so it comes back here
    a = env['account.account'].with_context(active_test=False).search([('code', '=', c), ('company_ids', 'in', co.id)], limit=1)
    if a and not a.active:
        a.active = True
    return a
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def near(a, b):
    return abs((a or 0.0) - b) < 0.005


def bal(move, account, partner=None):
    lines = move.line_ids.filtered(lambda l: l.account_id == account and (not partner or l.partner_id == partner))
    return sum(lines.mapped('balance'))


co.sb_vat_registered = True
bank = env['account.journal'].create({'name': 'Audit Test Bank', 'type': 'bank', 'code': 'ATB'}).default_account_id
expense = env['account.account'].search([('account_type', '=', 'expense'), ('company_ids', 'in', co.id)], limit=1)
income = acc('110000')
wht_pay = co.sb_wht_payable_account_id
vadv, cadv = co.sb_vendor_advance_account_id, co.sb_customer_advance_account_id
vend = P.create({'name': 'Audit Supplier PLC', 'is_company': True, 'vat': '0099887766', 'sb_trade_license': 'AA/1/2020'})
cust = P.create({'name': 'Audit Customer'})
mor = P.create({'name': 'Ministry of Revenue (audit test)'})


def bill(amount, day, vendor=vend):
    v = V.create({'voucher_type': 'bill', 'partner_id': vendor.id, 'date': D(day), 'line_ids': [Command.create(
        {'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]})
    v.action_post()
    return v


FS = iter(range(1, 100))


def fs():
    """Ethiopia: sales documents carry the fiscal receipt number."""
    return 'FS-AUDIT-%03d' % next(FS)


def invoice(amount, day):
    v = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': D(day), 'due_date': D(day) + __import__('datetime').timedelta(days=30),
                  'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]})
    v.action_post()
    return v


def payment(day, applies=(), lines=(), received=0.0, supply='goods', post=True, partner=vend):
    """applies: (open move line, amount); lines: (account, amount). Runs the form's onchange (withholding auto-tick, caps)."""
    v = V.create({'voucher_type': 'payment', 'partner_id': partner.id, 'date': D(day), 'wht_supply': supply,
                  'received_amount': received,
                  'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})],
                  'apply_ids': [Command.create({'line_id': l.id, 'amount_applied': a, 'pay': True}) for l, a in applies],
                  'line_ids': [Command.create({'account_id': a.id, 'name': a.name, 'amount': x, 'partner_id': partner.id})
                               for a, x in lines]})
    v._onchange_sync_single_deposit()
    if post:
        v.action_post()
    return v


def payable_line(b):
    return b.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')


# ---------------------------------------------------------------- 1. advance-only receipt / payment posts
r = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-04-02'), 'received_amount': 5000,
              'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})]})
r.action_post()
check(r.state == 'posted', '1. receipt of 5,000 with nothing applied posts')
check(near(bal(r.move_id, bank), 5000) and near(bal(r.move_id, cadv, cust), -5000), '1. Dr bank 5,000 / Cr customer advances 5,000')
p = payment('2027-04-02', received=7000, supply='service')
check(p.state == 'posted' and near(bal(p.move_id, vadv, vend), 7000) and near(bal(p.move_id, bank), -7000),
      '1. payment of 7,000 with nothing applied: Dr vendor advances 7,000 / Cr bank 7,000')
check(not p.withholding, '1. a 7,000 services advance (under the 10,000 threshold) is not withheld on')

# ---------------------------------------------------------------- 2. threshold per bill, not per payment
b30 = bill(30000, '2027-04-03')
p1 = payment('2027-04-04', applies=[(payable_line(b30), 15000)])
check(p1.withholding and near(p1.wht_base, 15000) and near(p1.wht_amount, 450), '2. first 15,000 of a 30,000 goods bill: withheld 450 on 15,000')
check(near(bal(p1.move_id, bank), -14550) and near(bal(p1.move_id, wht_pay), -450) and near(bal(p1.move_id, payable_line(b30).account_id, vend), 15000),
      '2. ledger: Dr payable 15,000 / Cr bank 14,550 / Cr withholding 450')
p2 = payment('2027-04-05', applies=[(payable_line(b30), 15000)])
check(p2.withholding and near(p2.wht_amount, 450), '2. second 15,000: withheld 450 again (900 = 3% of 30,000)')
check(b30.move_id.payment_state == 'paid', '2. the bill is settled: 14,550 + 450 twice')
b15 = bill(15000, '2027-04-05')
p3 = payment('2027-04-06', applies=[(payable_line(b15), 15000)])
check(not p3.withholding and near(bal(p3.move_id, bank), -15000), '2. a 15,000 goods bill (under 20,000) is paid gross')
p4 = payment('2027-04-06', lines=[(expense, 25000)])
check(p4.withholding and near(p4.wht_amount, 750), '2. a 25,000 goods payment with no bill is still tested on the payment: 750')

# ---------------------------------------------------------------- 3. withholding taken once on an advance
purpose = env['sb.voucher.purpose'].search([('name', '=', 'Vendor advance'), ('company_id', '=', co.id)], limit=1)
adv = payment('2027-04-07', lines=[(purpose.account_id, 50000)])
check(adv.withholding and near(adv.wht_amount, 1500) and near(bal(adv.move_id, bank), -48500) and near(bal(adv.move_id, vadv, vend), 50000),
      '3. vendor advance 50,000: withheld 1,500, Dr 225300 50,000 / Cr bank 48,500 / Cr withholding 1,500')
b50 = bill(50000, '2027-04-08')
adv_line = adv.move_id.line_ids.filtered(lambda l: l.account_id == vadv)
p5 = payment('2027-04-09', applies=[(payable_line(b50), 50000), (adv_line, 50000)])
check(not p5.withholding and near(p5.wht_amount, 0) and near(p5.amount_cash, 0), '3. bill 50,000 settled by the withheld advance: no second withholding, no cash')
check(p5.state == 'posted' and near(sum(env['account.move.line'].search([('account_id', '=', vadv.id), ('partner_id', '=', vend.id), ('parent_state', '=', 'posted')]).mapped('balance')), 7000),
      '3. 225300 for the supplier is back to the 7,000 unapplied advance of test 1 (no 1,500 left over)')
check(near(-sum(env['account.move.line'].search([('account_id', '=', wht_pay.id), ('partner_id', '=', vend.id), ('parent_state', '=', 'posted')]).mapped('balance')), 450 + 450 + 750 + 1500),
      '3. withholding for the supplier: 3,150 in all (1,500 taken once on the advance)')
adv2 = payment('2027-04-10', lines=[(purpose.account_id, 50000)])
b80 = bill(80000, '2027-04-10')
p6 = payment('2027-04-11', applies=[(payable_line(b80), 80000), (adv2.move_id.line_ids.filtered(lambda l: l.account_id == vadv), 50000)])
check(near(p6.wht_base, 30000) and near(p6.wht_amount, 900) and near(bal(p6.move_id, bank), -29100),
      '3. bill 80,000 less a withheld advance of 50,000: withheld 900 on 30,000, bank 29,100 (2,400 = 3% of 80,000 in all)')

# ---------------------------------------------------------------- 10. an advance bigger than the bill is capped
vend2 = P.create({'name': 'Audit Supplier Two PLC', 'is_company': True, 'vat': '0099887755', 'sb_trade_license': 'AA/2/2020'})
open_adv = payment('2027-04-12', received=60000, partner=vend2)
check(open_adv.withholding and near(open_adv.wht_amount, 1800) and near(bal(open_adv.move_id, vadv, vend2), 60000)
      and near(bal(open_adv.move_id, bank), -58200), '10. advance of 60,000 (goods) withheld as it is paid: 1,800, bank 58,200')
b50b = bill(50000, '2027-04-13', vend2)
p7 = payment('2027-04-14', partner=vend2, post=False, applies=[
    (payable_line(b50b), 50000), (open_adv.move_id.line_ids.filtered(lambda l: l.account_id == vadv), 60000)])
credit_row = p7.apply_ids.filtered(lambda a: a.line_id.account_id == vadv)
check(near(credit_row.amount_applied, 50000) and near(p7.wht_amount, 0) and near(p7.amount_cash, 0),
      '10. withheld advance 60,000 ticked against a 50,000 bill: applies 50,000, no second withholding, no cash')
p7.action_post()
check(p7.state == 'posted' and near(bal(p7.move_id, vadv, vend2), -50000) and near(bal(p7.move_id, wht_pay), 0),
      '10. posts: Dr payable 50,000 / Cr 225300 50,000; 10,000 advance left')
r2 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-04-14'), 'cash_line_ids': [Command.create({'account_id': bank.id})],
               'apply_ids': [Command.create({'line_id': invoice(3000, '2027-04-14').move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').id, 'amount_applied': 3000, 'pay': True}),
                             Command.create({'line_id': r.move_id.line_ids.filtered(lambda l: l.account_id == cadv).id, 'amount_applied': 5000, 'pay': True})]})
r2._onchange_sync_single_deposit()
check(near(r2.apply_ids.filtered(lambda a: a.line_id.account_id == cadv).amount_applied, 3000), '10. receipt: customer advance 5,000 ticked against a 3,000 invoice applies 3,000')

# ---------------------------------------------------------------- 5. withholding declaration and receipt
check((p1.wht_certificate_no or '').startswith('WHT/') and p1.wht_certificate_date == p1.date, '5. withholding receipt numbered on posting: %s' % p1.wht_certificate_no)
p1.write({'wht_certificate_no': 'PAD-0042'})
check(p1.wht_certificate_no == 'PAD-0042', '5. the receipt number can be filled after posting')
pdf = env['ir.actions.report']._render_qweb_pdf('sonador_books.action_report_wht_receipt', p1.ids)[0]
check(pdf[:4] == b'%PDF', '5. withholding receipt prints (%d bytes)' % len(pdf))
remit = V.create({'voucher_type': 'payment', 'partner_id': mor.id, 'date': D('2027-04-20'), 'cash_line_ids': [Command.create({'account_id': bank.id})],
                  'line_ids': [Command.create({'account_id': wht_pay.id, 'name': 'Withholding remitted', 'amount': 3000, 'partner_id': mor.id})]})
remit.action_post()
doc = R.get_report('wht_register', {'date_from': '2027-04-01', 'date_to': '2027-04-30', 'side': 'payable', 'mode': 'month'})
total = next(r for r in doc['rows'] if r['id'] == 'total')['values']
lines = [r for r in doc['rows'] if r['kind'] == 'line']
expected = 450 + 450 + 750 + 1500 + 1500 + 900 + 1800
check(near(total['amount'], expected), '5. declaration: tax withheld only, %s (remittance of 3,000 not netted): %s' % (expected, total['amount']))
check(not any(l['values']['number'] == remit.name for l in lines), '5. the remittance is not a declared line')
row1 = next(l for l in lines if l['values']['number'] == p1.name)['values']
check(near(row1['base'], 15000) and row1['vat'] == '0099887766' and row1['cert'] == 'PAD-0042', '5. line has base 15,000, supplier TIN and receipt number')

# ---------------------------------------------------------------- 6. VAT
co.sb_vat_registered = False
s1 = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': D('2027-04-15'), 'sb_pad_number': fs(),
               'line_ids': [Command.create({'account_id': income.id, 'name': 'No item', 'price_unit': 1000})]})
check(not s1.line_ids.tax_ids, '6a. not registered for VAT: a GL-account line gets no VAT')
co.sb_vat_registered = True
s2 = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': D('2027-04-15'), 'sb_pad_number': fs(),
               'line_ids': [Command.create({'account_id': income.id, 'name': 'No item', 'price_unit': 1000})]})
check(s2.line_ids.tax_ids == co.account_sale_tax_id, '6a. registered: the default %s' % co.account_sale_tax_id.name)
s2.action_post()  # 1,000 + 150 VAT
doc = R.get_report('vat_return', {'date_from': '2027-04-01', 'date_to': '2027-04-30'})
free = next((r for r in doc['rows'] if r['id'] == 'sale_exempt'), None)
sale = next(r for r in doc['rows'] if r['id'] == 'sale')['values']
# invoices without VAT this month: 3,000 (test 10) + 10,000 + 10,000 (tests 8, 9 below are in May) = 3,000
check(free and near(free['values']['base'], 3000), '6b. VAT return shows exempt sales (no VAT) of 3,000: %s' % (free and free['values']))
check(near(sale['base'], 4000) and near(sale['tax'], 150), '6b. output: total sales 4,000 (1,000 at 15%% + 3,000 without VAT), VAT 150: %s' % sale)

# ---------------------------------------------------------------- 4. finished bank reconciliation is protected
pay = payment('2027-04-21', lines=[(expense, 1000)], supply='service')
rec = env['sb.bank.rec'].create({'account_id': bank.id, 'date': D('2027-04-21')})
rec.action_load()
rec.line_ids.write({'cleared': False})
rec.line_ids.filtered(lambda l: l.move_line_id.move_id == pay.move_id).write({'cleared': True})
rec.statement_balance = rec.cleared_balance
rec.action_finish()
for what, fn in (('edit', pay.action_edit), ('void', pay.action_void),
                 ('change the opening balance', lambda: rec.write({'opening_balance': 500})),
                 ('untick an item', lambda: rec.line_ids[:1].write({'cleared': False})),
                 ('reset the entry to draft', pay.move_id.button_draft)):
    try:
        with env.cr.savepoint():
            fn()
        check(False, '4. %s is blocked on a finished reconciliation' % what)
    except UserError as e:
        check('Reopen' in str(e) or 'reopen' in str(e).lower(), '4. %s is blocked: %s' % (what, e))
rec.action_reopen()
pay.action_edit()
pay.line_ids.amount = 1500
pay.action_post()
check(near(bal(pay.move_id, bank), -1500), '4. after Reopen the payment can be changed to 1,500')

# ---------------------------------------------------------------- 8. bounced cheque reopens the invoice
inv = invoice(10000, '2027-05-02')
inv_line = inv.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
chq = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-05-10'),
                'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'reference': 'CHQ-77'})],
                'apply_ids': [Command.create({'line_id': inv_line.id, 'amount_applied': 10000, 'pay': True})]})
chq.action_post()
check(inv.move_id.payment_state in ('paid', 'in_payment'), '8. invoice paid by cheque')
due = inv_line.date_maturity
env['sb.cheque.wizard'].create({'row_id': chq.cash_line_ids.id, 'action': 'bounce', 'date': D('2027-05-20'),
                                'charges': 100, 'charges_to_customer': True}).action_confirm()
inv.invalidate_recordset()
check(near(inv.move_id.amount_residual, 10000) and inv.payment_state == 'not_paid', '8. after the bounce the invoice is open again for 10,000')
check(inv_line.date_maturity == due and inv_line.date == D('2027-05-02'), '8. with its own date and due date (aging not restarted)')
open_rec = env['account.move.line'].search([('partner_id', '=', cust.id), ('account_id.account_type', '=', 'asset_receivable'),
                                            ('parent_state', '=', 'posted'), ('reconciled', '=', False), ('date', '>=', D('2027-05-01'))])
check(near(sum(open_rec.mapped('amount_residual')), 10100), '8. customer owes 10,100 (invoice + 100 charges charged to them): %s' % sum(open_rec.mapped('amount_residual')))
jv = chq.cash_line_ids.settle_voucher_id
check(near(bal(jv.move_id, inv_line.account_id, cust), 10100) and near(bal(jv.move_id, co.sb_cheque_on_hand_account_id), -10000),
      '8. bounce entry: Dr %s (the account used) 10,000 + 100 charges / Cr cheques on hand 10,000' % inv_line.account_id.code)

# ---------------------------------------------------------------- 9. credit note + receipt = Paid
inv9 = invoice(10000, '2027-05-03')
cn = V.create({'voucher_type': 'sale_return', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': D('2027-05-04'), 'origin_id': inv9.id,
               'line_ids': [Command.create({'account_id': income.id, 'name': 'Return', 'quantity': 1, 'price_unit': 4000, 'tax_ids': []})]})
cn.action_post()
r9 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-05-05'), 'cash_line_ids': [Command.create({'account_id': bank.id})],
               'apply_ids': [Command.create({'line_id': inv9.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').id,
                                            'amount_applied': 6000, 'pay': True})]})
r9.action_post()
inv9.invalidate_recordset()
check(inv9.move_id.payment_state == 'paid' and inv9.payment_state == 'paid', '9. 4,000 credit note + 6,000 receipt: Paid (Odoo: %s)' % inv9.move_id.payment_state)
inv10 = invoice(5000, '2027-05-03')
cn10 = V.create({'voucher_type': 'sale_return', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': D('2027-05-04'), 'origin_id': inv10.id,
                 'line_ids': [Command.create({'account_id': income.id, 'name': 'Return', 'quantity': 1, 'price_unit': 5000, 'tax_ids': []})]})
cn10.action_post()
inv10.invalidate_recordset()
check(inv10.payment_state == 'reversed', '9. fully credited invoice still shows Reversed')

# ---------------------------------------------------------------- hands-on review: A. ticking Apply fills the amount
from odoo.tests import Form
inv_a = invoice(10000, '2027-05-06')
f = Form(V.with_context(default_voucher_type='receipt'), view='sonador_books.sb_voucher_form')
f.partner_id = cust
idx = next(i for i, r in enumerate(f.apply_ids._records) if r.get('line_id') in inv_a.move_id.line_ids.ids)
with f.apply_ids.edit(idx) as row:
    row.pay = True
    check(near(row.amount_applied, 10000), 'A. ticking Apply fills the open 10,000')
f = Form(V.with_context(default_voucher_type='receipt'), view='sonador_books.sb_voucher_form')
f.partner_id = cust
f.received_amount = 6000
with f.apply_ids.edit(idx) as row:
    row.pay = True
    check(near(row.amount_applied, 6000), 'A. with 6,000 received, ticking applies 6,000 (what is left of the cash): %s' % row.amount_applied)
ra = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-05-06'),
               'apply_ids': [Command.create({'line_id': inv_a.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').id, 'pay': True})]})
check(near(ra.apply_ids.amount_applied, 10000), 'A. a row saved ticked without an amount gets the open amount')

# ---------------------------------------------------------------- B. 30% warning
notin = P.create({'name': 'Audit Supplier Without TIN', 'is_company': True})
b_nt = bill(30000, '2027-05-07', notin)
pb = payment('2027-05-08', partner=notin, applies=[(payable_line(b_nt), 30000)], post=False)
check(pb.withholding and near(pb.wht_amount, 9000) and 'Withheld at 30%' in (pb.sb_wht_warning or ''),
      'B. no TIN / licence: 9,000 withheld (30%%) with a warning: %s' % pb.sb_wht_warning)
check(not p1.sb_wht_warning, 'B. a licensed company with a TIN: no warning')

# ---------------------------------------------------------------- C. default bank
dflt = V.with_context(default_voucher_type='receipt')._default_cash_account()
check(dflt == bank, 'C. a new receipt defaults to the bank this user used last (%s)' % dflt.display_name)
check(env['sb.voucher'].with_user(env.ref('base.user_admin')).with_context(default_voucher_type='payment')._default_cash_account().code != '211001',
      'C. never the template 211001 Bank')

# ---------------------------------------------------------------- D. bank reconciliation history
bank2 = env['account.journal'].create({'name': 'Audit Test Bank 2', 'type': 'bank', 'code': 'ATB2'}).default_account_id
for amount, day in ((100000, '2027-03-01'), (-20000, '2027-03-05')):
    h = V.create({'voucher_type': 'receipt' if amount > 0 else 'payment', 'partner_id': cust.id, 'date': D(day), 'cash_line_ids': [Command.create({'account_id': bank2.id})],
                  'line_ids': [Command.create({'account_id': expense.id, 'name': 'History', 'amount': abs(amount)})]})
    h.action_post()
new = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-04-02'), 'cash_line_ids': [Command.create({'account_id': bank2.id, 'reference': 'DEP-9'})],
                'line_ids': [Command.create({'account_id': expense.id, 'name': 'New', 'amount': 5000})]})
new.action_post()
r1 = env['sb.bank.rec'].create({'account_id': bank2.id, 'date': D('2027-04-30'), 'opening_balance': 80000, 'statement_balance': 85000,
                                'stmt_ids': [Command.create({'date': D('2027-04-02'), 'description': 'Deposit', 'reference': 'DEP-9', 'amount': 5000})]})
r1.action_load()
r1.action_match()
r1.action_finish()
later = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': D('2027-05-10'), 'cash_line_ids': [Command.create({'account_id': bank2.id})],
                  'line_ids': [Command.create({'account_id': expense.id, 'name': 'Later', 'amount': 1000})]})
later.action_post()
r2 = env['sb.bank.rec'].create({'account_id': bank2.id, 'date': D('2027-05-31'), 'statement_balance': 84000})
r2.action_load()
check(near(r2.opening_balance, 85000) and len(r2.line_ids) == 1, 'D. next reconciliation opens at 85,000 with only the new item, not the history (%d items)' % len(r2.line_ids))
r2.tick_until = D('2027-05-31')
r2.action_tick_until()
check(near(r2.difference, 0), 'D. "tick everything up to" ticks it: difference 0')

# ---------------------------------------------------------------- E. brought-over invoice paid by a receipt
mig = V.search([('sb_migrated', '=', True), ('voucher_type', '=', 'sale'), ('state', '=', 'posted'), ('payment_state', '=', 'not_paid')], limit=1)
if mig:
    mlines = mig.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
    re_ = V.create({'voucher_type': 'receipt', 'partner_id': mig.partner_id.id, 'date': D('2027-05-12'), 'cash_line_ids': [Command.create({'account_id': bank.id})],
                    'apply_ids': [Command.create({'line_id': l.id, 'amount_applied': l.amount_residual, 'pay': True}) for l in mlines]})
    re_.action_post()
    mig.invalidate_recordset()
    check(mig.payment_state == 'paid' and near(mig.amount_residual, 0), 'E. %s (brought over) shows Paid after a receipt' % mig.name)
    check(near(re_.amount_cash, sum(mlines.mapped('balance'))), 'E. the posted receipt shows the amount received (%s)' % re_.amount_cash)

# ---------------------------------------------------------------- R1. bank reconciliation opening balance cannot be forged
bank3 = env['account.journal'].create({'name': 'Audit Test Bank 3', 'type': 'bank', 'code': 'ATB3'}).default_account_id
h3 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-03-01'), 'cash_line_ids': [Command.create({'account_id': bank3.id})],
               'line_ids': [Command.create({'account_id': expense.id, 'name': 'History', 'amount': 100000})]})
h3.action_post()
rr1 = env['sb.bank.rec'].create({'account_id': bank3.id, 'date': D('2027-03-31'), 'opening_balance': 5})
check(near(rr1.opening_balance, 0), 'R1. first reconciliation without a statement opens at nil, not the 5.00 typed: %s' % rr1.opening_balance)
rr1.action_load()
rr1.action_clear_all()
rr1.statement_balance = 100000
rr1.action_finish()
rr2 = env['sb.bank.rec'].create({'account_id': bank3.id, 'date': D('2027-04-30'), 'opening_balance': 5})
check(near(rr2.opening_balance, 100000), 'R1. next reconciliation opens at 100,000 (the last ending balance), not 5.00: %s' % rr2.opening_balance)
try:
    with env.cr.savepoint():
        rr2.write({'opening_balance': 5})
    check(False, 'R1. typing the opening balance is refused')
except UserError as e:
    check(True, 'R1. typing the opening balance is refused: %s' % e)
rr2.with_context(sb_rec_opening=True).write({'opening_balance': 5})   # forged behind the screen
rr2.statement_balance = 5
try:
    with env.cr.savepoint():
        rr2.action_finish()
    check(False, 'R1. finishing with a forged opening balance is refused')
except UserError as e:
    check('100,000.00' in str(e), 'R1. finishing with a forged opening balance is refused: %s' % e)

# ---------------------------------------------------------------- R2. entries back-dated into a finished reconciliation
late = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': D('2027-03-15'), 'cash_line_ids': [Command.create({'account_id': bank3.id})],
                 'line_ids': [Command.create({'account_id': expense.id, 'name': 'Back-dated', 'amount': 2000})]})
late.action_post()
rr1.invalidate_recordset()
check(any('falls inside the bank reconciliation' in (m.body or '') for m in rr1.message_ids)
      and any('falls inside the bank reconciliation' in (m.body or '') for m in late.message_ids),
      'R2. posting into the finished reconciliation of 31 Mar warns on the voucher and on the reconciliation')
check(near(rr1.gl_balance, 98000) and near(rr1.reconciled_balance, 100000) and near(rr1.late_amount, -2000) and near(rr1.unexplained, 0),
      'R2. 31 Mar proof: books 98,000 = reconciled 100,000 - 2,000 posted after reconciling; nothing unexplained (%s)' % rr1.unexplained)
rr2.action_load()
check(near(rr2.opening_balance, 100000) and late.move_id.line_ids.filtered(lambda l: l.account_id == bank3) in rr2.line_ids.move_line_id,
      'R2. Load puts the opening back to 100,000 and carries the back-dated payment into 30 Apr as outstanding')
rr2.statement_balance = 100000
rr2.action_finish()
check(rr2.state == 'done' and near(rr2.outstanding_out, 2000) and near(rr2.gl_balance, 98000) and near(rr2.unexplained, 0)
      and near(rr2.reconciled_balance, rr2.gl_balance), 'R2. 30 Apr proves: 100,000 - 2,000 outstanding = 98,000 per books')
check(near(rr1.unexplained, 0), 'R2. 31 Mar still proves after the item is carried forward')

# ---------------------------------------------------------------- R3. cancelled issued cheque reopens the bill
b12 = bill(12000, '2027-05-01')
b12_line = payable_line(b12)
chq_out = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': D('2027-05-05'), 'wht_supply': 'goods',
                    'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'reference': 'OUT-55'})],
                    'apply_ids': [Command.create({'line_id': b12_line.id, 'amount_applied': 12000, 'pay': True})]})
chq_out._onchange_sync_single_deposit()
chq_out.action_post()
b12.invalidate_recordset()
check(b12.move_id.payment_state in ('paid', 'in_payment'), 'R3. bill of 12,000 paid by cheque OUT-55')
due12 = b12_line.date_maturity
env['sb.cheque.wizard'].create({'row_id': chq_out.cash_line_ids.id, 'action': 'bounce', 'date': D('2027-05-12')}).action_confirm()
b12.invalidate_recordset()
check(near(b12.move_id.amount_residual, 12000) and b12.payment_state == 'not_paid', 'R3. after the cheque is cancelled the bill is open again for 12,000 (%s)' % b12.payment_state)
check(b12_line.date == D('2027-05-01') and b12_line.date_maturity == due12, 'R3. with its own date and due date')
jv_c = chq_out.cash_line_ids.settle_voucher_id
check(near(bal(jv_c.move_id, co.sb_cheque_outstanding_account_id), 12000) and near(bal(jv_c.move_id, b12_line.account_id, vend), -12000),
      'R3. cancellation: Dr outstanding cheques 12,000 / Cr %s 12,000, matched back to the payment' % b12_line.account_id.code)
open_ap = env['account.move.line'].search([('partner_id', '=', vend.id), ('account_id', '=', b12_line.account_id.id), ('parent_state', '=', 'posted'),
                                           ('reconciled', '=', False), ('date', '>=', D('2027-05-01'))])
check(open_ap == b12_line, 'R3. the only open item is the bill (no stray credit on the payable)')

# ---------------------------------------------------------------- R4. withholding on an advance-only payment
vend3 = P.create({'name': 'Audit Supplier Three PLC', 'is_company': True, 'vat': '0099887744', 'sb_trade_license': 'AA/3/2020'})
a50 = payment('2027-05-02', received=50000, partner=vend3, post=False)
check(a50.withholding and near(a50.wht_base, 50000) and near(a50.wht_amount, 1500) and near(a50.amount_cash, 48500),
      'R4. goods advance of 50,000 to a licensed company: withholding ticks, base 50,000, 1,500, bank 48,500 (%s / %s)' % (a50.wht_base, a50.amount_cash))
a50.action_post()
check(near(bal(a50.move_id, vadv, vend3), 50000) and near(bal(a50.move_id, bank), -48500) and near(bal(a50.move_id, wht_pay), -1500),
      'R4. ledger: Dr 225300 50,000 / Cr bank 48,500 / Cr withholding 1,500')
a15 = payment('2027-05-02', received=15000, partner=vend3)
check(not a15.withholding and near(bal(a15.move_id, vadv, vend3), 15000), 'R4. a 15,000 goods advance (under 20,000) is not withheld')
b50c = bill(50000, '2027-05-03', vend3)
p50 = payment('2027-05-04', partner=vend3, applies=[(payable_line(b50c), 50000), (a50.move_id.line_ids.filtered(lambda l: l.account_id == vadv), 50000)])
check(not p50.withholding and near(p50.amount_cash, 0) and p50.state == 'posted', 'R4. the bill settled by the withheld advance: not withheld again, no cash')

# ---------------------------------------------------------------- R6. withholding left out over the threshold needs a reason
b30x = bill(30000, '2027-05-05', vend3)
px = payment('2027-05-06', partner=vend3, applies=[(payable_line(b30x), 30000)], post=False)
check(px.withholding and near(px.wht_amount, 900), 'R6. 30,000 goods bill: withholding 900 ticked')
px.withholding = False   # unticked by hand (the form re-ticks it until a reason is given)
px.cash_line_ids.amount_currency = 30000
try:
    with env.cr.savepoint():
        px.action_post()
    check(False, 'R6. unticked without a reason is refused')
except ValidationError as e:
    check('900.00' in str(e), 'R6. unticked without a reason is refused: %s' % e)
px.write({'withholding': True})
px.wht_amount = 300
px.cash_line_ids.amount_currency = 29700
try:
    with env.cr.savepoint():
        px.action_post()
    check(False, 'R6. withholding overwritten to 300 without a reason is refused')
except ValidationError:
    check(True, 'R6. withholding overwritten to 300 without a reason is refused')
px.write({'withholding': False, 'wht_exempt_reason': 'Exemption certificate EX-77'})
px._onchange_sync_single_deposit()
px.action_post()
check(px.state == 'posted' and not px.withholding and near(px.wht_due, 900) and near(bal(px.move_id, bank), -30000),
      'R6. with the exemption certificate it posts gross 30,000; 900 due is recorded')
doc = R.get_report('wht_register', {'date_from': '2027-05-01', 'date_to': '2027-05-31', 'side': 'payable', 'mode': 'month'})
exc = [r for r in doc['rows'] if r.get('parent') == 'exc']
check(len(exc) == 1 and exc[0]['values']['number'] == px.name and 'EX-77' in exc[0]['values']['note'] and near(exc[0]['values']['amount'], 0),
      'R6. listed under "Not withheld, or withheld less": %s' % (exc and exc[0]['values']['note']))

# ---------------------------------------------------------------- R5. brought-over withholding: base derived, filled after posting
indiv = P.create({'name': 'Audit Individual Supplier'})   # no TIN: 30% by rule, but the entry was withheld at 3%
mig1 = V.create({'voucher_type': 'journal', 'date': D('2027-06-02'), 'partner_id': indiv.id, 'line_ids': [
    Command.create({'account_id': expense.id, 'name': 'Brought over', 'debit': 20619.0, 'partner_id': indiv.id}),
    Command.create({'account_id': bank.id, 'name': 'Brought over', 'credit': 20000.43, 'partner_id': indiv.id}),
    Command.create({'account_id': wht_pay.id, 'name': 'Withholding', 'credit': 618.57, 'partner_id': indiv.id})]})
mig1.action_post()
mig2 = V.create({'voucher_type': 'journal', 'date': D('2027-06-03'), 'partner_id': vend3.id, 'line_ids': [
    Command.create({'account_id': expense.id, 'name': 'Brought over, with VAT', 'debit': 347845.33, 'partner_id': vend3.id}),
    Command.create({'account_id': bank.id, 'name': 'Brought over', 'credit': 338771.10, 'partner_id': vend3.id}),
    Command.create({'account_id': wht_pay.id, 'name': 'Withholding', 'credit': 9074.23, 'partner_id': vend3.id})]})
mig2.action_post()
doc = R.get_report('wht_register', {'date_from': '2027-06-01', 'date_to': '2027-06-30', 'side': 'payable', 'mode': 'month'})
rows_ = {r['values']['number']: r for r in doc['rows'] if r['kind'] == 'line'}
r1v, r2v = rows_[mig1.name]['values'], rows_[mig2.name]['values']
check(near(r1v['base'], 20619) and near(r1v['rate'], 3) and 'Derived' in r1v['note'],
      'R5. base derived 20,619 at 3%% (the rate that fits the payment, not 30%%): %s %s' % (r1v['base'], r1v['note']))
check(near(r2v['base'], 302474.33) and 'Derived' in r2v['note'], 'R5. VAT-inclusive payment: base before VAT 302,474.33: %s' % r2v['base'])
check(rows_[mig1.name]['open']['model'] == 'account.move', 'R5. the line opens the entry, where the base and receipt no. are filled')
mig1.move_id.write({'sb_wht_base': 20619.0, 'sb_wht_receipt_no': 'R-18-0001', 'sb_wht_receipt_date': D('2027-06-02')})
doc = R.get_report('wht_register', {'date_from': '2027-06-01', 'date_to': '2027-06-30', 'side': 'payable', 'mode': 'month'})
r1v = next(r for r in doc['rows'] if r['kind'] == 'line' and r['values']['number'] == mig1.name)['values']
check(r1v['cert'] == 'R-18-0001' and near(r1v['base'], 20619) and 'entered' in r1v['note'], 'R5. base and receipt no. filled after posting: %s' % r1v)
check(any(r['id'] == 'no_tin' for r in doc['rows']), 'R5. lines without a TIN are flagged')

# ---------------------------------------------------------------- R7. VAT return: zero-rated and exempt apart; not registered
Tax = env['account.tax']
zero = Tax.search([('company_id', '=', co.id), ('type_tax_use', '=', 'sale'), ('amount', '=', 0), ('name', '=', '0%')], limit=1)
exempt = Tax.search([('company_id', '=', co.id), ('type_tax_use', '=', 'sale'), ('amount', '=', 0), ('name', 'ilike', 'exempt')], limit=1)
for t, amount in ((zero, 2000), (exempt, 500), (Tax, 3000)):
    sv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'sb_pad_number': fs(), 'date': D('2027-06-10'),
                   'line_ids': [Command.create({'account_id': income.id, 'name': 'Sale', 'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.set(t.ids)]})]})
    sv.action_post()
bv = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': D('2027-06-10'), 'line_ids': [Command.create(
    {'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': 10000, 'tax_ids': [Command.set(co.account_purchase_tax_id.ids)]})]})
bv.action_post()
doc = R.get_report('vat_return', {'date_from': '2027-06-01', 'date_to': '2027-06-30'})
rv = {r['id']: r for r in doc['rows']}
check(near(rv['sale_zero']['values']['base'], 2000) and near(rv['sale_exempt']['values']['base'], 3500)
      and not any(r['label'] in ('0%', zero.name, exempt.name) for r in doc['rows']),
      'R7. zero-rated 2,000 and exempt 3,500 (500 exempt tax + 3,000 no tax) on separate lines: %s / %s' % (rv['sale_zero']['values'], rv['sale_exempt']['values']))
check(near(rv['purchase']['values']['tax'], 1500) and near(rv['payable']['values']['tax'], -1500), 'R7. registered: input VAT 1,500 is a credit')
co.sb_vat_registered = False
doc = R.get_report('vat_return', {'date_from': '2027-06-01', 'date_to': '2027-06-30'})
rv = {r['id']: r for r in doc['rows']}
check(near(rv['purchase']['values']['tax'], 0) and near(rv['purchase']['values'].get('cost', 0), 1500) and near(rv['payable']['values']['tax'], 0)
      and 'not_registered' in rv, 'R7. not registered: the 1,500 input VAT is cost, not a credit, with a note: %s' % rv.get('not_registered', {}).get('label'))
co.sb_vat_registered = True

# ---------------------------------------------------------------- R8. reconciliation: one draft per account, rounded proof, issued cheques
again = env['sb.bank.rec'].create({'account_id': bank3.id, 'date': D('2027-05-31')})
again2 = env['sb.bank.rec'].create({'account_id': bank3.id, 'date': D('2027-05-31'), 'statement_balance': 1})
check(again == again2 and env['sb.bank.rec'].search_count([('account_id', '=', bank3.id), ('state', '=', 'draft')]) == 1,
      'R8. saving a new reconciliation again for the same account and date carries on with the one started (no extra drafts)')
try:
    with env.cr.savepoint():
        env['sb.bank.rec'].create({'account_id': bank3.id, 'date': D('2027-06-30')})
    check(False, 'R8. a second reconciliation in progress for the account is refused')
except UserError as e:
    check(True, 'R8. a second reconciliation in progress for the account is refused: %s' % e)
chq3 = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': D('2027-05-10'),
                 'cash_line_ids': [Command.create({'account_id': bank3.id, 'method': 'cheque', 'reference': 'CHQ-3001'})],
                 'line_ids': [Command.create({'account_id': expense.id, 'name': 'Paid by cheque', 'amount': 4000})]})
chq3._onchange_sync_single_deposit()
chq3.action_post()
again.action_load()
item = again.line_ids.filtered(lambda l: l.cheque_row_id == chq3.cash_line_ids)
check(len(item) == 1 and near(item.amount, -4000) and near(again.outstanding_out, 4000 + 2000),
      'R8. the issued cheque CHQ-3001 (in outstanding cheques, not the bank) is listed as outstanding: 4,000 (+ 2,000 carried)')
check(near(again.gl_balance, 98000 - 4000), 'R8. balance per books counts the cheque as paid out: 94,000 (%s)' % again.gl_balance)
item.cleared = True
again.statement_balance = 96000
again.invalidate_recordset()
check(near(again.difference, 0) and near(again.unexplained, 0), 'R8. ticked as presented: difference 0, proof 0')
again.action_finish()
row3 = chq3.cash_line_ids
check(again.state == 'done' and row3.cheque_state == 'cleared' and near(bal(row3.settle_voucher_id.move_id, bank3), -4000)
      and row3.settle_voucher_id.move_id.line_ids.filtered(lambda l: l.account_id == bank3).sb_rec_id == again,
      'R8. Finish clears the cheque (outstanding cheques -> bank 4,000) and the bank line is ticked in this reconciliation')
check(near(again.gl_balance, 94000) and near(again.unexplained, 0), 'R8. still proves after clearing: books 94,000')
bank4 = env['account.journal'].create({'name': 'Audit Test Bank 4', 'type': 'bank', 'code': 'ATB4'}).default_account_id
d4 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2027-06-01'), 'cash_line_ids': [Command.create({'account_id': bank4.id, 'reference': 'D4'})],
               'line_ids': [Command.create({'account_id': expense.id, 'name': 'Deposit', 'amount': 1000})]})
d4.action_post()
r9 = env['sb.bank.rec'].create({'account_id': bank4.id, 'date': D('2027-06-30'), 'statement_balance': 1000.01,
                                'stmt_ids': [Command.create({'date': D('2027-06-01'), 'description': 'Deposit', 'reference': 'D4', 'amount': 1000})]})
r9.with_context(sb_rec_opening=True).stmt_opening = 0.01   # the bank's statement opens a cent above the books
r9.action_load()
r9.action_match()
r9.invalidate_recordset()
check(near(r9.difference, 0) and near(r9.unexplained, -0.01), 'R8. statement ticks to nil but the books are a cent short (%s)' % r9.unexplained)
try:
    with env.cr.savepoint():
        r9.action_finish()
    check(False, 'R8. a 0.01 difference to the books is refused')
except UserError as e:
    check('-0.01' in str(e), 'R8. a 0.01 difference to the books is refused: %s' % e)

# ---------------------------------------------------------------- R9. not registered for VAT: input VAT on a bill goes to cost
co.sb_vat_registered = False
bnr = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': D('2027-06-12'), 'line_ids': [Command.create(
    {'account_id': expense.id, 'name': 'Goods with VAT', 'quantity': 2, 'price_unit': 14000, 'tax_ids': [Command.set(co.account_purchase_tax_id.ids)]})]})
bnr.action_post()
vat_acc = co.account_purchase_tax_id.invoice_repartition_line_ids.account_id
check(near(bal(bnr.move_id, expense), 32200) and not bnr.move_id.line_ids.filtered(lambda l: l.account_id in vat_acc)
      and near(bal(payable_line(bnr).move_id, payable_line(bnr).account_id), -32200),
      'R9. bill 28,000 + 15%% VAT, company not registered: Dr expense 32,200 (VAT in cost), nothing to VAT receivable, Cr payable 32,200')
check(any('not registered for VAT' in (m.body or '') for m in bnr.message_ids), 'R9. the bill says why')
cpn = V.create({'voucher_type': 'cash_purchase', 'partner_id': vend.id, 'date': D('2027-06-12'), 'wht_supply': 'goods',
                'cash_line_ids': [Command.create({'account_id': bank.id})],
                'line_ids': [Command.create({'account_id': expense.id, 'name': 'Small', 'quantity': 1, 'price_unit': 1000,
                                             'tax_ids': [Command.set(co.account_purchase_tax_id.ids)]})]})
cpn._onchange_sync_single_deposit()
cpn.action_post()
check(near(bal(cpn.move_id, expense), 1150) and near(bal(cpn.move_id, bank), -1150), 'R9. cash purchase: Dr expense 1,150 / Cr bank 1,150')
co.sb_vat_registered = True

# ---------------------------------------------------------------- W1. withholding base before VAT, registered for VAT or not
vat15 = co.account_purchase_tax_id
for reg in (True, False):
    co.sb_vat_registered = reg
    tag = 'registered' if reg else 'not registered'
    b20 = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': D('2027-08-02'), 'line_ids': [Command.create(
        {'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': 20000, 'tax_ids': [Command.set(vat15.ids)]})]})
    b20.action_post()
    p20 = payment('2027-08-03', [(payable_line(b20), 23000)])
    check(not p20.withholding and near(p20.amount_cash, 23000) and not p20.wht_due,
          'W1. %s: bill 20,000 + VAT 3,000 (23,000): nothing withheld, the 20,000 before VAT is not over the threshold (%s)' % (tag, p20.wht_amount))
    b30v = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': D('2027-08-02'), 'line_ids': [Command.create(
        {'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': 30000, 'tax_ids': [Command.set(vat15.ids)]})]})
    b30v.action_post()
    p30v = payment('2027-08-03', [(payable_line(b30v), 34500)])
    check(near(p30v.wht_base, 30000) and near(p30v.wht_amount, 900) and near(p30v.amount_cash, 33600),
          'W1. %s: bill 30,000 + VAT (34,500): base 30,000, withheld 900, bank 33,600 (%s / %s)' % (tag, p30v.wht_base, p30v.wht_amount))
    cp30 = V.create({'voucher_type': 'cash_purchase', 'partner_id': vend.id, 'date': D('2027-08-03'),
                     'cash_line_ids': [Command.create({'account_id': bank.id})],
                     'line_ids': [Command.create({'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': 30000,
                                                  'tax_ids': [Command.set(vat15.ids)]})]})
    cp30._onchange_sync_single_deposit()
    cp30.action_post()
    check(cp30.wht_supply == 'goods' and near(cp30.wht_base, 30000) and near(cp30.wht_amount, 900) and near(cp30.amount_cash, 33600),
          'W1. %s: cash purchase 30,000 + VAT: the same rule, base 30,000, withheld 900 (%s)' % (tag, cp30.wht_base))
    doc = R.get_report('wht_register', {'date_from': '2027-08-03', 'date_to': '2027-08-03', 'side': 'payable', 'mode': 'month'})
    line = next(r for r in doc['rows'] if r['kind'] == 'line' and r['values']['number'] == p30v.name)['values']
    check(near(line['base'], 30000) and near(line['amount'], 900), 'W1. %s: the declaration shows base 30,000 (%s)' % (tag, line['base']))
    (p20 | p30v | cp30).action_void()
co.sb_vat_registered = True

# ---------------------------------------------------------------- W2. Pay Several Bills: the single-payment rule, per bill
vend4 = P.create({'name': 'Audit Supplier Four PLC', 'is_company': True, 'vat': '0099887733', 'sb_trade_license': 'AA/4/2020'})
nolic = P.create({'name': 'Audit No Licence PLC', 'is_company': True, 'vat': '0099887722'})
g1, g2, g3 = bill(12000, '2027-08-05', vend4), bill(12000, '2027-08-05', vend4), bill(50000, '2027-08-05', vend4)
s1 = bill(12000, '2027-08-05', vend4)
n1, n2 = bill(12000, '2027-08-05', nolic), bill(12000, '2027-08-05', nolic)
Batch = env['sb.batch.payment']


def batch(bills, withholding=True, reason=False, supply=None):
    w = Batch.create({'date': D('2027-08-10'), 'due_by': D('2027-08-31'), 'account_id': bank.id, 'withholding': withholding,
                      'wht_exempt_reason': reason})
    w.line_ids = [Command.create({'move_line_id': payable_line(b).id, 'pay': True, 'amount': -payable_line(b).amount_residual}) for b in bills]
    for line in w.line_ids:
        line.wht_supply = (supply or {}).get(line.move_id, 'goods')
    return w


w = batch([g1, g2, g3, s1], withholding=False, supply={s1.move_id: 'service'})
check(w.withholding is False and near(w.wht_due_total, 1860), 'W2. unticked: 1,860 due shown (50,000 goods bill 1,500 + 12,000 services bill 360): %s' % w.wht_due_total)
try:
    with env.cr.savepoint():
        w.action_pay()
    check(False, 'W2. paying without withholding and no reason is refused')
except UserError as e:
    check('1,860.00' in str(e), 'W2. paying without withholding and no reason is refused: %s' % e)
w = batch([g1, g2, g3, s1], supply={s1.move_id: 'service'})
per_bill = {l.move_id: l.wht_amount for l in w.line_ids}
check(near(per_bill[g1.move_id], 0) and near(per_bill[g2.move_id], 0) and near(per_bill[g3.move_id], 1500) and near(per_bill[s1.move_id], 360)
      and near(w.wht_total, 1860) and near(w.net_total, 86000 - 1860),
      'W2. shown per bill before posting: 12,000 goods 0, 12,000 goods 0, 50,000 goods 1,500, 12,000 services 360')
w.action_pay()
made = w.created_ids
check(len(made) == 2 and near(sum(made.mapped('wht_amount')), 1860) and near(sum(made.mapped('amount_cash')), 84140)
      and set(made.mapped('wht_supply')) == {'goods', 'service'},
      'W2. one payment per vendor and supply: withheld 1,860 in all, bank 84,140 (%s)' % made.mapped('wht_amount'))
w = batch([n1, n2])
w.action_pay()
check(near(w.created_ids.wht_amount, 0) and near(w.created_ids.amount_cash, 24000),
      'W2. two 12,000 goods bills (30%% supplier) each under the threshold: nothing withheld (was 7,200): %s' % w.created_ids.wht_amount)
g5 = bill(50000, '2027-08-06', vend4)
w = batch([g5], withholding=False, reason='Exemption certificate EX-91')
w.action_pay()
x = w.created_ids
check(not x.withholding and near(x.wht_due, 1500) and x.wht_exempt_reason == 'Exemption certificate EX-91' and near(x.amount_cash, 50000),
      'W2. unticked with a reason: paid gross, 1,500 due recorded')
doc = R.get_report('wht_register', {'date_from': '2027-08-01', 'date_to': '2027-08-31', 'side': 'payable', 'mode': 'month'})
check(any(r.get('parent') == 'exc' and r['values']['number'] == x.name for r in doc['rows']), 'W2. listed under "Not withheld, or withheld less"')

# ---------------------------------------------------------------- W3. goods or services left blank: decided on the server
vend5 = P.create({'name': 'Audit Supplier Five PLC', 'is_company': True, 'vat': '0099887711', 'sb_trade_license': 'AA/5/2020'})
b100 = bill(100000, '2027-08-12', vend5)
blank = V.create({'voucher_type': 'payment', 'partner_id': vend5.id, 'date': D('2027-08-13'),
                  'cash_line_ids': [Command.create({'account_id': bank.id, 'amount_currency': 100000})],
                  'apply_ids': [Command.create({'line_id': payable_line(b100).id, 'amount_applied': 100000, 'pay': True})]})
try:
    with env.cr.savepoint():
        blank.action_post()
    check(False, 'W3. a 100,000 bill paid with goods / services blank (a supplier with no history) is refused')
except ValidationError as e:
    check('goods or services' in str(e), 'W3. a 100,000 bill paid with goods / services blank is refused: %s' % e)
srv = env['product.product'].create({'name': 'Audit consulting', 'type': 'service'})
b15 = V.create({'voucher_type': 'bill', 'partner_id': vend5.id, 'date': D('2027-08-12'), 'line_ids': [Command.create(
    {'product_id': srv.id, 'account_id': expense.id, 'name': 'Consulting', 'quantity': 1, 'price_unit': 15000, 'tax_ids': []})]})
b15.action_post()
code_path = V.create({'voucher_type': 'payment', 'partner_id': vend5.id, 'date': D('2027-08-13'),
                      'cash_line_ids': [Command.create({'account_id': bank.id, 'amount_currency': 15000})],
                      'apply_ids': [Command.create({'line_id': payable_line(b15).id, 'amount_applied': 15000, 'pay': True})]})
try:
    with env.cr.savepoint():
        code_path.action_post()
    check(False, 'W3. a services bill of 15,000 paid gross from code, no reason: refused')
except ValidationError as e:
    check('services over' in str(e) and '450.00' in str(e),
          'W3. its supply is taken from the bill (a service item) and 450 is due: %s' % e)
blank.unlink()
code_path.unlink()

# ---------------------------------------------------------------- W4. a cancelled / bounced cheque takes its withholding back
b30c = bill(30000, '2027-08-15', vend4)
pc = V.create({'voucher_type': 'payment', 'partner_id': vend4.id, 'date': D('2027-08-16'), 'wht_supply': 'goods',
               'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'reference': 'OUT-77'})],
               'apply_ids': [Command.create({'line_id': payable_line(b30c).id, 'amount_applied': 30000, 'pay': True})]})
pc._onchange_sync_single_deposit()
pc.action_post()
check(near(pc.wht_amount, 900) and near(pc.cash_line_ids.amount, 29100), 'W4. 30,000 bill paid by cheque 29,100, withheld 900 (%s)' % pc.wht_certificate_no)
env['sb.cheque.wizard'].create({'row_id': pc.cash_line_ids.id, 'action': 'bounce', 'date': D('2027-08-18')}).action_confirm()
b30c.invalidate_recordset()
jv = pc.cash_line_ids.settle_voucher_id
check(near(b30c.move_id.amount_residual, 30000) and near(bal(jv.move_id, wht_pay), 900) and near(bal(jv.move_id, co.sb_cheque_outstanding_account_id), 29100),
      'W4. cancelled: the bill reopens for 30,000; Dr withholding payable 900, Dr outstanding cheques 29,100 (%s)' % b30c.move_id.amount_residual)
doc = R.get_report('wht_register', {'date_from': '2027-08-16', 'date_to': '2027-08-18', 'side': 'payable', 'mode': 'month'})
lines_ = [r['values'] for r in doc['rows'] if r['kind'] == 'line' and r['values']['cert'] == pc.wht_certificate_no]
check(len(lines_) == 2 and near(sum(l['amount'] for l in lines_), 0) and near(sum(l['base'] for l in lines_), 0)
      and any('cancelled' in l['note'] for l in lines_),
      'W4. the declaration shows receipt %s and its void (-30,000 / -900)' % pc.wht_certificate_no)
pr = payment('2027-08-20', [(payable_line(b30c), 30000)], partner=vend4)
moves = pc.move_id | jv.move_id | pr.move_id
check(near(pr.wht_amount, 900) and near(-sum(moves.line_ids.filtered(lambda l: l.account_id == wht_pay).mapped('balance')), 900),
      'W4. paid again: 900 withheld, 900 in all on the bill (was 1,773)')
cust4 = P.create({'name': 'Audit Withholding Customer PLC', 'is_company': True, 'vat': '0099887700'})
inv40 = V.create({'voucher_type': 'sale', 'partner_id': cust4.id, 'sb_pad_number': fs(), 'date': D('2027-08-15'),
                  'line_ids': [Command.create({'account_id': income.id, 'name': 'Fertilizer', 'quantity': 1, 'price_unit': 40000, 'tax_ids': []})]})
inv40.action_post()
rline40 = inv40.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
rc = V.create({'voucher_type': 'receipt', 'partner_id': cust4.id, 'date': D('2027-08-16'), 'withholding': True,
               'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'reference': 'IN-77'})],
               'apply_ids': [Command.create({'line_id': rline40.id, 'amount_applied': 40000, 'pay': True})]})
rc._onchange_sync_single_deposit()
rc.action_post()
env['sb.cheque.wizard'].create({'row_id': rc.cash_line_ids.id, 'action': 'bounce', 'date': D('2027-08-18')}).action_confirm()
inv40.invalidate_recordset()
wht_rec = co.sb_wht_receivable_account_id
moves = rc.move_id | rc.cash_line_ids.settle_voucher_id.move_id
check(near(rc.wht_amount, 1200) and near(inv40.move_id.amount_residual, 40000)
      and near(sum(moves.line_ids.filtered(lambda l: l.account_id == wht_rec).mapped('balance')), 0),
      'W4. received cheque 38,800 (1,200 withheld) bounced: the invoice reopens for 40,000, withholding receivable back to 0')

# ---------------------------------------------------------------- W5. a debit note lowers the bill tested
b30d = bill(30000, '2027-08-22', vend4)
dn = V.create({'voucher_type': 'purchase_return', 'partner_id': vend4.id, 'date': D('2027-08-23'), 'origin_id': b30d.id,
               'line_ids': [Command.create({'account_id': expense.id, 'name': 'Returned', 'quantity': 1, 'price_unit': 15000, 'tax_ids': []})]})
dn.action_post()
pd = payment('2027-08-24', [(payable_line(b30d), 15000)], partner=vend4)
check(not pd.withholding and near(pd.amount_cash, 15000), 'W5. 30,000 bill less a 15,000 debit note: the 15,000 paid is not withheld (was 450)')
b30e = bill(30000, '2027-08-22', vend4)
dn2 = V.create({'voucher_type': 'purchase_return', 'partner_id': vend4.id, 'date': D('2027-08-23'), 'origin_id': b30e.id,
                'line_ids': [Command.create({'account_id': expense.id, 'name': 'Returned', 'quantity': 1, 'price_unit': 5000, 'tax_ids': []})]})
dn2.action_post()
pe = payment('2027-08-24', [(payable_line(b30e), 25000)], partner=vend4)
check(near(pe.wht_base, 25000) and near(pe.wht_amount, 750), 'W5. 30,000 bill less a 5,000 debit note: 25,000 withheld 750 (%s)' % pe.wht_amount)

# ---------------------------------------------------------------- W6. VAT: out of scope filed alike, purchases without VAT, no withholding taxes
TaxA = env['account.tax'].with_context(active_test=False)
out_p = TaxA.search([('company_id', '=', co.id), ('type_tax_use', '=', 'purchase'), ('name', '=', '0% Out')], limit=1)
wh2 = TaxA.search([('company_id', '=', co.id), ('type_tax_use', '=', 'purchase'), ('amount', '<', 0)], limit=1)
legacy = TaxA.search([('company_id', '=', co.id), ('amount', '<', 0)])
check(legacy and not legacy.filtered('active'),
      'W6. the chart\'s old withholding taxes (%s) are archived where unused' % ', '.join(legacy.mapped('name')))
for taxes, amount in ((out_p, 1000), (TaxA, 2000), (vat15 | wh2, 10000)):
    bw = V.create({'voucher_type': 'bill', 'partner_id': vend4.id, 'date': D('2027-09-10'), 'line_ids': [Command.create(
        {'account_id': expense.id, 'name': 'Goods', 'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.set(taxes.ids)]})]})
    bw.action_post()
doc = R.get_report('vat_return', {'date_from': '2027-09-01', 'date_to': '2027-09-30'})
rv = {r['id']: r for r in doc['rows']}
check(near(rv.get('purchase_out', {}).get('values', {}).get('base', 0), 1000), 'W6. return: 1,000 at "0% Out" filed as outside the scope of VAT')
check(near(rv.get('purchase_none', {}).get('values', {}).get('base', 0), 2000) and near(rv['purchase']['values']['base'], 13000),
      'W6. return: the 2,000 bought without VAT is on the purchase side; purchases 13,000 in all (%s)' % rv['purchase']['values'])
check(near(rv['purchase']['values']['tax'], 1500) and not any(wh2.name == r['label'] for r in doc['rows']),
      'W6. return: input VAT 1,500; the -2%% withholding tax on the same line is not VAT (%s)' % rv['purchase']['values']['tax'])
doc = R.get_report('vat_register', {'date_from': '2027-09-01', 'date_to': '2027-09-30', 'side': 'purchase'})
tot = next(r for r in doc['rows'] if r['id'] == 'total')['values']
check(near(tot.get('base_out', 0), 1000) and near(tot.get('base_zero', 0), 0) and near(tot.get('tax', 0), 1500),
      'W6. register: "0%% Out" in the Out of scope column, as the return files it; VAT 1,500 (%s)' % tot)

# ---------------------------------------------------------------- W7. a foreign-currency bill: withheld at the bill's rate
usd = env.ref('base.USD')
usd.active = True
for day, etb in (('2027-10-01', 150.0), ('2027-10-20', 160.0)):
    env['res.currency.rate'].create({'currency_id': usd.id, 'name': D(day), 'company_rate': 1.0 / etb, 'company_id': co.id})
fb = V.create({'voucher_type': 'bill', 'partner_id': vend4.id, 'date': D('2027-10-01'), 'doc_currency_id': usd.id, 'line_ids': [Command.create(
    {'account_id': expense.id, 'name': 'Imported', 'quantity': 1, 'price_unit': 1000, 'tax_ids': []})]})
fb.action_post()
pf = V.create({'voucher_type': 'payment', 'partner_id': vend4.id, 'date': D('2027-10-20'), 'wht_supply': 'goods',
               'cash_line_ids': [Command.create({'account_id': bank.id})],
               'apply_ids': [Command.create({'line_id': payable_line(fb).id, 'amount_applied': 400, 'pay': True})]})
pf._onchange_sync_single_deposit()
pf.action_post()
check(near(pf.apply_ids.amount_applied_company, 64000) and near(pf.wht_base, 60000) and near(pf.wht_amount, 1800),
      'W7. USD 400 of a USD 1,000 bill billed at 150, paid at 160: base 60,000 (not 64,000), withheld 1,800 (%s)' % pf.wht_base)

print('\n%d / %d passed' % (sum(ok), len(ok)))
env.cr.rollback()
