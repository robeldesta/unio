from odoo import fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    sb_has_cost = fields.Boolean(copy=False, help='The cost comes from the voucher, not the product cost.')
    sb_unit_cost = fields.Float('Document unit cost', copy=False)  # unrounded: quantity x unit cost must give the exact value back
    sb_stock_voucher_id = fields.Many2one('sb.stock.voucher', 'Inventory voucher', index='btree_not_null', copy=False)
    sb_stock_line_id = fields.Many2one('sb.stock.voucher.line', 'Inventory voucher line', index='btree_not_null', copy=False,
                                       ondelete='set null')
    sb_voucher_id = fields.Many2one('sb.voucher', index='btree_not_null', copy=False)
    sb_reversal_of_id = fields.Many2one('stock.move', 'Reverses', copy=False, index='btree_not_null')
    # documents brought over from another system (e.g. Peachtree FPRR-1552, GIV 2569, FS00001323)
    sb_ref = fields.Char('Document no.', copy=False, index='btree_not_null')
    sb_doc_type = fields.Char('Document type', copy=False)
    sb_party = fields.Char('Customer / supplier / department', copy=False)

    def _get_value_from_account_move(self, quantity, at_date=None):
        # GRN, opening stock, purchases and returns carry their own cost
        if self.sb_has_cost:
            return {'value': self.sb_unit_cost * quantity, 'quantity': quantity,
                    'description': self.env._('Cost from %s', self.origin or self.reference or '')}
        return super()._get_value_from_account_move(quantity, at_date)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _get_cogs_value(self):
        # sales invoices / credit notes posted by a voucher: the cost is what the stock move took (FIFO / average)
        voucher = self.env['sb.voucher'].search([('move_id', '=', self.move_id.id)], limit=1) if self.move_id.id else False
        if voucher:
            moves = voucher.sb_stock_move_ids.filtered(lambda m: m.product_id == self.product_id and not m.sb_reversal_of_id)
            qty = sum(moves.mapped('quantity'))
            if moves and qty:
                return abs(sum(moves.mapped('value'))) / qty
        return super()._get_cogs_value()


class SbStockRevaluation(models.Model):
    """A change in the value of stock without a change in quantity: cost adjustments, landed costs added later,
    revaluations, and value-only lines brought over from Peachtree."""
    _name = 'sb.stock.revaluation'
    _description = 'Stock Cost Adjustment'
    _order = 'date, id'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    product_id = fields.Many2one('product.product', 'Item', required=True, index=True)
    date = fields.Date(required=True, index=True)
    value = fields.Monetary(required=True, help='Positive: stock is worth more. Negative: worth less.')
    reference = fields.Char()
    description = fields.Char()
    move_id = fields.Many2one('account.move', 'Journal entry', help='Empty when the money side was posted elsewhere (e.g. migrated history).')
    voucher_id = fields.Many2one('sb.voucher', 'Bill', index='btree_not_null', readonly=True, copy=False,
                                 help='A bill for goods already received (GRN) at a price different from the GRN cost, or with VAT '
                                      'the company cannot claim: the difference is part of the cost of the goods, from the GRN date.')
