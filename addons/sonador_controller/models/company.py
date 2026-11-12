from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ctl_materiality = fields.Monetary(
        'Materiality', default=10000.0,
        help='Controller: the smallest amount worth a finding. Below it, a difference is noise.')
    ctl_start_date = fields.Date(
        'Check months from',
        help='Controller: compare margins from this date only, e.g. the day the books moved to Unio. Months before it '
             'come from the old system and are left out.')
    ctl_margin_points = fields.Float(
        'Margin change (points)', default=10.0,
        help='Controller: a month whose gross margin moves more than this many percentage points away from the three '
             'months before it is a finding.')
    ctl_overdue_days = fields.Integer(
        'Overdue after (days)', default=30, help='Controller: customer balances this many days past their due date.')
    ctl_slower_days = fields.Integer(
        'Paying slower by (days)', default=15,
        help='Controller: a customer who now takes this many days longer to pay than over the year before.')
    ctl_draft_days = fields.Integer(
        'Unposted after (days)', default=3, help='Controller: documents saved but not posted for this many days.')
    ctl_duplicate_days = fields.Integer(
        'Same amount within (days)', default=7,
        help='Controller: two bills from one supplier for the same amount this close together may be one bill entered twice.')
