# In the company books (sonador): the one real entry from HQ, the Ethio Telecom bill paid from petty cash.
from odoo import Command
A = env['account.account']
V = env['sb.voucher']
if not V.search_count([('reference', '=', 'Invoice 000113471410')]):
    partner = env['res.partner'].search([('name', '=', 'Ethio Telecom')], limit=1) or env['res.partner'].create({'name': 'Ethio Telecom', 'is_company': True})
    v = V.create({'voucher_type': 'payment', 'partner_id': partner.id, 'date': '2026-10-27', 'reference': 'Invoice 000113471410',
                  'memo': 'Ethio Telecom September 2026 bill - invoice 000113471410', 'cash_account_id': A._sb_find('211200')[:1].id,
                  'line_ids': [Command.create({'name': 'Telecommunication expense (incl. excise and disaster fund) - Sept 2026',
                                               'account_id': A._sb_find('625800')[:1].id, 'amount': 979.66, 'partner_id': partner.id}),
                               Command.create({'name': 'Input VAT - Ethio Telecom invoice 000113471410',
                                               'account_id': A._sb_find('221200')[:1].id, 'amount': 140.32, 'partner_id': partner.id})]})
    v.action_post()
    print('MOVED', v.name, v.amount_total)
env.cr.commit()
