"""A purchase request line can be for a flock (any job): the order line made from it carries it on to the GRN and bill."""
from odoo import fields, models


class SbPurchaseRequestLine(models.Model):
    _inherit = 'sb.purchase.request.line'

    job_id = fields.Many2one('sb.job', 'For (job / flock)', index='btree_not_null')


class SbPurchaseRequest(models.Model):
    _inherit = 'sb.purchase.request'

    def action_create_rfq(self):
        res = super().action_create_rfq()
        for l in self.line_ids.filtered(lambda x: x.job_id and x.purchase_line_id and not x.purchase_line_id.job_id):
            l.purchase_line_id.job_id = l.job_id
        return res
