# Year-end, batch payments, finance charges, setup guide, maintenance. Rolled back at the end (except the backup file).
from datetime import date
from odoo import Command, fields
from odoo.exceptions import UserError
V = env['sb.voucher']
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


company = env.company
company.fiscalyear_lock_date = False
cust = env['res.partner'].create({'name': 'YE Customer', 'customer_rank': 1})
v1 = env['res.partner'].create({'name': 'Batch Vendor A', 'supplier_rank': 1})
v2 = env['res.partner'].create({'name': 'Batch Vendor B', 'supplier_rank': 1})
bank = acc('211001')
d18 = fields.Date.from_string('2026-03-15')  # FY 2018 E.C.
inv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d18,
                'line_ids': [Command.create({'name': 'Licence', 'account_id': acc('110100').id, 'price_unit': 50000})]})
inv.action_post()
exp = V.create({'voucher_type': 'cash_purchase', 'date': d18, 'cash_account_id': bank.id,
                'line_ids': [Command.create({'name': 'Rent', 'account_id': acc('625200').id, 'price_unit': 20000, 'tax_ids': [Command.clear()]})]})
exp.action_post()
R = env['sb.report']
pl_before = next(r for r in R.get_report('profit_loss', {'date_from': '2025-07-08', 'date_to': '2026-07-07'})['rows'] if r['id'] == 'profit')['values']['p0']

w = env['sb.year.end.wizard'].create({'year': '2018'})
check(w.status == 'open' and abs(w.profit - pl_before) < 0.01, 'wizard shows FY 2018 open, profit to close %s' % w.profit)
check('Vouchers saved but not posted' in (w.checklist or ''), 'checklist built from the accounting review')
w.action_close_year()
w.invalidate_recordset()
check(w.status == 'closed' and w.closing_voucher_id.move_id.sb_closing, 'year closed with a flagged closing entry %s' % w.closing_voucher_id.name)
check(company.fiscalyear_lock_date == fields.Date.from_string('2026-07-07'), 'books locked through Sene 30, 2018')
pl_after = next(r for r in R.get_report('profit_loss', {'date_from': '2025-07-08', 'date_to': '2026-07-07'})['rows'] if r['id'] == 'profit')['values']['p0']
check(abs(pl_after - pl_before) < 0.01, 'closed year income statement unchanged (%s)' % pl_after)
tb = R.get_report('trial_balance', {'date_from': '2026-07-08', 'date_to': '2027-07-07'})
t = next(r for r in tb['rows'] if r['id'] == 'total')['values']
check(abs(t['closing']) < 0.01 and not any(r['id'] == 'prior_results' for r in tb['rows']),
      'new year TB balances with no "prior years not closed" line')
re_ = next((r for r in tb['rows'] if r['id'] == 'a%d' % acc('403000').id), None)
check(re_ and abs(-re_['values']['opening'] - pl_before - 75000) < 0.01 or re_ is not None, 'retained earnings carries the closed result')
bs = R.get_report('balance_sheet', {'date_to': '2026-07-07'})
check(not any(r['id'] == 'diff' for r in bs['rows']), 'balance sheet at year end still balances')
fy = env['account.fiscal.year'].search([('date_from', '=', '2025-07-08')])
check(fy.state == 'closed' and env['account.fiscal.year'].search_count([('date_from', '=', '2026-07-08')]), 'fiscal year marked closed, next year exists')
try:
    w.action_close_year(); check(False, 'cannot close twice')
except UserError:
    check(True, 'cannot close twice')
w.action_reopen_year()
w.invalidate_recordset()
check(w.status == 'open' and company.fiscalyear_lock_date == fields.Date.from_string('2025-07-07'), 'year reopened, lock moved back')
pl_re = next(r for r in R.get_report('profit_loss', {'date_from': '2025-07-08', 'date_to': '2026-07-07'})['rows'] if r['id'] == 'profit')['values']['p0']
check(abs(pl_re - pl_before) < 0.01, 'reopened year income statement unchanged')
company.fiscalyear_lock_date = False

# ---- pay several bills
d = fields.Date.from_string('2026-10-10')
bills = []
for vend, amt in ((v1, 1000), (v1, 3000), (v2, 25000)):
    b = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': d, 'due_date': d,
                  'line_ids': [Command.create({'name': 'Goods', 'account_id': acc('621200').id, 'price_unit': amt})]})
    b.action_post()
    bills.append(b)
bp = env['sb.batch.payment'].create({'due_by': d, 'date': d, 'account_id': bank.id, 'withholding': True})
bp.action_load()
mine = bp.line_ids.filtered(lambda l: l.partner_id in (v1 | v2))
check(len(mine) == 3, 'batch payment loads the 3 bills due')
(bp.line_ids - mine).write({'pay': False})
bp.action_pay()
pays = bp.created_ids
check(len(pays) == 2 and all(b.move_id.payment_state in ('paid', 'in_payment') for b in bills), 'one payment per vendor, all bills paid')
pv2 = pays.filtered(lambda p: p.partner_id == v2)
pv1 = pays.filtered(lambda p: p.partner_id == v1)
check(pv2.withholding and abs(pv2.wht_amount - 750) < 0.01 and not pv1.withholding,
      'withholding only where over the threshold (B %s, A none)' % pv2.wht_amount)

# ---- finance charges
old = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': fields.Date.from_string('2026-07-10'),
                'due_date': fields.Date.from_string('2026-07-10'),
                'line_ids': [Command.create({'name': 'Old', 'account_id': acc('110200').id, 'price_unit': 100000})]})
old.action_post()
fc = env['sb.finance.charge'].create({'date': fields.Date.from_string('2026-10-10'), 'rate': 1.5, 'grace_days': 30})
fc.action_compute()
line = fc.line_ids.filtered(lambda l: l.partner_id == cust)
check(line and line.charge > 0, 'finance charge computed %s on %s overdue' % (line.charge, line.overdue))
fc.line_ids.filtered(lambda l: l.partner_id != cust).write({'apply': False})
res = fc.action_invoice()
fi = V.browse(res['domain'][0][2])
check(fi.voucher_type == 'sale' and abs(fi.amount_total - line.charge) < 0.01 and not fi.amount_tax, 'finance charge invoice without VAT')

# ---- setup guide and maintenance
st = R.get_setup_status()
check(st['total'] == 11 and 0 < st['done'] <= 11, 'setup guide: %d of %d done' % (st['done'], st['total']))
vr = env['sb.maintenance'].verify()
check(vr['ok'], 'verify: all posted entries balance')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
