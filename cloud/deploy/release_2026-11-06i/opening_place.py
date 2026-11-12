"""Sonador's books: the opening balances in their own place (Beginning balances, opening vouchers), not in journals.
  - OB/24-25/0001 (Jul 7, 2025): the shareholder receivable as it really was (258,203.60: 428,796.40 of it was
    dividends paid, out of retained earnings), and last year's withholding credit (13,041.48) set against last year's
    profit tax, which leaves 180,400.49, the amount paid by CPV/25-26/0010;
  - a new opening voucher on Jul 7, 2026 (Sene 30, 2018): Telebirr's top-ups of the year from the banks (down to their
    real balances), the cash on hand and the shareholder, so 2026/27 opens on the real bank balances;
  - void: the journals that carried these (JV/24-25/0001, JV/25-26/0015, JV/25-26/0017), the year-end journal
    JV/25-26/0016 (made again without the withholding lines: depreciation and profit tax only), and JV/25-26/0004 (the
    2024 dividend booked a second time; paid once, from CBE, CPV/25-26/0014, its tax by CPV/25-26/0009).
Checks every balance against the agreed opening before it keeps anything."""
from datetime import date

from odoo import Command

D0, D1 = date(2025, 7, 7), date(2026, 7, 7)
env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
B = env['sb.beginning']


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def bal(code, day=D1):
    return round(sum(env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted'),
                                                      ('date', '<=', day)]).mapped('balance')), 2)


AGREED = {'1104-0004': 2205.99, '1104-0005': 124.67, '1104-0006': 102.23, '1104-0007': 26.48, '1101-0001': 0.0,
          '1200-0002': 202608.62, '1300-0003': 79643.68, '1300-0006': 4500.0, '1401-0002': 25686.98, '1700-0004': 23960.82,
          '1701-0005': -4792.16, '2000-0013': 0.0, '2100-0001': -10080.0, '2100-0003': -7500.0, '2100-0004': -15500.0,
          '2100-0006': -76380.91, '2100-0007': 0.0, '3000-0001': -100000.0, '3000-0003': -22568.23, '3000-0004': 79982.49}
before = {c: bal(c) for c in AGREED}
for c, v in AGREED.items():
    assert abs(before[c] - v) < 0.005, 'before: %s is %s, expected %s' % (c, before[c], v)

ob26 = V.search([('voucher_type', '=', 'opening'), ('sb_ob_period', '=', D1), ('state', '=', 'posted')], limit=1)
if ob26:
    print('> already done: %s' % ob26.name)
else:
    # 1. the journals that carried opening balances, and the duplicate Schedule D: void
    for name in ('JV/24-25/0001', 'JV/25-26/0015', 'JV/25-26/0017', 'JV/25-26/0016', 'JV/25-26/0004'):
        v = V.search([('name', '=', name), ('state', '=', 'posted')], limit=1)
        assert v, 'no posted %s' % name
        v.action_void()
        print('> %s voided' % name)
    # 2. the opening of Jul 7, 2025, as it really was
    ob = V.search([('name', '=', 'OB/24-25/0001')])
    lines = [B._vline_vals(l) for l in ob.line_ids]
    fix = {'1200-0002': -428796.40, '3000-0004': 428796.40 - 90732.10 + 90732.10, '1401-0002': -13041.48, '2100-0006': 13041.48}
    for l in lines:
        code = A.browse(l['account_id']).code
        if code in fix:
            net = round(l['debit'] - l['credit'] + fix.pop(code), 2)
            l.update(debit=max(net, 0.0), credit=max(-net, 0.0))
    assert not fix, 'lines not found in OB/24-25/0001: %s' % fix
    lines = [l for l in lines if round(l['debit'] - l['credit'], 2)]
    B._repost(ob, lines)
    print('> %s rewritten: shareholder 258,203.60, retained earnings 6,905.20, profit tax 180,400.49 (old withholding set off)' % ob.name)
    # 3. the year-end journal again, without the withholding lines
    rob = env['res.partner'].search([('name', '=', 'Robel Desta')], limit=1)
    mor = env['res.partner'].search([('name', '=', 'Ministry of Revenue')], limit=1)
    ye = V.create({'voucher_type': 'journal', 'date': D1, 'reference': 'YE2018', 'memo': 'Year end 2018: depreciation and profit tax',
                   'line_ids': [Command.create({'account_id': acc('8200-0041').id, 'name': 'Depreciation of computers and IT, 20%', 'debit': 4792.16, 'credit': 0.0}),
                                Command.create({'account_id': acc('1701-0005').id, 'name': 'Depreciation of computers and IT, 20%', 'debit': 0.0, 'credit': 4792.16}),
                                Command.create({'account_id': acc('9000-0003').id, 'name': 'Business profit tax 2018 (Schedule C, line 240)', 'debit': 76380.91, 'credit': 0.0}),
                                Command.create({'account_id': acc('2100-0006').id, 'partner_id': mor.id, 'name': 'Business profit tax 2018 (Schedule C, line 240)', 'debit': 0.0, 'credit': 76380.91})]})
    ye.action_post()
    print('> %s posted: depreciation 4,792.16, profit tax 76,380.91' % ye.name)
    # 4. the opening of Jul 7, 2026: the real bank balances
    tb = round(0.0 - bal('1104-0007'), 2) + 26.48
    out = {c: round(bal(c) - AGREED[c], 2) for c in ('1104-0004', '1104-0005', '1104-0006')}
    cash = bal('1101-0001')
    share = round(tb - sum(out.values()) - cash, 2)
    ol = [{'account_id': acc('1104-0007').id, 'name': 'Opening balance (Telebirr top-ups of the year)', 'debit': tb, 'credit': 0.0}]
    ol += [{'account_id': acc(c).id, 'name': 'Opening balance (transferred to Telebirr during the year)', 'debit': 0.0, 'credit': out[c]} for c in out]
    ol += [{'account_id': acc('1101-0001').id, 'name': 'Opening balance (cash on hand paid into Telebirr)', 'debit': 0.0, 'credit': cash},
           {'account_id': acc('1200-0002').id, 'partner_id': rob.id, 'name': 'Opening balance (paid into Telebirr by the shareholder)', 'debit': 0.0, 'credit': share}]
    ob26 = V.create({'voucher_type': 'opening', 'sb_ob_kind': 'account', 'sb_ob_period': D1, 'date': D1, 'reference': 'Opening balances',
                     'memo': 'Opening balances as of Jul 7, 2026', 'line_ids': [Command.create(l) for l in ol]})
    ob26.action_post()
    print('> %s posted on %s: Telebirr %s from CBE %s, Dashen %s, BOA %s, cash %s, shareholder %s'
          % (ob26.name, D1, '{:,.2f}'.format(tb), *['{:,.2f}'.format(out[c]) for c in out], '{:,.2f}'.format(cash), '{:,.2f}'.format(share)))
    after = {c: bal(c) for c in AGREED}
    for c, v in AGREED.items():
        assert abs(after[c] - v) < 0.005, 'after: %s is %s, expected %s' % (c, after[c], v)
    env.cr.commit()
    print('> every opening balance on Jul 8, 2026 is as agreed')
for n in ('OB/24-25/0001', ob26.name):
    v = V.search([('name', '=', n)], limit=1)
    print('> %-15s %s  %s  %s' % (v.name, v.date, v.reference or '', '{:,.2f}'.format(sum(v.line_ids.mapped('debit')))))
