# Scenario tests for Accounting. Everything is rolled back at the end.
# Run: docker compose run --rm -T odoo odoo shell -d sonador --no-http < addons/sonador_books/tests_manual/smoke.py
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError

V = env['sb.voucher']
P = env['res.partner']
company = env.company
def acc(c):
    # a standard-chart account the company never used is archived (hidden): the tests use it, so it comes back here
    a = env['account.account'].with_context(active_test=False).search([('code', '=', c)], limit=1)
    if a and not a.active:
        a.active = True
    return a
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def section(title):
    print('--- ' + title)


def entry(v):
    out = {}
    for l in v.move_id.line_ids:
        out[l.account_id.code] = round(out.get(l.account_id.code, 0.0) + l.balance, 2)
    return out


def raises(fn, exc=(UserError, ValidationError)):
    try:
        fn()
    except exc as e:
        return str(e) or True
    return False


cust = P.create({'name': 'Test Customer PLC', 'vat': '0001234567'})
vend = P.create({'name': 'Test Vendor PLC'})
d = fields.Date.from_string('2026-10-10')
bank = acc('211001')
cash_acc = acc('211000')
petty = acc('211200')
J = env['account.journal']
awash = J.create({'name': 'Awash Bank', 'type': 'bank', 'code': 'AWB'}).default_account_id
telebirr = J.create({'name': 'telebirr', 'type': 'bank', 'code': 'TLB'}).default_account_id
usd = env.ref('base.USD')
usd.active = True
env['res.currency.rate'].create({'currency_id': usd.id, 'name': '2026-10-01', 'inverse_company_rate': 150.0, 'company_id': company.id})
env['res.currency.rate'].create({'currency_id': usd.id, 'name': '2026-10-10', 'inverse_company_rate': 155.0, 'company_id': company.id})
usd_bank = J.create({'name': 'CBE USD', 'type': 'bank', 'code': 'USD1', 'currency_id': usd.id}).default_account_id
check(awash.sb_ifrs_line_id.code == 'SFP-CA-06' and usd_bank.currency_id == usd, 'new bank accounts auto-mapped to IFRS cash (%s)' % awash.code)


def sale(partner, price, qty=1, date=d, taxes=None, cur=None):
    vals = {'voucher_type': 'sale', 'partner_id': partner.id, 'date': date,
            'line_ids': [Command.create({'name': 'Service', 'account_id': acc('110200').id, 'quantity': qty, 'price_unit': price})]}
    if cur:
        vals['doc_currency_id'] = cur.id
    v = V.create(vals)
    if taxes is not None:
        v.line_ids.tax_ids = taxes
    v.action_post()
    return v


def bill(partner, price, date=d):
    v = V.create({'voucher_type': 'bill', 'partner_id': partner.id, 'date': date,
                  'line_ids': [Command.create({'name': 'Supplies', 'account_id': acc('621200').id, 'price_unit': price})]})
    v.action_post()
    return v


def paid(v):
    # credit / debit notes settled show as 'reversed' in Odoo
    return v.move_id.payment_state in ('paid', 'in_payment', 'reversed') and abs(v.move_id.amount_residual) < 0.005


# ======================================================================
section('Sales invoice, receipt, withholding certificate')
s = sale(cust, 10000)
check(abs(s.amount_total - 11500) < 0.01 and s.name == 'INV/26-27/0001', 'credit invoice %s total %s' % (s.name, s.amount_total))
r = V.new({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d})
r._onchange_partner_load_open_items()
check(len(r.apply_ids) == 1 and r.apply_ids.kind == 'invoice', 'receipt loads the open invoice')
r = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d, 'withholding': True,
              'wht_certificate_no': 'WH-0042', 'wht_certificate_date': d, 'cash_account_id': bank.id,
              'apply_ids': [Command.create({'invoice_id': s.move_id.id, 'amount_applied': 11500})]})
check(abs(r.wht_amount - 300) < 0.01 and abs(r.amount_cash - 11200) < 0.01, 'receipt WHT %s cash %s' % (r.wht_amount, r.amount_cash))
r.action_post()
check(paid(s) and r.name == 'CRV/26-27/0001', 'invoice paid by %s' % r.name)
check(any('WH-0042' in (l.name or '') for l in r.move_id.line_ids), 'withholding receipt no. on the GL line')

