"""Average costing (AVCO, the Sage 200 / X3 way) with the same-day receipt rule.

Within a day, receipts come first: an issue or sale dated D is costed at the average of everything in stock at the
end of D-1 plus all receipts dated D. Every movement of an average-cost item - a receipt, an issue, a void or a
correction, back-dated or not - replays the item from its date: later issues are re-costed and the difference is posted
on their own dates, to cost of sales, to the expense of the GIV, or into the finished goods of the build (or the
FPRR) that took the item.

Voids and corrections: the reversal takes the stock out (or back) at exactly the value of the original, and both are
then left out of the average for good, so they always net to zero and everything after is costed as if the document
had never been posted. A correction (reset to draft) does the same until the document is posted again, which re-costs
them from its date: meanwhile the stock card, the valuation and the ledger agree on quantity and value.

Negative stock: an issue that takes more than is on hand goes out at the latest average; the receipt that covers the
shortfall trues it up to the receipt's cost (Sage's negative stock adjustment). The difference is posted on the
issue's own date, like every other re-cost, so the stock records equal the ledger on every date.

Sales returns: goods a customer brings back come in at the unit cost of the delivery they return, and follow it when
that sale is re-costed (the difference goes to cost of sales on the return's date). A return with no sale behind it
comes in at the average on its own date, and follows that average like an issue does.

Speed: an item is replayed from plain rows read in one query, every changed value is written in one statement, and
the recalculation entries of one action are created and posted together: one entry per affected date (an entry has
one date, and the stock records must equal the ledger on every date), never one per issue. A company with very busy
items can choose one entry per month instead (on its last day): far fewer entries, the ledger then agrees with the stock
records at each month end.

Migrated history (fixed values) and closed periods are never changed.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta


from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of
from odoo.addons.sonador_books.models.move_guard import controlled, in_flow


def _period_end(day):
    """Last day of the accounting period holding day: the Ethiopian month (Pagume with Nehase) or the calendar
    month, as the books are kept, so stock and ledger agree at every period end the reports use."""
    return next(p[3] for p in fiscal_periods(fiscal_year_of(day)) if p[2] <= day <= p[3])

# sales vouchers whose cost of sales a recalculation changes
SALE_TYPES = ('cash_sale', 'sale', 'sale_return')

# a recalculation that keeps passing changes between items (an item made, in the end, from itself) is stopped loudly
MAX_RECOST_STEPS = 10000


class StockMove(models.Model):
    _inherit = 'stock.move'

    sb_fixed_value = fields.Boolean('Value fixed', copy=False,
                                    help='Never re-costed (migrated history, voids). Still counts in the running average.')
    sb_cancelled = fields.Boolean('Undone', copy=False, index=True,
                                  help='Voided or corrected: this move and its reversal net to zero and are left out of the average.')
    sb_recost_total = fields.Float('Re-costed by', copy=False,
                                   help='Total of the cost recalculations posted for this move since its document was posted.')
    sb_return_of_id = fields.Many2one('stock.move', 'Return of', copy=False, index='btree_not_null',
                                      help='Goods a customer brought back: they come back at the unit cost of this delivery, '
                                           'and follow it when the sale is re-costed.')
    sb_at_average = fields.Boolean('At the average', copy=False,
                                   help='Goods brought back with no sale to follow: valued at the average cost on their date, '
                                        'and re-costed with it.')
    sb_bill_cost = fields.Float('Bill difference', copy=False, digits=(16, 2),
                                help='Goods received (GRN) billed at another price (or with VAT the company cannot claim): what '
                                     'the bill added to the cost of this receipt.')
    sb_bill_id = fields.Many2one('sb.voucher', 'Billed by', copy=False, index='btree_not_null',
                                 help='The bill whose price difference is in the cost of this receipt.')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sb_recost_move_id = fields.Many2one('stock.move', 'Re-costed delivery', index='btree_not_null', readonly=True, copy=False,
                                        help='Cost recalculation line: the delivery (or return) of the sale whose cost it changes.')


class AccountMove(models.Model):
    """Entries the stock engine posts (cost recalculations, their reversals, a GIV's change passed into finished goods,
    price and return differences) belong to the stock records: like a document's entry, they change only through the
    engine. Reversed, reset or edited by hand (Odoo's Reverse button), the ledger would leave the stock records and nothing
    would bring it back."""
    _inherit = 'account.move'

    sb_stock_engine = fields.Boolean('Posted by the stock engine', copy=False, readonly=True, index=True,
                                     help='Cost recalculation and other entries the stock records are kept by: they change only '
                                          'when the stock documents change.')

    def _sb_engine_refused(self):
        """Refuse a change to engine entries (and their reversals) outside the app's flows. Document entries keep the
        message naming their document."""
        if in_flow(self.env):
            return
        engine = self.filtered(lambda m: m.sb_stock_engine or m.reversed_entry_id.sb_stock_engine)
        if not engine:
            return
        docs = engine._sb_documents()
        engine = engine.filtered(lambda m: m not in docs)
        if engine:
            raise UserError(self.env._(
                '%s was posted by the stock costing (a cost recalculation or a cost difference): it follows the stock '
                'records and cannot be reversed, reset or changed by hand. Correct or void the stock document (receipt, '
                'issue or sale) that caused it, or enter a stock adjustment.',
                ', '.join(engine.mapped(lambda m: m.name if m.name and m.name != '/' else m.ref or str(m.id)))))

    def _sb_check_change(self, labels=None):
        self._sb_locked()._sb_engine_refused()
        return super()._sb_check_change(labels=labels)

    def write(self, vals):
        if 'sb_stock_engine' in vals and not in_flow(self.env):
            raise UserError(self.env._('"Posted by the stock engine" is set by the stock costing only.'))
        return super().write(vals)

    def _post(self, soft=True):
        # a journal entry typed by hand on an inventory (stock valuation) account changes the ledger without the stock
        # records: a manager's decision only (stock is changed by a stock document or a stock count / adjustment)
        if not in_flow(self.env):
            for move in self.filtered(lambda m: m.move_type == 'entry' and not m.sb_stock_engine):
                self.env['sb.stock']._check_manual_stock_lines(move.company_id, move.line_ids.account_id,
                                                               move.name if move.name and move.name != '/' else move.ref or '')
        return super()._post(soft=soft)

    def _reverse_moves(self, default_values_list=None, cancel=False):
        self._sb_engine_refused()
        return super()._reverse_moves(default_values_list, cancel=cancel)

    @api.model
    def _sb_mark_engine_entries(self):
        """On upgrade: flag the entries the stock engine posted before the flag was kept (Inventory journal: the
        recalculations, their reversals, the differences passed on; and the entries of stock documents)."""
        self.env.cr.execute("""
            UPDATE account_move m SET sb_stock_engine = TRUE
              FROM res_company c
             WHERE m.journal_id = c.account_stock_journal_id AND m.company_id = c.id AND m.move_type = 'entry'
               AND COALESCE(m.sb_stock_engine, FALSE) = FALSE
               AND (m.ref LIKE 'Cost recalculation%%' OR m.ref LIKE 'Recalculations reversed%%'
                    OR m.ref LIKE '%%cost from work in process%%' OR m.ref LIKE 'Price difference %%'
                    OR m.ref LIKE 'Cost difference on return %%'
                    OR m.id IN (SELECT move_id FROM sb_stock_voucher WHERE move_id IS NOT NULL)
                    OR m.id IN (SELECT move_id FROM sb_voucher_cost_move_rel))""")


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _update_standard_price(self, extra_value=None, extra_quantity=None):
        # a receipt posted by the stock engine: Odoo would re-average the item from all its moves (most of the time of a
        # back-dated GRN on a busy item); the engine's own replay, which follows at once, sets the cost (_sync_standard_price)
        if self.env.context.get('sb_engine_average'):
            self = self.filtered(lambda p: p.cost_method != 'average' or p.lot_valuated)
        return super(ProductProduct, self)._update_standard_price(extra_value=extra_value, extra_quantity=extra_quantity)


class SbStock(models.AbstractModel):
    _inherit = 'sb.stock'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _direction(self, m):
        src, dst = m.location_id.usage == 'internal', m.location_dest_id.usage == 'internal'
        return 1 if dst and not src else (-1 if src and not dst else 0)

    @api.model
    def _valued_moves(self, product, company, date_from=None):
        dom = [('product_id', '=', product.id), ('company_id', '=', company.id), ('state', '=', 'done')]
        if date_from:
            dom.append(('date', '>=', datetime.combine(date_from, time.min)))
        moves = self.env['stock.move'].sudo().search(dom)
        return moves.filtered(lambda m: self._direction(m) != 0)

    @api.model
    def _left_out(self, m, start):
        """A voided / corrected move and its reversal are left out of a replay starting on `start`. The one exception is a
        void of a document in a closed period: its reversal (dated in the open period) takes the original's value out."""
        if not m.sb_cancelled:
            return False
        if m.sb_reversal_of_id:
            return m.sb_reversal_of_id.date.date() >= start
        return True

    @api.model
    def _stock_valuation_accounts(self, company):
        """The inventory (stock valuation) accounts of the company: its default and every item class's."""
        cats = self.env['product.category'].sudo().with_company(company).search([])
        return cats.mapped('property_stock_valuation_account_id') | company.sudo().account_stock_valuation_id

    @api.model
    def _check_manual_stock_lines(self, company, accounts, what):
        """Refuse a journal entry / journal voucher typed on an inventory account unless a manager posts it."""
        if self.env.su or self.env.user.has_group('sonador_books.group_sb_manager'):
            return
        hit = accounts & self._stock_valuation_accounts(company)
        if hit:
            raise UserError(self.env._(
                '%(what)s: %(accounts)s is an inventory account kept by the stock records (each item\'s quantity and value). '
                'A journal entry on it would make the ledger differ from the stock: enter a stock count / adjustment, or a '
                'GRN or GIV, instead. Only a manager can post a journal entry to an inventory account.',
                what=what or self.env._('This entry'), accounts=', '.join(hit.mapped('display_name'))))

    @api.model
    def _first_open_day(self, company):
        return company._get_user_fiscal_lock_date(company.account_stock_journal_id) + timedelta(days=1)

    @api.model
    def _state_before(self, product, company, day):
        """(qty, value) at the end of day - 1 (moves and cost adjustments). One aggregate query: replaying every move of a
        busy product made each sale slower as the year went on. Voided pairs net to zero, so they need no special care."""
        self.env['stock.move'].flush_model(['product_id', 'company_id', 'state', 'date', 'quantity', 'value', 'location_id', 'location_dest_id'])
        self.env['stock.location'].flush_model(['usage'])
        self.env['sb.stock.revaluation'].flush_model(['product_id', 'company_id', 'date', 'value'])
        self.env.cr.execute("""
            WITH d AS (
                SELECT CASE WHEN dst.usage = 'internal' AND src.usage <> 'internal' THEN 1
                            WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN -1 ELSE 0 END AS dir,
                       m.quantity, ABS(COALESCE(m.value, 0)) AS val
                  FROM stock_move m
                  JOIN stock_location src ON src.id = m.location_id
                  JOIN stock_location dst ON dst.id = m.location_dest_id
                 WHERE m.product_id = %(p)s AND m.company_id = %(c)s AND m.state = 'done' AND m.date < %(t)s)
            SELECT COALESCE(SUM(dir * quantity), 0), COALESCE(SUM(dir * val), 0)
                   + (SELECT COALESCE(SUM(value), 0) FROM sb_stock_revaluation WHERE product_id = %(p)s AND company_id = %(c)s AND date < %(day)s)
              FROM d""", {'p': product.id, 'c': company.id, 't': datetime.combine(day, time.min), 'day': day})
        qty, value = self.env.cr.fetchone()
        return float(qty), float(value)

    @api.model
    def _last_cost(self, product, company, day):
        """Cost of the latest receipt up to `day`: what an issue takes when nothing is in stock (Sage: last cost price).
        With no receipt in effect up to `day`, the last known cost of the item: the latest receipt up to `day` that is out
        for a correction (it is coming back), else the first receipt after `day` (the one that covers the shortfall). The
        item's own cost (standard_price) only for an item never received: it follows the stock records and can be far off
        while a receipt is out for a correction. A voided receipt never counts: it is as if it had never been posted."""
        Move = self.env['stock.move'].sudo()
        base = [('product_id', '=', product.id), ('company_id', '=', company.id), ('state', '=', 'done'), ('quantity', '>', 0),
                ('location_dest_id.usage', '=', 'internal'), ('location_id.usage', '!=', 'internal'), ('sb_reversal_of_id', '=', False)]
        upto, after = ('date', '<=', datetime.combine(day, time.max)), ('date', '>', datetime.combine(day, time.max))
        in_correction = [('sb_cancelled', '=', True), ('sb_stock_voucher_id.state', '=', 'draft'),
                         ('sb_stock_voucher_id.sb_correction_before', '!=', False)]
        for dom, order in (([upto, ('sb_cancelled', '=', False)], 'date desc, id desc'), ([upto] + in_correction, 'date desc, id desc'),
                           ([after, ('sb_cancelled', '=', False)], 'date, id')):
            move = Move.search(base + dom, order=order, limit=1)
            if move and move.value:
                return abs(move.value) / move.quantity
        return product.with_company(company).standard_price

    @api.model
    def _avg_on(self, product, company, day, exclude=None):
        """Same-day receipt rule: average at the end of day-1 plus every receipt and cost adjustment dated `day`."""
        qty, value = self._state_before(product, company, day)
        start, end = datetime.combine(day, time.min), datetime.combine(day, time.max)
        dom = [('product_id', '=', product.id), ('company_id', '=', company.id), ('state', '=', 'done'),
               ('date', '>=', start), ('date', '<=', end)]
        for m in self.env['stock.move'].sudo().search(dom):
            if (exclude and m in exclude) or self._left_out(m, day):
                continue
            if self._direction(m) > 0:
                qty += m.quantity
                value += abs(m.value)
        value += sum(self.env['sb.stock.revaluation'].sudo().search(
            [('product_id', '=', product.id), ('company_id', '=', company.id), ('date', '=', day)]).mapped('value'))
        if qty > 1e-9:
            return value / qty
        return self._last_cost(product, company, day)

    @api.model
    def _uses_daily_average(self, product, company):
        return product.with_company(company).cost_method == 'average'

    @api.model
    def _sync_standard_price(self, product, company):
        """Keep Odoo's own cost (standard_price) equal to the engine's current average, so every screen and report that
        shows it (item costing, transfers, the cost proposed on new lines) agrees with the stock records."""
        far = fields.Date.context_today(self) + timedelta(days=36500)
        qty, value = self._state_before(product, company, far)
        cost = value / qty if qty > 1e-9 else self._last_cost(product, company, far)
        p = product.with_company(company).sudo()
        if abs(p.standard_price - cost) > 1e-6:
            p.with_context(disable_auto_revaluation=True).standard_price = cost

    @api.model
    def _sync_all_standard_prices(self):
        """On upgrade: bring Odoo's cost of every average-cost item in line with the stock records."""
        for company in self.env['res.company'].search([]):
            for p in self.env['product.product'].with_company(company).search([('is_storable', '=', True)]):
                if self._uses_daily_average(p, company):
                    self._sync_standard_price(p, company)

    @api.model
    def _link_sale_returns(self):
        """On upgrade: goods brought back on a credit note follow the delivery of the sale it credits (from the next
        recalculation of the item on). Values are not changed here."""
        returns = self.env['stock.move'].sudo().search([('sb_voucher_id.voucher_type', '=', 'sale_return'), ('state', '=', 'done'),
                                                        ('sb_return_of_id', '=', False), ('sb_reversal_of_id', '=', False),
                                                        ('sb_fixed_value', '=', False), ('sb_cancelled', '=', False)])
        for m in returns:
            sold = m.sb_voucher_id.origin_id.sb_stock_move_ids.filtered(
                lambda o: o.product_id == m.product_id and not o.sb_reversal_of_id and not o.sb_cancelled and o.state == 'done')[:1]
            if sold:
                m.sb_return_of_id = sold

    # ------------------------------------------------------------------ out moves
    def _sb_cost_out(self, move, day):
        """Value an issue / sale / consumption under the same-day rule; returns the change made."""
        company = move.company_id
        if not self._uses_daily_average(move.product_id, company) or move.sb_fixed_value or self._direction(move) >= 0:
            return 0.0
        new = company.currency_id.round(move.quantity * self._avg_on(move.product_id, company, day, exclude=move))
        delta = new - abs(move.value)
        if abs(delta) > 0.005:
            move.value = new
        return delta

    @api.model
    def _move(self, product, qty, src, dst, day, origin, company, unit_cost=None, link=None, reverses=None):
        if not reverses and self._uses_daily_average(product, company):
            self = self.with_context(sb_engine_average=True)  # the replay below sets the item's cost
        move = super()._move(product, qty, src, dst, day, origin, company, unit_cost=unit_cost, link=link, reverses=reverses)
        if reverses:
            # exactly the original's value: the pair nets to zero and both leave the average (the caller re-costs)
            move.write({'value': abs(reverses.value), 'sb_fixed_value': True, 'sb_cancelled': True})
            reverses.sb_cancelled = True
            return move
        if self._direction(move) and self._uses_daily_average(product, company):
            self._lock_items(company, product)
            self._sb_cost_out(move, day)
            # the document posts its own entry with what the move ends up worth: no recalculation entry for it
            self._recost(product, company, day, reason=origin, fresh=move)
        return move

    # ------------------------------------------------------------------ re-costing
    @api.model
    def _recost(self, product, company, day, reason='', fresh=None):
        """Replay `product` from `day` (open periods only), then every finished good a re-costed build or FPRR passes the
        change on to. `fresh`: moves whose document is being posted now (their value changes, no entry is posted for them).

        Run with full rights (the engine's own reads and writes: a storekeeper's receipt re-costs sales, whose vouchers
        a storekeeper cannot open) as the same user, who stays the author of what is written. One posting at a time per
        item: each item is locked before it is replayed."""
        self = controlled(self).sudo()  # re-costing rewrites the values of posted documents: one of their flows
        fresh = (fresh or self.env['stock.move']).sudo()
        pending = {product: day}
        touched = set()
        steps = 0
        while pending:
            steps += 1
            if steps > MAX_RECOST_STEPS:
                raise UserError(self.env._('The cost recalculation of %s does not settle: check for an item that is, in the end, '
                                           'made from itself.', product.display_name))
            p = next(iter(pending))
            d = pending.pop(p)
            touched.add(p)
            self._lock_items(company, p)
            for follow_product, follow_day in self._recost_one(p, company, d, reason, fresh):
                pending[follow_product] = min(pending.get(follow_product, follow_day), follow_day)
        for p in touched:
            if self._uses_daily_average(p, company):
                self._sync_standard_price(p, company)

    @api.model
    def _negative_run_start(self, product, company, day, first_open):
        """Stock below zero at the start of `day`: start the replay where it went below zero, so the issues that took
        more than was there are trued up by the receipts that covered them (not earlier than the first open day)."""
        qty, _value = self._state_before(product, company, day)
        if qty >= -1e-9 or day <= first_open:
            return day
        self.env.cr.execute("""
            SELECT m.date::date AS d,
                   SUM(CASE WHEN dst.usage = 'internal' AND src.usage <> 'internal' THEN m.quantity
                            WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN -m.quantity ELSE 0 END)
              FROM stock_move m
              JOIN stock_location src ON src.id = m.location_id
              JOIN stock_location dst ON dst.id = m.location_dest_id
             WHERE m.product_id = %s AND m.company_id = %s AND m.state = 'done' AND m.date >= %s AND m.date < %s
             GROUP BY 1 ORDER BY 1 DESC""", (product.id, company.id, datetime.combine(first_open, time.min), datetime.combine(day, time.min)))
        for d, net in self.env.cr.fetchall():
            if qty >= -1e-9:
                break
            qty -= float(net)
            day = d
        return day

    @api.model
    def _share_left(self, product, company, day, until):
        """Of a cost added to the stock of `product` on `day` (after the day's receipts, before its issues: the same-day
        rule), the part still in stock at the start of `until`, under average cost: every issue takes its share of what is
        on hand (all of it when it takes the last units, or more)."""
        qty, _value = self._state_before(product, company, day)
        per_day = defaultdict(lambda: [0.0, 0.0])
        for r in self._replay_rows(product, company, day):
            if r['day'] < until:
                per_day[r['day']][0 if r['dir'] > 0 else 1] += r['qty']
        left = 1.0
        for d in sorted(per_day):
            ins, outs = per_day[d]
            qty += ins
            if outs > 1e-9:
                left = left * max(qty - outs, 0.0) / qty if qty > 1e-9 else 0.0
            qty -= outs
        return left

    @api.model
    def _replay_rows(self, product, company, start):
        """The item's moves from `start` as plain rows in date order, read in one query (a replay of a busy item went
        through the ORM move by move). Voided / corrected pairs are left out as in `_left_out`."""
        self.env['stock.move'].flush_model(['product_id', 'company_id', 'state', 'date', 'quantity', 'value', 'location_id',
                                            'location_dest_id', 'sb_fixed_value', 'sb_cancelled', 'sb_reversal_of_id',
                                            'sb_return_of_id', 'sb_at_average'])
        self.env['stock.location'].flush_model(['usage'])
        self.env.cr.execute("""
            SELECT m.id, m.date::date, m.quantity, ABS(COALESCE(m.value, 0)),
                   CASE WHEN dst.usage = 'internal' AND src.usage <> 'internal' THEN 1
                        WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN -1 ELSE 0 END,
                   COALESCE(m.sb_fixed_value, FALSE), COALESCE(m.sb_cancelled, FALSE), r.date::date,
                   m.sb_return_of_id, COALESCE(m.sb_at_average, FALSE)
              FROM stock_move m
              JOIN stock_location src ON src.id = m.location_id
              JOIN stock_location dst ON dst.id = m.location_dest_id
              LEFT JOIN stock_move r ON r.id = m.sb_reversal_of_id
             WHERE m.product_id = %s AND m.company_id = %s AND m.state = 'done' AND m.date >= %s
             ORDER BY m.date, m.id""", (product.id, company.id, datetime.combine(start, time.min)))
        rows = []
        for mid, day, qty, value, direction, fixed, cancelled, reversal_of_day, return_of, at_average in self.env.cr.fetchall():
            if not direction or (cancelled and (reversal_of_day is None or reversal_of_day >= start)):
                continue
            rows.append({'id': mid, 'day': day, 'qty': float(qty), 'value': float(value), 'dir': direction, 'fixed': fixed,
                         'return_of': return_of, 'at_average': at_average})
        return rows

    @api.model
    def _sold_rows(self, ids):
        """{move id: row} of deliveries that returns follow but that are not in the replay (earlier, or voided)."""
        if not ids:
            return {}
        self.env.cr.execute("SELECT id, quantity, ABS(COALESCE(value, 0)), COALESCE(sb_cancelled, FALSE) FROM stock_move WHERE id = ANY(%s)",
                            (list(ids),))
        return {mid: {'id': mid, 'day': None, 'qty': float(qty), 'value': float(value), 'cancelled': cancelled}
                for mid, qty, value, cancelled in self.env.cr.fetchall()}

    @api.model
    def _write_move_amounts(self, amounts, field='value', add=False):
        """{move id: amount} written in one statement (`add`: added to what is there), keeping the ORM caches right.
        Writing the moves one by one also made Odoo recompute the remaining quantities of every move of the item each time."""
        if not amounts:
            return
        Move = self.env['stock.move']
        Move.flush_model([field])
        ids = list(amounts)
        column = {'value': 'value', 'sb_recost_total': 'sb_recost_total'}[field]
        target = 'COALESCE(m.%s, 0) + v.amount' % column if add else 'v.amount'
        self.env.cr.execute('UPDATE stock_move m SET %s = %s FROM unnest(%%s::int[], %%s::numeric[]) AS v(id, amount) '
                            'WHERE m.id = v.id' % (column, target), (ids, [amounts[i] for i in ids]))
        moves = Move.browse(ids)
        moves.invalidate_recordset([field])
        moves.modified([field])

    @api.model
    def _recost_one(self, product, company, day, reason, fresh):
        if not self._uses_daily_average(product, company):
            return []
        first_open = self._first_open_day(company)
        day = self._negative_run_start(product, company, max(day, first_open), first_open)
        rows = self._replay_rows(product, company, day)
        follows = [r for r in rows if r['dir'] > 0 and not r['fixed'] and (r['return_of'] or r['at_average'])]
        if not any(r['dir'] < 0 and not r['fixed'] for r in rows) and not follows:
            return []
        qty, value = self._state_before(product, company, day)
        self.env['sb.stock.revaluation'].flush_model(['product_id', 'company_id', 'date', 'value'])
        self.env.cr.execute('SELECT date, SUM(value) FROM sb_stock_revaluation WHERE product_id = %s AND company_id = %s '
                            'AND date >= %s GROUP BY date', (product.id, company.id, day))
        revals = {d: float(v) for d, v in self.env.cr.fetchall()}
        by_day = defaultdict(list)
        for r in rows:
            by_day[r['day']].append(r)
        by_id = {r['id']: r for r in rows}
        sold = {**self._sold_rows({r['return_of'] for r in follows if r['return_of'] not in by_id}), **by_id}
        before = {r['id']: r['value'] for r in rows}
        cur = company.currency_id
        changes = defaultdict(float)
        avg = value / qty if qty > 1e-9 else self._last_cost(product, company, day - timedelta(days=1))
        short = []  # issues that took more than was on hand: [row, quantity short, unit cost it went out at]

        def settle(r, new):
            """Give the row its new value; returns what it is worth now (changes under half a cent are left alone)."""
            delta = new - r['value']
            if abs(delta) > 0.005:
                changes[r['id']] += delta
                r['value'] = new
            return r['value']

        def true_up(r):
            """Negative stock true-up: the shortfall is re-costed at the cost of the receipt that covers it."""
            nonlocal value
            if not (qty < -1e-9 and short and r['qty'] > 0):
                return
            unit_in, cover = r['value'] / r['qty'], min(r['qty'], -qty)
            while cover > 1e-9 and short:
                s = short[0]
                part = min(cover, s[1])
                delta = cur.round(part * (unit_in - s[2]))
                if delta:
                    s[0]['value'] += delta
                    changes[s[0]['id']] += delta
                    value -= delta
                s[1] -= part
                cover -= part
                if s[1] <= 1e-9:
                    short.pop(0)

        for d in sorted(set(by_day) | set(revals)):
            todays = sorted(by_day.get(d, []), key=lambda r: (r['dir'] < 0, r['id']))
            at_avg = []  # returns valued at the average of the day: they come in after the other receipts
            for r in todays:  # receipts first
                if r['dir'] < 0:
                    continue
                if not r['fixed'] and r['return_of']:
                    sale = sold.get(r['return_of'])
                    if sale and sale['day'] == d:
                        at_avg.append(r)  # sold and brought back the same day: both at the day's average
                        continue
                    if sale and not sale.get('cancelled') and sale['qty']:
                        # a return follows the unit cost of the delivery it brings back (all of it: exactly its value)
                        settle(r, sale['value'] if abs(r['qty'] - sale['qty']) < 1e-9 else cur.round(r['qty'] * sale['value'] / sale['qty']))
                elif not r['fixed'] and r['at_average']:
                    at_avg.append(r)
                    continue
                true_up(r)
                qty += r['qty']
                value += r['value']
            value += revals.get(d, 0.0)
            avg = value / qty if qty > 1e-9 else avg
            for r in at_avg:
                settle(r, cur.round(r['qty'] * avg))
                true_up(r)
                qty += r['qty']
                value += r['value']
            if at_avg:
                avg = value / qty if qty > 1e-9 else avg
            for r in todays:
                if r['dir'] > 0:
                    continue
                if r['fixed']:
                    new = r['value']
                elif abs(qty - r['qty']) < 1e-9:
                    new = settle(r, cur.round(value))  # the last units take what is left: no stray cents on an empty shelf
                else:
                    new = settle(r, cur.round(r['qty'] * avg))
                went_short = r['qty'] - max(qty, 0.0)
                if not r['fixed'] and went_short > 1e-9:
                    short.append([r, min(went_short, r['qty']), new / r['qty'] if r['qty'] else 0.0])
                qty -= r['qty']
                value -= new
        self._write_move_amounts({i: by_id[i]['value'] for i in changes if abs(by_id[i]['value'] - before[i]) > 1e-9})
        fresh_ids = set(fresh.ids)
        moves = self.env['stock.move'].browse([i for i, delta in changes.items() if abs(delta) > 0.005 and i not in fresh_ids])
        return self._post_recost([(m, changes[m.id]) for m in moves], company, reason)  # one recordset: read together

    @api.model
    def _recost_counter(self, m, company):
        """The account a re-costed move's difference goes to (the other side is the item's inventory account)."""
        counter = company.sb_cost_difference_account_id
        v, sv = m.sb_voucher_id, m.sb_stock_voucher_id
        if v and v.voucher_type in SALE_TYPES:
            counter = self._cogs_account(m.product_id, company)
        elif sv and sv.voucher_type == 'giv':
            line = self._voucher_line(m)
            counter = (line.account_id if line else False) or sv.account_id or counter
        elif sv and sv.voucher_type == 'adjustment':
            counter = sv.account_id or counter
        return counter

    @api.model
    def _voucher_line(self, m):
        """The inventory voucher line a stock move was posted for: a GIV with the same item on two lines (two jobs, two
        accounts) takes each line's change to that line. Moves posted before the link was kept: by position."""
        if m.sb_stock_line_id:
            return m.sb_stock_line_id
        sv = m.sb_stock_voucher_id
        lines = sv.line_ids.filtered(lambda l: l.product_id == m.product_id and l.quantity)
        if len(lines) <= 1:
            return lines
        own = sv.stock_move_ids.filtered(lambda x: x.product_id == m.product_id and x.state == 'done' and not x.sb_reversal_of_id
                                         and x.sb_cancelled == m.sb_cancelled).sorted('id')
        pos = list(own.ids).index(m.id) if m.id in own.ids else 0
        return lines[pos] if pos < len(lines) else lines[:1]

    @api.model
    def _counter_extra(self, m, item):
        """Extra values of the line on the other side of a re-costed move: a sale's (or a return's) names the delivery,
        so the cost of sales journal puts the change against that sale; a GIV's carries the job / cost centre of the
        issue, as the GIV's own entry does."""
        if m.sb_voucher_id.voucher_type in SALE_TYPES:
            return dict(item, sb_recost_move_id=m.id)
        sv = m.sb_stock_voucher_id
        if sv and sv.voucher_type == 'giv' and sv.analytic_account_id:
            return dict(item, analytic_distribution={str(sv.analytic_account_id.id): 100})
        return item

    @api.model
    def _recost_pass_on(self, m, delta, company, name, per_day):
        """Hook: a document that took the re-costed issue into its own cost (an FPRR costed from work in process) passes
        the change on, adding its lines to `per_day`. Returns [(finished product, date)] to re-cost next."""
        return []

    @api.model
    def _post_recost(self, changes, company, reason):
        """Entries for re-costed moves: one per date (an entry has one date, and the stock records must equal the ledger
        on every date) or, if the company chose it, one per month; all created and posted together. Builds pass the
        change on to their finished goods."""
        follow = []
        per_day = defaultdict(list)
        totals = {}
        line_deltas = defaultdict(float)
        for m, delta in changes:
            inv = self._valuation_account(m.product_id, company)
            day = m.date.date()
            name = 'Cost recalculation %s (%s)' % (m.product_id.display_name, reason or 'receipt dated earlier')
            totals[m.id] = totals.get(m.id, 0.0) + delta
            # every line names its item; a sale's (or a return's) cost line also names the delivery, so the cost of
            # sales journal puts the change against that sale
            item = {'product_id': m.product_id.id}
            counter_item = self._counter_extra(m, item)
            if self._direction(m) > 0:
                # goods brought back that follow their sale (or the average): stock worth more, cost of sales less
                per_day[day] += [(inv, delta, name, item), (self._recost_counter(m, company), -delta, name, counter_item)]
                continue
            production = getattr(m, 'raw_material_production_id', False)
            if production:
                finished = production.move_finished_ids.filtered(lambda f: f.state == 'done' and f.product_id == production.product_id)[:1]
                if finished:
                    finished.value = abs(finished.value) + delta
                    per_day[day] += [(self._valuation_account(finished.product_id, company), delta, name, {'product_id': finished.product_id.id}),
                                     (inv, -delta, name, item)]
                    sv = self.env['sb.stock.voucher'].search([('production_id', '=', production.id)], limit=1)
                    if sv:
                        line = sv.line_ids.filtered(lambda l: l.product_id == m.product_id)[:1]
                        if line:
                            line_deltas[line] += delta
                        sv.build_value += delta
                    follow.append((finished.product_id, day))
                continue
            sv = m.sb_stock_voucher_id
            if sv and sv.voucher_type == 'giv':
                line = self._voucher_line(m)
                if line:
                    line_deltas[line] += delta
            per_day[day] += [(self._recost_counter(m, company), delta, name, counter_item), (inv, -delta, name, item)]
            follow += self._recost_pass_on(m, delta, company, name, per_day)
        self._write_move_amounts(totals, 'sb_recost_total', add=True)
        for line, delta in line_deltas.items():
            line.value += delta
        if company.sb_recost_entries == 'month':  # the company's choice: one entry per accounting period, on its last day
            per_month = defaultdict(list)
            for day, lines in per_day.items():
                per_month[_period_end(day)] += lines
            per_day = per_month
        self._post_entries(company, [(day, 'Cost recalculation (same-day receipt rule)', lines) for day, lines in sorted(per_day.items())])
        return follow

    # ------------------------------------------------------------------ voids and corrections
    @api.model
    def _undo_recost_lines(self, m, company, name):
        """Entry lines taking back the recalculations posted for `m` since its document was posted: a void or a
        correction reverses the document's own entry, these lines the rest."""
        total = m.sb_recost_total
        if company.currency_id.is_zero(total):
            return []
        inv = self._valuation_account(m.product_id, company)
        item = {'product_id': m.product_id.id}
        if self._direction(m) > 0:
            if getattr(m, 'production_id', False):
                return []  # finished goods of a build: its components' lines take the change back
            sv = m.sb_stock_voucher_id
            counter = sv.account_id if sv and sv.account_id else self._recost_counter(m, company)
            return [(counter, total, name, self._counter_extra(m, item)), (inv, -total, name, item)]
        production = getattr(m, 'raw_material_production_id', False)
        if production:
            return [(inv, total, name, item), (self._valuation_account(production.product_id, company), -total, name,
                                               {'product_id': production.product_id.id})]
        return [(inv, total, name, item), (self._recost_counter(m, company), -total, name, self._counter_extra(m, item))]

    @api.model
    def _undo_moves(self, moves, day, origin, link, company, recost=True):
        """Reverse `moves` on `day` at their own value (a void or a correction) and re-cost every item from the original
        date as if they had never been posted. Returns a warning per item that goes below zero because of it.
        recost=False (a correction): the later issues keep the cost they were posted at until the document is posted
        again (or voided), which re-costs them once, from the document date. While the document is out, the stock records
        still equal the ledger (the pair nets to zero, its entry is back in draft), and no cost is posted that only holds
        for a document that is about to come back. Engine internals run with full rights, as in _recost."""
        self = self.sudo()
        moves = moves.sudo()
        self._lock_items(company, moves.product_id)
        entry = []
        name = self.env._('Recalculations reversed: %s', origin)
        for m in moves:
            entry += self._undo_recost_lines(m, company, name)
            self._move(m.product_id, m.quantity, m.location_dest_id, m.location_id, day, origin, company,
                       unit_cost=abs(m.value) / m.quantity if m.quantity else 0.0, link=link, reverses=m)
        if entry:
            self._post_entry(company, day, name, entry)
        warnings = []
        for product in moves.product_id:
            own = moves.filtered(lambda m: m.product_id == product)
            since = min(m.date.date() for m in own)
            if recost and self._uses_daily_average(product, company):
                self._recost(product, company, since, reason=origin)
            for loc in own.mapped(lambda m: m.location_dest_id if m.location_dest_id.usage == 'internal' else m.location_id):
                low = self._lowest_on_hand(product, loc, since)
                if low < -1e-6 and recost:
                    warnings.append(self.env._('%(item)s goes below zero in %(loc)s (lowest %(qty)s). The issues after it were '
                                               're-costed; the next receipt trues up the shortfall.',
                                               item=product.display_name, loc=loc.display_name, qty='{:,.2f}'.format(low)))
                elif low < -1e-6:
                    warnings.append(self.env._('%(item)s is below zero in %(loc)s while this correction is in draft (lowest '
                                               '%(qty)s). The issues after it keep their costs until it is posted again.',
                                               item=product.display_name, loc=loc.display_name, qty='{:,.2f}'.format(low)))
        return warnings
