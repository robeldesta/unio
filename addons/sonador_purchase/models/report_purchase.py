from odoo import fields, models

from odoo.addons.sonador_books.models.report_catalog import E

EXTRA = [
    E('po_status', 'ap', 'Purchase Orders: Ordered, Received, Invoiced', 'Every open purchase order line: ordered, received on GRNs, '
      'invoiced and still to come, with the GRNs.', 1),
    E('po_payments', 'ap', 'Purchase Orders: Paid and Owed', 'Per order: total, invoiced, paid (advances included), '
      'paid ahead of the goods and still to pay.', 1),
]
BLURBS = {
    'po_status': 'What each supplier still has to deliver, line by line.',
    'po_payments': 'Money and goods per order: an advance not covered by goods is a risk to follow up.',
}


class SbReportPurchase(models.AbstractModel):
    _inherit = 'sb.report'

    def _extra_catalog(self):
        return super()._extra_catalog() + EXTRA

    def _present(self, doc):
        super()._present(doc)
        if not doc.get('blurb') and doc['key'] in BLURBS:
            doc['blurb'] = BLURBS[doc['key']]

    def _po_domain(self, opts):
        dom = [('company_id', '=', self.env.company.id), ('state', '=', 'purchase')]
        if not opts.get('all_orders'):
            dom.append('|')
            dom.append(('sb_receipt_status', 'in', ('none', 'partial')))
            dom.append(('sb_bill_status', '!=', 'full'))
        return dom

    def _report_po_status(self, opts):
        rows = []
        status = dict(self.env['purchase.order']._fields['sb_receipt_status'].selection)
        today = fields.Date.context_today(self)
        for o in self.env['purchase.order'].search(self._po_domain(opts), order='date_planned, name'):
            if not self._search_ok(opts, o.name, o.partner_id.name, o.partner_ref):
                continue
            gid = 'o%d' % o.id
            due = o.date_planned.date() if o.date_planned else False
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': o.name,
                         'values': {'ref': o.name, 'who': o.partner_id.name, 'date': fields.Date.to_string(o.date_order.date()),
                                    'due': fields.Date.to_string(due) if due else '', 'late': (today - due).days if due and due < today and o.sb_receipt_status in ('none', 'partial') else '',
                                    'status': status.get(o.sb_receipt_status),
                                    'grns': ', '.join(o.sb_grn_ids.filtered(lambda g: g.state == 'posted').mapped('name'))},
                         'open': {'model': 'purchase.order', 'id': o.id}})
            for l in o.order_line.filtered(lambda x: x.product_id and not x.display_type):
                rows.append({'id': 'l%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'ordered': l.product_qty, 'received': l.sb_qty_received,
                                        'to_receive': l.sb_qty_to_receive, 'billed': l.sb_qty_billed, 'uom': l.product_uom_id.name}})
        cols = [{'key': 'ref', 'label': 'Order / Item', 'type': 'text'}, {'key': 'who', 'label': 'Supplier', 'type': 'text'},
                {'key': 'date', 'label': 'Ordered', 'type': 'date'}, {'key': 'due', 'label': 'Expected', 'type': 'date'},
                {'key': 'late', 'label': 'Days late', 'type': 'number_int'}, {'key': 'status', 'label': 'Receipt', 'type': 'text'},
                {'key': 'ordered', 'label': 'Ordered', 'type': 'number'}, {'key': 'received', 'label': 'Received', 'type': 'number'},
                {'key': 'to_receive', 'label': 'Still to come', 'type': 'number'}, {'key': 'billed', 'label': 'Invoiced', 'type': 'number'},
                {'key': 'uom', 'label': 'Unit', 'type': 'text'}, {'key': 'grns', 'label': 'GRNs', 'type': 'text'}]
        return self._doc('po_status', opts, cols, rows, 'Open purchase orders today', 'Purchase Orders: Ordered, Received, Invoiced')

    def _report_po_payments(self, opts):
        rows = []
        grand = {'total': 0.0, 'billed': 0.0, 'paid': 0.0, 'advance': 0.0, 'due': 0.0}
        pay = dict(self.env['purchase.order']._fields['sb_payment_status'].selection)
        rec = dict(self.env['purchase.order']._fields['sb_receipt_status'].selection)
        dom = [('company_id', '=', self.env.company.id), ('state', '=', 'purchase')]
        for o in self.env['purchase.order'].search(dom, order='partner_id, date_order'):
            if not self._search_ok(opts, o.name, o.partner_id.name, o.partner_ref):
                continue
            if not opts.get('all_orders') and o.sb_payment_status == 'paid' and o.sb_receipt_status == 'full' \
                    and o.sb_bill_status == 'full':
                continue
            total = o.currency_id._convert(o.amount_total, o.company_currency_id, o.company_id, fields.Date.context_today(self))
            vals = {'ref': o.name, 'who': o.partner_id.name, 'receipt': rec.get(o.sb_receipt_status), 'payment': pay.get(o.sb_payment_status),
                    'total': total, 'billed': o.sb_amount_billed, 'paid': o.sb_amount_paid, 'advance': o.sb_amount_advance,
                    'due': o.sb_amount_due}
            for k in grand:
                grand[k] += vals[k]
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name, 'values': vals,
                         'open': {'model': 'purchase.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, ref='Total')})
        cols = [{'key': 'ref', 'label': 'Order', 'type': 'text'}, {'key': 'who', 'label': 'Supplier', 'type': 'text'},
                {'key': 'receipt', 'label': 'Goods', 'type': 'text'}, {'key': 'payment', 'label': 'Payment', 'type': 'text'},
                {'key': 'total', 'label': 'Order total', 'type': 'monetary'}, {'key': 'billed', 'label': 'Invoiced', 'type': 'monetary'},
                {'key': 'paid', 'label': 'Paid', 'type': 'monetary'}, {'key': 'advance', 'label': 'Paid ahead of purchase vouchers', 'type': 'monetary'},
                {'key': 'due', 'label': 'Still to pay', 'type': 'monetary'}]
        return self._doc('po_payments', opts, cols, rows, 'Open purchase orders today', 'Purchase Orders: Paid and Owed')

    def _report_po_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows, grand = [], {'untaxed': 0.0, 'total': 0.0}
        for o in self.env['purchase.order'].search([('company_id', '=', self.env.company.id), ('date_order', '>=', '%s 00:00:00' % df),
                                                    ('date_order', '<=', '%s 23:59:59' % dt)], order='date_order, name'):
            if not self._search_ok(opts, o.name, o.partner_id.name, o.partner_ref):
                continue
            if o.state != 'cancel':
                grand['untaxed'] += o.amount_untaxed
                grand['total'] += o.amount_total
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name,
                         'values': {'date': fields.Date.to_string(o.date_order.date()), 'ref': o.name, 'who': o.partner_id.name, 'their': o.partner_ref or '',
                                    'cur': o.currency_id.name, 'state': dict(o._fields['state']._description_selection(self.env)).get(o.state),
                                    'receipt': dict(o._fields['sb_receipt_status']._description_selection(self.env)).get(o.sb_receipt_status) if 'sb_receipt_status' in o._fields else '',
                                    'billing': dict(o._fields['sb_bill_status']._description_selection(self.env)).get(o.sb_bill_status) if 'sb_bill_status' in o._fields else '',
                                    'untaxed': o.amount_untaxed, 'total': o.amount_total},
                         'open': {'model': 'purchase.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, ref='Total')})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Order', 'type': 'text'}, {'key': 'who', 'label': 'Supplier', 'type': 'text'},
                {'key': 'their', 'label': 'Their ref.', 'type': 'text'}, {'key': 'cur', 'label': 'Currency', 'type': 'text'}, {'key': 'state', 'label': 'Status', 'type': 'text'},
                {'key': 'receipt', 'label': 'Received', 'type': 'text'}, {'key': 'billing', 'label': 'Invoiced', 'type': 'text'},
                {'key': 'untaxed', 'label': 'Before tax', 'type': 'monetary'}, {'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        return self._doc('po_register', opts, cols, rows, self._period_name(df, dt), 'Purchase Order Register')

    def _report_items_purchased(self, opts):
        return self._items_report('items_purchased', 'Items Purchased from Vendors', 'supplier', opts, ('bill', 'cash_purchase', 'purchase_return'), ('purchase_return',))

    def _report_vend_history(self, opts):
        return self._tx_history('vend_history', 'Vendor Transaction History', opts, ('bill', 'cash_purchase', 'purchase_return', 'payment'))
