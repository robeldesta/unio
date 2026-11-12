"""Store Damage Voucher (SDV), Sales Return (RET) and Recycled Product Receiving (RPRV): inventory documents.

Prepared by the store (saved and sent to finance), posted by finance (Clerk or higher). Numbered in their series (SDV/,
RET/, RPRV/: PREFIX/ + 5 digits, no year), dated in an open period, voided (never edited) with a reason.

SDV - goods found damaged in the store, at the good stock's average cost on the SDV date:
    Dr  damaged inventory (the damaged item's class, e.g. 1504-0001 Finished Goods - Defect)
    Cr  inventory of the item (e.g. 1504-0000 Finished Product)
    stock: item out of the store, its damaged counterpart into <warehouse>/Damaged (same quantity, same value).

RET - goods a customer brings back, against the invoice / cash sale (Eco Green: required). The credit note (CN/) is the
money side, posted with it (sonador_books' own Sales Return / Credit Note, applied to the invoice):
    Dr  Sales returns and allowances (4000-0005)      at the return price (may differ from the sale price)
    Cr  the customer (applied to the invoice; a cash refund is a payment against the credit note)
    Dr  damaged inventory                              at the cost the goods were SOLD at (that sale's delivery)
    Cr  cost of sales of the item sold (5000-0007)     (the sale's own cost of sales reversed; company choice: the
                                                        damaged class's expense, Damage of Product, as Peachtree did)
    stock: the damaged counterpart into <warehouse>/Damaged. A line in good condition goes back to the good stock
    (Dr inventory / Cr cost of sales at the sold cost, as any credit note). Without an invoice (company allowing it):
    at the item's average cost on the return date. The good stock's average is never touched by a damaged return.

RPRV - damaged goods taken out of the damaged stock and received as a recycled item (like a production receipt whose
input is damaged stock instead of work in process), at the damaged stock's cost:
    Dr  inventory of the recycled item                 total value of what was taken out (shared by quantity)
    Cr  damaged inventory                              at the damaged items' average cost
    The quantities may differ (20 L of damaged product in, 18 L recycled out): the value is the same, any loss is in
    the recycled item's unit cost.

Back-dated documents: costed on their date by the stock engine (same-day receipt rule); later documents are re-costed
and every later change of the good item's cost is passed on to the damaged / recycled item (sb.stock._recost_pass_on).
A document dated in a closed period is refused.
"""
from collections import defaultdict

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

from odoo.addons.sonador_books.models.move_guard import controlled

