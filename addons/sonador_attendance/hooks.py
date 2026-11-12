from odoo import fields


def post_init_hook(env):
    """Count absences from today on: nobody is absent on days before attendance was kept here."""
    today = fields.Date.context_today(env.user)
    env['hr.employee'].sudo().with_context(active_test=False).search([('sb_att_since', '=', False)]).write({'sb_att_since': today})
