from odoo import fields, models


class ResUsersSettings(models.Model):
    _inherit = 'res.users.settings'

    # the person's app order: a JSON list of the apps' xml ids, read by the apps bar and the apps grid
    homemenu_config = fields.Char('App order')
