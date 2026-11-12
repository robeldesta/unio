"""AfyaSoft (Ghion Investment), 10 Nov 2026 (Robel, from the product pack): the address is Kool Industry Park, Nyamitanga
Division, Mbarara City South; sales@ghioninvestment.com. Both companies. Safe to repeat."""
for c in env['res.company'].sudo().search([]):
    vals = {'street': 'Kool Industry Park', 'street2': 'Nyamitanga Division', 'city': 'Mbarara City South'}
    if c.name == 'AfyaSoft':
        vals['email'] = 'sales@ghioninvestment.com'
    c.write(vals)
    p = c.partner_id
    print('> %s: %s, %s, %s; %s' % (c.name, p.street, p.street2, p.city, p.email))
env.cr.commit()
