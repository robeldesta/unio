from odoo import fields, models

from odoo.addons.sonador_books.models.report_catalog import E

EXTRA = [
    E('undelivered', 'ar', 'Invoiced, Not Yet Delivered', 'Goods sold and invoiced that are still in the yard, per customer and item.', 1),
    E('delivery_register', 'ar', 'Delivery Register', 'Every delivery batch: customer, vehicle, driver, items and loading / gate / unloading times.', 1),
    E('open_orders', 'ar', 'Open Sales Orders', 'Sales orders with what is ordered, invoiced, delivered and still to invoice.', 1),
]
BLURBS = {
    'undelivered': 'Sold and costed, still waiting in the yard. You owe these goods to the customer.',
    'delivery_register': 'What left the yard, on which vehicle, when.',
    'open_orders': 'Orders not yet fully invoiced or delivered.',
}


class SbReportSales(models.AbstractModel):
    _inherit = 'sb.report'

    def _extra_catalog(self):
        return super()._extra_catalog() + EXTRA

    def _present(self, doc):
        super()._present(doc)
        if not doc.get('blurb') and doc['key'] in BLURBS:
            doc['blurb'] = BLURBS[doc['key']]

    def _report_undelivered(self, opts):
        rows, grand = [], 0.0
        dos = self.env['sb.delivery.order'].search([('company_id', '=', self.env.company.id), ('sb_to_deliver', '=', True)], order='partner_id, date')
        for d in dos:
            if not self._search_ok(opts, d.name, d.partner_id.name, d.sale_order_id.name):
                continue
            gid = 'd%d' % d.id
            lines = d.line_ids.filtered(lambda l: l.qty_remaining > 1e-6)
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': d.name,
                         'values': {'ref': d.name, 'date': fields.Date.to_string(d.date), 'who': d.partner_id.name,
                                    'so': d.sale_order_id.name or '', 'invoices': ', '.join(d.voucher_ids.filtered(lambda v: v.state == 'posted').mapped('name')),
                                    'days': (fields.Date.context_today(self) - d.date).days},
                         'open': {'model': 'sb.delivery.order', 'id': d.id}})
            for l in lines:
                grand += l.qty_remaining
                rows.append({'id': 'l%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'invoiced': l.qty_invoiced, 'delivered': l.qty_delivered,
                                        'remaining': l.qty_remaining, 'uom': l.uom_id.name}})
        cols = [{'key': 'ref', 'label': 'Delivery order / Item', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'so', 'label': 'Sales order', 'type': 'text'},
                {'key': 'invoices', 'label': 'Invoices', 'type': 'text'}, {'key': 'days', 'label': 'Days waiting', 'type': 'number_int'},
                {'key': 'invoiced', 'label': 'Invoiced', 'type': 'number'}, {'key': 'delivered', 'label': 'Delivered', 'type': 'number'},
                {'key': 'remaining', 'label': 'To deliver', 'type': 'number'}, {'key': 'uom', 'label': 'Unit', 'type': 'text'}]
        return self._doc('undelivered', opts, cols, rows, 'Open delivery orders today', 'Invoiced, Not Yet Delivered')

    def _report_delivery_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        methods = dict(self.env['sb.delivery.batch']._fields['delivery_method'].selection)
        rows = []
        for b in self.env['sb.delivery.batch'].search([('company_id', '=', self.env.company.id), ('state', '=', 'done'),
                                                       ('date', '>=', df), ('date', '<=', dt)], order='date, name'):
            if not self._search_ok(opts, b.reference, b.partner_id.name, b.vehicle, b.driver):
                continue
            gid = 'b%d' % b.id
            t = lambda f: fields.Datetime.context_timestamp(self, b[f]).strftime('%d %b %H:%M') if b[f] else ''  # noqa: E731
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': b.reference,
                         'values': {'ref': b.reference, 'date': fields.Date.to_string(b.date), 'who': b.partner_id.name,
                                    'method': methods.get(b.delivery_method), 'vehicle': b.vehicle or '', 'driver': b.driver or '',
                                    'loaded': t('loaded_time'), 'gate': t('gate_time'), 'unloaded': t('unloading_time'), 'qty': b.total_qty},
                         'open': {'model': 'sb.delivery.batch', 'id': b.id}})
            for l in b.line_ids:
                rows.append({'id': 'bl%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'qty': l.quantity}})
        cols = [{'key': 'ref', 'label': 'Batch / Product', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'method', 'label': 'Method', 'type': 'text'},
                {'key': 'vehicle', 'label': 'Vehicle', 'type': 'text'}, {'key': 'driver', 'label': 'Driver', 'type': 'text'},
                {'key': 'loaded', 'label': 'Loaded Time', 'type': 'text'}, {'key': 'gate', 'label': 'Gate Exit Time', 'type': 'text'},
                {'key': 'unloaded', 'label': 'Unloading Time', 'type': 'text'}, {'key': 'qty', 'label': 'Qty', 'type': 'number'}]
        return self._doc('delivery_register', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Delivery Register')

    def _report_open_orders(self, opts):
        rows = []
        for so in self.env['sale.order'].search([('company_id', '=', self.env.company.id), ('state', '=', 'sale')], order='date_order'):
            if so.sb_status == 'done' or not self._search_ok(opts, so.name, so.partner_id.name, so.client_order_ref):
                continue
            gid = 's%d' % so.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': so.name,
                         'values': {'ref': so.name, 'date': fields.Date.to_string(so.date_order.date()), 'who': so.partner_id.name,
                                    'status': dict(so._fields['sb_status']._description_selection(self.env)).get(so.sb_status),
                                    'amount': so.amount_total},
                         'open': {'model': 'sale.order', 'id': so.id}})
            for l in so.order_line.filtered(lambda x: x.product_id and not x.display_type):
                rows.append({'id': 'sl%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'ordered': l.product_uom_qty, 'invoiced': l.sb_qty_invoiced,
                                        'delivered': l.sb_qty_delivered, 'to_invoice': l.sb_qty_to_invoice}})
        cols = [{'key': 'ref', 'label': 'Order / Item', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'status', 'label': 'Progress', 'type': 'text'},
                {'key': 'ordered', 'label': 'Ordered', 'type': 'number'}, {'key': 'invoiced', 'label': 'Invoiced', 'type': 'number'},
                {'key': 'delivered', 'label': 'Delivered', 'type': 'number'}, {'key': 'to_invoice', 'label': 'To invoice', 'type': 'number'},
                {'key': 'amount', 'label': 'Order total', 'type': 'monetary'}]
        return self._doc('open_orders', opts, cols, rows, 'Confirmed orders not yet fully invoiced or delivered', 'Open Sales Orders')

    # ------------------------------------------------------------------ registers and item analysis
    def _so_domain(self, opts, df, dt, model='sale.order'):
        return [('company_id', '=', self.env.company.id), ('date_order', '>=', '%s 00:00:00' % df), ('date_order', '<=', '%s 23:59:59' % dt)]

    def _report_so_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows, grand = [], {'untaxed': 0.0, 'total': 0.0}
        status = dict(self.env['sale.order']._fields['sb_status']._description_selection(self.env))
        for o in self.env['sale.order'].search(self._so_domain(opts, df, dt), order='date_order, name'):
            if not self._search_ok(opts, o.name, o.partner_id.name, o.client_order_ref):
                continue
            st = 'Quotation' if o.state in ('draft', 'sent') else ('Cancelled' if o.state == 'cancel' else status.get(o.sb_status, o.state))
            if o.state not in ('cancel',):
                grand['untaxed'] += o.amount_untaxed
                grand['total'] += o.amount_total
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name,
                         'values': {'date': fields.Date.to_string(o.date_order.date()), 'ref': o.name, 'who': o.partner_id.name, 'po': o.client_order_ref or '',
                                    'rep': o.user_id.name or '', 'status': st, 'untaxed': o.amount_untaxed, 'total': o.amount_total},
                         'open': {'model': 'sale.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, ref='Total')})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Order', 'type': 'text'}, {'key': 'who', 'label': 'Customer', 'type': 'text'},
                {'key': 'po', 'label': 'Customer PO', 'type': 'text'}, {'key': 'rep', 'label': 'Salesperson', 'type': 'text'},
                {'key': 'status', 'label': 'Status', 'type': 'text'}, {'key': 'untaxed', 'label': 'Before tax', 'type': 'monetary'},
                {'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        return self._doc('so_register', opts, cols, rows, self._period_name(df, dt), 'Quotation / Sales Order Register')

    def _report_items_sold(self, opts):
        return self._items_report('items_sold', 'Items Sold to Customers', 'customer', opts, ('sale', 'cash_sale', 'sale_return'), ('sale_return',))

    def _report_backorder(self, opts):
        dt = self._d(opts['date_to'])
        rows, grand = [], 0.0
        for o in self.env['sale.order'].search([('company_id', '=', self.env.company.id), ('state', '=', 'sale'), ('date_order', '<=', '%s 23:59:59' % dt)],
                                               order='date_order'):
            for l in o.order_line.filtered(lambda x: x.product_id and x.product_id.is_storable):
                left = l.product_uom_qty - l.sb_qty_delivered
                if left <= 1e-6 or not self._search_ok(opts, o.name, o.partner_id.name, l.product_id.display_name):
                    continue
                value = left * l.price_unit
                grand += value
                rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name,
                             'values': {'date': fields.Date.to_string(o.date_order.date()), 'ref': o.name, 'who': o.partner_id.name, 'item': l.product_id.display_name,
                                        'ordered': l.product_uom_qty, 'delivered': l.sb_qty_delivered, 'left': left, 'value': value,
                                        'days': (dt - o.date_order.date()).days},
                             'open': {'model': 'sale.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'ref': 'Total', 'value': grand}})
        cols = [{'key': 'date', 'label': 'Ordered', 'type': 'date'}, {'key': 'ref', 'label': 'Order', 'type': 'text'}, {'key': 'who', 'label': 'Customer', 'type': 'text'},
                {'key': 'item', 'label': 'Item', 'type': 'text'}, {'key': 'ordered', 'label': 'Ordered', 'type': 'number'},
                {'key': 'delivered', 'label': 'Delivered', 'type': 'number'}, {'key': 'left', 'label': 'Still to deliver', 'type': 'number'},
                {'key': 'value', 'label': 'Value', 'type': 'monetary'}, {'key': 'days', 'label': 'Days waiting', 'type': 'number_int'}]
        return self._doc('backorder', opts, cols, rows, 'As at %s' % dt, 'Sales Backorder Report')

    def _report_cust_tx_history(self, opts):
        return self._tx_history('cust_tx_history', 'Customer Transaction History', opts, ('sale', 'cash_sale', 'sale_return', 'receipt'))

    def _report_picklist(self, opts):
        """What the store must pick: items on delivery orders still to deliver, per order, with the stock on hand."""
        orders = self.env['sb.delivery.order'].search([('company_id', '=', self.env.company.id), ('sb_to_deliver', '=', True)], order='date, id')
        rows, need = [], {}
        for o in orders:
            if not self._search_ok(opts, o.name, o.partner_id.name, o.sale_order_id.name):
                continue
            todo = o.line_ids.filtered(lambda l: l.qty_remaining > 1e-6)
            if not todo:
                continue
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'group', 'label': o.name, 'expandable': True,
                         'values': {'ref': '%s - %s' % (o.name, o.partner_id.name), 'date': fields.Date.to_string(o.date)}})
            for l in todo:
                p = l.product_id
                on_hand = p.with_context(warehouse_id=o.warehouse_id.id).qty_available
                need[p] = need.get(p, 0.0) + l.qty_remaining
                rows.append({'id': 'l%d' % l.id, 'parent': 'o%d' % o.id, 'level': 1, 'kind': 'line', 'label': p.display_name,
                             'warning': on_hand < need[p],
                             'values': {'ref': p.display_name, 'date': fields.Date.to_string(o.date), 'qty': l.qty_remaining, 'uom': p.uom_id.name,
                                        'on_hand': on_hand}, 'open': {'model': 'sb.delivery.order', 'id': o.id}})
        cols = [{'key': 'ref', 'label': 'Delivery order / item', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'qty', 'label': 'To pick', 'type': 'number'}, {'key': 'uom', 'label': 'Unit', 'type': 'text'},
                {'key': 'on_hand', 'label': 'On hand', 'type': 'number'}]
        return self._doc('picklist', opts, cols, rows, 'Open delivery orders (red: not enough in the store)', 'Picklist Report')

    def _proposals(self, opts, states):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        return self.env['sale.order'].search([('company_id', '=', self.env.company.id), ('state', 'in', states),
                                              ('date_order', '>=', '%s 00:00:00' % df), ('date_order', '<=', '%s 23:59:59' % dt)], order='date_order')

    def _report_proposal_register(self, opts):
        """Quotations (proposals) in the period and what became of them."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        status = {'draft': 'Open', 'sent': 'Sent, waiting', 'sale': 'Accepted', 'cancel': 'Lost / cancelled'}
        rows, grand = [], 0.0
        for o in self._proposals(opts, ('draft', 'sent', 'sale', 'cancel')):
            if not self._search_ok(opts, o.name, o.partner_id.name, o.client_order_ref):
                continue
            grand += o.amount_total
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name,
                         'values': {'date': fields.Date.to_string(o.date_order.date()), 'ref': o.name, 'who': o.partner_id.name,
                                    'rep': o.user_id.name or '', 'status': status.get(o.state, o.state), 'amount': o.amount_total},
                         'open': {'model': 'sale.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Proposal', 'type': 'text'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'rep', 'label': 'Salesperson', 'type': 'text'},
                {'key': 'status', 'label': 'Status', 'type': 'text'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('proposal_register', opts, cols, rows, self._period_name(df, dt), 'Proposal Register')

    def _report_proposal_report(self, opts):
        """Accepted proposals: amount, invoiced so far and what is left to bill."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows, grand = [], {'amount': 0.0, 'billed': 0.0, 'left': 0.0}
        for o in self._proposals(opts, ('sale',)):
            if not self._search_ok(opts, o.name, o.partner_id.name):
                continue
            billed = sum(l.sb_qty_invoiced * l.price_unit * (1 - (l.discount or 0) / 100.0) for l in o.order_line if l.product_id)
            untaxed = o.amount_untaxed
            vals = {'date': fields.Date.to_string(o.date_order.date()), 'ref': o.name, 'who': o.partner_id.name, 'amount': untaxed,
                    'billed': billed, 'left': max(untaxed - billed, 0.0)}
            for k in grand:
                grand[k] += vals[k]
            rows.append({'id': 'o%d' % o.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': o.name, 'values': vals,
                         'open': {'model': 'sale.order', 'id': o.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, date='Total')})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Order', 'type': 'text'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'amount', 'label': 'Amount (ex VAT)', 'type': 'monetary'},
                {'key': 'billed', 'label': 'Invoiced', 'type': 'monetary'}, {'key': 'left', 'label': 'Left to invoice', 'type': 'monetary'}]
        return self._doc('proposal_report', opts, cols, rows, self._period_name(df, dt), 'Proposal Report')
