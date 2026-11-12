"""Sales app (the commercial department) and its share of the Inventory app (dispatch)."""
from odoo import api, fields, models
from odoo.exceptions import UserError

S = 'sonador_sales.%s'
B = 'sonador_books.%s'
SALE_SIDE = ('sale', 'cash_sale', 'sale_return')


def tile(label, icon, action, create=False, hint=''):
    return {'label': label, 'icon': icon, 'action': action, 'create': create, 'hint': hint}


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def _sb_require_post(self):
        # a sales clerk posts what the Sales app makes: invoices, cash sales, returns
        if self.voucher_type in SALE_SIDE and self.env.user.has_group('sonador_sales.group_sb_sales_user'):
            return
        return super()._sb_require_post()


class SbDeliveryBatch(models.Model):
    _inherit = 'sb.delivery.batch'

    gate_pass_id = fields.Many2one('sb.gate.pass', 'Gate pass', copy=False, readonly=True)

    def action_gate_pass(self):
        """Gate pass for the truck: items loaded, vehicle and driver of this batch."""
        self.ensure_one()
        if self.gate_pass_id and self.gate_pass_id.state != 'cancel':
            return self.gate_pass_id._open()
        if self.state == 'cancel':
            raise UserError(self.env._('The batch is cancelled.'))
        d = self.delivery_id
        sources = ', '.join(filter(None, [self.display_name, d.sale_order_id.name] +
                                   d.voucher_ids.filtered(lambda v: v.state == 'posted').mapped('name')))
        g = self.env['sb.gate.pass']._from_lines({
            'purpose': 'delivery', 'date': self.date, 'warehouse_id': d.warehouse_id.id, 'partner_id': self.partner_id.id,
            'destination': (d.notes or '').split('\n')[0][:120], 'vehicle': self.vehicle, 'driver': self.driver, 'source': sources,
        }, [(l.product_id, l.quantity, l.uom_id, False) for l in self.line_ids])
        self.gate_pass_id = g
        return g._open()


class SbAppHome(models.AbstractModel):
    _inherit = 'sb.app.home'

    @api.model
    def _home_sales(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        base = [('company_id', '=', company.id)]
        SO = self.env['sale.order']
        to_invoice = SO.search(base + [('state', '=', 'sale'), ('sb_status', '=', 'to_invoice')])
        V = self.env['sb.voucher']
        invoiced = sum(V.search(base + [('state', '=', 'posted'), ('voucher_type', 'in', ('sale', 'cash_sale')),
                                        ('date', '>=', month_start), ('date', '<=', today)]).mapped('amount_total')) - \
            sum(V.search(base + [('state', '=', 'posted'), ('voucher_type', '=', 'sale_return'),
                                 ('date', '>=', month_start), ('date', '<=', today)]).mapped('amount_total'))
        money = lambda x: '{:,.2f}'.format(x)  # noqa: E731
        queues = [
            self._queue('quotes', 'Open quotations', 'fa-file-text-o', 'sale.order', base + [('state', 'in', ('draft', 'sent'))],
                        lambda o: o.name, lambda o: o.partner_id.name, lambda o: money(o.amount_total), order='date_order desc'),
            self._queue('to_invoice', 'Orders to invoice', 'fa-file-text', 'sale.order', [('id', 'in', to_invoice.ids)],
                        lambda o: o.name, lambda o: o.partner_id.name, lambda o: money(o.amount_total), tone='warn',
                        order='date_order'),
            self._queue('drafts', 'Invoices not posted', 'fa-pencil-square-o', 'sb.voucher',
                        base + [('state', '=', 'draft'), ('voucher_type', 'in', SALE_SIDE), ('partner_id', '!=', False)],
                        lambda v: dict(v._fields['voucher_type'].selection).get(v.voucher_type), lambda v: v.partner_id.name,
                        lambda v: money(v.amount_total), tone='warn', order='date, id'),
            self._queue('to_deliver', 'Invoiced, not yet delivered', 'fa-truck', 'sb.delivery.order', base + [('sb_to_deliver', '=', True)],
                        lambda d: d.name, lambda d: d.partner_id.name, lambda d: d.date_ec, order='date'),
        ]
        return {
            'title': 'Sales', 'subtitle': 'Customers, orders, invoicing and deliveries', 'icon': 'fa-line-chart',
            'kpis': [{'label': 'Sales this month', 'value': money(invoiced)},
                     {'label': 'Orders to invoice', 'value': len(to_invoice)}],
            'flows': [
                [tile('Quotation', 'fa-file-text-o', S % 'action_sb_quotations', True)],
                [tile('Sales Order', 'fa-list-alt', S % 'action_sb_sale_orders', True)],
                [tile('Sales Invoice', 'fa-file-text', B % 'action_sb_sale', True, 'on account'),
                 tile('Cash Sale', 'fa-money', B % 'action_sb_cash_sale', True, 'paid on the spot')],
                [tile('Delivery Order', 'fa-truck', S % 'action_sb_delivery_orders', False, 'batched loading')],
            ],
            'more': [tile('Customers', 'fa-users', B % 'action_sb_customers'),
                     tile('Prices', 'fa-tags', S % 'action_sb_price_rules'),
                     tile('Price Levels', 'fa-sort-amount-desc', S % 'action_sb_price_levels'),
                     tile('Returns / Credit Notes', 'fa-undo', B % 'action_sb_sale_return')],
            'reports': ['open_orders', 'undelivered', 'delivery_register', 'cust_sales_history', 'sj'],
            'queues': queues,
        }

    @api.model
    def _home_store(self):
        data = super()._home_store()
        base = [('company_id', '=', self.env.company.id)]
        data['flows'][-1].insert(0, tile('Delivery Orders', 'fa-truck', S % 'action_sb_delivery_orders', False, 'load and dispatch'))
        data['queues'].insert(0, self._queue(
            'loading', 'Draft delivery batches', 'fa-truck', 'sb.delivery.batch', base + [('state', '=', 'draft')],
            lambda b: b.reference, lambda b: ' · '.join(filter(None, [b.partner_id.name, b.vehicle, b.driver])),
            lambda b: '{:,.2f}'.format(b.total_qty), tone='warn', order='date'))
        data['queues'].insert(1, self._queue(
            'to_dispatch', 'Invoiced, waiting for dispatch', 'fa-clock-o', 'sb.delivery.order', base + [('sb_to_deliver', '=', True)],
            lambda d: d.name, lambda d: d.partner_id.name, lambda d: d.date_ec, order='date'))
        data['reports'] = data['reports'] + ['undelivered', 'delivery_register']
        return data
