"""The flock (any job) carried through buying: a purchase request line, its purchase order line, the GRN and the bill
lines that come from it. A bill for pullets lands on the flock; feed received into the store reaches the flock when it
is issued to it."""
from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    job_id = fields.Many2one('sb.job', 'For (job / flock)', index='btree_not_null')


class _JobFromOrderLine(models.AbstractModel):
    """A GRN or bill line made from an order line takes the order line's job (flock), whichever screen made it."""
    _name = 'sb.farm.job.from.order'
    _description = 'Job from the purchase order line'

    @api.model
    def _fill(self, vals_list):
        ids = {v['sb_po_line_id'] for v in vals_list if v.get('sb_po_line_id') and not v.get('job_id')}
        if not ids:
            return
        jobs = {l.id: l.job_id.id for l in self.env['purchase.order.line'].sudo().browse(list(ids)) if l.job_id}
        for v in vals_list:
            if not v.get('job_id') and jobs.get(v.get('sb_po_line_id')):
                v['job_id'] = jobs[v['sb_po_line_id']]


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    @api.model_create_multi
    def create(self, vals_list):
        self.env['sb.farm.job.from.order']._fill(vals_list)
        return super().create(vals_list)


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    @api.model_create_multi
    def create(self, vals_list):
        self.env['sb.farm.job.from.order']._fill(vals_list)
        return super().create(vals_list)


class SbProjects(models.AbstractModel):
    _inherit = 'sb.projects'

    @api.model
    def _actuals(self, jobs):
        """A job's cost is what is spent on it, not what it buys to keep: lines on fixed or non-current asset accounts (a
        flock's pullets, equipment) are left out; their depreciation, tagged to the job, is the cost."""
        out = super()._actuals(jobs)
        keep = ('asset_non_current', 'asset_fixed')
        VL = self.env['sb.voucher.line'].sudo()
        for l in VL.search([('job_id', 'in', jobs.ids), ('voucher_id.state', '=', 'posted'), ('account_id.account_type', 'in', keep)]):
            v, vt = l.voucher_id, l.voucher_id.voucher_type
            if vt in ('bill', 'cash_purchase'):
                amt = l.price_subtotal
            elif vt == 'purchase_return':
                amt = -l.price_subtotal
            else:
                continue
            o = out[l.job_id.id]
            code = self._code_of(l.job_id, l, 'other')
            o['cost'][code.id] -= amt
            o['lines'] = [x for x in o['lines'] if not (x.get('model') == 'sb.voucher' and x.get('res_id') == v.id
                                                        and abs(x.get('cost', 0) - round(amt, 2)) < 0.01)]
        return out