section('Bill and CPV with withholding')
b = bill(vend, 20000)
p = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': d, 'withholding': True, 'cash_account_id': bank.id,
              'apply_ids': [Command.create({'invoice_id': b.move_id.id, 'amount_applied': 23000})],
              'line_ids': [Command.create({'account_id': acc('625600').id, 'name': 'Bank charge', 'amount': 50})]})
check(abs(p.wht_amount - 601.5) < 0.01 and abs(p.amount_cash - 22448.5) < 0.01, 'CPV WHT %s cash %s' % (p.wht_amount, p.amount_cash))
p.action_post()
check(paid(b) and entry(p).get('300600') == -601.5, 'bill paid, WHT payable credited')

# ======================================================================
section('Split tender: cash sale over 2 banks + cash + mobile money, with bank charges')
cs = V.create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'date': d,
               'line_ids': [Command.create({'name': 'Hosting', 'account_id': acc('110300').id, 'quantity': 10, 'price_unit': 1000})],
               'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 5000, 'reference': 'FT-1'}),
                                 Command.create({'account_id': awash.id, 'method': 'deposit', 'amount_currency': 3000, 'charges': 10, 'reference': 'SLIP-9'}),
                                 Command.create({'account_id': cash_acc.id, 'method': 'cash', 'amount_currency': 2000})]})
check(abs(cs.amount_to_allocate - 1500) < 0.01, 'remaining to allocate shown: %s' % cs.amount_to_allocate)
check(raises(cs.action_post), 'posting blocked while rows do not add up')
cs.cash_line_ids = [Command.create({'account_id': telebirr.id, 'method': 'mobile', 'amount_currency': 1500, 'reference': 'TXN-55'})]
cs.action_post()
e = entry(cs)
check(e == {bank.code: 5000.0, awash.code: 2990.0, cash_acc.code: 2000.0, telebirr.code: 1500.0, '625600': 10.0,
            '110300': -10000.0, '300700': -1500.0}, 'one entry, one line per bank: %s' % e)
check(any(l.name and 'SLIP-9' in l.name for l in cs.move_id.line_ids), 'deposit slip no. on the bank line (bank book)')
num = cs.name
cs.action_edit()
cs.line_ids.price_unit = 1100
cs.cash_line_ids.filtered(lambda r: r.account_id == telebirr).amount_currency = 2650
cs.action_post()
check(cs.name == num and abs(cs.move_id.amount_total - 12650) < 0.01, 'cash sale edit/repost keeps %s' % num)
cs.action_void()
check(cs.state == 'void', 'cash sale void')

section('Single-row receipt still one field')
single = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d,
                   'cash_line_ids': [Command.create({'account_id': bank.id})],
                   'line_ids': [Command.create({'account_id': acc('120200').id, 'name': 'Misc', 'amount': 700})]})
single.action_post()
check(entry(single) == {bank.code: 700.0, '120200': -700.0}, 'single bank row takes the whole amount')

section('Mixed currencies on one voucher (USD bank + ETB cash)')
mx = V.create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'date': d,
               'line_ids': [Command.create({'name': 'Licence', 'account_id': acc('110100').id, 'price_unit': 1000})],
               'cash_line_ids': [Command.create({'account_id': usd_bank.id, 'method': 'transfer', 'amount_currency': 5}),
                                 Command.create({'account_id': cash_acc.id, 'method': 'cash', 'amount_currency': 375})]})
row = mx.cash_line_ids.filtered(lambda r: r.account_id == usd_bank)
check(abs(row.rate - 155) < 0.001 and abs(row.amount - 775) < 0.01, 'USD row at 155 = %s ETB' % row.amount)
mx.action_post()
ul = mx.move_id.line_ids.filtered(lambda l: l.account_id == usd_bank)
check(ul.currency_id == usd and abs(ul.amount_currency - 5) < 0.001 and abs(ul.balance - 775) < 0.01, 'USD bank line 5 USD / 775 ETB')

# ======================================================================
section('Cheques received: post-dated, deposited, bounced')
inv = sale(cust, 2000)
crv = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d,
                'apply_ids': [Command.create({'invoice_id': inv.move_id.id, 'amount_applied': 2300})],
                'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 1300}),
                                  Command.create({'account_id': awash.id, 'method': 'cheque', 'amount_currency': 1000,
                                                  'reference': 'CHQ-77', 'cheque_bank': 'Dashen', 'cheque_date': d + timedelta(days=20)})]})
