# the accounts added on 5 Nov (CBE, Dashen, BOA, Telebirr, Shareholder Receivable) written like the rest of the chart: 1104-0004
c = env.company
if c.sb_account_code_style != 'dash':
    c.sb_account_code_style = 'dash'
    c.action_sb_apply_code_style()
    env.cr.commit()
print('> codes:', ', '.join(env['account.account'].search([('name', 'in', ('CBE', 'Dashen', 'BOA', 'Telebirr', 'Shareholder Receivable'))]).mapped('code')))
