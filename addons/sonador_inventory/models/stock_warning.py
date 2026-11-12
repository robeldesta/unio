"""Selling without stock, Sage style: the sale warns and the user can sell anyway (the next receipt trues the cost up),
unless the company never allows it. Goods issued are charged to the item's own consumption account by default."""
from datetime import datetime, time, timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

SALE_OUT = ('cash_sale', 'sale')


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_block_negative_sales = fields.Boolean(
        'Never sell without stock',
        help='Off (Sage): a sale of more than is on hand warns, and can be posted anyway with "Sell without stock". '
             'On: such a sale is refused. "Allow negative stock" lets everything through without a warning.')


class SbStock(models.AbstractModel):
    _inherit = 'sb.stock'

    @api.model
    def _check_available(self, company, wanted, location, what, day=None):
        ctx = self.env.context
        if ctx.get('sb_sell_anyway') and not company.sb_block_negative_sales:
            return
        try:
            return super()._check_available(company, wanted, location, what, day)
        except UserError as e:
            if ctx.get('sb_sale_check') and not company.sb_block_negative_sales:
                raise UserError(self.env._('%s\n\nTo sell anyway, tick "Sell without stock" on the voucher and post again.',
                                           e.args[0])) from None
            raise


    @api.model
    def _sb_cost_warning(self, product, company, day, unit_cost=None):
        """An issue cost far from what the item was bought at (non-blocking): the average more than 50% off the last receipt
        cost, or stock value left on an item with nothing on hand. Usually stock value that needs an adjustment."""
        if not product.is_storable:
            return False
        cur = company.currency_id
        qty, value = self._state_before(product, company, day + timedelta(days=1))
        if abs(qty) < 1e-9 and not cur.is_zero(value):
            return self.env._('%(item)s: stock value %(value)s with nothing on hand. The item\'s stock value may need an '
                              'adjustment.', item=product.display_name, value='{:,.2f}'.format(value))
        if not unit_cost:
            unit_cost = value / qty if qty > 1e-9 else 0.0
        move = self.env['stock.move'].sudo().search([
            ('product_id', '=', product.id), ('company_id', '=', company.id), ('state', '=', 'done'), ('sb_cancelled', '=', False),
            ('date', '<=', datetime.combine(day, time.max)), ('quantity', '>', 0),
            ('location_dest_id.usage', '=', 'internal'), ('location_id.usage', 'not in', ('internal', 'inventory'))],
            order='date desc, id desc', limit=1)
        last = abs(move.value) / move.quantity if move else 0.0
        if not unit_cost or last < 0.005 or abs(unit_cost - last) <= 0.5 * last:
            return False
        ratio = unit_cost / last
        if ratio >= 2:
            how = self.env._('%s× the', ('{:,.0f}' if ratio >= 3 else '{:,.1f}').format(ratio))
        elif ratio > 1:
            how = self.env._('%s%% above the', '{:,.0f}'.format((ratio - 1) * 100))
        else:
            how = self.env._('%s%% below the', '{:,.0f}'.format((1 - ratio) * 100))
        return self.env._('%(item)s: average cost %(avg)s is %(how)s last purchase price %(last)s. The item\'s stock value '
                          'may need an adjustment.', item=product.display_name, avg='{:,.2f}'.format(unit_cost), how=how,
                          last='{:,.2f}'.format(last))


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_sell_anyway = fields.Boolean('Sell without stock', copy=False,
                                    help='Post this sale although the warehouse does not have all of it (Sage: negative stock). '
                                         'The cost is trued up when the goods are received.')
    sb_stock_short = fields.Text('Stock warning', compute='_compute_sb_stock_short')
    sb_negative_blocked = fields.Boolean(related='company_id.sb_block_negative_sales')
    sb_below_cost = fields.Text('Below cost', compute='_compute_sb_below_cost')
    sb_cost_warning = fields.Text('Unusual cost', compute='_compute_sb_cost_warning')

    @api.depends('line_ids.product_id', 'voucher_type', 'date')
    def _compute_sb_cost_warning(self):
        for v in self:
            v.sb_cost_warning = v._sb_cost_warning_lines() if v.voucher_type in SALE_OUT else False

    def _sb_cost_warning_lines(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        day = self.date or fields.Date.context_today(self)
        notes = [self.env['sb.stock']._sb_cost_warning(p, company, day)
                 for p in self.line_ids.filtered(lambda x: x.quantity > 0).product_id]
        return '\n'.join(n for n in notes if n) or False

    @api.depends('line_ids.product_id', 'line_ids.quantity', 'sb_warehouse_id', 'voucher_type', 'state')
    def _compute_sb_stock_short(self):
        Stock = self.env['sb.stock']
        for v in self:
            v.sb_stock_short = False
            if v.state != 'draft' or v.voucher_type not in SALE_OUT or not v.sb_warehouse_id \
                    or v.company_id.sb_allow_negative_stock:
                continue
            wanted = {}
            for l in v.line_ids.filtered(lambda x: x.product_id.is_storable and x.quantity > 0):
                wanted[l.product_id] = wanted.get(l.product_id, 0.0) + l.quantity
            short = []
            for product, qty in wanted.items():
                have = Stock._on_hand(product, v.sb_warehouse_id.lot_stock_id)
                if float_compare(qty, have, precision_rounding=product.uom_id.rounding) > 0:
                    short.append(self.env._('%(item)s: %(have)s on hand, %(qty)s on this sale',
                                            item=product.display_name, have='{:,.2f}'.format(have), qty='{:,.2f}'.format(qty)))
            v.sb_stock_short = '\n'.join(short) or False

    @api.depends('line_ids.product_id', 'line_ids.price_unit', 'voucher_type', 'state', 'date', 'doc_currency_id')
    def _compute_sb_below_cost(self):
        for v in self:
            v.sb_below_cost = v._sb_below_cost_lines() if v.state == 'draft' else False

    def _sb_below_cost_lines(self):
        """'Selling below cost' lines (non-blocking): a line priced under the item's current average cost."""
        self.ensure_one()
        if self.voucher_type not in SALE_OUT:
            return False
        company = self.company_id or self.env.company
        doc_currency = self.doc_currency_id or company.currency_id
        day = self.date or fields.Date.context_today(self)
        below = []
        for l in self.line_ids.filtered(lambda x: x.product_id.is_storable and x.quantity > 0):
            cost = l.product_id.with_company(company).standard_price
            price = doc_currency._convert(l.price_unit, company.currency_id, company, day)
            if cost > 0 and company.currency_id.compare_amounts(price, cost) < 0:
                below.append(self.env._('%(item)s: price %(price)s, cost %(cost)s',
                                        item=l.product_id.display_name, price='{:,.2f}'.format(price), cost='{:,.2f}'.format(cost)))
        return '\n'.join(below) or False

    def action_post(self):
        notes = {v: v._sb_below_cost_lines() for v in self if v.state == 'draft' and v.voucher_type in SALE_OUT}
        costs = {v: v._sb_cost_warning_lines() for v in notes}
        res = super().action_post()
        for v, note in notes.items():
            if note and v.state == 'posted':
                v.message_post(body=Markup('<br/>').join([self.env._('Sold below cost:')] + note.split('\n')))
            if costs[v] and v.state == 'posted':
                v.message_post(body=Markup('<br/>').join([self.env._('Unusual cost:')] + costs[v].split('\n')))
        return res

    def _check_before_post(self):
        if self.voucher_type in SALE_OUT:
            flow = self.with_context(sb_sale_check=True, sb_sell_anyway=self.sb_sell_anyway)
            return super(SbVoucher, flow)._check_before_post()
        return super()._check_before_post()


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    @api.onchange('product_id')
    def _onchange_product(self):
        """Goods issued: charged to the item class's "Issues go to" account (work in process for production materials),
        else the item's own expense (consumption) account, not the company's catch-all."""
        res = super()._onchange_product()
        if self.product_id and self.voucher_type == 'giv':
            company = self.voucher_id.company_id or self.env.company
            account = self.product_id.categ_id.with_company(company).sb_issue_account_id or \
                self.product_id.with_company(company).product_tmpl_id.get_product_accounts().get('expense')
            if account:
                self.account_id = account
        return res


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_cost_warning = fields.Text('Unusual cost', compute='_compute_sb_cost_warning')

    @api.model
    def _sb_fmt_cost(self, cost):
        """A unit cost as printed: money precision (141.45), more decimals only when it has them (3.3333)."""
        text = '{:,.4f}'.format(cost or 0.0)
        head, tail = text.split('.')
        return '%s.%s' % (head, tail.rstrip('0').ljust(2, '0'))

    @api.depends('line_ids.product_id', 'line_ids.unit_cost', 'voucher_type', 'date', 'state')
    def _compute_sb_cost_warning(self):
        for v in self:
            v.sb_cost_warning = v._sb_cost_warning_lines() if v.voucher_type == 'giv' and v.state != 'void' else False

    def _sb_cost_warning_lines(self):
        """Goods issued at a cost far from the last purchase price (non-blocking)."""
        self.ensure_one()
        company = self.company_id or self.env.company
        day = self.date or fields.Date.context_today(self)
        notes = []
        for l in self.line_ids.filtered(lambda x: x.product_id and x.quantity > 0):
            note = self.env['sb.stock']._sb_cost_warning(l.product_id, company, day, unit_cost=l.unit_cost)
            if note and note not in notes:
                notes.append(note)
        return '\n'.join(notes) or False

    def action_post(self):
        res = super().action_post()
        for v in self.filtered(lambda v: v.voucher_type == 'giv' and v.state == 'posted'):
            note = v._sb_cost_warning_lines()
            if note:
                v.message_post(body=Markup('<br/>').join([self.env._('Unusual cost:')] + note.split('\n')))
        return res