crv.action_post()
chq = crv.cash_line_ids.filtered(lambda r: r.method == 'cheque')
check(entry(crv).get('211100') == 1000.0 and chq.cheque_state == 'open' and paid(inv), 'post-dated cheque held in Cheques on hand, invoice paid')
early = env['sb.cheque.wizard'].create({'row_id': chq.id, 'action': 'clear', 'account_id': awash.id, 'date': d})
check(raises(early.action_confirm), 'post-dated cheque cannot be deposited before its date')
w = env['sb.cheque.wizard'].create({'row_id': chq.id, 'action': 'clear', 'account_id': awash.id, 'date': d + timedelta(days=21)})
w.action_confirm()
check(chq.cheque_state == 'cleared' and entry(chq.settle_voucher_id) == {awash.code: 1000.0, '211100': -1000.0},
      'cheque deposited: %s' % chq.settle_voucher_id.name)
check(raises(crv.action_void), 'cannot void a receipt whose cheque is already deposited')

inv2 = sale(cust, 400)
crv2 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d,
                 'apply_ids': [Command.create({'invoice_id': inv2.move_id.id, 'amount_applied': 460})],
                 'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'amount_currency': 460, 'reference': 'CHQ-88'})]})
crv2.action_post()
chq2 = crv2.cash_line_ids
w = env['sb.cheque.wizard'].create({'row_id': chq2.id, 'action': 'bounce', 'account_id': bank.id, 'charges': 20, 'date': d})
w.action_confirm()
e = entry(chq2.settle_voucher_id)
check(chq2.cheque_state == 'bounced' and e == {'221100': 460.0, '211100': -460.0, '625600': 20.0, bank.code: -20.0},
      'bounced cheque back to customer + charges: %s' % e)
nx = V.new({'voucher_type': 'receipt', 'partner_id': cust.id})
nx._onchange_partner_load_open_items()
check(any(abs(a.amount_residual - 460) < 0.01 for a in nx.apply_ids), 'bounced amount offered on the next receipt')

section('Cheques issued: outstanding, cleared, cancelled')
b2 = bill(vend, 1000)
cpv = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': d,
                'apply_ids': [Command.create({'invoice_id': b2.move_id.id, 'amount_applied': 1150})],
                'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'amount_currency': 1150, 'reference': '000123'})]})
cpv.action_post()
check(entry(cpv).get('306500') == -1150.0 and paid(b2), 'issued cheque in Outstanding cheques')
w = env['sb.cheque.wizard'].create({'row_id': cpv.cash_line_ids.id, 'action': 'clear', 'account_id': bank.id, 'date': d})
w.action_confirm()
check(entry(cpv.cash_line_ids.settle_voucher_id) == {'306500': 1150.0, bank.code: -1150.0}, 'issued cheque cleared by bank')
b3 = bill(vend, 500)
cpv3 = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': d,
                 'apply_ids': [Command.create({'invoice_id': b3.move_id.id, 'amount_applied': 575})],
                 'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'amount_currency': 575, 'reference': '000124'})]})
cpv3.action_post()
env['sb.cheque.wizard'].create({'row_id': cpv3.cash_line_ids.id, 'action': 'bounce', 'date': d}).action_confirm()
nx = V.new({'voucher_type': 'payment', 'partner_id': vend.id})
nx._onchange_partner_load_open_items()
check(cpv3.cash_line_ids.cheque_state == 'bounced' and any(abs(a.amount_residual - 575) < 0.01 for a in nx.apply_ids),
      'cancelled cheque: vendor owed again, offered on next payment')

section('CPV split over two banks')
b4 = bill(vend, 20000)
cpv4 = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': d,
                 'apply_ids': [Command.create({'invoice_id': b4.move_id.id, 'amount_applied': 23000})],
                 'cash_line_ids': [Command.create({'account_id': bank.id, 'amount_currency': 13000}),
                                   Command.create({'account_id': awash.id, 'amount_currency': 10000})]})
