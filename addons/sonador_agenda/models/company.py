from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    agenda_min_amount = fields.Monetary(
        'Smallest amount', default=10000.0,
        help='Agenda: bills and customer balances below this are left off, so the agenda keeps to what matters.')
    agenda_horizon = fields.Integer('Look ahead (days)', default=30, help='Agenda: how far ahead to show what is coming.')
