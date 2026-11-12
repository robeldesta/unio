"""CSI0000108 (Jobale): the invoice is 107,125.00 incl. VAT (93,152.17 + 13,972.83), withholding 2,794.57,
received 104,330.43 into CBE, as the statement. Drops the 6.00 'short payment' journal. Safe to repeat."""
from datetime import date

env = env(context=dict(env.context, tracking_disable=True))
V = env['sb.voucher']
TAG = 'JOBALE108'
v = V.search([('name', '=', 'CSI0000108'), ('voucher_type', '=', 'cash_sale')], limit=1)
assert v, 'no CSI0000108'
if TAG in (v.memo or ''):
    print('> already done')
else:
    j = V.search([('name', '=', 'JV/26-27/0008'), ('state', '=', 'posted')], limit=1)
    assert j and 'Jobale' in (j.memo or ''), 'JV/26-27/0008 not the Jobale 6.00'
    j.action_void()
    v.action_edit()
    v.line_ids[:1].price_unit = 93152.17
    v.wht_base, v.wht_amount = 93152.17, 2794.57
    v.cash_line_ids[:1].amount_currency = 104330.43
    v.memo = ((v.memo or '') + ' [%s]' % TAG).strip()
    v.action_post()
    m = v.move_id
    assert abs(v.amount_total - 107125.00) < 0.005, 'total %s' % v.amount_total
    assert abs(v.amount_cash - 104330.43) < 0.005, 'cash %s' % v.amount_cash
    assert abs(sum(m.line_ids.mapped('debit')) - sum(m.line_ids.mapped('credit'))) < 0.005
    acc = env['account.account'].with_company(env.company).search([('code', 'in', ('1104-0004', '11040004'))], limit=1)
    cbe = round(sum(env['account.move.line'].search([('account_id', '=', acc.id), ('parent_state', '=', 'posted'),
                                                     ('date', '<=', date(2026, 11, 6))]).mapped('balance')), 2)
    assert abs(cbe - 491.42) < 0.005, 'CBE %s' % cbe
    env.cr.commit()
    for l in m.line_ids:
        print('> %-26s %12s %12s' % (l.name, '{:,.2f}'.format(l.debit), '{:,.2f}'.format(l.credit)))
    print('> %s voided. CBE %s' % (j.name, '{:,.2f}'.format(cbe)))