cpv4.action_post()
check(paid(b4) and entry(cpv4) == {bank.code: -13000.0, awash.code: -10000.0, '300200': 23000.0}, 'CPV from two banks')

# ======================================================================
section('Partial, multiple and cross settlements; discount write-off')
i1, i2 = sale(cust, 100), sale(cust, 200)
r3 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
               'apply_ids': [Command.create({'invoice_id': i1.move_id.id, 'amount_applied': 115}),
                             Command.create({'invoice_id': i2.move_id.id, 'amount_applied': 100})]})
r3.action_post()
check(paid(i1) and i2.move_id.payment_state == 'partial', 'one CRV: one invoice paid, one partial')
r4 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
               'apply_ids': [Command.create({'invoice_id': i2.move_id.id, 'amount_applied': 129.5, 'discount': 0.5})]})
r4.action_post()
check(paid(i2) and entry(r4).get('110900') == 0.5, 'second CRV settles the rest, 0.50 written off to discounts allowed')

section('Advances both sides, overpayment, capital injection, loan, transfer')
share = P.create({'name': 'Test Shareholder'})
Pu = env['sb.voucher.purpose']
cap = V.new({'voucher_type': 'receipt', 'partner_id': share.id, 'received_amount': 1000000,
             'purpose_id': Pu.search([('name', '=', 'Capital injection (share capital)')]).id})
cap._onchange_purpose()
check(cap.line_ids.account_id.code == '401000' and cap.line_ids.amount == 1000000, 'purpose pre-fills share capital')
cap = V.create({'voucher_type': 'receipt', 'partner_id': share.id, 'date': d, 'cash_account_id': bank.id,
                'line_ids': [Command.create({'account_id': acc('401000').id, 'name': 'Capital', 'amount': 1000000})]})
cap.action_post()
check(entry(cap) == {bank.code: 1000000.0, '401000': -1000000.0}, 'capital injection Dr bank / Cr share capital')
c2 = P.create({'name': 'Advance Customer'})
adv = V.create({'voucher_type': 'receipt', 'partner_id': c2.id, 'date': d, 'cash_account_id': bank.id, 'received_amount': 5000})
adv.action_post()
check(entry(adv) == {bank.code: 5000.0, '305900': -5000.0}, 'deposit with no invoice -> customer advance')
i4 = sale(c2, 2000)
nx = V.new({'voucher_type': 'receipt', 'partner_id': c2.id})
nx._onchange_partner_load_open_items()
check(sorted(nx.apply_ids.mapped('kind')) == ['credit', 'invoice'], 'next receipt offers invoice and advance')
use = V.create({'voucher_type': 'receipt', 'partner_id': c2.id, 'date': d, 'cash_account_id': bank.id,
                'apply_ids': [Command.create({'invoice_id': i4.move_id.id, 'amount_applied': 2300}),
                              Command.create({'line_id': adv.move_id.line_ids.filtered(lambda l: l.account_id.code == '305900').id, 'amount_applied': 2300})]})
use.action_post()
check(paid(i4) and abs(use.amount_cash) < 0.01, 'advance applied to invoice without cash')
check(raises(adv.action_void), 'cannot void an advance already used')
i5 = sale(c2, 1000)
over = V.create({'voucher_type': 'receipt', 'partner_id': c2.id, 'date': d, 'cash_account_id': bank.id, 'received_amount': 2000,
                 'apply_ids': [Command.create({'invoice_id': i5.move_id.id, 'amount_applied': 1150})]})
over.action_post()
check(entry(over) == {bank.code: 2000.0, '221100': -1150.0, '305900': -850.0}, 'overpayment kept as advance')
v2 = P.create({'name': 'Advance Vendor'})
vadv = V.create({'voucher_type': 'payment', 'partner_id': v2.id, 'date': d, 'cash_account_id': bank.id, 'received_amount': 3000})
vadv.action_post()
b5 = bill(v2, 2000)
pv = V.new({'voucher_type': 'payment', 'partner_id': v2.id})
pv._onchange_partner_load_open_items()
credit_item = pv.apply_ids.filtered(lambda a: a.kind == 'credit')
pv2 = V.create({'voucher_type': 'payment', 'partner_id': v2.id, 'date': d, 'cash_account_id': bank.id,
                'apply_ids': [Command.create({'invoice_id': b5.move_id.id, 'amount_applied': 2300}),
                              Command.create({'line_id': credit_item.line_id.id, 'amount_applied': 2300})]})
