# In HQ (my): no books any more. The test entries go, the real one now lives in the company books.
P = env['ir.config_parameter'].sudo()
P.set_param('saas.books_db', 'sonador')
V = env['sb.voucher'].sudo().with_context(force_delete=True)
for v in V.search([], order='id'):
    moves = env['account.move'].sudo().search(['|', ('id', '=', v.move_id.id), ('ref', 'ilike', v.name or '-')]) if v.move_id else env['account.move']
    for m in moves:
        if m.state == 'posted':
            m.button_draft()
        m.with_context(force_delete=True).unlink()
    v.write({'state': 'draft'})
    v.unlink()
print('LEFT vouchers', env['sb.voucher'].search_count([]), 'posted entries', env['account.move'].search_count([('state', '=', 'posted')]))
# Sonador Systems is Unio's own company: free, active, never billed or locked
t = env['saas.tenant'].search([('subdomain', '=', 'sonador')], limit=1)
free = env['saas.plan'].search([('code', '=', 'free')], limit=1)
if t and free:
    t.write({'plan_id': free.id, 'state': 'active', 'paid_until': False})
    env['saas.service']._set_lock(t, False)
    print('SONADOR', t.state, t.plan_id.name)
# HQ keeps no books: the Accounting app is hidden here
menu = env.ref('sonador_books.menu_sb_root', raise_if_not_found=False)
if menu:
    menu.active = False
    print('ACCOUNTING HIDDEN')
env.cr.commit()
