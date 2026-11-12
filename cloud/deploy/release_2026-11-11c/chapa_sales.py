"""Sonador, 11 Nov 2026 (Robel): the two Chapa payments are sales, both receipted to Worthy Trading (the real estate agent), declared with VAT: 1,150 from the real estate agent
(0922105737, 25 Oct) and 115 (22 Oct). They were journal vouchers (JV/26-27/0001, 0002: other income +
VAT payable), which the VAT return and the sales register do not count. They become two cash sales (receipts) into Bank
Suspense (Chapa) with 15% VAT; the JVs are voided. Safe to repeat."""
import os
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
c = env.company
V = env['sb.voucher']
P = env['res.partner']
A = env['account.account'].with_company(c)
TAG = 'CHAPASALES'


def acc(code):
    a = A.search([('code', '=', code), ('company_ids', 'in', c.id)], limit=1)
    assert a, code
    return a


lock = max(filter(None, [c.fiscalyear_lock_date, c.tax_lock_date, c.sale_lock_date])) if any(
    [c.fiscalyear_lock_date, c.tax_lock_date, c.sale_lock_date]) else None
assert not lock or lock < date(2026, 10, 22), 'October is locked (%s): reopen it first' % lock
FS_115, FS_1150 = (os.environ.get('FS_115') or '').strip(), (os.environ.get('FS_1150') or '').strip()
if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    assert FS_115 and FS_1150, 'give the two receipt numbers (FS No. or e-invoice reference)'
    SUSP, SALES = acc('1102-0001'), acc('4000-0001')
    vat = env['account.tax'].search([('type_tax_use', '=', 'sale'), ('amount', '=', 15), ('amount_type', '=', 'percent'),
                                     ('company_id', '=', c.id)], limit=1)
    assert vat, 'no 15% sales VAT'
    old = V.search([('name', 'in', ('JV/26-27/0001', 'JV/26-27/0002')), ('state', '=', 'posted')])
    assert len(old) == 2, 'JV/26-27/0001 and 0002 not found (posted)'
    for v in old:
        v.sb_void_reason = 'Booked again as a cash sale with VAT (Chapa payment)'
        v.action_void()
        print('> voided %s' % v.name)
    agent = P.search([('name', '=', 'Worthy Trading')], limit=1)  # the real estate agent, already a client (TIN 0071389957)
    assert agent, 'Worthy Trading not found'
    robel = agent  # both receipts in the agent's name (Robel, 11 Nov 2026)
    for d, partner, net, label, fs in ((date(2026, 10, 22), robel, 100.0, 'Unio subscription, trial (paid through Chapa)', FS_115),
                                       (date(2026, 10, 25), agent, 1000.0, 'Unio subscription, trial (paid through Chapa)', FS_1150)):
        s = V.create({'voucher_type': 'cash_sale', 'date': d, 'partner_id': partner.id, 'cash_account_id': SUSP.id, 'sb_pad_number': fs,
                      'reference': 'Chapa', 'memo': 'Chapa payment [%s]' % TAG,
                      'line_ids': [Command.create({'name': label, 'account_id': SALES.id, 'quantity': 1, 'price_unit': net,
                                                   'tax_ids': [Command.set(vat.ids)]})]})
        s.action_post()
        print('> %s %s %s: %s (VAT %s)' % (s.name, d, partner.name, '{:,.2f}'.format(s.amount_total),
                                          '{:,.2f}'.format(s.amount_total - net)))
    env.cr.commit()
bal = round(sum(env['account.move.line'].search([('account_id', '=', A.search([('code', '=', '1102-0001')], limit=1).id),
                                                 ('parent_state', '=', 'posted')]).mapped('balance')), 2)
print('> Bank Suspense (Chapa) %s' % '{:,.2f}'.format(bal))
