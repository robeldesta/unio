"""AfyaSoft (Ghion Investment), 10 Nov 2026: the seller's details on invoices, from the URA e-receipt: TIN 1047587863,
BRN 80034191433252, Namirembe Bakuli, Mengo, Kampala Central Division. Both companies are the one legal entity. Safe to repeat."""
C = env['res.company'].sudo()
LEGAL = 'Ghion Investment and General Trading Limited'
for c in C.search([]):
    vals = {'vat': '1047587863', 'company_registry': '80034191433252'}
    if c.name == 'AfyaSoft':
        vals.update(street='00 Namirembe Bakuli, Mengo', street2='Kampala Central Division', city='Kampala',
                    report_footer='<p>AfyaSoft is a brand of %s</p>' % LEGAL)
    c.write(vals)
    p = c.partner_id
    print('> %s: TIN %s, BRN %s, %s, %s, %s' % (c.name, c.vat, c.company_registry, p.street or '', p.street2 or '', p.city or ''))
env.cr.commit()
