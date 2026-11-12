"""Sonador, 10 Nov 2026 (Robel): Telebirr 440 to Dashen, then Dashen 400 to petty cash (replaces f). Safe to repeat."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
TAG = 'NOV10'


def acc(code):
    a = A.search([('code', '=', code), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, code
    return a


def bal(code):
    return round(sum(env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)


TELEBIRR, DASHEN, PETTY = '1104-0007', '1104-0005', '1101-0002'
D = date(2026, 11, 10)
if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    assert bal(TELEBIRR) >= 440, 'Telebirr has %s' % bal(TELEBIRR)
    for frm, to, memo, amt in ((TELEBIRR, DASHEN, 'Telebirr to Dashen', 440.0), (DASHEN, PETTY, 'Dashen to petty cash', 400.0)):
        v = V.create({'voucher_type': 'transfer', 'date': D, 'reference': memo, 'memo': '%s [%s]' % (memo, TAG),
                      'cash_account_id': acc(frm).id,
                      'cash_line_ids': [Command.create({'account_id': acc(to).id, 'method': 'transfer', 'amount_currency': amt,
                                                        'reference': memo})]})
        v.action_post()
        print('> %s %s %s' % (v.name, memo, '{:,.2f}'.format(amt)))
    env.cr.commit()
for code, name in ((TELEBIRR, 'Telebirr'), (DASHEN, 'Dashen'), (PETTY, 'Petty cash')):
    print('> %-12s %12s' % (name, '{:,.2f}'.format(bal(code))))
