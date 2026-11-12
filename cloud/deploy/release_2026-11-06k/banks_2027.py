"""Sonador's 2026/27 bank movements from the CBE statement (to Aug 18) and the Dashen statement (to Sep 29), and the
Telebirr movements Robel listed. Bank transfers as Bank Transfer vouchers (charges on each); the rest as journals.
CBE ends at 491.42 and Dashen at 42.49, as the statements; checked before anything is kept. Safe to repeat."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
TAG = 'BANKS2027'


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def bal(code, day):
    return round(sum(env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted'),
                                                      ('date', '<=', day)]).mapped('balance')), 2)


CBE, DASHEN, TELEBIRR, PETTY, CHAPA = '1104-0004', '1104-0005', '1104-0007', '1101-0002', '1104-0003'
SH, FEES = '1200-0002', '8200-0034'
rob = env['res.partner'].search([('name', '=', 'Robel Desta')], limit=1)
assert rob

# (date, from, to, amount, charges, reference)
TRANSFERS = [
    (date(2026, 7, 15), DASHEN, TELEBIRR, 17580.00, 18.00, '449FTTO2619600WH'),
    (date(2026, 7, 16), DASHEN, TELEBIRR, 15500.00, 18.00, '449FTTO2619700WJ'),
    (date(2026, 7, 17), DASHEN, TELEBIRR, 1000.00, 6.00, '449FTTO261980001'),
    (date(2026, 7, 17), DASHEN, TELEBIRR, 3000.00, 6.00, '449FTTO2619800WH'),
    (date(2026, 7, 18), DASHEN, TELEBIRR, 1500.00, 6.00, '449FTTO261990005'),
    (date(2026, 7, 20), DASHEN, TELEBIRR, 1200.00, 6.00, '449FTTO2620100WK'),
    (date(2026, 8, 1), DASHEN, PETTY, 10000.00, 0.0, '449FTWD262130002'),
    (date(2026, 8, 3), DASHEN, CBE, 1000.00, 4.80, '449IOOT2621501Yn'),
    (date(2026, 8, 6), CBE, DASHEN, 33080.00, 165.00, 'FT26218PXPLZ'),
    (date(2026, 8, 6), DASHEN, TELEBIRR, 34000.00, 12.00, '449TEAD262180001'),
    (date(2026, 8, 12), DASHEN, TELEBIRR, 300.00, 6.00, '449FTTO262240001'),
    (date(2026, 8, 15), DASHEN, PETTY, 1000.00, 0.0, '449FTWD2622700WH'),
    (date(2026, 8, 21), DASHEN, PETTY, 2500.00, 0.0, '449FTWD262330003'),
    (date(2026, 8, 22), DASHEN, PETTY, 1500.00, 0.0, '449FTWD26234012X'),
    (date(2026, 8, 24), DASHEN, TELEBIRR, 1400.00, 6.00, '449FTTO262360006'),
    (date(2026, 8, 24), TELEBIRR, DASHEN, 500.00, 0.0, 'DHO04Q3VCO'),
    (date(2026, 8, 24), DASHEN, TELEBIRR, 500.00, 6.00, '449FTTO2623600WL'),
    (date(2026, 9, 23), TELEBIRR, DASHEN, 4000.00, 0.0, 'DIN51ZY2RL'),
    (date(2026, 9, 23), DASHEN, PETTY, 2000.00, 0.0, '449FTWD262660001'),
    (date(2026, 9, 26), DASHEN, PETTY, 1500.00, 0.0, '449FTWD262690004'),
    (date(2026, 9, 27), TELEBIRR, DASHEN, 700.00, 0.0, 'DIR86Z9B34'),
    (date(2026, 9, 29), DASHEN, PETTY, 9500.00, 0.0, '449FTWD262720001'),
    (date(2026, 10, 30), CHAPA, TELEBIRR, 1000.00, 0.0, 'Chapa: realtors.et trials'),
]
# (date, reference, memo, [(account, partner, label, debit, credit)])
JOURNALS = [
    (date(2026, 8, 4), 'CBE AB1464839', 'Cheque to Robel Desta (shareholder)',
     [(SH, rob, 'Cheque AB1464839', 64000.00, 0), (CBE, None, 'Cheque AB1464839', 0, 64000.00)]),
    (date(2026, 8, 4), 'FT26216D3WDD', 'Jobale paid 104,330.43 for CSI0000108 (booked 104,336.43)',
     [(FEES, None, 'Jobale short payment / transfer charge', 6.00, 0), (CBE, None, 'Jobale short payment / transfer charge', 0, 6.00)]),
    (date(2026, 8, 18), 'CBE AB1464843', 'Cheque to Robel Desta (shareholder)',
     [(SH, rob, 'Cheque AB1464843', 9800.00, 0), (CBE, None, 'Cheque AB1464843', 0, 9800.00)]),
    (date(2026, 8, 3), '449FTWD262150003', 'Dashen ADVANCE PAYMENT to the shareholder',
     [(SH, rob, 'Advance payment', 20000.00, 0), (DASHEN, None, 'Advance payment', 0, 20000.00)]),
    (date(2026, 9, 27), '449FTWD262710001', 'Dashen fund transfer to 5045781338036 (shareholder)',
     [(SH, rob, 'Fund transfer to 5045781338036', 1000.00, 0), (DASHEN, None, 'Fund transfer to 5045781338036', 0, 1000.00)]),
    (date(2026, 9, 29), '449FTWD2627201Yn', 'Dashen fund transfer to 5045781338036 (shareholder)',
     [(SH, rob, 'Fund transfer to 5045781338036', 400.00, 0), (DASHEN, None, 'Fund transfer to 5045781338036', 0, 400.00)]),
    (date(2026, 8, 6), 'Payroll Hamle 2018', 'Salaries of Hamle 2018 (CPV/26-27/0004, 0005) were paid from Dashen, not CBE',
     [(CBE, None, 'Paid from Dashen', 39380.00, 0), (DASHEN, None, 'Salaries Hamle 2018', 0, 39380.00)]),
    (date(2026, 9, 10), 'Payroll Nehase 2018', 'Salaries of Nehase 2018 (CPV/26-27/0008, 0009) were paid from Dashen, not CBE',
     [(CBE, None, 'Paid from Dashen', 39380.00, 0), (DASHEN, None, 'Salaries Nehase 2018', 0, 39380.00)]),
    (date(2026, 9, 28), '449FTWD2627101Yn', 'Nehase 2018 salary plus bonus: the bonus',
     [('8200-0003', None, 'Bonus (Nehase 2018 salary plus bonus)', 29880.00, 0), (DASHEN, None, 'Bonus', 0, 29880.00)]),
    (date(2026, 8, 20), '449IOOT2623200WH', 'Dashen charge on the Sene 2018 salary transfer',
     [(FEES, None, 'Transfer charge', 47.38, 0), (DASHEN, None, 'Transfer charge', 0, 47.38)]),
    (date(2026, 9, 28), 'CSI0000111', 'CSI0000111 was paid into Dashen (booked to Bank)',
     [(DASHEN, None, 'CSI0000111 into Dashen', 56000.00, 0), (CHAPA, None, 'CSI0000111 into Dashen', 0, 56000.00)]),
    (date(2026, 9, 23), 'Phone bill', 'Company phone bill paid from Telebirr',
     [('8200-0036', None, 'Company phone bill', 648.40, 0), (TELEBIRR, None, 'Company phone bill', 0, 648.40)]),
    (date(2026, 9, 9), 'Telebirr top-up', 'Telebirr top-up by the shareholder',
     [(TELEBIRR, None, 'Top-up', 95000.00, 0), (SH, rob, 'Top-up of Telebirr', 0, 95000.00)]),
    (date(2026, 10, 10), 'Telebirr top-up', 'Telebirr top-up by the shareholder',
     [(TELEBIRR, None, 'Top-up', 40000.00, 0), (SH, rob, 'Top-up of Telebirr', 0, 40000.00)]),
    (date(2026, 11, 6), 'Telebirr top-up', 'Telebirr top-up by the shareholder',
     [(TELEBIRR, None, 'Top-up', 5975.00, 0), (SH, rob, 'Top-up of Telebirr', 0, 5975.00)]),
]

if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    made = []
    for d, frm, to, amount, charges, ref in TRANSFERS:
        v = V.create({'voucher_type': 'transfer', 'date': d, 'reference': ref, 'memo': 'Bank transfer [%s]' % TAG,
                      'cash_account_id': acc(frm).id,
                      'cash_line_ids': [Command.create({'account_id': acc(to).id, 'method': 'transfer', 'amount_currency': amount,
                                                        'reference': ref})],
                      'line_ids': [Command.create({'account_id': acc(FEES).id, 'name': 'Bank charges', 'amount': charges})] if charges else []})
        v.action_post()
        made.append(v)
    for d, ref, memo, lines in JOURNALS:
        v = V.create({'voucher_type': 'journal', 'date': d, 'reference': ref, 'memo': '%s [%s]' % (memo, TAG),
                      'line_ids': [Command.create({'account_id': acc(a).id, 'partner_id': p.id if p else False, 'name': n,
                                                   'debit': dr, 'credit': cr}) for a, p, n, dr, cr in lines]})
        v.action_post()
        made.append(v)
    c, dsh = bal(CBE, date(2026, 8, 18)), bal(DASHEN, date(2026, 9, 29))
    assert abs(c - 491.42) < 0.005, 'CBE at Aug 18 is %s, the statement says 491.42' % c
    assert abs(dsh - 42.49) < 0.005, 'Dashen at Sep 29 is %s, the statement says 42.49' % dsh
    env.cr.commit()
    print('> %d vouchers posted (%d bank transfers, %d journals)' % (len(made), len(TRANSFERS), len(JOURNALS)))
today = date(2026, 11, 6)
for code, name in ((CBE, 'CBE'), (DASHEN, 'Dashen'), ('1104-0006', 'BOA'), (TELEBIRR, 'Telebirr'), (PETTY, 'Petty cash'),
                   (CHAPA, 'Bank (Chapa)'), (SH, 'Shareholder receivable')):
    print('> %-24s %14s' % (name, '{:,.2f}'.format(bal(code, today))))