pv2.action_post()
check(paid(b5), 'vendor advance applied to bill')
lender = P.create({'name': 'Test Bank SC'})
loan = V.create({'voucher_type': 'payment', 'partner_id': lender.id, 'date': d, 'cash_account_id': bank.id,
                 'line_ids': [Command.create({'account_id': acc('315300').id, 'name': 'Principal', 'amount': 50000}),
                              Command.create({'account_id': acc('643400').id, 'name': 'Interest', 'amount': 4000})]})
loan.action_post()
check(entry(loan) == {bank.code: -54000.0, '315300': 50000.0, '643400': 4000.0}, 'loan principal to liability, interest to expense')
bt = V.create({'voucher_type': 'transfer', 'date': d, 'cash_account_id': bank.id,
               'cash_line_ids': [Command.create({'account_id': awash.id, 'amount_currency': 6000}),
                                 Command.create({'account_id': cash_acc.id, 'amount_currency': 4000})],
               'line_ids': [Command.create({'account_id': acc('625600').id, 'name': 'Fee', 'amount': 25})]})
bt.action_post()
check(bt.name.startswith('BT/') and entry(bt) == {awash.code: 6000.0, cash_acc.code: 4000.0, bank.code: -10025.0, '625600': 25.0},
      'one source to two destinations + fee')

# ======================================================================
section('Returns: credit notes and debit notes')
inv6 = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d,
                 'line_ids': [Command.create({'name': 'Router', 'account_id': acc('110000').id, 'quantity': 2, 'price_unit': 1000})]})
inv6.action_post()
ret = V.new({'voucher_type': 'sale_return', 'partner_id': cust.id, 'origin_id': inv6.id})
ret._onchange_origin()
check(len(ret.line_ids) == 1 and ret.line_ids.quantity == 2, 'return copies the invoice lines')
ret = V.create({'voucher_type': 'sale_return', 'partner_id': cust.id, 'date': d, 'origin_id': inv6.id,
                'line_ids': [Command.create({'name': 'Router returned', 'account_id': acc('110000').id, 'quantity': 1, 'price_unit': 1000})]})
ret.action_post()
check(ret.move_id.move_type == 'out_refund' and abs(inv6.move_id.amount_residual - 1150) < 0.01,
      'credit note against open invoice reduces it to %s' % inv6.move_id.amount_residual)
cs2 = V.create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
                'line_ids': [Command.create({'name': 'Modem', 'account_id': acc('110000').id, 'price_unit': 1000})]})
cs2.action_post()
ret2 = V.create({'voucher_type': 'sale_return', 'partner_id': cust.id, 'date': d, 'origin_id': cs2.id,
                 'line_ids': [Command.create({'name': 'Modem returned', 'account_id': acc('110000').id, 'price_unit': 1000})]})
ret2.action_post()
refund = V.new({'voucher_type': 'payment', 'partner_id': cust.id})
refund._onchange_partner_load_open_items()
item = refund.apply_ids.filtered(lambda a: a.invoice_id == ret2.move_id)
check(item and item.direction == 'credit', 'cash sale return leaves a customer credit, offered on a Payment (refund)')
refund = V.create({'voucher_type': 'payment', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
                   'apply_ids': [Command.create({'line_id': item.line_id.id, 'amount_applied': 1150})]})
refund.action_post()
check(paid(ret2) and entry(refund) == {bank.code: -1150.0, '221100': 1150.0}, 'customer refunded by CPV %s %s %s' % (ret2.move_id.payment_state, entry(refund), refund.amount_cash))
b6 = bill(vend, 1000)
pay6 = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': d, 'cash_account_id': bank.id,
                 'apply_ids': [Command.create({'invoice_id': b6.move_id.id, 'amount_applied': 1150})]})
pay6.action_post()
dn = V.create({'voucher_type': 'purchase_return', 'partner_id': vend.id, 'date': d, 'origin_id': b6.id,
               'line_ids': [Command.create({'name': 'Returned', 'account_id': acc('621200').id, 'price_unit': 1000})]})
