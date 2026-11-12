"""Sonador's books, year end 7 Jul 2026 (Sene 30, 2018): the money moved into Telebirr during the year and never recorded,
so the new year (from 8 Jul 2026) opens on the bank balances Robel gave: CBE 2,205.99, Dashen 124.67, BOA 102.23,
Telebirr 26.48. Telebirr paid the year's taxes; what it received came from the banks (down to their real balances), the
cash on hand (all of it) and the shareholder (the rest, which lowers what the shareholder owes the company).
Two journal vouchers, dated 7 Jul 2026: the year's closing (depreciation, profit tax) and Telebirr. Checks every balance before it keeps anything."""
from datetime import date

from odoo import Command

D = date(2026, 7, 7)
REAL = {'1104-0004': 2205.99, '1104-0005': 124.67, '1104-0006': 102.23, '1104-0007': 26.48}
CASH, SHARE, TELEBIRR = '1101-0001', '1200-0002', '1104-0007'
env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def bal(code):
    lines = env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted'), ('date', '<=', D)])
    return round(sum(lines.mapped('balance')), 2)


V = env['sb.voucher']
# the year's closing entries that change the balance sheet: depreciation of the computers (20%), the profit tax of the
# year as declared (Schedule C line 240), and last year's withholding credit set against last year's unpaid profit tax
CLOSE = [('8200-0041', 'Depreciation of computers and IT, 20%', 4792.16, 0.0),
         ('1701-0005', 'Depreciation of computers and IT, 20%', 0.0, 4792.16),
         ('9000-0003', 'Business profit tax 2018 (Schedule C, line 240)', 76380.91, 0.0),
         ('2100-0006', 'Business profit tax 2018 (Schedule C, line 240)', 0.0, 76380.91),
         ('2100-0006', "Last year's withholding credit against last year's profit tax", 13041.48, 0.0),
         ('1401-0002', "Last year's withholding credit against last year's profit tax", 0.0, 13041.48)]
if not V.search_count([('voucher_type', '=', 'journal'), ('date', '=', D), ('reference', '=', 'YE2018-CLOSE')]):
    before = bal('2100-0006')
    assert abs(before + 13041.48) < 0.005, 'profit tax payable before closing is %s, expected -13,041.48' % before
    c = V.create({'voucher_type': 'journal', 'date': D, 'reference': 'YE2018-CLOSE', 'memo': 'Year end 2018: depreciation and profit tax',
                  'line_ids': [Command.create({'account_id': acc(a).id, 'name': n, 'debit': dr, 'credit': cr}) for a, n, dr, cr in CLOSE]})
    c.action_post()
    assert abs(bal('2100-0006') + 76380.91) < 0.005
    print('> %s posted on %s: depreciation 4,792.16, profit tax 76,380.91, old withholding 13,041.48 set off' % (c.name, D))
if V.search_count([('voucher_type', '=', 'journal'), ('date', '=', D), ('reference', '=', 'YE2018-TELEBIRR')]):
    print('> already done')
else:
    tb_in = round(REAL[TELEBIRR] - bal(TELEBIRR), 2)
    lines, moved = [], 0.0
    for code in ('1104-0004', '1104-0005', '1104-0006'):
        out = round(bal(code) - REAL[code], 2)
        assert out >= 0, '%s: the books show less than the bank (%s)' % (code, out)
        lines.append((code, 'Transferred to Telebirr during the year', 0.0, out))
        moved += out
    cash = bal(CASH)
    lines.append((CASH, 'Cash on hand paid into Telebirr during the year', 0.0, cash))
    moved += cash
    rest = round(tb_in - moved, 2)
    assert rest >= 0, 'banks and cash moved more than Telebirr received (%s)' % rest
    lines.append((SHARE, 'Paid into Telebirr by the shareholder', 0.0, rest))
    lines.append((TELEBIRR, 'Top-ups during the year (taxes paid from Telebirr)', tb_in, 0.0))
    v = V.create({'voucher_type': 'journal', 'date': D, 'reference': 'YE2018-TELEBIRR',
                  'memo': 'Year end 2018: money moved into Telebirr during the year, not recorded at the time',
                  'line_ids': [Command.create({'account_id': acc(c).id, 'name': n, 'debit': dr, 'credit': cr}) for c, n, dr, cr in lines]})
    v.action_post()
    for code, real in REAL.items():
        assert abs(bal(code) - real) < 0.005, '%s is %s, should be %s' % (code, bal(code), real)
    assert abs(bal(CASH)) < 0.005
    env.cr.commit()
    print('> %s posted on %s' % (v.name, D))
    for c, n, dr, cr in lines:
        print('>   %-10s %-55s %14s %14s' % (c, n, '{:,.2f}'.format(dr) if dr else '', '{:,.2f}'.format(cr) if cr else ''))
env.cr.commit()
print('> OPENING BALANCES 8 Jul 2026 (Hamle 1, 2018)')
PL = ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
agg, profit = {}, 0.0
for l in env['account.move.line'].search([('parent_state', '=', 'posted'), ('date', '<=', D), ('company_id', '=', env.company.id)]):
    if l.account_id.account_type in PL:
        profit += l.balance
    else:
        agg[l.account_id] = agg.get(l.account_id, 0.0) + l.balance
profit = round(profit, 2)
tot = 0.0
for a in sorted(agg, key=lambda a: a.code):
    if abs(agg[a]) >= 0.005:
        tot += agg[a]
        print('>   %-10s %-45s %16s' % (a.code, a.name[:45], '{:,.2f}'.format(agg[a])))
print('>   %-10s %-45s %16s' % ('', 'Profit of years to 2018 (to retained earnings)', '{:,.2f}'.format(profit)))
print('>   check (should be 0.00): %s' % '{:,.2f}'.format(tot + profit))
