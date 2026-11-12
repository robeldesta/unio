"""Reasons for stock adjustments (Peachtree's "Reason to adjust"): each has a code that numbers its documents
(DAM/26-27/0001) and a default account for the gain or loss."""
from odoo import api, fields, models

DEFAULT_REASONS = [
    # code, name, account field on the company (None = count differences account)
    ('CC', 'Physical count', None),
    ('DAM', 'Damage', None),
    ('SDV', 'Store damage', None),
    ('EXP', 'Expired', None),
    ('REP', 'Replacement to customer', None),
    ('DT', 'Demo trial / sample', None),
    ('CON', 'Unrecorded consignment', None),
    ('WRG', 'Wrong delivery', None),
    ('EXHB', 'Exhibition', None),
    ('FND', 'Found stock', None),
]


class SbAdjustReason(models.Model):
    _name = 'sb.adjust.reason'
    _description = 'Stock Adjustment Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    code = fields.Char(required=True, size=6, help='Prefix of the document numbers, e.g. DAM gives DAM/26-27/0001.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    account_id = fields.Many2one('account.account', 'Gain / loss account', check_company=True,
                                 help='Charged for losses and credited for gains. Empty: the stock count differences account.')
    note = fields.Char()

    _code_uniq = models.Constraint('unique (company_id, code)', 'Each reason needs its own code.')

    @api.model
    def _sb_defaults(self, company):
        for i, (code, name, _f) in enumerate(DEFAULT_REASONS):
            if not self.with_context(active_test=False).search_count([('company_id', '=', company.id), ('code', '=', code)]):
                self.create({'code': code, 'name': name, 'sequence': (i + 1) * 10, 'company_id': company.id})
