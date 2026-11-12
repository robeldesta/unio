"""OB/25-26/0001 reads the opening balance of Jul 8, 2026 itself, account by account (338,859.47), like OB/24-25/0001.
A closing voucher on the same day takes the 2018 balance sheet off first, so nothing counts twice; the 2018 income
statement is left as it is. Every balance after = before; checked before anything is kept."""
from datetime import date

from odoo import Command

D1 = date(2026, 7, 7)
env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
B = env['sb.beginning']
ML = env['account.move.line']


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def partner(name):
    p = env['res.partner'].search([('name', '=', name)], limit=1)
    assert p, 'no partner %s' % name
    return p


OPEN = [('1104-0004', '', 2205.99, 0), ('1104-0005', '', 124.67, 0), ('1104-0006', '', 102.23, 0),
        ('1104-0007', '', 26.48, 0), ('1200-0002', 'Robel Desta', 202608.62, 0), ('1300-0003', '', 79643.68, 0),
        ('1300-0006', '', 4500.0, 0), ('1401-0002', '', 25686.98, 0), ('1700-0004', '', 23960.82, 0),
        ('1701-0005', '', 0, 4792.16), ('2100-0001', 'POESSA (Private Organization Employees Social Security Agency)', 0, 10080.0),
        ('2100-0003', '', 0, 7500.0), ('2100-0004', '', 0, 15500.0), ('2100-0006', '', 0, 76380.91),
        ('3000-0001', '', 0, 100000.0), ('3000-0003', '', 0, 22568.23), ('3000-0004', '', 0, 102038.17)]
PARTNER_KEPT = ('asset_receivable', 'liability_payable')

ob = V.search([('voucher_type', '=', 'opening'), ('sb_ob_period', '=', D1), ('reference', '=', 'Opening balances'), ('state', '=', 'posted')], limit=1)
assert ob, 'no opening voucher of Jul 7, 2026'
if V.search_count([('voucher_type', '=', 'opening'), ('sb_ob_period', '=', D1), ('reference', '=', 'Closing balances 2018')]):
    print('> already done')
else:
    def snapshot():
        out = {}
        for l in ML.search([('parent_state', '=', 'posted'), ('company_id', '=', env.company.id), ('date', '<=', D1)]):
            out[l.account_id.code] = out.get(l.account_id.code, 0.0) + l.balance
        return {k: round(v, 2) for k, v in out.items()}
    before = snapshot()
    # the balance sheet of Jul 7, 2026 without the opening voucher of that day (per person where an account keeps one)
    hist = {}
    for l in ML.search([('parent_state', '=', 'posted'), ('company_id', '=', env.company.id), ('date', '<=', D1),
                        ('move_id', '!=', ob.move_id.id), ('account_id.include_initial_balance', '=', True)]):
        a = l.account_id
        p = l.partner_id.id if a.account_type in PARTNER_KEPT else False
        hist[(a.id, p)] = hist.get((a.id, p), 0.0) + l.balance
    lines, total = [], 0.0
    re = acc('3000-0004')
    for (aid, pid), b in sorted(hist.items(), key=lambda x: A.browse(x[0][0]).code):
        b = round(b, 2)
        if not b:
            continue
        total += b
        lines.append({'account_id': aid, 'partner_id': pid, 'name': 'Closing balance 2018', 'debit': max(-b, 0.0), 'credit': max(b, 0.0)})
    # the year's profit stays in the 2018 income statement: carried by retained earnings in the opening list
    profit = round(total, 2)
    lines.append({'account_id': re.id, 'partner_id': False, 'name': 'Profit 2018 (in the 2018 income statement)',
                  'debit': max(profit, 0.0), 'credit': max(-profit, 0.0)})
    close = V.create({'voucher_type': 'opening', 'sb_ob_kind': 'account', 'sb_ob_period': D1, 'date': D1, 'reference': 'Closing balances 2018',
                      'memo': 'Closing balances of 2018 (the opening of 2026/27 is OB %s)' % ob.name,
                      'line_ids': [Command.create(l) for l in lines]})
    close.action_post()
    olines = [{'account_id': acc(c).id, 'partner_id': partner(p).id if p else False, 'name': 'Opening balance', 'due_date': False,
               'debit': float(dr), 'credit': float(cr)} for c, p, dr, cr in OPEN]
    assert abs(sum(l['debit'] for l in olines) - 338859.47) < 0.005 and abs(sum(l['credit'] for l in olines) - 338859.47) < 0.005
    B._repost(ob, olines)
    after = snapshot()
    for k in set(before) | set(after):
        assert abs(before.get(k, 0.0) - after.get(k, 0.0)) < 0.005, '%s changed: %s -> %s' % (k, before.get(k), after.get(k))
    env.cr.commit()
    print('> %s (Closing balances 2018) and %s (Opening balances, 338,859.47) posted; every balance unchanged' % (close.name, ob.name))
print('> %s' % ob.name)
for l in ob.line_ids:
    print('>   %-10s %-45s %14s %14s' % (l.account_id.code, l.account_id.name[:45], '{:,.2f}'.format(l.debit) if l.debit else '',
                                         '{:,.2f}'.format(l.credit) if l.credit else ''))
print('>   %-56s %14s %14s' % ('Total', '{:,.2f}'.format(sum(ob.line_ids.mapped('debit'))), '{:,.2f}'.format(sum(ob.line_ids.mapped('credit')))))
