"""Sonador, 10 Nov 2026 (Robel): Bank Suspense 1102-0001 holds the Chapa money (208.37 available), so it is a bank / cash
account: it shows with the account balances on Business status and can be the source of a transfer (Chapa to Telebirr).
Safe to repeat."""
a = env['account.account'].with_company(env.company).search([('code', '=', '1102-0001'), ('company_ids', 'in', env.company.id)], limit=1)
assert a
if a.account_type != 'asset_cash':
    a.account_type = 'asset_cash'
env.cr.commit()
b = round(sum(env['account.move.line'].search([('account_id', '=', a.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)
print('> %s %s: %s, balance %s' % (a.code, a.name, a.account_type, '{:,.2f}'.format(b)))
