"""Purchase orders: the request for quotation keeps its draft number (RFQ/...); approval takes the next number of the
purchase order series. A company can instead number orders on creation, in the one series (P005822 from the first
save, as Odoo 18 did): approval then keeps that number, and a cancelled order keeps its number (never given again)."""
from odoo import api, fields, models
from odoo.exceptions import UserError

NUMBERING = [('confirm', 'Draft number (RFQ), final number on approval'), ('create', 'Number on creation (as one series)')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_po_numbering = fields.Selection(
        NUMBERING, 'Purchase order numbers', default='confirm', required=True,
        help='Draft number: a request for quotation is numbered RFQ/26-27/0001 and takes the next purchase order number when '
             'it is approved.\nNumber on creation: every order takes the next number of the purchase order series when it '
             'is first saved and keeps it when approved; a cancelled order keeps its number (a gap, as before).')


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('purchase_order', 'Purchase Order')], ondelete={'purchase_order': 'cascade'})

    @api.model
    def _used_numbers(self, series, key):
        res = super()._used_numbers(series, key)
        for o in self.env['purchase.order'].sudo().search([('sb_series_id', '=', series.id)]):
            if series._key(o.sb_confirm_date) == key:
                res.setdefault(series._parse(o.name), []).append(o.name)
        return res

    @api.model
    def _default_specs(self):
        return super()._default_specs() + [(('purchase_order', 'regular'), 'PO/{fy}/', 'purchase_order', 'name')]


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sb_series_id = fields.Many2one('sb.doc.series', 'Series / pad', copy=False, check_company=True,
                                   domain="[('doc_type', '=', 'purchase_order'), ('active', '=', True)]")
    sb_pad_number = fields.Char('Pad number', copy=False, help='Purchase order pad number: must be the next of the series.')
    sb_rfq_number = fields.Char('RFQ number', copy=False, readonly=True)
    sb_confirm_date = fields.Date('Numbered on', copy=False, readonly=True)

    def _sb_number_on_create(self, vals):
        """vals of a new order, numbered now when its company numbers purchase orders on creation (else unchanged)."""
        company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
        if company.sb_po_numbering != 'create':
            return False
        Series = self.env['sb.doc.series']
        series = Series._for('purchase_order', company=company, series=Series.browse(vals.get('sb_series_id')))
        if not series:
            return False
        day = fields.Date.context_today(self)
        vals.update(name=series._take(day, manual=vals.get('sb_pad_number'), what=self.env._('Purchase order')),
                    sb_series_id=series.id, sb_confirm_date=day)
        return True

    def button_approve(self, force=False):
        res = super().button_approve(force=force)
        for o in self.filtered(lambda x: x.state == 'purchase' and not x.sb_confirm_date):
            series = self.env['sb.doc.series']._for('purchase_order', company=o.company_id, series=o.sb_series_id)
            if not series:
                continue
            day = fields.Date.context_today(o)
            o.write({'sb_rfq_number': o.name, 'name': series._take(day, manual=o.sb_pad_number, what=self.env._('Purchase order')),
                     'sb_series_id': series.id, 'sb_confirm_date': day})
        return res

    def unlink(self):
        if self.filtered('sb_confirm_date'):
            raise UserError(self.env._('A numbered purchase order cannot be deleted: cancel it (the number stays used).'))
        return super().unlink()
