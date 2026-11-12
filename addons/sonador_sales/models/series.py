"""Sales orders: a quotation keeps its draft number; confirming takes the next number of the sales order series.
A company can instead number orders on creation, in the one series (S05078 from the first save, as Odoo 18 did):
confirming then keeps that number, and a cancelled order keeps its number too (it is never given again)."""
from odoo import api, fields, models
from odoo.exceptions import UserError

NUMBERING = [('confirm', 'Draft number, final number on confirmation'), ('create', 'Number on creation (as one series)')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_so_numbering = fields.Selection(
        NUMBERING, 'Sales order numbers', default='confirm', required=True,
        help='Draft number: a quotation is numbered Q00001 and takes the next sales order number when it is confirmed.\n'
             'Number on creation: every order takes the next number of the sales order series when it is first saved and '
             'keeps it when confirmed; a cancelled order keeps its number (a gap in the confirmed orders, as before).')


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('sale_order', 'Sales Order'), ('delivery', 'Delivery Order')],
                                ondelete={'sale_order': 'cascade', 'delivery': 'cascade'})

    @api.model
    def _sb_extra_name_tables(self):
        # delivery orders: DO/26-27/0001 (or DO/0001 without the year) unless the company adds a delivery order series
        return dict(super()._sb_extra_name_tables(), delivery=('sb_delivery_order', 'name'))

    @api.model
    def _used_numbers(self, series, key):
        res = super()._used_numbers(series, key)
        for o in self.env['sale.order'].sudo().search([('sb_series_id', '=', series.id)]):
            if series._key(o.sb_confirm_date or o.date_order.date()) == key:
                res.setdefault(series._parse(o.name), []).append(o.name)
        return res

    @api.model
    def _default_specs(self):
        return super()._default_specs() + [(('sale_order', 'regular'), 'SO/{fy}/', 'sale_order', 'name')]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sb_series_id = fields.Many2one('sb.doc.series', 'Series / pad', copy=False, check_company=True,
                                   domain="[('doc_type', '=', 'sale_order'), ('active', '=', True)]")
    sb_pad_number = fields.Char('Pad number', copy=False, help='Sales order pad number: must be the next of the series.')
    sb_quotation_number = fields.Char('Quotation number', copy=False, readonly=True)
    sb_confirm_date = fields.Date('Numbered on', copy=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """A company numbering sales orders on creation: the order takes its sales order number now (kept on confirmation)."""
        Series = self.env['sb.doc.series']
        for vals in vals_list:
            if vals.get('name', 'New') not in ('New', '/', False, self.env._('New')):
                continue
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if company.sb_so_numbering != 'create':
                continue
            series = Series._for('sale_order', company=company, series=Series.browse(vals.get('sb_series_id')))
            if not series:
                continue
            day = fields.Date.context_today(self)
            vals.update(name=series._take(day, manual=vals.get('sb_pad_number'), what=self.env._('Sales order')),
                        sb_series_id=series.id, sb_confirm_date=day)
        return super().create(vals_list)

    def action_confirm(self):
        res = super().action_confirm()
        for o in self.filtered(lambda x: x.state == 'sale' and not x.sb_confirm_date):
            series = self.env['sb.doc.series']._for('sale_order', company=o.company_id, series=o.sb_series_id)
            if not series:
                continue
            day = fields.Date.context_today(o)
            o.write({'sb_quotation_number': o.name, 'name': series._take(day, manual=o.sb_pad_number, what=self.env._('Sales order')),
                     'sb_series_id': series.id, 'sb_confirm_date': day})
        return res

    def unlink(self):
        if self.filtered('sb_confirm_date'):
            raise UserError(self.env._('A numbered sales order cannot be deleted: cancel it (the number stays used).'))
        return super().unlink()
