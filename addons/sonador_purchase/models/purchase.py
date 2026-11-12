"""Purchase orders: ordered -> received (GRNs, in batches) -> billed -> paid, tracked per line and per order.

Odoo Purchase gives the RFQ / order / approval / vendor price list screens. Its own receipts (pickings) and vendor
bills are not used: goods come in on GRNs, bills and payments are vouchers, all linked to the order.
"""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

RECEIPT = [('none', 'Not received'), ('partial', 'Partly received'), ('full', 'Fully received'), ('na', 'Services only')]
BILLING = [('none', 'Not billed'), ('partial', 'Partly billed'), ('full', 'Fully billed')]
PAYMENT = [('none', 'Not paid'), ('advance', 'Paid in advance'), ('partial', 'Partly paid'), ('paid', 'Paid')]


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    sb_grn_line_ids = fields.One2many('sb.stock.voucher.line', 'sb_po_line_id', string='Received on')
    sb_bill_line_ids = fields.One2many('sb.voucher.line', 'sb_po_line_id', string='Billed on')
    sb_is_goods = fields.Boolean(compute='_compute_sb_is_goods')
    sb_qty_received = fields.Float('Received', compute='_compute_sb_qty', store=True, digits='Product Unit',
                                   help='On posted GRNs, in the unit of the order line.')
    sb_qty_to_receive = fields.Float('To receive', compute='_compute_sb_qty', store=True, digits='Product Unit')
    sb_qty_billed = fields.Float('Billed', compute='_compute_sb_qty', store=True, digits='Product Unit')
    sb_qty_to_bill = fields.Float('To bill', compute='_compute_sb_qty', store=True, digits='Product Unit',
                                  help='Goods: received and not billed. Services: ordered and not billed.')

    @api.depends('product_id')
    def _compute_sb_is_goods(self):
        for l in self:
            l.sb_is_goods = l.product_id.type == 'consu' and bool(l.product_id.is_storable)

    def _sb_to_line_uom(self, qty):
        """quantity in the item's unit -> unit of the order line"""
        self.ensure_one()
        if not self.product_id or not self.product_uom_id or self.product_uom_id == self.product_id.uom_id:
            return qty
        return self.product_id.uom_id._compute_quantity(qty, self.product_uom_id, round=False)

    @api.depends('product_qty', 'product_uom_id', 'product_id.is_storable', 'product_id.type',
                 'sb_grn_line_ids.quantity', 'sb_grn_line_ids.voucher_id.state', 'sb_grn_line_ids.voucher_id.sb_migrated',
                 'sb_bill_line_ids.quantity', 'sb_bill_line_ids.voucher_id.state', 'sb_bill_line_ids.voucher_id.voucher_type')
    def _compute_sb_qty(self):
        for l in self:
            received = l._sb_to_line_uom(sum(g.quantity for g in l.sb_grn_line_ids if g.voucher_id.state == 'posted'))
            # brought-over GRNs were billed on receipt (Peachtree posted them to the supplier's payable)
            billed = l._sb_to_line_uom(sum(g.quantity for g in l.sb_grn_line_ids
                                          if g.voucher_id.state == 'posted' and g.voucher_id.sb_migrated))
            for b in l.sb_bill_line_ids:
                if b.voucher_id.state != 'posted':
                    continue
                sign = -1 if b.voucher_id.voucher_type == 'purchase_return' else 1
                billed += sign * l._sb_to_line_uom(b.quantity)
            goods = l.product_id.type == 'consu' and bool(l.product_id.is_storable)
            l.sb_qty_received = received if goods else 0.0
            l.sb_qty_to_receive = max(l.product_qty - received, 0.0) if goods else 0.0
            l.sb_qty_billed = billed
            l.sb_qty_to_bill = max((received if goods else l.product_qty) - billed, 0.0)

    def _sb_price_company(self, day):
        """Unit cost in company currency per unit of the item (discount applied, taxes excluded)."""
        self.ensure_one()
        order = self.order_id
        price = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        if self.product_uom_id and self.product_id and self.product_uom_id != self.product_id.uom_id:
            price = self.product_uom_id._compute_price(price, self.product_id.uom_id)
        if order.currency_id != order.company_id.currency_id:
            price = order.currency_id._convert(price, order.company_id.currency_id, order.company_id, day or fields.Date.today(),
                                               round=False)
        return price


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sb_grn_ids = fields.One2many('sb.stock.voucher', 'sb_purchase_id', string='GRNs')
    sb_voucher_ids = fields.One2many('sb.voucher', 'sb_purchase_id', string='Bills and payments')
    sb_receipt_status = fields.Selection(RECEIPT, string='Receipt', compute='_compute_sb_status', store=True)
    sb_bill_status = fields.Selection(BILLING, string='Billing', compute='_compute_sb_status', store=True)
    sb_grn_count = fields.Integer(compute='_compute_sb_counts')
    sb_bill_count = fields.Integer(compute='_compute_sb_counts')
    sb_payment_count = fields.Integer(compute='_compute_sb_counts')
    sb_amount_billed = fields.Monetary('Billed', compute='_compute_sb_money', currency_field='company_currency_id', compute_sudo=True)
    sb_amount_paid = fields.Monetary('Paid', compute='_compute_sb_money', currency_field='company_currency_id', compute_sudo=True,
                                     help='Cash paid for this order: payments made against it (advances included) and payments of its bills.')
    sb_amount_advance = fields.Monetary('Advance not yet billed', compute='_compute_sb_money', currency_field='company_currency_id',
                                        compute_sudo=True, help='Paid ahead of the bills: the supplier owes goods for it.')
    sb_amount_due = fields.Monetary('Still to pay', compute='_compute_sb_money', currency_field='company_currency_id', compute_sudo=True)
    sb_payment_status = fields.Selection(PAYMENT, string='Payment', compute='_compute_sb_money', compute_sudo=True)
    sb_late = fields.Boolean('Delivery late', compute='_compute_sb_late')

    @api.depends('state', 'order_line.sb_qty_to_receive', 'order_line.sb_qty_received', 'order_line.sb_qty_billed',
                 'order_line.product_qty')
    def _compute_sb_status(self):
        for o in self:
            lines = o.order_line.filtered(lambda l: l.product_id and not l.display_type)
            goods = lines.filtered('sb_is_goods')
            if not goods:
                o.sb_receipt_status = 'na' if lines else 'none'  # nothing to receive on a GRN
            elif o.state != 'purchase':
                o.sb_receipt_status = 'none'
            elif all(float_is_zero(l.sb_qty_to_receive, precision_digits=4) for l in goods):
                o.sb_receipt_status = 'full'
            elif any(l.sb_qty_received > 0 for l in goods):
                o.sb_receipt_status = 'partial'
            else:
                o.sb_receipt_status = 'none'
            if not any(l.sb_qty_billed for l in lines):
                o.sb_bill_status = 'none'
            elif all(float_compare(l.sb_qty_billed, l.product_qty, precision_digits=4) >= 0 for l in lines):
                o.sb_bill_status = 'full'
            else:
                o.sb_bill_status = 'partial'

    def _compute_sb_counts(self):
        for o in self:
            o.sb_grn_count = len(o.sb_grn_ids.filtered(lambda g: g.state != 'void'))
            o.sb_bill_count = len(o._sb_bills().filtered(lambda v: v.state != 'void'))
            o.sb_payment_count = len(o._sb_payments().filtered(lambda v: v.state != 'void'))

    def _sb_bills(self):
        return self.sb_voucher_ids.filtered(lambda v: v.voucher_type in ('bill', 'purchase_return'))

    def _sb_payments(self):
        return self.sb_voucher_ids.filtered(lambda v: v.voucher_type in ('payment', 'cash_purchase'))

    def _compute_sb_money(self):
        """From the ledger: what the order's bills have been settled by (cash, or advances used on them), plus
        advances paid against the order and not yet used, plus cash purchases for it."""
        for o in self:
            cur = o.company_currency_id
            adv_acc = o.company_id.sb_vendor_advance_account_id
            bills = o._sb_bills().filtered(lambda v: v.state == 'posted')
            billed = settled = 0.0
            for v in bills:
                total = abs(v.move_id.amount_total_signed)
                if v.voucher_type == 'purchase_return':
                    billed -= total
                else:
                    billed += total
                    settled += total - abs(v.move_id.amount_residual_signed)
            # GRNs brought over from Peachtree were posted straight to the supplier's payable: they are the bill too
            for g in o.sb_grn_ids.filtered(lambda x: x.state == 'posted' and x.sb_migrated):
                for l in g.move_id.line_ids:
                    if l.balance < 0 and (l.account_id.account_type == 'liability_payable' or l.account_id.sb_partner_account):
                        billed += -l.balance
                        settled += -l.balance - abs(l.amount_residual)
            pays = o._sb_payments().filtered(lambda v: v.state == 'posted')
            partner = o.partner_id.commercial_partner_id
            open_adv = sum(l.amount_residual for l in pays.move_id.line_ids
                           if l.balance > 0 and l.partner_id.commercial_partner_id == partner and (
                               l.account_id == adv_acc or (l.account_id.sb_partner_account and l.account_id.internal_group == 'asset')))
            cash = sum(pays.filtered(lambda v: v.voucher_type == 'cash_purchase').mapped('amount_total'))
            # payments that paid the order's costs directly (no bill: an expense, an asset, stock)
            for l in pays.filtered(lambda v: v.voucher_type == 'payment').move_id.line_ids:
                acc = l.account_id
                if l.balance > 0 and acc.account_type not in ('asset_cash', 'liability_payable', 'asset_receivable') \
                        and not acc.sb_partner_account and acc != adv_acc:
                    cash += l.balance
            paid = settled + open_adv + cash
            if o.state == 'purchase':
                total = o.currency_id._convert(o.amount_total, cur, o.company_id, fields.Date.context_today(o))
            else:
                total = 0.0
            o.sb_amount_billed = billed
            o.sb_amount_paid = paid
            o.sb_amount_advance = open_adv
            o.sb_amount_due = max(max(total, billed + cash) - paid, 0.0)
            if cur.is_zero(paid):
                o.sb_payment_status = 'none'
            elif cur.is_zero(o.sb_amount_due):
                o.sb_payment_status = 'paid'
            elif open_adv and cur.compare_amounts(open_adv, 0.0) > 0 and cur.compare_amounts(settled, billed) >= 0:
                o.sb_payment_status = 'advance'
            else:
                o.sb_payment_status = 'partial'

    def _compute_sb_late(self):
        now = fields.Datetime.now()
        for o in self:
            o.sb_late = o.state == 'purchase' and o.sb_receipt_status != 'full' and bool(o.date_planned) and o.date_planned < now

    # ------------------------------------------------------------------ Odoo receipts and bills are not used
    def _create_picking(self):
        return True

    def action_create_invoice(self):
        raise UserError(self.env._('Bills are vouchers here: use "Bill" on the order (it clears the GRNs received).'))

    # ------------------------------------------------------------------ receive
    def _sb_grn_lines_vals(self, day=None):
        self.ensure_one()
        vals = []
        for l in self.order_line.filtered(lambda x: x.sb_is_goods and x.sb_qty_to_receive > 1e-9):
            qty = l.product_uom_id._compute_quantity(l.sb_qty_to_receive, l.product_id.uom_id, round=False) \
                if l.product_uom_id and l.product_uom_id != l.product_id.uom_id else l.sb_qty_to_receive
            vals.append({'product_id': l.product_id.id, 'name': l.name.split('\n')[0] if l.name else l.product_id.display_name,
                         'quantity': qty, 'unit_cost': l._sb_price_company(day), 'sb_po_line_id': l.id})
        return vals

    def action_sb_receive(self):
        """GRN for what is still to come (edit the quantities to what this truck brought)."""
        self.ensure_one()
        if self.state != 'purchase':
            raise UserError(self.env._('Confirm the purchase order first.'))
        lines = self._sb_grn_lines_vals(fields.Date.context_today(self))
        if not lines:
            raise UserError(self.env._('%s is fully received.', self.name))
        draft = self.sb_grn_ids.filtered(lambda g: g.state == 'draft')[:1]
        if draft:
            return draft._sb_open()
        wh = self.picking_type_id.warehouse_id if 'picking_type_id' in self._fields else self.env['stock.warehouse']
        grn = self.env['sb.stock.voucher'].create({
            'voucher_type': 'grn', 'partner_id': self.partner_id.id, 'sb_purchase_id': self.id,
            'warehouse_id': (wh or self.env['stock.warehouse'].search([('company_id', '=', self.company_id.id)], limit=1)).id,
            'reference': self.partner_ref or False, 'memo': self.env._('Received against %s', self.name),
            'line_ids': [Command.create(v) for v in lines]})
        return grn._sb_open()

    # ------------------------------------------------------------------ bill
    def action_sb_bill(self):
        """One bill for every GRN of the order not yet billed, plus services ordered and not billed."""
        self.ensure_one()
        grns = self.sb_grn_ids.filtered(lambda g: g.state == 'posted' and not g.billed)
        bill = self.env['sb.voucher'].create(self._sb_bill_vals(grns))
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': bill.id, 'view_mode': 'form',
                'name': self.env._('Purchase Bill')}

    def _sb_bill_vals(self, grns, services=True):
        """The bill of these GRNs at the order's prices and taxes (and of the services ordered and not billed)."""
        self.ensure_one()
        company = self.company_id
        grni = company.sb_grni_account_id
        lines = []
        for g in grns:
            for gl in g.line_ids.filtered('product_id'):
                pol = gl.sb_po_line_id
                price = pol.price_unit * (1 - (pol.discount or 0.0) / 100.0) if pol else gl.unit_cost
                qty = gl.quantity
                if pol and pol.product_uom_id and pol.product_uom_id != gl.product_id.uom_id:
                    price = pol.product_uom_id._compute_price(price, gl.product_id.uom_id)
                lines.append({'product_id': gl.product_id.id, 'name': '%s (%s)' % (gl.name or gl.product_id.display_name, g.name),
                              'quantity': qty, 'price_unit': price, 'account_id': (g.account_id or grni).id, 'sb_po_line_id': pol.id or False,
                              'tax_ids': [Command.set((pol.tax_ids if pol else gl.product_id.supplier_taxes_id).filtered(
                                  lambda t: t.company_id == company).ids)]})
        for l in self.order_line.filtered(lambda x: services and x.product_id and not x.sb_is_goods and x.sb_qty_to_bill > 1e-9):
            lines.append({'product_id': l.product_id.id, 'name': l.name, 'quantity': l.sb_qty_to_bill,
                          'price_unit': l.price_unit * (1 - (l.discount or 0.0) / 100.0), 'sb_po_line_id': l.id,
                          'account_id': (l.product_id.product_tmpl_id.get_product_accounts()['expense']).id,
                          'tax_ids': [Command.set(l.tax_ids.ids)]})
        if not lines:
            raise UserError(self.env._('Nothing to bill on %s: goods are billed after they are received (GRN).', self.name))
        return {
            'voucher_type': 'bill', 'partner_id': self.partner_id.id, 'sb_purchase_id': self.id,
            'doc_currency_id': self.currency_id.id, 'reference': self.partner_ref or False,
            'memo': self.env._('Bill for %(po)s%(grns)s', po=self.name,
                               grns=(' (%s)' % ', '.join(grns.mapped('name'))) if grns else ''),
            'sb_grn_ids': [Command.set(grns.ids)], 'line_ids': [Command.create(v) for v in lines]}

    # ------------------------------------------------------------------ pay (advance or bills)
    def action_sb_pay(self):
        self.ensure_one()
        if self.state != 'purchase':
            raise UserError(self.env._('Confirm the purchase order first.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'view_mode': 'form', 'target': 'current',
                'name': self.env._('Payment for %s', self.name),
                'context': {'default_voucher_type': 'payment', 'default_partner_id': self.partner_id.id,
                            'default_sb_purchase_id': self.id, 'default_payer_type': 'vendor',
                            'default_received_amount': self.sb_amount_due,
                            'default_memo': self.env._('Payment for %s', self.name),
                            'default_reference': self.partner_ref or self.name}}

    # ------------------------------------------------------------------ smart buttons
    def _sb_list(self, records, name, model):
        return self.env['sb.voucher']._sb_open(records or self.env[model], name)

    def action_sb_view_grns(self):
        return self._sb_list(self.sb_grn_ids, self.env._('GRNs of %s', self.name), 'sb.stock.voucher')

    def action_sb_view_bills(self):
        return self._sb_list(self._sb_bills(), self.env._('Bills of %s', self.name), 'sb.voucher')

    def action_sb_view_payments(self):
        return self._sb_list(self._sb_payments(), self.env._('Payments of %s', self.name), 'sb.voucher')


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _sb_hide_purchase_app(self):
        group = self.env.ref('sonador_books.group_sb_legacy_apps')
        menu = self.env.ref('purchase.menu_purchase_root', raise_if_not_found=False)
        if menu:
            menu.sudo().group_ids = [Command.set(group.ids)]


class PurchaseOrderCancel(models.Model):
    _inherit = 'purchase.order'

    def button_cancel(self):
        for o in self:
            done = o.sb_grn_ids.filtered(lambda g: g.state == 'posted').mapped('name') + \
                o.sb_voucher_ids.filtered(lambda v: v.state == 'posted').mapped('name')
            if done:
                raise UserError(self.env._('%(po)s has posted documents (%(docs)s). Void them first, or close the order '
                                           'as it is.', po=o.name, docs=', '.join(done)))
        return super().button_cancel()


class PurchaseOrderNumber(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, vals_list):
        """RFQ/26-27/0001 (RFQ/0001 for a company numbering without the year) while it is a request; the strict purchase
        order number is taken on approval (series.py), or now for a company numbering purchase orders on creation."""
        for vals in vals_list:
            if vals.get('name', 'New') in ('New', '/', False) and not self._sb_number_on_create(vals):
                day = fields.Datetime.to_datetime(vals.get('date_order')) or fields.Datetime.now()
                company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
                vals['name'] = self.env['sb.doc.series']._sb_plain_next('purchase.order', 'RFQ', day.date(), company)
        return super().create(vals_list)
