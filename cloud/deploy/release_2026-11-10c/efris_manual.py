"""AfyaSoft (Ghion Investment), 10 Nov 2026 (Robel): until URA gives the system-to-system device and key, EFRIS invoices are
issued in the URA portal and their fiscal document no. and verification code are typed on our invoice, which is then
titled Invoice. The test numbers of the simulation are taken off the invoices (they were never fiscal). Safe to repeat."""
for c in env['res.company'].sudo().search([]).filtered(lambda c: c.country_id.code == 'UG'):
    c.sb_efris_mode = 'manual'
    print('> %s: EFRIS %s' % (c.name, dict(c._fields['sb_efris_mode'].selection)[c.sb_efris_mode]))
V = env['sb.voucher'].sudo()
test = V.search([('sb_efris_test', '=', True)])
test.write({'sb_efris_fdn': False, 'sb_efris_code': False, 'sb_efris_qr': False, 'sb_efris_test': False, 'sb_efris_at': False,
            'sb_efris_invoice_id': False, 'sb_efris_error': False, 'sb_efris_state': 'manual'})
V.search([('sb_efris_state', '=', 'waiting')]).write({'sb_efris_state': 'manual'})
print('> test numbers taken off %d invoices; %d to type in from URA: %s' % (
    len(test), V.search_count([('sb_efris_state', '=', 'manual')]), ', '.join(V.search([('sb_efris_state', '=', 'manual')], order='name').mapped('name'))))
env.cr.commit()
