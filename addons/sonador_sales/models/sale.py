"""Sales orders on Odoo Sales, invoiced through vouchers.

Odoo's own delivery pickings are switched off: stock leaves when the Sales Invoice / Cash Sale is posted
(cost of sales on the same voucher and date). Goods to be delivered later go to the warehouse's 'Awaiting dispatch'
location (sold and costed, no longer available) and reach the customer through delivery order batches.
"""

from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.move_guard import controlled


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    sb_dispatch_location_id = fields.Many2one('stock.location', 'Awaiting dispatch location', copy=False)

    def _sb_dispatch_location(self):
        self.ensure_one()
        if not self.sb_dispatch_location_id:
            # sold goods still in the yard: outside available stock (a customer-side location), counted per delivery order
            parent = self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
            # set up once, whoever invoices first (a sales clerk cannot edit warehouses)
            self.sudo().sb_dispatch_location_id = self.env['stock.location'].sudo().create({
                'name': '%s: Awaiting dispatch' % self.code, 'usage': 'customer', 'location_id': parent.id if parent else False,
                'company_id': self.company_id.id})
        elif self.sb_dispatch_location_id.usage != 'customer':
            self.sb_dispatch_location_id.sudo().usage = 'customer'
        return self.sb_dispatch_location_id


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sb_voucher_line_ids = fields.One2many('sb.voucher.line', 'sb_sale_line_id', string='Invoice lines (vouchers)')
    sb_batch_line_ids = fields.One2many('sb.delivery.batch.line', 'sale_line_id', string='Delivery batch lines')
    # stored, so lists, the Sales home and Odoo's own invoice status read them instead of recomputing every order
    sb_qty_invoiced = fields.Float('Invoiced', compute='_compute_sb_qty', digits='Product Unit', store=True)
    sb_qty_delivered = fields.Float('Delivered', compute='_compute_sb_qty', digits='Product Unit', store=True)
    sb_qty_to_invoice = fields.Float('To invoice', compute='_compute_sb_qty', digits='Product Unit', store=True)

    @api.depends('sb_qty_invoiced')
    def _compute_qty_invoiced(self):
        """Odoo's invoiced quantity comes from the vouchers (Odoo's own invoices are not used)."""
        for line in self:
            line.qty_invoiced = line.sb_qty_invoiced

    @api.depends('sb_qty_to_invoice', 'state')
    def _compute_qty_to_invoice(self):
        for line in self:
            line.qty_to_invoice = line.sb_qty_to_invoice if line.state == 'sale' and not line.display_type else 0.0

    @api.depends('sb_qty_to_invoice', 'sb_qty_invoiced', 'state', 'product_uom_qty')
    def _compute_invoice_status(self):
        for line in self:
            if line.state != 'sale' or line.display_type or not line.product_id:
                line.invoice_status = 'no'
            elif line.sb_qty_to_invoice > 1e-6:
                line.invoice_status = 'to invoice'
            else:
                line.invoice_status = 'invoiced'

    def _action_launch_stock_rule(self, *, previous_product_uom_qty=False):
        # The voucher moves the stock when the invoice / cash sale is posted, not through Odoo pickings
        return True

    @api.depends('product_uom_qty', 'order_id.order_line.sb_voucher_line_ids.quantity', 'order_id.order_line.sb_voucher_line_ids.voucher_id.state',
                 'order_id.order_line.sb_batch_line_ids.quantity', 'order_id.order_line.sb_batch_line_ids.batch_id.state',
                 'order_id.sb_voucher_ids.state')
    def _compute_sb_qty(self):
        """Invoiced and delivered per item of the order, spread over the order's lines of that item in order
        (an order can carry the same item on several lines; an invoice line points at one of them)."""
        VL = self.env['sb.voucher.line']
        BL = self.env['sb.delivery.batch.line']
        orders = self.order_id
        all_lines = orders.order_line.filtered(lambda l: l.id and l.product_id and not l.display_type)
        # one search each for all the orders (per-order searches made Open Orders take ~10 s)
        inv, dlv = defaultdict(float), defaultdict(float)
        if all_lines:
            for l in VL.search([('sb_sale_line_id', 'in', all_lines.ids), ('voucher_id.state', '=', 'posted')]):
                key = (l.sb_sale_line_id.order_id.id, l.product_id.id)
                inv[key] += l.quantity * (-1 if l.voucher_id.voucher_type == 'sale_return' else 1)
            for l in BL.search([('sale_line_id', 'in', all_lines.ids), ('batch_id.state', '=', 'done')]):
                dlv[(l.sale_line_id.order_id.id, l.product_id.id)] += l.quantity
        for order in orders:
            lines = all_lines.filtered(lambda l: l.order_id == order)
            for line in order.order_line:
                line.sb_qty_invoiced = line.sb_qty_delivered = line.sb_qty_to_invoice = 0.0
            for product in set(lines.product_id):
                same = lines.filtered(lambda l: l.product_id == product).sorted(lambda l: (l.sequence, l.id))
                left_inv, left_dlv = inv[(order.id, product.id)], dlv[(order.id, product.id)]
                for i, line in enumerate(same):
                    last = i == len(same) - 1
                    take_inv = left_inv if last else min(left_inv, line.product_uom_qty)
                    take_dlv = left_dlv if last else min(left_dlv, line.product_uom_qty)
                    line.sb_qty_invoiced, line.sb_qty_delivered = take_inv, take_dlv
                    line.sb_qty_to_invoice = max(line.product_uom_qty - take_inv, 0.0)
                    left_inv -= take_inv
                    left_dlv -= take_dlv
            # orders invoiced in the earlier system sometimes billed another item than the one ordered (Aquapure for Avon):
            # those count as invoiced when the quantity invoiced covers the quantity ordered
            posted = VL.search([('sb_sale_line_id', 'in', lines.ids), ('voucher_id.state', '=', 'posted')]).voucher_id \
                | order.sb_voucher_ids.filtered(lambda v: v.state == 'posted')
            if posted and all(posted.mapped('sb_migrated')):
                billed = sum(posted.line_ids.filtered(lambda l: l.product_id and l.product_id.type != 'service').mapped('quantity'))
                if billed >= sum(lines.mapped('product_uom_qty')) - 1e-6:
                    for line in lines:
                        line.sb_qty_invoiced = max(line.sb_qty_invoiced, line.product_uom_qty)
                        line.sb_qty_to_invoice = 0.0
                        if not order.sb_delivery_ids.filtered('sb_to_deliver'):
                            line.sb_qty_delivered = max(line.sb_qty_delivered, line.sb_qty_invoiced)  # delivered in the earlier system
        for line in self.filtered(lambda l: not l.order_id):
            line.sb_qty_invoiced = line.sb_qty_delivered = line.sb_qty_to_invoice = 0.0


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sb_voucher_ids = fields.One2many('sb.voucher', 'sb_sale_order_id', string='Invoices')
    sb_delivery_ids = fields.One2many('sb.delivery.order', 'sale_order_id', string='Delivery orders')
    sb_invoice_count = fields.Integer(compute='_compute_sb_counts')
    sb_delivery_count = fields.Integer(compute='_compute_sb_counts')
    sb_status = fields.Selection([('to_invoice', 'To invoice'), ('to_deliver', 'To deliver'), ('done', 'Invoiced and delivered')],
                                 compute='_compute_sb_status', string='Progress', store=True, index=True)

    @api.depends('sb_voucher_ids.state', 'sb_delivery_ids.batch_ids.state')
    def _compute_sb_counts(self):
        for so in self:
            so.sb_invoice_count = len(so.sb_voucher_ids.filtered(lambda v: v.state != 'void'))
            so.sb_delivery_count = len(so.sb_delivery_ids)

    @api.depends('order_line.sb_qty_to_invoice', 'order_line.sb_qty_delivered', 'order_line.sb_qty_invoiced')
    def _compute_sb_status(self):
        for so in self:
            lines = so.order_line.filtered(lambda l: l.product_id and not l.display_type)
            if any(l.sb_qty_to_invoice > 1e-6 for l in lines):
                so.sb_status = 'to_invoice'
            elif any(l.product_id.is_storable and l.sb_qty_delivered < l.sb_qty_invoiced - 1e-6 for l in lines):
                so.sb_status = 'to_deliver'
            else:
                so.sb_status = 'done'

    def _sb_invoice(self, vtype):
        self.ensure_one()
        if self.state not in ('sale',):
            raise UserError(self.env._('Confirm the sales order first.'))
        lines = []
        for l in self.order_line.filtered(lambda x: x.product_id and not x.display_type and x.sb_qty_to_invoice > 1e-6):
            price = l.price_unit * (1 - (l.discount or 0.0) / 100.0)
            lines.append(Command.create({'product_id': l.product_id.id, 'name': l.name, 'quantity': l.sb_qty_to_invoice,
                                         'price_unit': price, 'tax_ids': [Command.set(l.tax_ids.ids)],
                                         'account_id': l.product_id.product_tmpl_id.get_product_accounts()['income'].id,
                                         'sb_sale_line_id': l.id}))
        if not lines:
            raise UserError(self.env._('Everything on %s is already invoiced.', self.name))
        v = self.env['sb.voucher'].create({
            'voucher_type': vtype, 'partner_id': self.partner_id.id, 'date': fields.Date.context_today(self),
            'reference': self.client_order_ref or self.name, 'memo': self.env._('Sales order %s', self.name),
            'sb_sale_order_id': self.id, 'sb_deliver_later': True, 'sb_warehouse_id': self.warehouse_id.id, 'line_ids': lines,
            'doc_currency_id': self.currency_id.id})  # a USD order is invoiced in USD
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': v.id, 'view_mode': 'form',
                'name': self.env._('Invoice %s', self.name)}

    def _action_confirm(self):
        res = super()._action_confirm()
        # strict delivery batches (as before at Eco Green): the delivery order is opened when the order is approved;
        # each invoice then adds its batch to it
        DO = self.env['sb.delivery.order'].sudo()
        for so in self.filtered(lambda o: o.company_id.sb_delivery_strict and o.state == 'sale'):
            if not so.sb_delivery_ids.filtered(lambda d: d.state not in ('done', 'cancel')) and so.order_line.filtered(
                    lambda l: l.product_id.is_storable):
                DO.create({'partner_id': so.partner_id.commercial_partner_id.id, 'sale_order_id': so.id, 'warehouse_id': so.warehouse_id.id,
                           'date': fields.Date.context_today(so), 'company_id': so.company_id.id})
        return res

    def action_sb_invoice(self):
        return self._sb_invoice('sale')

    def action_sb_cash_sale(self):
        return self._sb_invoice('cash_sale')

    def action_sb_view_invoices(self):
        self.ensure_one()
        return self.env['sb.voucher']._sb_open(self.sb_voucher_ids, self.env._('Invoices'))

    def action_sb_view_deliveries(self):
        self.ensure_one()
        return self.env['sb.voucher']._sb_open(self.sb_delivery_ids, self.env._('Delivery orders'))


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def action_sb_view_sale_order(self):
        return self._sb_open(self.sb_sale_order_id, self.sb_sale_order_id.name)

    def action_sb_view_delivery(self):
        return self._sb_open(self.sb_delivery_id, self.sb_delivery_id.name)

    sb_sale_order_id = fields.Many2one('sale.order', 'Sales order', check_company=True, copy=False)
    sb_deliver_later = fields.Boolean('Deliver later (delivery order)',
                                      help='On: the goods wait in "Awaiting dispatch" and leave with delivery order batches. '
                                           'Off: handed over at once (counter sale).')
    sb_delivery_id = fields.Many2one('sb.delivery.order', 'Delivery order', copy=False, readonly=True)

    def _sb_sale_destination(self, loc):
        if self.sb_deliver_later and self.voucher_type in ('cash_sale', 'sale'):
            return self.sb_warehouse_id._sb_dispatch_location()
        return super()._sb_sale_destination(loc)

    def action_post(self):
        drafts = self.filtered(lambda v: v.state == 'draft')
        res = super().action_post()
        for v in drafts.filtered(lambda x: x.state == 'posted' and x.sb_deliver_later and x.voucher_type in ('cash_sale', 'sale')
                                 and x.sb_stock_move_ids):
            flow = controlled(v)  # part of posting: the delivery order is linked to the posted voucher
            flow.sb_delivery_id = self.env['sb.delivery.order']._sb_for_voucher(flow)
        return res

    def action_void(self):
        for v in self.filtered(lambda x: x.state == 'posted' and x.sb_delivery_id):
            if v.sb_delivery_id.batch_ids.filtered(lambda b: b.state == 'done'):
                raise UserError(self.env._('%(v)s has goods already delivered on %(do)s. Return them with a sales return.',
                                           v=v.name, do=v.sb_delivery_id.name))
        res = super().action_void()
        for v in self.filtered(lambda x: x.state == 'void' and x.sb_delivery_id):
            v.sb_delivery_id.batch_ids.filtered(lambda b: b.state == 'draft' and (b.invoice_id == v or not v.sb_delivery_id.voucher_ids.filtered(
                lambda o: o.state == 'posted'))).unlink()
            v.sb_delivery_id._refresh_lines()
            v.sb_delivery_id._sb_sync_state()
        return res


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    sb_sale_line_id = fields.Many2one('sale.order.line', 'Sales order line', copy=False, index='btree_not_null')


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _sb_hide_sales_app(self):
        group = self.env.ref('sonador_books.group_sb_legacy_apps')
        menu = self.env.ref('sale.sale_menu_root', raise_if_not_found=False)
        if menu:
            menu.sudo().group_ids = [Command.set(group.ids)]
