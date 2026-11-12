"""GRNs, bills and payments linked to a purchase order."""
from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_purchase_id = fields.Many2one(
        'purchase.order', 'Purchase order', check_company=True, index='btree_not_null',
        domain="[('state', '=', 'purchase'), ('partner_id', '=', partner_id), ('sb_receipt_status', 'in', ('none', 'partial'))]",
        help='Receive against a purchase order: its lines still to come are loaded; enter what this delivery brought.')
    sb_po_payment_status = fields.Selection(related='sb_purchase_id.sb_payment_status', string='Order payment')
    sb_po_advance = fields.Monetary(related='sb_purchase_id.sb_amount_advance', string='Paid in advance on the order',
                                    currency_field='currency_id')

    def action_sb_view_purchase(self):
        return self.env['sb.voucher']._sb_open(self.sb_purchase_id, self.sb_purchase_id.name)

    @api.onchange('sb_purchase_id')
    def _onchange_sb_purchase(self):
        po = self.sb_purchase_id
        if not po or self.voucher_type != 'grn' or self.state != 'draft':
            return
        self.partner_id = po.partner_id
        if not self.reference and po.partner_ref:
            self.reference = po.partner_ref
        self.line_ids = [Command.clear()] + [Command.create(v) for v in po._sb_grn_lines_vals(self.date)]

    def _sb_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'name': self._type_label(), 'target': 'current'}

    def _check(self):
        res = super()._check()
        if self.voucher_type == 'grn':
            self._sb_check_purchase()
        return res

    def _sb_check_purchase(self):
        """Against the order: same supplier, not before the order date, never more than still to come
        (over-delivery tolerance in settings), at the order's price."""
        po_lines = self.line_ids.filtered('sb_po_line_id')
        if not po_lines:
            return
        company = self.company_id
        tolerance = company.sb_po_over_receipt / 100.0
        errors = []
        for l in po_lines:
            pol = l.sb_po_line_id
            po = pol.order_id
            if po.state != 'purchase':
                errors.append(self.env._('%s is not a confirmed purchase order.', po.name))
                continue
            if po.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
                errors.append(self.env._('%(po)s is for %(v)s, not %(s)s.', po=po.name, v=po.partner_id.name, s=self.partner_id.name))
            if po.date_order and self.date < po.date_order.date():
                errors.append(self.env._('%(po)s was ordered on %(d)s: goods cannot arrive before.', po=po.name, d=po.date_order.date()))
            if pol.product_id != l.product_id:
                errors.append(self.env._('%(item)s is not on %(po)s line "%(line)s".', item=l.product_id.display_name, po=po.name,
                                         line=pol.name))
        # quantities per order line: this GRN plus what was received before, against what was ordered
        per_line = {}
        for l in po_lines:
            per_line.setdefault(l.sb_po_line_id, 0.0)
            per_line[l.sb_po_line_id] += l.sb_po_line_id._sb_to_line_uom(l.quantity)
        for pol, qty in per_line.items():
            allowed = pol.product_qty * (1 + tolerance) - pol.sb_qty_received
            if float_compare(qty, allowed, precision_digits=4) > 0:
                errors.append(self.env._(
                    '%(item)s: %(qty)s %(uom)s received, but only %(left)s left on %(po)s (ordered %(ord)s, received %(rec)s).',
                    item=pol.product_id.display_name, qty=round(qty, 4), uom=pol.product_uom_id.name,
                    left=round(max(pol.product_qty - pol.sb_qty_received, 0.0), 4), po=pol.order_id.name,
                    ord=pol.product_qty, rec=round(pol.sb_qty_received, 4)))
        if errors:
            raise ValidationError('\n'.join(dict.fromkeys(errors)))
        # the order's price is the cost (differences show up on the bill)
        for l in po_lines:
            l.unit_cost = l.sb_po_line_id._sb_price_company(self.date)
        if not self.sb_purchase_id:
            self.sb_purchase_id = po_lines[:1].sb_po_line_id.order_id


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    sb_po_line_id = fields.Many2one('purchase.order.line', 'Order line', index='btree_not_null', copy=False,
                                    domain="[('order_id', '=', parent.sb_purchase_id), ('product_id', '=', product_id)]")
    sb_po_ordered = fields.Float('Ordered', related='sb_po_line_id.product_qty')
    sb_po_to_receive = fields.Float('Still to come', related='sb_po_line_id.sb_qty_to_receive')


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_purchase_id = fields.Many2one(
        'purchase.order', 'Purchase order', check_company=True, index='btree_not_null',
        domain="[('state', '=', 'purchase'), ('partner_id', '=', partner_id)]",
        help='Purchase voucher or payment for this purchase order. A payment before the goods arrive is an advance on the order.')

    def action_sb_view_purchase(self):
        return self._sb_open(self.sb_purchase_id, self.sb_purchase_id.name)

    def _check_before_post(self):
        res = super()._check_before_post()
        po = self.sb_purchase_id
        if po and self.partner_id and po.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(self.env._('%(po)s is for %(v)s.', po=po.name, v=po.partner_id.name))
        if self.voucher_type == 'bill' and self.sb_grn_ids.filtered(lambda g: g.billed):
            raise ValidationError(self.env._('Already on a purchase voucher: %s', ', '.join(self.sb_grn_ids.filtered('billed').mapped('name'))))
        return res


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    sb_po_line_id = fields.Many2one('purchase.order.line', 'Order line', index='btree_not_null', copy=False)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_po_over_receipt = fields.Float('Over-delivery allowed (%)', default=0.0,
                                      help='How much more than ordered a GRN may receive against a purchase order line. '
                                           '0 = never more than ordered.')
