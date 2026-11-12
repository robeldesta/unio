"""Strict series for goods received (GRN) and goods issued (GIV): regular and direct raw material (DRM)."""
from odoo import api, fields, models
from odoo.exceptions import UserError


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('grn', 'Goods Received'), ('giv', 'Goods Issued (GIV)'),
                                               ('adjustment', 'Stock Adjustment'), ('stock_transfer', 'Stock Transfer'),
                                               ('gate_pass', 'Gate Pass')],
                                ondelete={'grn': 'cascade', 'giv': 'cascade', 'adjustment': 'cascade', 'stock_transfer': 'cascade',
                                          'gate_pass': 'cascade'})

    @api.model
    def _sb_extra_name_tables(self):
        # numbered PREFIX/26-27/0001 (or without the year) unless the company adds a series: an adjustment by the series
        # whose prefix is its reason's code (DT/ for demo trials), a gate pass by the gate pass series
        return dict(super()._sb_extra_name_tables(), adjustment=('sb_stock_voucher', 'name'),
                    stock_transfer=('sb_stock_voucher', 'name'), gate_pass=('sb_gate_pass', 'name'))

    @api.model
    def _used_numbers(self, series, key):
        res = super()._used_numbers(series, key)
        for v in self.env['sb.stock.voucher'].sudo().search([('sb_series_id', '=', series.id), ('state', 'in', ('posted', 'void'))]):
            if series._key(v.date) == key and v.name not in (False, '/'):
                res.setdefault(series._parse(v.name), []).append(v.name)
        return res

    def _sb_type_names(self):
        """Goods received / issued numbered before the series or by another system (Odoo 18's GRV/03183, GIV-2706, DRM GRN
        2971), of the same kind: names with DRM are direct raw material. Issues written by a sale (SALE/FS...) are not counted."""
        res = super()._sb_type_names()
        if self.doc_type not in ('grn', 'giv'):
            return res
        codes = {self._sb_doc_code(self.prefix).replace('DRM', '')} | ({'GRN', 'GRV'} if self.doc_type == 'grn' else {'GIV'})
        self.env['sb.stock.voucher'].flush_model(['name', 'date', 'sb_doc_kind', 'voucher_type'])
        self.env.cr.execute("SELECT name, date, sb_doc_kind FROM sb_stock_voucher WHERE company_id = %s AND voucher_type = %s "
                            "AND name != '/'", (self.company_id.id, self.doc_type))
        for name, day, kind in self.env.cr.fetchall():
            code = self._sb_doc_code(name)
            if code.replace('DRM', '') in codes and ('DRM' in code or kind == 'drm') == (self.kind == 'drm'):
                res.append((name, day))
        return res

    @api.model
    def _default_specs(self):
        return super()._default_specs() + [
            (('grn', 'regular'), 'GRN/{fy}/', 'sb_stock_voucher', 'name'),
            (('grn', 'drm'), 'DRM/GRN/{fy}/', 'sb_stock_voucher', 'name'),
            (('giv', 'regular'), 'GIV/{fy}/', 'sb_stock_voucher', 'name'),
            (('giv', 'drm'), 'DRM/GIV/{fy}/', 'sb_stock_voucher', 'name')]


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_doc_kind = fields.Selection([('regular', 'Regular'), ('drm', 'Direct raw material (DRM)')], 'Kind', default='regular',
                                   help='GRN / GIV: regular or direct raw material, each numbered in its own series.')
    sb_series_id = fields.Many2one('sb.doc.series', 'Series / pad', check_company=True, copy=False,
                                   domain="[('doc_type', '=', voucher_type), ('kind', '=', sb_doc_kind), ('active', '=', True)]")
    # worded like the vouchers: a document not posted yet is "New" and "Not posted", not "/" and "Draft"
    state = fields.Selection(selection=[('draft', 'Not posted'), ('posted', 'Posted'), ('void', 'Void')])
    sb_number = fields.Char('Number', compute='_compute_sb_number')
    sb_pad_number = fields.Char('Pad number', copy=False,
                                help='The number printed on the GRN / GIV pad. It must be the next number of the series; '
                                     'leave it empty to take the next number automatically.')

    @api.depends('name')
    def _compute_sb_number(self):
        for v in self:
            v.sb_number = v.name if v.name not in (False, '/') else self.env._('New')

    @api.depends('name', 'voucher_type')
    def _compute_display_name(self):
        """A document not numbered yet is named by its kind ("New Goods Issued (GIV)"), never "/"."""
        super()._compute_display_name()
        for v in self.filtered(lambda x: x.name in (False, '/')):
            v.display_name = self.env._('New %s', v._type_label())

    @api.onchange('sb_doc_kind', 'voucher_type')
    def _onchange_sb_doc_kind(self):
        if self.sb_series_id and self.sb_series_id.kind != self.sb_doc_kind:
            self.sb_series_id = False

    def _sb_series_doc_type(self):
        """The series document type this voucher is numbered in when the company has such a series (else None)."""
        return {'grn': 'grn', 'giv': 'giv', 'adjustment': 'adjustment', 'transfer': 'stock_transfer'}.get(self.voucher_type)

    def _sb_find_series(self):
        doc_type = self._sb_series_doc_type()
        if not doc_type:
            return self.env['sb.doc.series']
        Series = self.env['sb.doc.series']
        if doc_type == 'adjustment':
            # a reason numbered in its own series: the one whose prefix is the reason's code (DT/00027 for a demo trial)
            if not self.reason_id:
                return Series
            return Series.search([('doc_type', '=', 'adjustment'), ('company_id', '=', self.company_id.id),
                                  ('active', '=', True)]).filtered(lambda s: Series._sb_doc_code(s.prefix) == self.reason_id.code.upper())[:1]
        chosen = self.sb_series_id if self.sb_series_id.doc_type == doc_type else False
        return Series._for(doc_type, self.sb_doc_kind or 'regular', self.company_id, chosen)

    def _next_name(self):
        series = self._sb_find_series()
        if series:
            self.sb_series_id = series
            label = '%s%s' % (self._type_label(), ' (DRM)' if self.sb_doc_kind == 'drm' else '')
            return series._take(self.date, manual=self.sb_pad_number, what=label)
        return super()._next_name()
