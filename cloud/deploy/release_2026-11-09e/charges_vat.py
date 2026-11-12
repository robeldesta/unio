"""Sonador, 9 Nov 2026 (Robel: "all transfer fees include VAT"): bank and transfer charges include VAT 15%; from now on the
VAT part goes to 1401-0001 VAT Receivable on Purchases. The charges booked before, VAT and all, to Fees and charges: their
VAT is moved out today in one journal (closed VAT months stay as filed). Safe to repeat."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
c = env.company
A = env['account.account'].with_company(c)
V = env['sb.voucher']
TAG = 'CHARGESVAT'
vat_acc = A.search([('code', '=', '1401-0001'), ('company_ids', 'in', c.id)], limit=1)
fees = c.sb_bank_charges_account_id
assert vat_acc and fees
c.write({'sb_bank_charges_vat': 15.0, 'sb_bank_charges_vat_account_id': vat_acc.id})
if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    L = env['account.move.line'].search([('account_id', '=', fees.id), ('parent_state', '=', 'posted'),
                                         '|', ('name', '=like', 'Bank charges%'), ('name', '=', 'Dashen transfer charge')])
    rows = [(l, round(l.balance - l.balance / 1.15, 2)) for l in L]  # voided ones cancel with their reversal
    total = round(sum(v for _, v in rows), 2)
    v = V.create({'voucher_type': 'journal', 'date': date(2026, 11, 9), 'reference': 'VAT in bank charges',
                  'memo': 'VAT 15%% in %d bank and transfer charges booked before (%s) [%s]' % (len([r for r in rows if r[0].balance > 0]) - len([r for r in rows if r[0].balance < 0]), '{:,.2f}'.format(sum(l.balance for l, _ in rows)), TAG),
                  'line_ids': [Command.create({'account_id': vat_acc.id, 'name': 'VAT in bank and transfer charges to 9 Nov 2026', 'debit': total, 'credit': 0.0}),
                               Command.create({'account_id': fees.id, 'name': 'VAT in bank and transfer charges to 9 Nov 2026', 'debit': 0.0, 'credit': total})]})
    v.action_post()
    env.cr.commit()
    print('> %s: VAT %s out of %s of charges (%d)' % (v.name, '{:,.2f}'.format(total), '{:,.2f}'.format(sum(l.balance for l, _ in rows)), len([r for r in rows if r[0].balance > 0]) - len([r for r in rows if r[0].balance < 0])))
env.cr.commit()
print('> settings: VAT in bank charges %s%% to %s' % (c.sb_bank_charges_vat, c.sb_bank_charges_vat_account_id.code))
