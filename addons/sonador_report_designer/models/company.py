from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_rep_reader_ids = fields.Many2many(
        'res.users', 'sb_rep_reader_rel', 'company_id', 'user_id', string='Read and write every report',
        help='The general manager: reads every report (besides those sent to them) and may write any of them.')
