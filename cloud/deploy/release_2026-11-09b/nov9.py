"""Sonador, 9 Nov 2026 (Robel): BOA 800 to petty cash; petty cash 24,930.02 and the shareholder 1,069.98 to Telebirr
(26,000); the Meskerem 2019 payroll (as Nehase); its income tax 15,500 and pension 10,080 paid from Telebirr with the
Telebirr charges (89.12 and 57.96, VAT in them). Telebirr ends at 442.10 as its statement. Safe to repeat."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
P = env['res.partner']
TAG = 'NOV9'


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def bal(code):
    return round(sum(env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)


BOA, PETTY, TELEBIRR, SH = '1104-0006', '1101-0002', '1104-0007', '1200-0002'
rob = P.search([('name', '=', 'Robel Desta')], limit=1)
mor = P.search([('name', '=', 'Ministry of Revenue')], limit=1)
poessa = P.search([('name', 'ilike', 'POESSA')], limit=1)
assert rob and mor and poessa
D = date(2026, 11, 9)
if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    assert abs(bal(PETTY) - 24130.02) < 0.005 and abs(bal(BOA) - 1102.23) < 0.005 and abs(bal(TELEBIRR) - 169.18) < 0.005, \
        'balances moved since: petty %s, BOA %s, Telebirr %s' % (bal(PETTY), bal(BOA), bal(TELEBIRR))

    def transfer(frm, to, amount, ref, memo):
        v = V.create({'voucher_type': 'transfer', 'date': D, 'reference': ref, 'memo': '%s [%s]' % (memo, TAG),
                      'cash_account_id': acc(frm).id,
                      'cash_line_ids': [Command.create({'account_id': acc(to).id, 'method': 'transfer', 'amount_currency': amount,
                                                        'reference': ref})]})
        v.action_post()
        return v
    made = [transfer(BOA, PETTY, 800.0, 'BOA to petty cash', 'BOA 800 to petty cash'),
            transfer(PETTY, TELEBIRR, 24930.02, 'Telebirr top-up', 'Petty cash to Telebirr')]
    j = V.create({'voucher_type': 'journal', 'date': D, 'reference': 'Telebirr top-up', 'memo': 'Telebirr top-up by the shareholder [%s]' % TAG,
                  'line_ids': [Command.create({'account_id': acc(TELEBIRR).id, 'name': 'Top-up', 'debit': 1069.98, 'credit': 0.0}),
                               Command.create({'account_id': acc(SH).id, 'partner_id': rob.id, 'name': 'Top-up of Telebirr', 'debit': 0.0,
                                               'credit': 1069.98})]})
    j.action_post()
    made.append(j)
    # the Meskerem 2019 payroll, as Nehase 2018 (JV/26-27/0005)
    tmpl = V.search([('name', '=', 'JV/26-27/0005')], limit=1)
    assert tmpl
    pj = V.create({'voucher_type': 'journal', 'date': date(2026, 10, 10), 'reference': 'Payroll Meskerem 2019',
                   'memo': 'Payroll Meskerem 2019 [%s]' % TAG,
                   'line_ids': [Command.create({'account_id': l.account_id.id, 'partner_id': l.partner_id.id or False,
                                                'name': l.name.replace('Nehase 2018', 'Meskerem 2019'), 'debit': l.debit, 'credit': l.credit})
                                for l in tmpl.line_ids]})
    pj.action_post()
    made.append(pj)

    def pay(partner, code, label, amount, charges, ref):
        p = V.create({'voucher_type': 'payment', 'partner_id': partner.id, 'date': D, 'reference': ref,
                      'memo': '%s, telebirr %s [%s]' % (label, ref, TAG),
                      'line_ids': [Command.create({'account_id': acc(code).id, 'name': label, 'quantity': 1, 'amount': amount})],
                      'cash_line_ids': [Command.create({'account_id': acc(TELEBIRR).id, 'method': 'transfer', 'amount_currency': amount,
                                                        'charges': charges, 'reference': ref})]})
        p.action_post()
        return p
    made.append(pay(poessa, '2100-0001', 'Pension 7% + 11% Meskerem 2019', 10080.0, 57.96, 'DK97WX92FL'))
    made.append(pay(mor, '2100-0004', 'Income tax (PAYE) Meskerem 2019', 15500.0, 89.12, 'DK96WXA0OW'))
    t = bal(TELEBIRR)
    assert abs(t - 442.10) < 0.005, 'Telebirr is %s, the statement says 442.10' % t
    env.cr.commit()
    for v in made:
        print('> %s %s' % (v.name, v.memo.replace(' [%s]' % TAG, '')))
for code, name in ((BOA, 'BOA'), (PETTY, 'Petty cash'), (TELEBIRR, 'Telebirr'), (SH, 'Shareholder receivable'),
                   ('2100-0004', 'PAYE payable'), ('2100-0001', 'Pension payable'), ('2000-0003', 'Salary payable')):
    print('> %-24s %14s' % (name, '{:,.2f}'.format(bal(code))))
