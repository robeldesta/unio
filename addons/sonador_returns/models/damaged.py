"""Damaged stock: every item has a damaged counterpart, kept apart from the good stock.

The counterpart is an item of its own (e.g. "5 Liter Finished Product - Aquapure" -> "Defect Product - Aquapure") in the
damaged item class of the item's class (Finished Products -> Defect Products), so it has:
  - its own inventory account (the class's: 1504-0000 Finished Product -> 1504-0001 Finished Goods - Defect),
  - its own costing pool (its own moving average): damaged goods never change the average cost of the good stock,
  - its own place: the warehouse's "Damaged" location (inside the warehouse's stock, so the documents that already take
    damaged goods out of the store - goods issued, sales of damaged jerrycans, counts - keep working).
An item without a counterpart gets one the first time it is damaged (named with the damaged class's prefix).

Costing (the stock engine, sb.stock): the documents of this module move the good item out and the damaged item in at the
same value, and pass every later cost recalculation of the good item on to the damaged item (and, for a recycled product
receiving, from the damaged item to the recycled item), so the ledger, the stock records and every average stay equal.
"""
import threading

from odoo import api, fields, models
from odoo.exceptions import UserError

_DAMAGED_MEMO = threading.local()  # see SbStock._sb_damaged_returned

# documents that turn one item into another at cost: the good item (or the damaged one) goes out, another comes in at
# the same value. A cost recalculation of what went out is passed on to what came in.
CONVERSION_TYPES = ('damage', 'rprv', 'van_return')


class ProductCategory(models.Model):
    _inherit = 'product.category'

    sb_damaged_categ_id = fields.Many2one(
        'product.category', 'Damaged counterpart class', company_dependent=True,
        help='Where the damaged counterpart of this class\'s items goes (e.g. Finished Products -> Defect Products). Its '
             'inventory account is the damaged inventory account of this class.')
    sb_damaged_prefix = fields.Char('Damaged item name', default='Damaged - ',
                                    help='On a damaged class: the start of the name of a counterpart made automatically, '
                                         'e.g. "Defect Product - " gives "Defect Product - 2 Liter Finished Product".')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    sb_damaged_product_id = fields.Many2one(
        'product.product', 'Damaged counterpart', copy=False, check_company=True,
        help='The item this one becomes when damaged (store damage, damaged returns). Empty: made the first time.')


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_sales_return_account_id = fields.Many2one(
        'account.account', 'Sales returns and allowances',
        help='Debited by a sales return (the money side: the customer, or the cash refunded, is credited).')
    sb_return_cost_account = fields.Selection(
        [('cogs', 'Cost of sales of the item sold'), ('damage', 'Expense account of the damaged item class')],
        'Cost of a damaged return credited to', default='cogs', required=True,
        help='Goods coming back damaged: the damaged inventory is debited at the cost they were sold at, and this is '
             'credited.\nCost of sales of the item sold: the sale\'s own cost of sales is reversed (Sage, Peachtree credit '
             'memo on the invoice).\nExpense account of the damaged class: e.g. Damage of Product (as Eco Green\'s Peachtree '
             'entries DAM 0107 ... did).')
    sb_return_needs_invoice = fields.Boolean(
        'A sales return names its invoice', default=True,
        help='On: a sales return must name the invoice or cash sale it returns (its goods come back at the cost they were '
             'sold at). Off: without one they come back at the average cost on the return date.')


class StockLocation(models.Model):
    _inherit = 'stock.location'

    sb_damaged = fields.Boolean('Damaged stock', help='Where the warehouse keeps damaged goods (their damaged counterpart items).')
    sb_conversion = fields.Boolean('Stock conversion', help='Virtual place an item leaves through when it becomes another '
                                                           'item (damaged, recycled) at the same value.')


