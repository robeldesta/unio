"""Sonador's Telebirr (organization account 938090) from its statement, Jul 8 to Nov 6, 2026: 26.48 -> 169.18.
  - the Dashen "TRANSFER TO TELEBIRR - +251911472891" were to Robel's own Telebirr, not the company's: the nine bank
    transfers are voided and booked as payments to the shareholder (with their Dashen charges);
  - the Telebirr charges on the tax payments (fee + its VAT, all as charges) on their CPVs: CPV/26-27/0006 (Aug 6) and
    CPV/26-27/0010 (Oct 8); CPV/26-27/0007 has them already;
  - the VAT of Meskerem 2019 (9/2026) paid on Nov 6, 7,500 + 43.12 charges: a new CPV;
  - Robel's 40,000 top-up was on Oct 8 (not Oct 10).
Telebirr ends at 169.18 and Dashen stays at 42.49; checked before anything is kept."""
from datetime import date

from odoo import Command

env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_company(env.company)
V = env['sb.voucher']
TAG = 'TELEBIRR2027'


def acc(code):
    a = A.search([('code', 'in', (code, code.replace('-', ''))), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, 'no account %s' % code
    return a


def bal(code, day):
    return round(sum(env['account.move.line'].search([('account_id', '=', acc(code).id), ('parent_state', '=', 'posted'),
                                                      ('date', '<=', day)]).mapped('balance')), 2)


DASHEN, TELEBIRR, SH, FEES, VAT = '1104-0005', '1104-0007', '1200-0002', '8200-0034', '2100-0003'
rob = env['res.partner'].search([('name', '=', 'Robel Desta')], limit=1)
mor = env['res.partner'].search([('name', '=', 'Ministry of Revenue')], limit=1)
assert rob and mor
PERSONAL = ['449FTTO2619600WH', '449FTTO2619700WJ', '449FTTO261980001', '449FTTO2619800WH', '449FTTO261990005',
            '449FTTO2620100WK', '449FTTO262240001', '449FTTO262360006', '449FTTO2623600WL']

if V.search_count([('memo', '=like', '%[' + TAG + ']')]):
    print('> already done')
else:
    # 1. Dashen to Robel's own Telebirr
    for ref in PERSONAL:
        t = V.search([('voucher_type', '=', 'transfer'), ('reference', '=', ref), ('state', '=', 'posted')], limit=1)
        assert t, 'no transfer %s' % ref
        amount = sum(t.cash_line_ids.mapped('amount_currency'))
        fee = sum(t.line_ids.mapped('amount'))
        t.action_void()
        j = V.create({'voucher_type': 'journal', 'date': t.date, 'reference': ref,
                      'memo': 'Dashen transfer to Robel Desta\'s Telebirr +251911472891 [%s]' % TAG,
                      'line_ids': [Command.create({'account_id': acc(SH).id, 'partner_id': rob.id, 'name': 'To Robel Desta (Telebirr +251911472891)',
                                                   'debit': amount, 'credit': 0.0}),
                                   Command.create({'account_id': acc(FEES).id, 'name': 'Dashen transfer charge', 'debit': fee, 'credit': 0.0}),
                                   Command.create({'account_id': acc(DASHEN).id, 'name': 'Transfer to +251911472891', 'debit': 0.0,
                                                   'credit': round(amount + fee, 2)})]})
        j.action_post()
        print('> %s voided, %s: %s to Robel Desta' % (t.name, j.name, '{:,.2f}'.format(amount)))
    # 2. Telebirr charges on the tax payments (fee and its VAT)
    for name, charges in (('CPV/26-27/0006', 190.20), ('CPV/26-27/0010', 224.70)):
        v = V.search([('name', '=', name), ('state', '=', 'posted')], limit=1)
        assert v and not any(v.cash_line_ids.mapped('charges')), '%s missing or already has charges' % name
        v.action_edit()
        v.cash_line_ids[:1].charges = charges
        v.memo = ((v.memo or '') + ' [%s]' % TAG).strip()
        v.action_post()
        print('> %s: Telebirr charges %s added' % (name, '{:,.2f}'.format(charges)))
    # 3. the VAT of 9/2026 paid on Nov 6
    p = V.create({'voucher_type': 'payment', 'partner_id': mor.id, 'date': date(2026, 11, 6), 'reference': 'DK66RSGWS0',
                  'memo': 'VAT 9/2026 (Meskerem 2019), telebirr DK66RSGWS0 [%s]' % TAG,
                  'line_ids': [Command.create({'account_id': acc(VAT).id, 'name': 'VAT 9/2026', 'quantity': 1, 'amount': 7500.0})],
                  'cash_line_ids': [Command.create({'account_id': acc(TELEBIRR).id, 'method': 'transfer', 'amount_currency': 7500.0,
                                                    'charges': 43.12, 'reference': 'DK66RSGWS0'})]})
    p.action_post()
    print('> %s: VAT 9/2026 7,500.00 + charges 43.12 from Telebirr' % p.name)
    # 4. the 40,000 top-up on Oct 8
    old = V.search([('voucher_type', '=', 'journal'), ('date', '=', date(2026, 10, 10)), ('reference', '=', 'Telebirr top-up'),
                    ('state', '=', 'posted')], limit=1)
    assert old, 'no top-up of Oct 10'
    old.action_void()
    new = V.create({'voucher_type': 'journal', 'date': date(2026, 10, 8), 'reference': 'DJ83JQX4H9',
                    'memo': 'Telebirr top-up by the shareholder [%s]' % TAG,
                    'line_ids': [Command.create({'account_id': acc(TELEBIRR).id, 'name': 'Top-up', 'debit': 40000.0, 'credit': 0.0}),
                                 Command.create({'account_id': acc(SH).id, 'partner_id': rob.id, 'name': 'Top-up of Telebirr',
                                                 'debit': 0.0, 'credit': 40000.0})]})
    new.action_post()
    print('> %s voided, %s: 40,000.00 top-up on Oct 8' % (old.name, new.name))
    tb, dsh = bal(TELEBIRR, date(2026, 11, 6)), bal(DASHEN, date(2026, 9, 29))
    assert abs(tb - 169.18) < 0.005, 'Telebirr is %s, the statement says 169.18' % tb
    assert abs(dsh - 42.49) < 0.005, 'Dashen is %s, the statement says 42.49' % dsh
    env.cr.commit()
print('> Telebirr %s · Dashen %s · Shareholder receivable %s' % (
    '{:,.2f}'.format(bal(TELEBIRR, date(2026, 11, 6))), '{:,.2f}'.format(bal(DASHEN, date(2026, 11, 6))),
    '{:,.2f}'.format(bal(SH, date(2026, 11, 6)))))