dn.action_post()
vr = V.new({'voucher_type': 'receipt', 'partner_id': vend.id})
vr._onchange_partner_load_open_items()
item = vr.apply_ids.filtered(lambda a: a.invoice_id == dn.move_id)
vr = V.create({'voucher_type': 'receipt', 'partner_id': vend.id, 'date': d, 'cash_account_id': bank.id,
               'apply_ids': [Command.create({'line_id': item.line_id.id, 'amount_applied': 1150})]})
vr.action_post()
check(dn.move_id.move_type == 'in_refund' and paid(dn), 'debit note on a paid bill refunded by the vendor via CRV %s %s' % (dn.move_id.payment_state, entry(vr)))

# ======================================================================
section('Mixed VAT, exempt, zero-rated and TOT')
Tax = env['account.tax']
t15 = company.account_sale_tax_id
t0 = Tax.search([('name', '=', '0%'), ('type_tax_use', '=', 'sale')], limit=1)
tex = Tax.search([('name', '=', '0% EXEMPT'), ('type_tax_use', '=', 'sale')], limit=1)
tot = Tax.search([('name', '=', 'TOT 2%')], limit=1)
mv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d, 'line_ids': [
    Command.create({'name': 'Std', 'account_id': acc('110000').id, 'price_unit': 1000, 'tax_ids': [Command.set(t15.ids)]}),
    Command.create({'name': 'Export', 'account_id': acc('110000').id, 'price_unit': 1000, 'tax_ids': [Command.set(t0.ids)]}),
    Command.create({'name': 'Exempt', 'account_id': acc('110000').id, 'price_unit': 1000, 'tax_ids': [Command.set(tex.ids)]})]})
mv.action_post()
check(abs(mv.amount_total - 3150) < 0.01 and abs(mv.amount_tax - 150) < 0.01, 'mixed VAT invoice total %s tax %s' % (mv.amount_total, mv.amount_tax))
tv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d, 'line_ids': [
    Command.create({'name': 'Goods', 'account_id': acc('110000').id, 'price_unit': 1000, 'tax_ids': [Command.set(tot.ids)]})]})
tv.action_post()
check(tot and abs(tv.amount_total - 1020) < 0.01 and entry(tv).get('300900') == -20.0, 'TOT 2% to TOT payable')
nv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d, 'line_ids': [
    Command.create({'name': 'No tax', 'account_id': acc('110000').id, 'price_unit': 1000, 'tax_ids': [Command.clear()]})]})
nv.action_post()
check(abs(nv.amount_total - 1000) < 0.01, 'no-tax line (non-VAT customer / out of scope)')

# ======================================================================
section('Foreign currency invoice settled in ETB (realised FX)')
fx = sale(cust, 100, date=fields.Date.from_string('2026-10-02'), taxes=env['account.tax'], cur=usd)
check(fx.move_id.currency_id == usd and abs(fx.move_id.amount_total_signed - 15000) < 0.01, 'USD 100 invoice booked at 150 = 15,000 ETB')
fr = V.new({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d})
fr._onchange_partner_load_open_items()
item = fr.apply_ids.filtered(lambda a: a.invoice_id == fx.move_id)
check(item.item_currency_id == usd and abs(item.amount_residual - 100) < 0.01, 'receipt shows the USD balance')
fr = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
               'apply_ids': [Command.create({'line_id': item.line_id.id, 'amount_applied': 100})]})
check(abs(fr.amount_cash - 15500) < 0.01, 'settled at 155 = %s ETB' % fr.amount_cash)
fr.action_post()
exch = env['account.move'].search([('journal_id', '=', company.currency_exchange_journal_id.id)], order='id desc', limit=1)
gain = sum(exch.line_ids.filtered(lambda l: l.account_id == company.income_currency_exchange_account_id).mapped('credit'))
check(paid(fx) and abs(gain - 500) < 0.01, 'invoice paid, realised FX gain %s booked by Odoo' % gain)

# ======================================================================
section('Staff advance, petty cash imprest')
emp = P.create({'name': 'Test Employee'})
sa = V.create({'voucher_type': 'payment', 'partner_id': emp.id, 'date': d, 'cash_account_id': cash_acc.id,
               'line_ids': [Command.create({'account_id': acc('220300').id, 'name': 'Advance', 'amount': 2000})]})