class SbStock(models.AbstractModel):
    _inherit = 'sb.stock'

    # ------------------------------------------------------------------ places and items
    @api.model
    def _sb_damaged_location(self, warehouse):
        """The warehouse's Damaged location (inside its stock), made the first time."""
        stock = warehouse.lot_stock_id
        Location = self.env['stock.location'].sudo()
        loc = Location.search([('sb_damaged', '=', True), ('location_id', '=', stock.id)], limit=1)
        if not loc:
            loc = Location.create({'name': self.env._('Damaged'), 'location_id': stock.id, 'usage': 'internal',
                                   'sb_damaged': True, 'company_id': warehouse.company_id.id})
        return loc.with_env(self.env)

    @api.model
    def _sb_conversion_location(self, company):
        Location = self.env['stock.location'].sudo()
        loc = Location.search([('sb_conversion', '=', True), ('company_id', 'in', (company.id, False))], limit=1)
        if not loc:
            parent = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
            loc = Location.create({'name': self.env._('Stock conversion'), 'usage': 'production', 'sb_conversion': True,
                                   'location_id': parent.id if parent else False, 'company_id': company.id})
        return loc.with_env(self.env)

    @api.model
    def _sb_damaged_counterpart(self, product, company):
        """The damaged counterpart of `product` (made in the damaged class the first time)."""
        tmpl = product.product_tmpl_id.sudo()
        if tmpl.sb_damaged_product_id:
            return tmpl.sb_damaged_product_id.with_env(self.env)
        if product.categ_id.with_company(company).sb_damaged_categ_id == product.categ_id or \
                self.env['product.category'].sudo().with_company(company).search_count([('sb_damaged_categ_id', '=', product.categ_id.id)]):
            return product  # already a damaged item
        categ = product.categ_id.with_company(company).sb_damaged_categ_id
        if not categ:
            raise UserError(self.env._('%(item)s: its item class %(cls)s has no damaged counterpart class. Set "Damaged counterpart '
                                       'class" on the item class (e.g. Finished Products -> Defect Products).',
                                       item=product.display_name, cls=product.categ_id.display_name))
        damaged = self.env['product.product'].sudo().create({
            'name': '%s%s' % (categ.sb_damaged_prefix or self.env._('Damaged - '), tmpl.name),
            'default_code': ('%s-D' % tmpl.default_code) if tmpl.default_code else False,
            'categ_id': categ.id, 'type': 'consu', 'is_storable': True, 'uom_id': tmpl.uom_id.id,
            'sale_ok': True, 'purchase_ok': False, 'company_id': tmpl.company_id.id,
            'standard_price': product.with_company(company).standard_price})
        tmpl.sb_damaged_product_id = damaged
        return damaged.with_env(self.env)

    # ------------------------------------------------------------------ cost recalculations
    @api.model
    def _sb_conversion_ins(self, m):
        """What came in for `m` (an item that went out on a conversion document): an SDV line's damaged item, an RPRV's
        recycled items, a van return's damaged items."""
        sv = m.sb_stock_voucher_id
        if not sv or sv.voucher_type not in CONVERSION_TYPES or self._direction(m) >= 0:
            return self.env['stock.move']
        ins = sv.stock_move_ids.filtered(lambda x: x.state == 'done' and not x.sb_reversal_of_id and not x.sb_cancelled
                                         and x.location_id.sb_conversion and self._direction(x) > 0)
        if sv.voucher_type != 'rprv':
            ins = ins.filtered(lambda x: x.sb_stock_line_id == m.sb_stock_line_id)
        return ins

    @api.model
    def _recost_counter(self, m, company):
        ins = self._sb_conversion_ins(m)
        if ins:
            # the change goes into what came in (passed on in _recost_pass_on)
            return self._valuation_account(ins[0].product_id, company)
        if m.sb_damaged_from_id:
            return self._sb_return_cost_account(m.sb_damaged_from_id, m.product_id, company)
        return super()._recost_counter(m, company)

    @api.model
    def _undo_recost_lines(self, m, company, name):
        """A void of a conversion takes back what the recalculations passed on once: on what went out (its own lines:
        Dr its inventory / Cr the first item that came in), plus, for an item that came in on another inventory account,
        the move between the two. What came in was only ever changed by what was passed on to it."""
        sv = m.sb_stock_voucher_id
        if sv and sv.voucher_type in CONVERSION_TYPES and m.location_id.sb_conversion and self._direction(m) > 0:
            total = m.sb_recost_total
            if company.currency_id.is_zero(total):
                return []
            outs = sv.stock_move_ids.filtered(lambda x: x.state == 'done' and not x.sb_reversal_of_id and x.location_dest_id.sb_conversion)
            ins = self.env['stock.move']
            for o in outs:
                ins = self._sb_conversion_ins(o)
                if m in ins:
                    break
            counter = self._valuation_account(ins[0].product_id, company) if ins else self._valuation_account(m.product_id, company)
            acc = self._valuation_account(m.product_id, company)
            if acc == counter:
                return []
            return [(counter, total, name, {'product_id': m.product_id.id}), (acc, -total, name, {'product_id': m.product_id.id})]
        return super()._undo_recost_lines(m, company, name)

    @api.model
    def _sb_return_cost_account(self, sold, damaged, company):
        """The account credited when goods come back damaged (the company's choice): the sold item's cost of sales, or
        the damaged class's expense account."""
        if company.sb_return_cost_account == 'damage':
            return self._cogs_account(damaged, company)
        return self._cogs_account(sold, company)

    @api.model
    def _sb_damaged_returned(self, product, per_day):
        """Ids of the item's issues that goods came back damaged against: one query per item and re-cost run (the run's
        `per_day`), not one per issue (re-costing 1,000 issues stays under 5 s). A re-cost creates no returns."""
        memo = getattr(_DAMAGED_MEMO, 'run', None)
        if memo is None or memo[0] is not per_day or memo[2] is not self.env.cr:
            memo = (per_day, {}, self.env.cr)
            _DAMAGED_MEMO.run = memo  # the last run only, per thread
        if product.id not in memo[1]:
            self.env['stock.move'].flush_model(['sb_return_of_id', 'sb_damaged_from_id', 'state', 'sb_cancelled',
                                                'sb_reversal_of_id', 'product_id'])
            self.env.cr.execute("""
                SELECT r.sb_return_of_id FROM stock_move r JOIN stock_move s ON s.id = r.sb_return_of_id
                 WHERE s.product_id = %s AND r.sb_damaged_from_id IS NOT NULL AND r.state = 'done'
                   AND NOT COALESCE(r.sb_cancelled, FALSE) AND r.sb_reversal_of_id IS NULL""", (product.id,))
            memo[1][product.id] = {row[0] for row in self.env.cr.fetchall()}
        return memo[1][product.id]

    @api.model
    def _recost_pass_on(self, m, delta, company, name, per_day):
        follow = super()._recost_pass_on(m, delta, company, name, per_day)
        cur = company.currency_id
        # 1. a conversion: the item that came in takes the change (by value share); its own issues follow
        ins = self._sb_conversion_ins(m)
        if ins:
            counter = self._valuation_account(ins[0].product_id, company)
            total = sum(abs(x.value) for x in ins) or sum(ins.mapped('quantity'))
            left = delta
            for i, x in enumerate(ins):
                share = left if i == len(ins) - 1 else cur.round(delta * (abs(x.value) or x.quantity) / total)
                left -= share
                self._write_move_amounts({x.id: abs(x.value) + share})
                self._write_move_amounts({x.id: share}, 'sb_recost_total', add=True)
                acc = self._valuation_account(x.product_id, company)
                if acc != counter:
                    per_day[m.date.date()] += [(acc, share, name, {'product_id': x.product_id.id}),
                                               (counter, -share, name, {'product_id': ins[0].product_id.id})]
                follow.append((x.product_id, x.date.date()))
        # 2. a sale whose goods came back damaged: the damaged goods follow the cost of the sale
        if self._direction(m) < 0 and m.quantity and m.id in self._sb_damaged_returned(m.product_id, per_day):
            back = self.env['stock.move'].sudo().search([('sb_return_of_id', '=', m.id), ('sb_damaged_from_id', '!=', False),
                                                         ('state', '=', 'done'), ('sb_cancelled', '=', False),
                                                         ('sb_reversal_of_id', '=', False)])
            first_open = self._first_open_day(company)
            for r in back:
                if self._uses_daily_average(r.product_id, company):
                    follow.append((r.product_id, r.date.date()))  # its own replay settles it at the sale's cost
                    continue
                if r.date.date() < first_open:
                    continue
                share = cur.round(delta * r.quantity / m.quantity)
                if cur.is_zero(share):
                    continue
                self._write_move_amounts({r.id: abs(r.value) + share})
                self._write_move_amounts({r.id: share}, 'sb_recost_total', add=True)
                per_day[r.date.date()] += [
                    (self._valuation_account(r.product_id, company), share, name, {'product_id': r.product_id.id}),
                    (self._sb_return_cost_account(m.product_id, r.product_id, company), -share, name,
                     self._counter_extra(r, {'product_id': r.product_id.id}))]
        return follow


class StockMove(models.Model):
    _inherit = 'stock.move'

    sb_damaged_from_id = fields.Many2one('product.product', 'Damaged return of', copy=False, index='btree_not_null',
                                         help='Goods that came back damaged: the item that was sold (this move brings in '
                                              'its damaged counterpart at the cost it was sold at).')
