"""The credit note of a sales return (RET): sonador_books' own Sales Return / Credit Note, with the damaged goods brought
back as their damaged counterpart, into the warehouse's Damaged location, at the cost they were sold at.

The credit note's entry (Odoo's cost of sales lines of a credit note, display type 'cogs') is then:
    Dr  Sales returns and allowances       return price x quantity
    Cr  customer                           (applied to the invoice)
    Dr  damaged inventory                  quantity x unit cost of the sale's delivery of the item
    Cr  cost of sales of the item sold     (or the damaged class's expense, as the company chooses)
A line in good condition stays as any credit note: Dr inventory / Cr cost of sales, the goods back to the good stock.
"""
from odoo import api, fields, models

from odoo.addons.sonador_books.models.move_guard import controlled  # noqa: F401  (flows)


class StockMove(models.Model):
    _inherit = 'stock.move'

    sb_return_line_id = fields.Many2one('sb.stock.voucher.line', 'Sales return line', copy=False, index='btree_not_null',
                                        ondelete='set null')


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_return_id = fields.Many2one('sb.stock.voucher', 'Sales return', copy=False, readonly=True, index='btree_not_null',
                                   help='The sales return (RET) this credit note is the money side of.')

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        if self.sb_return_id:
            commands = [c for c in vals.get('invoice_line_ids', []) if c[0] == 0]
            for cmd, line in zip(commands, self.line_ids):
                if line.sb_return_line_id:
                    cmd[2]['sb_return_line_id'] = line.sb_return_line_id.id
        return vals

    def _sb_stock_lines(self):
        lines = super()._sb_stock_lines()
        if self.env.context.get('sb_returns_skip_damaged'):
            lines = lines.filtered(lambda l: l.sb_return_line_id.sb_condition != 'damaged')
        return lines

    def _sb_sold_cost(self, product):
        """(unit cost, delivery move) of `product` on the invoice / cash sale returned: what its delivery took (several
        deliveries: their weighted cost), or (None, None) without a sale."""
        moves = self.origin_id.sb_stock_move_ids.filtered(
            lambda m: m.product_id == product and not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')
        # the delivery out of stock; a sale delivered later (goods first to 'awaiting dispatch'): the move it posted
        moves = moves.filtered(lambda m: self.env['sb.stock']._direction(m) < 0) or moves
        qty = sum(moves.mapped('quantity'))
        if not moves or not qty:
            return None, None
        return sum(abs(m.value) for m in moves) / qty, moves.sorted('id')[:1]

    def _sb_stock_out(self):
        if self.voucher_type != 'sale_return' or not self.sb_return_id:
            return super()._sb_stock_out()
        if self.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id and m.state == 'done'):
            return
        super(SbVoucher, self.with_context(sb_returns_skip_damaged=True))._sb_stock_out()
        Stock, company = self.env['sb.stock'], self.company_id
        loc = Stock._locations(company)
        dmg = Stock._sb_damaged_location(self.sb_warehouse_id)
        ref = self.name if self.name not in (False, '/', 'New') else (self.reference or self.env._('Voucher'))
        for l in self.line_ids.filtered(lambda x: x.product_id.is_storable and x.sb_return_line_id.sb_condition == 'damaged'):
            rl = l.sb_return_line_id
            damaged = rl.sb_damaged_product_id or Stock._sb_damaged_counterpart(l.product_id, company)
            unit, sold = self._sb_sold_cost(l.product_id)
            follow = {'sb_return_of_id': sold.id} if sold else {}
            if unit is None:
                unit = Stock._avg_on(l.product_id, company, self.date)
            Stock._move(damaged, l.quantity, loc['customer'], dmg, self.date, ref, company, unit_cost=unit,
                        link=dict({'sb_voucher_id': self.id, 'sb_damaged_from_id': l.product_id.id, 'sb_return_line_id': rl.id}, **follow))


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    sb_return_line_id = fields.Many2one('sb.stock.voucher.line', 'Sales return line', copy=False, index='btree_not_null')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sb_return_line_id = fields.Many2one('sb.stock.voucher.line', 'Sales return line', copy=False, index='btree_not_null')

    def _get_cogs_value(self):
        rl = self.sb_return_line_id
        if rl and rl.sb_condition == 'damaged':
            voucher = self.env['sb.voucher'].search([('move_id', '=', self.move_id.id)], limit=1)
            moves = voucher.sb_stock_move_ids.filtered(lambda m: m.sb_return_line_id == rl and not m.sb_reversal_of_id)
            qty = sum(moves.mapped('quantity'))
            if qty:
                return sum(abs(m.value) for m in moves) / qty
        return super()._get_cogs_value()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _stock_account_prepare_realtime_out_lines_vals(self):
        """Damaged goods of a sales return: the inventory side of the cost lines is the damaged item's inventory account,
        the other side the company's choice (cost of sales of the item sold, or the damaged class's expense)."""
        vals_list = super()._stock_account_prepare_realtime_out_lines_vals()
        Line, Stock = self.env['account.move.line'], self.env['sb.stock']
        for vals in vals_list:
            origin = Line.browse(vals.get('cogs_origin_id'))
            rl = origin.sb_return_line_id
            if not rl or rl.sb_condition != 'damaged' or not rl.sb_damaged_product_id:
                continue
            company = origin.move_id.company_id
            sold, damaged = origin.product_id, rl.sb_damaged_product_id
            if vals.get('amount_currency', 0) > 0:  # a credit note's cost lines: inventory debited, cost of sales credited
                vals.update(account_id=Stock._valuation_account(damaged, company).id, product_id=damaged.id)
            else:
                vals['account_id'] = Stock._sb_return_cost_account(sold, damaged, company).id
        return vals_list
