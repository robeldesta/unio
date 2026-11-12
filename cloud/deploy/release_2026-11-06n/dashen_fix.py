"""Dashen as its statement: salaries paid from Dashen on the days and refs of the statement (the CPVs themselves, no
correcting journals), CSI0000111 received into Dashen. Voids JV/26-27/0013, 0014, 0016, 0017. Safe to repeat."""
from datetime import date

env = env(context=dict(env.context, tracking_disable=True))
V = env['sb.voucher']
A = env['account.account'].with_company(env.company)
TAG = 'DASHEN2027'
dashen = A.search([('code', 'in', ('1104-0005', '11040005'))], limit=1)
assert dashen


def bal(day):
    return round(sum(env['account.move.line'].search([('account_id', '=', dashen.id), ('parent_state', '=', 'posted'),
                                                      ('date', '<=', day)]).mapped('balance')), 2)


if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    for name in ('JV/26-27/0013', 'JV/26-27/0014', 'JV/26-27/0016', 'JV/26-27/0017'):
        j = V.search([('name', '=', name), ('state', '=', 'posted')], limit=1)
        assert j and 'BANKS2027' in (j.memo or ''), name
        j.action_void()
    # (voucher, date, reference, charges)
    for name, d, ref, charges in (('CPV/26-27/0004', date(2026, 7, 31), '449FTWD262120001', 0.0),
                                  ('CPV/26-27/0005', date(2026, 8, 20), '449IOOT2623200WH', 47.38),
                                  ('CPV/26-27/0008', date(2026, 8, 20), '449FTWD2623200WH', 0.0),
                                  ('CPV/26-27/0009', date(2026, 9, 28), '449FTWD2627101Yn', 0.0),
                                  ('CSI0000111', None, None, 0.0)):
        v = V.search([('name', '=', name), ('state', '=', 'posted')], limit=1)
        assert v and len(v.cash_line_ids) == 1, name
        v.action_edit()
        if d:
            v.date = d
            v.reference = ref
        c = v.cash_line_ids
        c.account_id = dashen
        if ref:
            c.reference = ref
        if charges:
            c.charges = charges
        v.memo = ((v.memo or '') + ' [%s]' % TAG).strip()
        v.action_post()
        print('> %-15s %s  Dashen %12s%s' % (v.name, v.date, '{:,.2f}'.format(c.amount_currency),
                                            ' + charges %s' % charges if charges else ''))
    for d, want in ((date(2026, 9, 29), 42.49), (date(2026, 11, 6), 42.49)):
        assert abs(bal(d) - want) < 0.005, 'Dashen %s is %s' % (d, bal(d))
    cbe = A.search([('code', 'in', ('1104-0004', '11040004'))], limit=1)
    c = round(sum(env['account.move.line'].search([('account_id', '=', cbe.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)
    assert abs(c - 491.42) < 0.005, 'CBE %s' % c
    env.cr.commit()
    print('> Dashen 42.49, CBE 491.42')