MY_TYPES = ('damage', 'customer_return', 'rprv')
MY_PREFIX = {'damage': 'SDV', 'customer_return': 'RET', 'rprv': 'RPRV'}
RETURN_REASONS = [('damaged', 'Damaged'), ('defective', 'Defective'), ('wrong_item', 'Wrong item'),
                  ('quality_issue', 'Quality issue'), ('customer_request', 'Customer request'), ('expired', 'Expired'),
                  ('other', 'Other')]


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('damage', 'Store Damage Voucher (SDV)'), ('customer_return', 'Sales Return (RET)'),
                                               ('rprv', 'Recycled Product Receiving (RPRV)')],
                                ondelete={'damage': 'cascade', 'customer_return': 'cascade', 'rprv': 'cascade'})

    @api.model
    def _sb_extra_name_tables(self):
        return dict(super()._sb_extra_name_tables(), damage=('sb_stock_voucher', 'name'),
                    customer_return=('sb_stock_voucher', 'name'), rprv=('sb_stock_voucher', 'name'))


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    voucher_type = fields.Selection(selection_add=[('damage', 'Store Damage Voucher (SDV)'), ('customer_return', 'Sales Return (RET)'),
                                                   ('rprv', 'Recycled Product Receiving (RPRV)')],
                                    ondelete={'damage': 'cascade', 'customer_return': 'cascade', 'rprv': 'cascade'})
    sb_return_reason = fields.Selection(RETURN_REASONS, 'Return reason', default='damaged')
    sb_found_by = fields.Char('Found / reported by', help='Who found the damage (store damage) or received the goods (returns).')
    sb_photo = fields.Image('Photo', max_width=1600, max_height=1600, attachment=True)
    sb_origin_voucher_id = fields.Many2one(
        'sb.voucher', 'Against invoice / cash sale', check_company=True,
        domain="[('state', '=', 'posted'), ('voucher_type', 'in', ('sale', 'cash_sale')), ('partner_id', '=', partner_id)]",
        help='The invoice or cash sale whose goods come back: they come back at the cost they were sold at.')
    sb_credit_note_id = fields.Many2one('sb.voucher', 'Credit note', copy=False, readonly=True)
    sb_credit_note_number = fields.Char('Credit note no.', copy=False,
                                        help='The number of the credit note from the fiscal register (CN ...): the credit note\'s '
                                             'number. Finance types it when posting.')
    sb_needs_note_number = fields.Boolean(compute='_compute_sb_needs_note_number')

    @api.depends('voucher_type', 'company_id')
    def _compute_sb_needs_note_number(self):
        from odoo.addons.sonador_books.models.fiscal_print import fiscal_only
        for v in self:
            v.sb_needs_note_number = v.voucher_type == 'customer_return' and fiscal_only(v.company_id or self.env.company)

    @api.onchange('sb_origin_voucher_id')
    def _onchange_sb_origin(self):
        """The invoice's items, quantities and prices (lower the quantities to what comes back)."""
        if self.voucher_type != 'customer_return' or not self.sb_origin_voucher_id or self.line_ids:
            return
        self._sb_warehouse_from_origin()
        self.line_ids = [Command.create({'product_id': l.product_id.id, 'name': l.name, 'quantity': l.quantity,
                                         'sb_price': l.price_unit, 'sb_condition': 'damaged'})
                         for l in self.sb_origin_voucher_id.line_ids.filtered(lambda x: x.product_id.is_storable)]

    def _sb_warehouse_from_origin(self):
        wh = self.sb_origin_voucher_id.sb_warehouse_id
        if wh and not wh.lot_stock_id.warehouse_id.name.startswith('Van'):
            self.warehouse_id = wh

    # ------------------------------------------------------------------ who, numbers
    def _store_types(self):
        return super()._store_types() + MY_TYPES

    def _sb_require(self):
        """The store prepares (saves, sends to finance), finance posts: Clerk or higher, always (whatever the company's
        "store documents are posted by finance" setting), since these documents change the cost of sales and the damaged
        stock."""
        if self.voucher_type in self._sb_finance_only_types() and not self.env.su \
                and not self.env.user.has_group('sonador_books.group_sb_clerk'):
            raise UserError(self.env._('%s is posted by finance: save it and press Send to finance.', self._type_label()))
        return super()._sb_require()

    def _sb_finance_only_types(self):
        return MY_TYPES

    def _name_prefix(self):
        return MY_PREFIX.get(self.voucher_type) or super()._name_prefix()

    def _sb_series_doc_type(self):
        return {'damage': 'damage', 'customer_return': 'customer_return', 'rprv': 'rprv'}.get(self.voucher_type) \
            or super()._sb_series_doc_type()

    # ------------------------------------------------------------------ checks
    def _check(self):
        super()._check()
        if self.voucher_type not in MY_TYPES + self._sb_more_types():
            return
        Stock = self.env['sb.stock']
        lines = self.line_ids.filtered('product_id')
        damaged_items = self.env['product.product'].browse(
            [l.product_id.id for l in lines if Stock._sb_damaged_counterpart_of(l.product_id, self.company_id)])
        if self.voucher_type == 'damage' and damaged_items:
            raise ValidationError(self.env._('%s is already damaged stock.', ', '.join(damaged_items.mapped('display_name'))))
        if self.voucher_type == 'customer_return':
            if not self.partner_id:
                raise ValidationError(self.env._('Choose the customer.'))
            if self.company_id.sb_return_needs_invoice and not self.sb_origin_voucher_id:
                raise ValidationError(self.env._('Name the invoice or cash sale the goods come back from: they come back at '
                                                 'the cost they were sold at.'))
            if not self.company_id.sb_sales_return_account_id:
                raise ValidationError(self.env._('Set the sales returns and allowances account (Inventory setup).'))
            if any(l.sb_price < 0 for l in lines):
                raise ValidationError(self.env._('Return prices cannot be negative.'))
        if self.voucher_type == 'rprv':
            outs, ins = lines.filtered(lambda l: l.sb_side == 'out'), lines.filtered(lambda l: l.sb_side == 'in')
            if not outs or not ins:
                raise ValidationError(self.env._('Enter the damaged goods taken out and the recycled goods received.'))
            not_damaged = outs.product_id.filtered(lambda p: not Stock._sb_damaged_counterpart_of(p, self.company_id))
            if not_damaged:
                raise ValidationError(self.env._('Only damaged stock is taken out by a recycled product receiving: %s is not.',
                                                 ', '.join(not_damaged.mapped('display_name'))))

    def _sb_more_types(self):
        """Types other modules add (the van return)."""
        return ()

    # ------------------------------------------------------------------ posting
    def _post_stock(self):
        if self.voucher_type == 'damage':
            return self._sb_post_damage()
        if self.voucher_type == 'customer_return':
            return self._sb_post_return()
        if self.voucher_type == 'rprv':
            return self._sb_post_rprv()
        return super()._post_stock()

    def _sb_link(self, line):
        return {'sb_stock_voucher_id': self.id, 'sb_stock_line_id': line.id}

    def _sb_post_damage(self):
        """SDV: each item leaves the store at its average cost and its damaged counterpart comes into Damaged at that value."""
        Stock, company = self.env['sb.stock'], self.company_id
        stock = self.warehouse_id.lot_stock_id
        conv, dmg = Stock._sb_conversion_location(company), Stock._sb_damaged_location(self.warehouse_id)
        lines = self.line_ids.filtered('product_id')
        wanted = defaultdict(float)
        for l in lines:
            wanted[l.product_id] += l.quantity
        Stock._check_available(company, wanted, stock, self.display_name, self.date)
        entry = []
        for l in lines:
            p, qty = l.product_id, l.quantity
            d = l.sb_damaged_product_id or Stock._sb_damaged_counterpart(p, company)
            name = '%s - %s' % (self.name, p.display_name)
            out = Stock._move(p, qty, stock, conv, self.date, self.name, company, link=self._sb_link(l))
            value = abs(out.value)
            Stock._move(d, qty, conv, dmg, self.date, self.name, company, unit_cost=value / qty, link=self._sb_link(l))
            entry += [(Stock._valuation_account(d, company), value, name, {'product_id': d.id}),
                      (Stock._valuation_account(p, company), -value, name, {'product_id': p.id})]
            l.write({'value': value, 'unit_cost': value / qty, 'sb_damaged_product_id': d.id})
        self.move_id = Stock._post_entry(company, self.date, '%s %s' % (self.name, self.memo or self.reference or ''), entry)

    def _sb_post_rprv(self):
        """RPRV: damaged goods out of Damaged at their cost; the recycled goods in at the same total, shared by quantity."""
        Stock, company = self.env['sb.stock'], self.company_id
        stock = self.warehouse_id.lot_stock_id
        conv, dmg = Stock._sb_conversion_location(company), Stock._sb_damaged_location(self.warehouse_id)
        lines = self.line_ids.filtered('product_id')
        outs, ins = lines.filtered(lambda l: l.sb_side == 'out'), lines.filtered(lambda l: l.sb_side == 'in')
        wanted = defaultdict(float)
        for l in outs:
            wanted[l.product_id] += l.quantity
        Stock._check_available(company, wanted, dmg, self.display_name, self.date)
        cur = company.currency_id
        entry, total = [], 0.0
        for l in outs:
            m = Stock._move(l.product_id, l.quantity, dmg, conv, self.date, self.name, company, link=self._sb_link(l))
            value = abs(m.value)
            total += value
            entry.append((Stock._valuation_account(l.product_id, company), -value, '%s - %s' % (self.name, l.product_id.display_name),
                          {'product_id': l.product_id.id}))
            l.write({'value': value, 'unit_cost': value / l.quantity})
        qty_in = sum(ins.mapped('quantity'))
        left = cur.round(total)
        for i, l in enumerate(ins):
            value = left if i == len(ins) - 1 else cur.round(total * l.quantity / qty_in)
            left -= value
            Stock._move(l.product_id, l.quantity, conv, stock, self.date, self.name, company, unit_cost=value / l.quantity,
                        link=self._sb_link(l))
            entry.append((Stock._valuation_account(l.product_id, company), value, '%s - %s' % (self.name, l.product_id.display_name),
                          {'product_id': l.product_id.id}))
            l.write({'value': value, 'unit_cost': value / l.quantity})
        self.move_id = Stock._post_entry(company, self.date, '%s %s' % (self.name, self.memo or self.reference or ''), entry)

    def _sb_post_return(self):
        """RET: the credit note (money side, applied to the invoice) is posted with it; the goods come back on the credit
        note, damaged ones as their damaged counterpart into Damaged, at the cost they were sold at."""
        Stock, company = self.env['sb.stock'], self.company_id
        lines = self.line_ids.filtered('product_id')
        for l in lines.filtered(lambda x: x.sb_condition == 'damaged' and not x.sb_damaged_product_id):
            l.sb_damaged_product_id = Stock._sb_damaged_counterpart(l.product_id, company)
        origin = self.sb_origin_voucher_id
        account = company.sb_sales_return_account_id
        taxes_of = {ol.product_id: ol.tax_ids for ol in origin.line_ids if ol.product_id}
        note = self.env['sb.voucher'].create({
            'voucher_type': 'sale_return', 'partner_id': self.partner_id.id, 'origin_id': origin.id or False, 'date': self.date,
            'sb_warehouse_id': self.warehouse_id.id, 'reference': self.name, 'sb_return_id': self.id,
            'memo': self.env._('Sales return %(ret)s%(of)s', ret=self.name, of=(' / %s' % origin.name) if origin else ''),
            'sb_pad_number': self.sb_credit_note_number or False,
            'line_ids': [Command.create({'product_id': l.product_id.id, 'name': l.name or l.product_id.display_name,
                                         'quantity': l.quantity, 'price_unit': l.sb_price, 'account_id': account.id,
                                         'tax_ids': [Command.set(taxes_of.get(l.product_id, self.env['account.tax']).ids)],
                                         'sb_return_line_id': l.id}) for l in lines]})
        note.action_post()
        self.sb_credit_note_id = note
        self.move_id = note.move_id
        # each line's value: what its goods came back at
        live = note.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id and m.state == 'done')
        good_qty = defaultdict(float)
        for l in lines.filtered(lambda x: x.sb_condition == 'good'):
            good_qty[l.product_id] += l.quantity
        for l in lines:
            if l.sb_condition == 'damaged':
                value = sum(abs(m.value) for m in live.filtered(lambda m: m.sb_return_line_id == l))
            else:
                moves = live.filtered(lambda m: m.product_id == l.product_id and not m.sb_damaged_from_id)
                value = sum(abs(m.value) for m in moves) * l.quantity / good_qty[l.product_id]
            l.write({'value': value, 'unit_cost': value / l.quantity if l.quantity else 0.0})

    # ------------------------------------------------------------------ void
    def action_void(self):
        """A sales return is voided with its credit note (goods out again at exactly the value they came in at, both
        entries reversed); the other documents as any inventory voucher."""
        returns = self.filtered(lambda v: v.voucher_type == 'customer_return' and v.state == 'posted')
        for v in returns:
            if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
                raise UserError(self.env._('Only accountants can void inventory vouchers.'))
            v._sb_require_void_reason()
            note = v.sb_credit_note_id
            if note and note.state == 'posted':
                note.with_context(sb_void_reason=self.env._('%(ret)s voided: %(why)s', ret=v.name, why=v.sb_void_reason)).action_void()
            controlled(v).state = 'void'
            v.message_post(body=self.env._('Voided with its credit note %(cn)s. Reason: %(why)s', cn=note.name or '', why=v.sb_void_reason))
        return super(SbStockVoucher, self - returns).action_void() if self - returns else True

    def action_sb_view_credit_note(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': self.sb_credit_note_id.id, 'view_mode': 'form',
                'name': self.env._('Credit Note')}

    def action_sb_refund(self):
        """The customer is paid back in cash: a payment voucher applying the credit note (finance picks the bank / cash)."""
        self.ensure_one()
        note = self.sb_credit_note_id
        if not note or note.state != 'posted':
            raise UserError(self.env._('Post the return first.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'view_mode': 'form', 'name': self.env._('Refund'),
                'context': {'default_voucher_type': 'payment', 'default_partner_id': self.partner_id.id,
                            'default_payer_type': 'customer', 'default_reference': self.name,
                            'default_memo': self.env._('Refund of %s', self.name), 'sb_apply_move_ids': note.move_id.ids}}


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    sb_damaged_product_id = fields.Many2one('product.product', 'Damaged item', check_company=True,
                                            help='The damaged counterpart the goods become. Empty: the item\'s own (made the '
                                                 'first time).')
    sb_condition = fields.Selection([('damaged', 'Damaged'), ('good', 'Good (back to stock)')], 'Condition', default='damaged',
                                    help='Sales return: damaged goods go to the damaged stock, goods in good condition back to '
                                         'the good stock.')
    sb_price = fields.Float('Return price', digits='Product Price',
                            help='Sales return: the price credited to the customer (may differ from the sale price). The goods '
                                 'always come back at the cost they were sold at.')
    sb_side = fields.Selection([('out', 'Taken out (damaged)'), ('in', 'Received (recycled)')], 'Side', default='out')

    @api.onchange('product_id')
    def _onchange_sb_return_product(self):
        v = self.voucher_id
        if not self.product_id or v.voucher_type not in MY_TYPES:
            return
        Stock = self.env['sb.stock']
        company = v.company_id or self.env.company
        if v.voucher_type in ('damage', 'customer_return'):
            self.sb_damaged_product_id = self.product_id.product_tmpl_id.sb_damaged_product_id
        if v.voucher_type == 'damage':
            self.unit_cost = Stock._avg_on(self.product_id, company, v.date or fields.Date.context_today(self))
        if v.voucher_type == 'customer_return' and not self.sb_price:
            sold = v.sb_origin_voucher_id.line_ids.filtered(lambda l: l.product_id == self.product_id)[:1]
            self.sb_price = sold.price_unit if sold else self.product_id.lst_price


class SbStockDamagedOf(models.AbstractModel):
    _inherit = 'sb.stock'

    @api.model
    def _sb_damaged_counterpart_of(self, product, company):
        """The good item `product` is the damaged counterpart of (empty when it is not damaged stock)."""
        good = self.env['product.template'].sudo().search([('sb_damaged_product_id', '=', product.id)], limit=1)
        if good:
            return good
        dmg_classes = self.env['product.category'].sudo().with_company(company).search([]).mapped('sb_damaged_categ_id')
        return product.categ_id if product.categ_id in dmg_classes else self.env['product.category']
