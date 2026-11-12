# Undeposited funds -> bank deposit. Rolled back at the end.
from odoo import Command, fields
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


env.company.fiscalyear_lock_date = False
V = env['sb.voucher']
undep = acc('211300')
check(undep and undep.sb_undeposited and undep.reconcile, 'Undeposited funds account ready')
bank = acc('211001')
d = fields.Date.from_string('2026-10-12')
cust = env['res.partner'].create({'name': 'Deposit Customer'})
made = []
for amt in (1000, 2500, 400):
    v = V.create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'date': d,
                  'line_ids': [Command.create({'name': 'Sale', 'account_id': acc('110000').id, 'price_unit': amt, 'tax_ids': [Command.clear()]})],
                  'cash_line_ids': [Command.create({'account_id': undep.id, 'method': 'cash'})]})
    v.action_post()
    made.append(v)
check(all(v.move_id.line_ids.filtered(lambda l: l.account_id == undep) for v in made), 'cash sales saved to Undeposited funds')
w = env['sb.bank.deposit'].create({'account_id': bank.id, 'date': d, 'slip': 'SLIP-77'})
w.action_load()
mine = w.line_ids.filtered(lambda l: l.partner_id == cust)
check(len(mine) == 3, 'deposit wizard loads the undeposited receipts')
(w.line_ids - mine).write({'include': False})
mine.filtered(lambda l: l.amount == 400).include = False
w.action_deposit()
dv = w.voucher_id
bank_line = dv.move_id.line_ids.filtered(lambda l: l.account_id == bank)
check(len(bank_line) == 1 and abs(bank_line.debit - 3500) < 0.01 and 'SLIP-77' in (bank_line.name or ''),
      'one bank line of 3,500 with the slip number (%s)' % bank_line.name)
open_left = env['account.move.line'].search([('account_id', '=', undep.id), ('reconciled', '=', False), ('partner_id', '=', cust.id)])
check(len(open_left) == 1 and abs(open_left.amount_residual - 400) < 0.01, 'the 400 left out stays undeposited')
w2 = env['sb.bank.deposit'].create({'account_id': bank.id, 'date': d})
w2.action_load()
check(not w2.line_ids.filtered(lambda l: l.amount in (1000, 2500) and l.partner_id == cust), 'deposited items do not come back')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
