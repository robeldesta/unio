"""Purchase requests: what a department needs bought (items, quantities, estimated cost). Approved like a payment request
(department head, then finance and the general manager as the company's rules say); then purchasing makes the request
for quotation from it in one click: one RFQ per vendor, the lines remember which order line they became."""
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class SbPurchaseRequest(models.Model):
    _name = 'sb.purchase.request'
    _description = 'Purchase request'
    _inherit = ['sb.request.mixin']
    _order = 'id desc'

    state = fields.Selection(selection_add=[('ordered', 'Ordered'), ('cancel',)], ondelete={'ordered': 'set default'})
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_required = fields.Date('Needed by', tracking=True)
    description = fields.Text('Purpose', required=True, tracking=True)
    vendor_id = fields.Many2one('res.partner', 'Suggested vendor', tracking=True,
                                help='Vendor of the request for quotation, unless a line names its own.')
    line_ids = fields.One2many('sb.purchase.request.line', 'request_id', 'Items', copy=True)
    amount = fields.Monetary('Estimated cost', compute='_compute_amount', store=True, currency_field='currency_id', tracking=True)
    purchase_ids = fields.Many2many('purchase.order', string='Purchase orders', copy=False, readonly=True)
    purchase_count = fields.Integer(compute='_compute_purchase_count')

    _SB_LOCKED = ('requester_id', 'company_id')

    def _sb_kind(self):
        return 'purchase'

    @api.depends('line_ids.subtotal')
    def _compute_amount(self):
        for r in self:
            r.amount = sum(r.line_ids.mapped('subtotal'))

    @api.depends('purchase_ids')
    def _compute_purchase_count(self):
        for r in self:
            r.purchase_count = len(r.purchase_ids)

    def _sb_check_submit(self):
        if not self.line_ids:
            raise UserError(self.env._('%s: add the items needed.', self.name))

    def _sb_check_cancel(self):
        if self.purchase_ids.filtered(lambda o: o.state in ('purchase', 'done')):
            raise UserError(self.env._('%s is ordered: cancel the purchase order first.', self.name))

    def action_create_rfq(self):
        """Purchasing's button: requests for quotation from the approved request (one per vendor)."""
        self.ensure_one()
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise UserError(self.env._('Only purchasing makes the request for quotation.'))
        if self.state != 'approved':
            raise UserError(self.env._('%s is not approved yet.', self.name))
        todo = self.line_ids.filtered(lambda l: not l.purchase_line_id)
        if not todo:
            raise UserError(self.env._('Every item of %s is on a purchase order already.', self.name))
        missing = todo.filtered(lambda l: not l.product_id)
        if missing:
            raise UserError(self.env._('Choose the item (product) for: %s', ', '.join(missing.mapped('name'))))
        groups = defaultdict(lambda: self.env['sb.purchase.request.line'])
        for l in todo:
            vendor = l.vendor_id or self.vendor_id
            if not vendor:
                raise UserError(self.env._('Choose the vendor (on the request or the line) for: %s', l.product_id.display_name))
            groups[vendor] |= l
        orders = self.env['purchase.order']
        for vendor, lines in groups.items():
            po = self.env['purchase.order'].create({
                'partner_id': vendor.id, 'company_id': self.company_id.id, 'origin': self.name,
                'date_planned': self.date_required or fields.Datetime.now(),
                'order_line': [Command.create({'product_id': l.product_id.id, 'name': l.name or l.product_id.display_name,
                                               'product_qty': l.qty, 'product_uom_id': (l.uom_id or l.product_id.uom_id).id,
                                               'price_unit': l.price_unit}) for l in lines]})
            for l, pol in zip(lines, po.order_line):
                l.sudo().purchase_line_id = pol
            orders |= po
        self.sudo().write({'purchase_ids': [Command.link(o.id) for o in orders], 'state': 'ordered'})
        self._sb_log(self.env._('Request for quotation %(p)s made by %(u)s.', p=', '.join(orders.mapped('name')), u=self.env.user.name))
        return self.action_view_purchases()

    def action_view_purchases(self):
        self.ensure_one()
        act = {'type': 'ir.actions.act_window', 'name': self.env._('Purchase orders'), 'res_model': 'purchase.order',
               'domain': [('id', 'in', self.purchase_ids.ids)], 'views': [[False, 'list'], [False, 'form']]}
        if len(self.purchase_ids) == 1:
            act.update(res_id=self.purchase_ids.id, views=[[False, 'form']])
        return act


class SbPurchaseRequestLine(models.Model):
    _name = 'sb.purchase.request.line'
    _description = 'Purchase request item'
    _order = 'sequence, id'

    request_id = fields.Many2one('sb.purchase.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    currency_id = fields.Many2one(related='request_id.currency_id')
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', 'Item', domain=[('purchase_ok', '=', True)])
    name = fields.Char('Description', required=True)
    specifications = fields.Char()
    qty = fields.Float('Quantity', default=1.0, digits='Product Unit', required=True)
    uom_id = fields.Many2one('uom.uom', 'Unit')
    price_unit = fields.Monetary('Estimated unit cost', currency_field='currency_id')
    subtotal = fields.Monetary('Estimated cost', compute='_compute_subtotal', store=True, currency_field='currency_id')
    vendor_id = fields.Many2one('res.partner', 'Vendor')
    purchase_line_id = fields.Many2one('purchase.order.line', 'Order line', readonly=True, copy=False)
    purchase_state = fields.Selection(related='purchase_line_id.order_id.state', string='Order status')

    @api.depends('qty', 'price_unit')
    def _compute_subtotal(self):
        for l in self:
            l.subtotal = l.qty * l.price_unit

    @api.onchange('product_id')
    def _onchange_product(self):
        p = self.product_id
        if p:
            self.name = self.name or p.display_name
            self.uom_id = p.uom_id
            if not self.price_unit:
                self.price_unit = p.standard_price
