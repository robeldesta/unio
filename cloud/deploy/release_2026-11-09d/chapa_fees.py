"""Sonador, 9 Nov 2026 (Robel): Chapa's fees on the Bank Suspense money, VAT 15% inside each: 2.88 on the 115 and 28.75 on
the 1,150 received 28 Oct; 25 on the 1,000 sent to Telebirr 30 Oct. Bank Suspense ends at 208.37 (Chapa's available
balance). Run after release c. Safe to repeat."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
TAG = 'CHAPAFEES'


def acc(code):
    a = A.search([('code', '=', code), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, code
    return a


def bal(a):
    return round(sum(env['account.move.line'].search([('account_id', '=', a.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)


SUSP, FEES, VAT = acc('1102-0001'), acc('8200-0034'), acc('1401-0001')
if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    assert abs(bal(SUSP) - 265.0) < 0.005, 'Bank Suspense is %s, expected 265.00 (run release c first)' % bal(SUSP)
    for d, items in ((date(2026, 10, 28), [('Chapa fee on 115 received', 2.88), ('Chapa fee on 1,150 received', 28.75)]),
                     (date(2026, 10, 30), [('Chapa withdrawal fee on 1,000 to Telebirr', 25.0)])):
        lines = []
        for label, total in items:
            fee = round(total / 1.15, 2)
            lines += [Command.create({'account_id': FEES.id, 'name': label, 'debit': fee, 'credit': 0.0}),
                      Command.create({'account_id': VAT.id, 'name': 'VAT 15%% in %s' % label.lower(), 'debit': round(total - fee, 2), 'credit': 0.0}),
                      Command.create({'account_id': SUSP.id, 'name': label, 'debit': 0.0, 'credit': total})]
        v = V.create({'voucher_type': 'journal', 'date': d, 'reference': 'Chapa fees', 'memo': 'Chapa fees [%s]' % TAG, 'line_ids': lines})
        v.action_post()
        print('> %s %s %s' % (v.name, d, '{:,.2f}'.format(sum(t for _, t in items))))
    s = bal(SUSP)
    assert abs(s - 208.37) < 0.005, 'Bank Suspense is %s, Chapa says 208.37' % s
    env.cr.commit()
print('> Bank Suspense %s' % '{:,.2f}'.format(bal(SUSP)))
