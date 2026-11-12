"""Documents made in the main system: while another system still makes a company's documents (and their numbers), the series
chosen here are not used in this one: no document is made in them (no New button, saving refused, a series taken at posting
refused too). Documents without a series follow one tick. The sync from that system still brings everything in; approvals
and everything else work as usual."""
from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError

# documents numbered by a series: the series document type of a new one (kind: goods received / issued, regular or DRM)
SERIES_MODELS = {
    'sb.voucher': lambda v: {'cash_purchase': 'payment', 'transfer': 'bank_transfer'}.get(v.get('voucher_type'), v.get('voucher_type')),
    'sb.stock.voucher': lambda v: {'transfer': 'stock_transfer'}.get(v.get('voucher_type'), v.get('voucher_type')),
    'sale.order': lambda v: 'sale_order',
    'purchase.order': lambda v: 'purchase_order',
    'sb.van.trip': lambda v: 'van_trip',
}
# documents without a series of their own
OTHER_MODELS = ('sb.payment.request', 'sb.purchase.request', 'sb.delivery.order', 'sb.gate.pass', 'sb.settlement', 'account.payment')
LOCKED = set(SERIES_MODELS) | set(OTHER_MODELS)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_only_invoices = fields.Boolean(
        'Documents are made in the main system',
        help='Another system still makes the documents: no document is made here in the series chosen below (no New button, '
             'saving refused). The sync from that system brings them in; approvals work as usual.')
    sb_lock_series_ids = fields.Many2many(
        'sb.doc.series', 'sb_company_lock_series_rel', 'company_id', 'series_id', string='Series made in the main system',
        domain="[('company_id', '=', id)]",
        help='Invoices, cash sales (each cash register, each pad), receipts, payments, store documents ... numbered in these '
             'series are made in the main system only. A series left out is made here.')
    sb_lock_other = fields.Boolean(
        'Payment and purchase requests, deliveries, gate passes, settlements', default=True,
        help='Documents without a series of their own: made in the main system only.')
    sb_lock_van = fields.Boolean(
        'Van sales invoices and receipts', default=True,
        help='The cash sales and receipts a van trip makes (sales from the van, shortages, settlement), whatever their series: '
             'made in the main system only.')

    def write(self, vals):
        res = super().write(vals)
        if {'sb_only_invoices', 'sb_lock_series_ids', 'sb_lock_other', 'sb_lock_van'} & set(vals):
            self.env.registry.clear_cache('templates')
        return res


class Base(models.AbstractModel):
    _inherit = 'base'

    def _sb_lock_applies(self):
        if self._name not in LOCKED or self.env.uid == SUPERUSER_ID or self.env.context.get('install_mode'):
            return False
        return self.env.company.sudo().sb_only_invoices

    def _sb_series_locked(self, series):
        company = self.env.company.sudo()
        # no series at all for this document: the main system makes it
        return not series or series in company.sb_lock_series_ids

    def _sb_create_locked(self, vals_list=None):
        if not self._sb_lock_applies():
            return False
        company = self.env.company.sudo()
        if self._name in OTHER_MODELS:
            return company.sb_lock_other
        Series = self.env['sb.doc.series'].sudo()
        ctx = {k[8:]: v for k, v in self.env.context.items() if k.startswith('default_')}
        for vals in vals_list or [{}]:
            vals = {**ctx, **vals}
            # a van trip's invoices and receipts follow their own tick
            if vals.get('sb_van_trip_id') and self._name == 'sb.voucher':
                if company.sb_lock_van:
                    return True
                continue
            if vals.get('sb_series_id'):
                if self._sb_series_locked(Series.browse(vals['sb_series_id'])):
                    return True
                continue
            # no series chosen yet: locked when every series of the type is (one still open is chosen, or refused at posting)
            open_ = Series.search([('company_id', '=', company.id), ('active', '=', True), ('kind', '=', vals.get('sb_doc_kind') or 'regular'),
                                   ('doc_type', '=', SERIES_MODELS[self._name](vals)), ('id', 'not in', company.sb_lock_series_ids.ids)],
                                  limit=1)
            if not open_:
                return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        if self._sb_create_locked(vals_list):
            raise UserError(self.env._('This document is made in the main system for now: make it there.'))
        return super().create(vals_list)

    def write(self, vals):
        # a series chosen or taken at posting
        if vals.get('sb_series_id') and self._name in SERIES_MODELS and self._sb_lock_applies() \
                and self._sb_series_locked(self.env['sb.doc.series'].sudo().browse(vals['sb_series_id'])):
            raise UserError(self.env._('This series is used in the main system for now: number the document there.'))
        return super().write(vals)

    def _sb_view_locked(self):
        # vouchers: by the kind the menu opens (Invoices, Receipts, Payments ...); a list of every kind keeps New
        if self._name == 'sb.voucher' and not self.env.context.get('default_voucher_type'):
            return False
        return self._sb_create_locked()

    @api.model
    def _get_view_cache_key(self, view_id=None, view_type='form', **options):
        key = super()._get_view_cache_key(view_id, view_type, **options)
        if self._name in LOCKED:
            key += (self._sb_view_locked(),)
        return key

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        # no New button where nothing can be made
        if self._name in LOCKED and view_type in ('list', 'form', 'kanban') and self._sb_view_locked():
            arch.set('create', '0')
            arch.set('duplicate', '0')
        return arch, view
