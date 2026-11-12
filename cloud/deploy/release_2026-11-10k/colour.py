"""AfyaSoft: the brand colour (AfyaSoft teal) on printed documents. Safe to repeat."""
for c in env['res.company'].sudo().search([]):
    c.primary_color = '#127C86'
    print('> %s: colour %s' % (c.name, c.primary_color))
env.cr.commit()