sa.action_post()
sr = V.new({'voucher_type': 'receipt', 'partner_id': emp.id})
sr._onchange_partner_load_open_items()
it = sr.apply_ids
sr = V.create({'voucher_type': 'receipt', 'partner_id': emp.id, 'date': d, 'cash_account_id': cash_acc.id,
               'apply_ids': [Command.create({'line_id': it.line_id.id, 'amount_applied': 2000})]})
sr.action_post()
check(len(it) == 1 and it.line_id.reconciled, 'staff advance issued and recovered')
imp = V.create({'voucher_type': 'transfer', 'date': d, 'cash_account_id': bank.id,
                'cash_line_ids': [Command.create({'account_id': petty.id, 'method': 'cash', 'amount_currency': 5000})]})
imp.action_post()
pc = V.create({'voucher_type': 'cash_purchase', 'date': d, 'line_ids': [Command.create({'name': 'Tea', 'account_id': acc('621800').id, 'price_unit': 1000, 'tax_ids': [Command.clear()]})],
               'cash_line_ids': [Command.create({'account_id': petty.id, 'method': 'cash'})]})
pc.action_post()
check(entry(pc) == {petty.code: -1000.0, '621800': 1000.0}, 'petty cash expense paid from Petty cash')

# ======================================================================
section('Opening balances')
ob = V.create({'voucher_type': 'opening', 'date': fields.Date.from_string('2025-07-08'), 'line_ids': [
    Command.create({'account_id': bank.id, 'name': 'CBE balance', 'debit': 100000}),
    Command.create({'account_id': acc('221100').id, 'partner_id': cust.id, 'name': 'OB-INV-1', 'debit': 20000, 'due_date': '2025-08-01'}),
    Command.create({'account_id': acc('300200').id, 'partner_id': vend.id, 'name': 'OB-BILL-7', 'credit': 15000})]})
ob.action_post()
check(ob.name.startswith('OB/') and entry(ob).get('409000') == -105000.0, 'opening balances, difference to opening equity: %s' % entry(ob))
ox = V.new({'voucher_type': 'receipt', 'partner_id': cust.id})
ox._onchange_partner_load_open_items()
check(any(a.invoice_id == ob.move_id and abs(a.amount_residual - 20000) < 0.01 for a in ox.apply_ids), 'opening customer balance offered on receipts')

# ======================================================================
section('Journal voucher, void rules, closed periods')
j = V.create({'voucher_type': 'journal', 'date': d, 'memo': 'Accrue audit fee', 'line_ids': [
    Command.create({'account_id': acc('625100').id, 'debit': 5000}),
    Command.create({'account_id': acc('305600').id, 'credit': 4000})]})
check(raises(j.action_post), 'unbalanced JV rejected')
j.line_ids[1].credit = 5000
j.action_post()
inv7 = sale(cust, 300)
r7 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d, 'cash_account_id': bank.id,
               'apply_ids': [Command.create({'invoice_id': inv7.move_id.id, 'amount_applied': 100})]})
r7.action_post()
check(raises(inv7.action_void), 'cannot void a partly paid invoice')
r7.action_void()
inv7.action_void()
check(inv7.state == 'void' and inv7.move_id.payment_state == 'reversed', 'void receipt, then invoice')
company.fiscalyear_lock_date = fields.Date.from_string('2026-10-10')
check(raises(V.create({'voucher_type': 'journal', 'date': d, 'line_ids': [
    Command.create({'account_id': acc('625100').id, 'debit': 1}),
    Command.create({'account_id': acc('305600').id, 'credit': 1})]}).action_post), 'posting into a closed period rejected')
j.action_void()
rev = j.move_id.reversal_move_ids
check(j.state == 'void' and rev.date == fields.Date.context_today(j), 'void in closed period reversed on %s' % rev.date)
company.fiscalyear_lock_date = False

w = env['sb.period.wizard'].create({})
check(len(w.line_ids) == 24, 'periods screen lists two fiscal years')
tot_ = env['account.move.line'].read_group([('parent_state', '=', 'posted')], ['debit:sum', 'credit:sum'], [])[0]
check(abs(tot_['debit'] - tot_['credit']) < 0.01, 'trial balance balanced: %s' % tot_['debit'])
print('NAV', V.sb_nav_data())
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
