from odoo import fields


def post_init_hook(env):
    """Ethiopian public holidays for last, this and next year."""
    y = fields.Date.context_today(env.user).year
    for company in env['res.company'].search([]):
        for year in (y - 1, y, y + 1):
            env['sb.holidays'].ensure_year(year, company)
