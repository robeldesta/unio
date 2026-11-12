"""Sales and purchase vouchers move stock: cash sales and invoices deliver, cash purchases and bills receive,
returns bring goods back. Cost of sales is what the delivery took at FIFO / average cost, on the voucher date."""
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare
from odoo.tools.misc import format_date

from odoo.addons.sonador_books.models.move_guard import controlled

SALE_OUT = ('cash_sale', 'sale')
PURCHASE_IN = ('cash_purchase', 'bill')
ITEM_VOUCHERS = SALE_OUT + PURCHASE_IN + ('sale_return', 'purchase_return')


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_warehouse_id = fields.Many2one(
        'stock.warehouse', 'Warehouse', check_company=True,
        default=lambda s: s.env['stock.warehouse'].search([('company_id', '=', s.env.company.id)], limit=1),
        help='Where stock items are delivered from or received into.')
    sb_stock_move_ids = fields.One2many('stock.move', 'sb_voucher_id', string='Stock moves')
    sb_has_stock = fields.Boolean('Moves stock', compute='_compute_sb_has_stock')
    sb_grn_id = fields.Many2one('sb.stock.voucher', 'From GRN', check_company=True,
                                domain="[('voucher_type', '=', 'grn'), ('state', '=', 'posted'), ('partner_id', '=', partner_id)]",
                                help='Purchase voucher for goods already received: the lines clear goods received not invoiced.')
    sb_grn_ids = fields.Many2many('sb.stock.voucher', 'sb_voucher_grn_rel', 'voucher_id', 'grn_id', string='From GRNs',
                                  check_company=True, copy=False,
                                  domain="[('voucher_type', '=', 'grn'), ('state', '=', 'posted'), ('partner_id', '=', partner_id)]",
                                  help='Purchase voucher for several deliveries (GRNs) at once, e.g. a purchase order received in batches.')
    sb_cost_move_ids = fields.Many2many('account.move', 'sb_voucher_cost_move_rel', 'voucher_id', 'move_id',
                                        string='Cost entries', copy=False, readonly=True)
    sb_cogs = fields.Monetary('Cost of sales', compute='_compute_sb_cogs')

    @api.depends('line_ids.product_id', 'voucher_type')
    def _compute_sb_has_stock(self):
        for v in self:
            v.sb_has_stock = v.voucher_type in ITEM_VOUCHERS and any(l.product_id.is_storable for l in v.line_ids)

    @api.depends('sb_stock_move_ids.value')
    def _compute_sb_cogs(self):
        for v in self:
            moves = v.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id)
            v.sb_cogs = abs(sum(moves.mapped('value'))) if v.voucher_type in SALE_OUT else 0.0

    def _sb_grns(self):
        """GRNs this bill is for: the clearing of goods received not invoiced covers all of them."""
        self.ensure_one()
        # read with full rights: a user who may not open stock documents (a petty cash keeper) posts vouchers with no GRN
        doc = self.sudo()
        return (doc.sb_grn_id | doc.sb_grn_ids).with_env(self.env)

    def _sb_grn_accounts(self):
        """The accounts the bill's GRNs credited (goods received not invoiced, or whatever account a GRN was posted to):
        the bill clears those, not always the company's GRNI account."""
        grns = self._sb_grns()
        return grns.mapped('account_id') or self.company_id.sb_grni_account_id

    def _sb_stock_lines(self):
        self.ensure_one()
        if self.voucher_type not in ITEM_VOUCHERS:
            return self.line_ids.browse()
        lines = self.line_ids.filtered(lambda l: l.product_id.is_storable)
        if self.voucher_type == 'bill' and self._sb_grns():
            cleared = self._sb_grn_accounts() | self.company_id.sb_grni_account_id
            lines = lines.filtered(lambda l: l.account_id not in cleared)
        return lines

    def _sb_items(self):
        """Stock items the voucher moves, moved, or (a bill from GRNs) re-costs: its posting lock covers them all."""
        doc = self.sudo()
        items = doc.line_ids.product_id.filtered('is_storable') | doc.sb_stock_move_ids.product_id
        if self.voucher_type == 'bill':
            items |= doc._sb_grns().line_ids.product_id
        return items.with_env(self.env)

    def _sb_qty(self, lines):
        qty = defaultdict(float)
        for l in lines:
            qty[l.product_id] += l.quantity
        return qty

    # ------------------------------------------------------------------ posting
    def _check_before_post(self):
        Stock = self.env['sb.stock']
        lines = self._sb_stock_lines()
        # purchased stock goes to the inventory account (a bill from a GRN clears goods received not invoiced)
        if self.voucher_type in PURCHASE_IN + ('purchase_return',):
            for l in lines:
                acc = Stock._valuation_account(l.product_id, self.company_id)
                if l.account_id != acc:
                    l.account_id = acc
        if self.voucher_type == 'journal':
            Stock._check_manual_stock_lines(self.company_id, self.line_ids.account_id, self.name if self.name not in (False, '/', 'New')
                                            else self.env._('This journal voucher'))
        super()._check_before_post()
        if lines and not self.sb_warehouse_id:
            raise UserError(self.env._('Choose the warehouse.'))
        if any(l.quantity <= 0 for l in lines):
            raise UserError(self.env._('Stock items need a quantity more than zero.'))
        if lines:
            self._sb_check_returned(lines)
        moved = self.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id and m.state == 'done')
        if self.voucher_type in SALE_OUT + ('purchase_return',) and lines and not moved:
            Stock._check_available(self.company_id, self._sb_qty(lines), self.sb_warehouse_id.lot_stock_id,
                                   self.name if self.name not in (False, '/', 'New') else self.env._('this %s', dict(
                                       self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type, '').lower()),
                                   self.date)

    def _sb_goods_returns(self):
        """Posted returns against this sale (or purchase) that moved goods."""
        return self.search([('origin_id', 'in', self.ids), ('state', '=', 'posted'),
                            ('voucher_type', 'in', ('sale_return', 'purchase_return'))]).filtered('sb_stock_move_ids')

    def _sb_check_returned(self, lines):
        """A return brings back at most what its sale (or purchase) had of each item, less what earlier returns brought."""
        origin = self.origin_id
        if self.voucher_type not in ('sale_return', 'purchase_return') or not origin:
            return
        had = self._sb_qty(origin.line_ids.filtered(lambda l: l.product_id.is_storable))
        earlier = self.search([('origin_id', '=', origin.id), ('voucher_type', '=', self.voucher_type), ('state', '=', 'posted'),
                               ('id', '!=', self.id)])
        back = self._sb_qty(earlier.line_ids.filtered(lambda l: l.product_id.is_storable))
        fmt = '{:,.2f}'.format
        over = []
        for p, qty in self._sb_qty(lines).items():
            left = had.get(p, 0.0) - back.get(p, 0.0)
            if float_compare(qty, left, precision_rounding=p.uom_id.rounding) > 0:
                over.append(self.env._('%(item)s: %(had)s on %(doc)s, %(back)s returned already, %(qty)s on this return',
                                       item=p.display_name, had=fmt(had.get(p, 0.0)), doc=origin.name, back=fmt(back.get(p, 0.0)),
                                       qty=fmt(qty)))
        if over:
            raise UserError(self.env._('A return cannot bring back more than %(doc)s had:\n%(items)s',
                                       doc=origin.name, items='\n'.join(over)))

    def action_post(self):
        drafts = self.filtered(lambda v: v.state == 'draft')
        for v in drafts:
            # one posting at a time per item, taken before any stock is read (all items at once, in a fixed order)
            self.env['sb.stock']._lock_items(v.company_id, v._sb_items())
            v._check_before_post()
            v._sb_stock_out()
        res = super().action_post()
        # the receipt, the cost difference of a return and the price difference of a bill from a GRN are part of posting:
        # the voucher is posted by then, so they are written under the posting flow
        for v in controlled(drafts).filtered(lambda x: x.state == 'posted'):
            v.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id).write({'origin': v.name})
            v._sb_stock_in()
            v._sb_grn_difference()
        return res

    def _sb_stock_out(self):
        """Before the entry: deliveries (they set the cost of sales) and goods coming back from customers."""
        Stock, company = self.env['sb.stock'], self.company_id
        lines = self._sb_stock_lines()
        if not lines or self.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id and m.state == 'done'):
            return
        loc, stock = Stock._locations(company), self.sb_warehouse_id.lot_stock_id
        link = {'sb_voucher_id': self.id}
        ref = self.name if self.name != '/' else (self.reference or self.env._('Voucher'))
        if self.voucher_type in SALE_OUT:
            dest = self._sb_sale_destination(loc)
            for p, qty in self._sb_qty(lines).items():
                Stock._move(p, qty, stock, dest, self.date, ref, company, link=link)
        elif self.voucher_type == 'purchase_return':
            # goods go back at the average cost (Sage); the debit note's price against it is a purchase price variance,
            # posted after the entry (_sb_return_difference)
            for p, qty in self._sb_qty(lines).items():
                Stock._move(p, qty, stock, loc['supplier'], self.date, ref, company, link=link)
        elif self.voucher_type == 'sale_return':
            # goods come back at the unit cost of the delivery they return and follow it when the sale is re-costed; with
            # no sale behind them, at the average on the return's date (and they follow that average)
            for p, qty in self._sb_qty(lines).items():
                sold = self.origin_id.sb_stock_move_ids.filtered(
                    lambda m: m.product_id == p and not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')[:1]
                if sold and sold.quantity:
                    unit, follow = abs(sold.value) / sold.quantity, {'sb_return_of_id': sold.id}
                else:
                    unit, follow = Stock._avg_on(p, company, self.date), {'sb_at_average': True}
                Stock._move(p, qty, loc['customer'], stock, self.date, ref, company, unit_cost=unit, link=dict(link, **follow))

    def _sb_sale_destination(self, loc):
        """Where sold goods go when the sale is posted (the sales app sends them to 'awaiting dispatch')."""
        return loc['customer']

    def _prepare_entry_lines(self):
        lines = super()._prepare_entry_lines()
        if self.voucher_type == 'cash_sale':
            Stock, company, cur = self.env['sb.stock'], self.company_id, self.currency_id
            per, qty = defaultdict(float), defaultdict(float)
            for m in self.sb_stock_move_ids.filtered(lambda x: not x.sb_reversal_of_id):
                per[m.product_id] += abs(m.value)
                qty[m.product_id] += m.quantity
            for p, value in per.items():
                if cur.is_zero(value):
                    continue
                name = self.env._('Cost of sales %s', p.display_name)
                # the quantity delivered, as on a sales invoice's cost lines (the cost of sales journal shows it)
                lines.append(Command.create({'account_id': Stock._cogs_account(p, company).id, 'name': name, 'product_id': p.id,
                                             'quantity': qty[p], 'debit': cur.round(value), 'credit': 0.0}))
                lines.append(Command.create({'account_id': Stock._valuation_account(p, company).id, 'name': name, 'product_id': p.id,
                                             'quantity': qty[p], 'debit': 0.0, 'credit': cur.round(value)}))
        return lines

    def _sb_stock_in(self):
        """After the entry: receipts are valued at what was posted to inventory (company currency, after discounts)."""
        Stock, company = self.env['sb.stock'], self.company_id
        lines = self._sb_stock_lines()
        if self.voucher_type not in PURCHASE_IN or not lines:
            if self.voucher_type == 'purchase_return' and lines:
                self._sb_return_difference()
            return
        loc, stock = Stock._locations(company), self.sb_warehouse_id.lot_stock_id
        posted = defaultdict(float)
        for aml in self.move_id.line_ids.filtered(lambda a: a.product_id.is_storable and a.display_type in ('product', False)
                                                   and a.account_id == Stock._valuation_account(a.product_id, company)):
            posted[aml.product_id] += aml.balance
        for p, qty in self._sb_qty(lines).items():
            unit = posted.get(p, 0.0) / qty if qty else 0.0
            Stock._move(p, qty, loc['supplier'], stock, self.date, self.name, company, unit_cost=unit, link={'sb_voucher_id': self.id})

    def _sb_return_difference(self):
        """Goods sent back to a vendor leave at FIFO / average cost; the debit note may credit a different amount."""
        Stock, company, cur = self.env['sb.stock'], self.company_id, self.currency_id
        entry = []
        for p in self._sb_stock_lines().product_id:
            moved = abs(sum(self.sb_stock_move_ids.filtered(lambda m: m.product_id == p and not m.sb_reversal_of_id).mapped('value')))
            inv_acc = Stock._valuation_account(p, company)
            credited = -sum(self.move_id.line_ids.filtered(lambda a: a.product_id == p and a.account_id == inv_acc).mapped('balance'))
            diff = moved - credited
            if not cur.is_zero(diff):
                name = self.env._('Cost difference on return %s', self.name)
                entry += [(company.sb_cost_difference_account_id, diff, name, None), (inv_acc, -diff, name, None)]
        if entry:
            self.sb_cost_move_ids |= Stock._post_entry(company, self.date, name, entry, self.partner_id)

    def _sb_grn_difference(self):
        """A bill entered from a GRN clears what the GRN credited (goods received not invoiced, or the account it was
        posted to) at the GRN cost. A different price, and the VAT a company not registered for VAT pays as part of the
        cost, are part of the cost of the goods (Sage X3): for an average-cost item the difference is added to the cost of
        the GRN receipt from the GRN date, so the goods still on hand take it into stock and the issues and sales since
        are re-costed (each its share, on its own date, to cost of sales, the GIV's expense or work in process), as a
        corrected GRN cost would, without reopening the GRN. The share of issues dated in a closed period goes to the
        price variance account on the bill date instead (closed periods never change); the bill says so. The rest (an
        item at another cost method, a quantity billed that differs from the GRN, a line without an item) stays a price
        difference."""
        grns = self._sb_grns()
        if self.voucher_type != 'bill' or not grns:
            return
        Stock, company = self.env['sb.stock'], self.company_id
        cur = company.currency_id
        ppv = company.sb_cost_difference_account_id
        grn_names = ', '.join(grns.mapped('name'))
        name = self.env._('Price difference %(bill)s / %(grn)s', bill=self.name, grn=grn_names)
        cost_name = self.env._('Cost from purchase voucher %(bill)s / %(grn)s', bill=self.name, grn=grn_names)
        entry, pairs, per_day, notes = [], {}, defaultdict(list), []
        R = self.env['sb.stock.revaluation'].sudo()
        revals, receipts, recost = [], [], {}
        first_open = Stock._first_open_day(company)
        for account in self._sb_grn_accounts():
            grn_lines = grns.move_id.line_ids.filtered(lambda a: a.account_id == account and not a.reconciled)
            bill_lines = self.move_id.line_ids.filtered(lambda a: a.account_id == account)
            if not bill_lines:
                continue
            diff = sum(bill_lines.mapped('balance')) + sum(grn_lines.mapped('balance'))
            open_grns = grns.filtered(lambda g: g.move_id in grn_lines.move_id)
            items = self._sb_grn_item_differences(account, open_grns, bill_lines) if not cur.is_zero(diff) else {}
            for product, (amount, shares, vat) in items.items():
                diff -= amount
                inv = Stock._valuation_account(product, company)
                gone_total = 0.0
                for move, share in shares:
                    day = move.date.date()
                    if day >= first_open:
                        # the receipt itself costs more (or less), from its date
                        per_day[day] += [(account, -share, cost_name, None), (inv, share, cost_name, {'product_id': product.id})]
                        receipts.append((move, share))
                        recost[product] = min(recost.get(product, day), day)
                        continue
                    # received in a closed period: the share of the issues in closed periods goes to price variance on the
                    # bill date; the rest is added to the cost of what was still on hand, from the first open day
                    kept = cur.round(share * Stock._share_left(product, company, day, first_open))
                    gone = cur.round(share - kept)
                    if not cur.is_zero(gone):
                        gone_total += gone
                        entry += [(account, -gone, name, None), (ppv, gone, name, {'product_id': product.id})]
                    if not cur.is_zero(kept):
                        per_day[first_open] += [(account, -kept, cost_name, None), (inv, kept, cost_name, {'product_id': product.id})]
                        revals.append({'company_id': company.id, 'product_id': product.id, 'date': first_open, 'value': kept,
                                       'reference': self.name, 'voucher_id': self.id,
                                       'description': self.env._('Purchase voucher price (%s)', move.sb_stock_voucher_id.name or '')})
                        recost[product] = min(recost.get(product, first_open), first_open)
                fmt = '{:,.2f}'.format
                note = self.env._('%(item)s: %(amount)s %(more)s than the GRN cost%(vat)s, part of its cost from the GRN date: the '
                                  'stock on hand and the issues since are re-costed.', item=product.display_name, amount=fmt(abs(amount)),
                                  more=self.env._('more') if amount > 0 else self.env._('less'),
                                  vat=self.env._(' (%s of it VAT the company cannot claim)', fmt(vat)) if not cur.is_zero(vat) else '')
                if any(m.date.date() < first_open for m, _s in shares):
                    note += ' ' + self.env._('The GRN is in a closed period (closed through %(lock)s): %(gone)s, the share of the '
                                             'issues in closed periods, goes to price variance on the bill date; the rest is added '
                                             'to the cost of the stock on hand from %(open)s.', gone=fmt(gone_total),
                                             lock=format_date(self.env, first_open - timedelta(days=1)),
                                             open=format_date(self.env, first_open))
                notes.append(note)
            # a company not registered for VAT pays the VAT as part of the cost (_sb_vat_in_cost): that part of the
            # difference is said to be VAT, not a price difference (a registered company claims it on its own VAT line)
            rest_lines = bill_lines.filtered(lambda a: a.product_id not in items)
            vat = self._sb_vat_in_cost_part(account, sum(rest_lines.mapped('balance'))) if rest_lines else 0.0
            vat = vat if abs(vat) <= abs(diff) and vat * diff > 0 else 0.0
            if not cur.is_zero(vat):
                vat_name = self.env._('VAT added to cost (not registered for VAT) %(bill)s / %(grn)s', bill=self.name, grn=grn_names)
                entry += [(account, -vat, vat_name, None), (ppv, vat, vat_name, None)]
                diff -= vat
            if not cur.is_zero(diff):
                entry += [(account, -diff, name, None), (ppv, diff, name, None)]
            pairs[account] = (grn_lines, bill_lines)
        moves = self.env['account.move']
        if entry:
            moves |= Stock._post_entry(company, self.date, name, entry, self.partner_id)
        for day, lines in sorted(per_day.items()):
            move = Stock._post_entry(company, day, cost_name, lines, self.partner_id)
            moves |= move
            for vals in revals:
                if vals['date'] == day:
                    vals['move_id'] = move.id
        if moves:
            self.sb_cost_move_ids |= moves
            for account, (grn_lines, bill_lines) in pairs.items():
                pairs[account] = (grn_lines, bill_lines | moves.line_ids.filtered(lambda a: a.account_id == account))
        if receipts:
            Move = self.env['stock.move'].sudo()
            Stock._write_move_amounts({m.id: abs(m.value) + share for m, share in receipts})
            for m, share in receipts:
                Move.browse(m.id).write({'sb_bill_cost': m.sb_bill_cost + share, 'sb_bill_id': self.id})
        if revals:
            R.create(revals)
        if recost:
            for product in sorted(recost, key=lambda p: p.id):
                Stock._recost(product, company, recost[product], reason=self.name)
        if notes:
            self.message_post(body=Markup('<br/>').join(notes))
        for account, (grn_lines, bill_lines) in pairs.items():
            if account.reconcile or account.account_type in ('asset_receivable', 'liability_payable'):
                (grn_lines | bill_lines).filtered(lambda a: not a.reconciled).with_context(no_exchange_difference=True).reconcile()

    def _sb_grn_item_differences(self, account, grns, bill_lines):
        """{item: (difference, [(GRN receipt move, share)], VAT in it)} for the average-cost items of the bill whose quantity
        is the quantity the GRNs received: what the bill put on `account` for the item less what the GRNs credited for it,
        shared over the GRN receipts at the bill's price per unit."""
        Stock, company = self.env['sb.stock'], self.company_id
        cur = company.currency_id
        billed, vat = defaultdict(float), defaultdict(float)
        for a in bill_lines.filtered('product_id'):
            billed[a.product_id] += a.balance
        qty = defaultdict(float)
        for l in self.line_ids.filtered(lambda l: l.account_id == account and l.product_id):
            qty[l.product_id] += l.quantity
            if l.tax_ids and self._sb_vat_in_cost(l)[1] != l.tax_ids:
                vat[l.product_id] += l.price_total - l.price_subtotal
        received = grns.sudo()._sb_live_moves().filtered(lambda m: Stock._direction(m) > 0)
        out = {}
        for product, amount in billed.items():
            moves = received.filtered(lambda m: m.product_id == product).sorted(lambda m: (m.date, m.id))
            if not moves or not product.is_storable or not Stock._uses_daily_average(product, company):
                continue
            got = sum(moves.mapped('quantity'))
            if float_compare(got, qty.get(product, 0.0), precision_rounding=product.uom_id.rounding):
                continue  # billed for another quantity than received: a price difference, as before
            diff = cur.round(amount - sum(abs(m.value) for m in moves))
            if cur.is_zero(diff):
                continue
            shares, left = [], diff
            for i, m in enumerate(moves):
                share = left if i == len(moves) - 1 else cur.round(amount * m.quantity / got - abs(m.value))
                left -= share
                shares.append((m, share))
            out[product] = (diff, shares, cur.round(vat[product]))
        return out

    def _sb_vat_in_cost_part(self, account, posted):
        """Of `posted` (what the bill put on `account`, company currency), the VAT a company not registered for VAT
        pays as part of the cost (dropped from the lines by _sb_vat_in_cost)."""
        lines = self.line_ids.filtered(lambda l: l.account_id == account)
        dropped = lines.filtered(lambda l: l.tax_ids and self._sb_vat_in_cost(l)[1] != l.tax_ids)
        total = sum(lines.mapped('price_total'))
        if not dropped or not total:
            return 0.0
        vat = sum(l.price_total - l.price_subtotal for l in dropped)
        return self.company_id.currency_id.round(posted * vat / total)

    # ------------------------------------------------------------------ edit / void
    def action_edit(self):
        """Stock has moved: editing means voiding (goods go back) and re-entering a copy."""
        stock = self.filtered(lambda v: v.state == 'posted' and v.sb_stock_move_ids)
        res = super(SbVoucher, self - stock).action_edit() if self - stock else True
        if not stock:
            return res
        stock.with_context(sb_void_reason=self.env._('Voided to edit; the corrected copy is a new draft.')).action_void()
        copy = stock[0].copy({'date': stock[0].date, 'name': '/'})
        stock[0].message_post(body=self.env._('Voided to edit; the corrected copy is a new draft.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': copy.id, 'view_mode': 'form',
                'name': self.env._('Edit %s', stock[0].name)}

    def action_void(self):
        todo = self.filtered(lambda v: v.state == 'posted')
        for v in todo.filtered(lambda x: x.voucher_type in SALE_OUT + PURCHASE_IN):
            returns = v._sb_goods_returns()
            if returns:
                raise UserError(self.env._('%(doc)s has returns posted against it (%(returns)s): void them first.',
                                           doc=v.name, returns=', '.join(returns.mapped('name'))))
        for v in todo:  # goods it brought in (a purchase, a return) cannot go back if later issues used them
            self.env['sb.stock']._lock_items(v.company_id, v._sb_items())
            changes = defaultdict(float)
            for m in v.sb_stock_move_ids.filtered(lambda m: m.state == 'done' and not m.sb_reversal_of_id and not m.sb_cancelled
                                                  and m.location_dest_id.usage == 'internal' and m.location_id.usage != 'internal'):
                changes[(m.product_id, m.location_dest_id)] -= m.quantity
            if changes:
                self.env['sb.stock']._check_take_back(v.company_id, changes, v.date, self.env._('Voiding %s', v.name),
                                                      doc_moves=v.sudo().sb_stock_move_ids)
        for v in todo.filtered(lambda x: x.voucher_type in ('sale_return', 'purchase_return') and x.origin_id.move_id):
            # a return applied to its own invoice (or bill) and nothing else is taken off it, so the return can be voided
            # (the invoice cannot be voided while the return stands)
            partials = v.move_id.line_ids.matched_debit_ids | v.move_id.line_ids.matched_credit_ids
            lines = partials.debit_move_id | partials.credit_move_id
            if partials and (lines.move_id - v.move_id) == v.origin_id.move_id:
                lines.filtered(lambda a: a.move_id == v.move_id).remove_move_reconcile()
        for v in todo.filtered(lambda x: x._sb_grns()):  # the bill's lines are matched with the GRN: unmatch before reversing
            cleared = v._sb_grn_accounts() | v.company_id.sb_grni_account_id
            (v.move_id.line_ids | v.sb_cost_move_ids.line_ids).filtered(lambda a: a.account_id in cleared and a.reconciled).remove_move_reconcile()
        res = super().action_void()
        for v in todo.filtered(lambda x: x.state == 'void'):
            v._sb_stock_reverse()
        return res

    def _sb_stock_reverse(self):
        """Goods go back at exactly the value they moved at (the pair leaves the average) and every item is re-costed from
        the voucher date as if it had never been posted. Stock already used by later issues goes below zero (a warning
        in the chatter); the next receipt trues the shortfall up."""
        Stock, company = self.env['sb.stock'], self.company_id
        lock = company._get_user_fiscal_lock_date(self._journal())
        day = self.date if self.date > lock else fields.Date.context_today(self)
        originals = self.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')
        warnings = Stock._undo_moves(originals, day, self.env._('Void %s', self.name), {'sb_voucher_id': self.id}, company)
        for move in self.sb_cost_move_ids.filtered(lambda x: x.state == 'posted'):
            move.line_ids.filtered('reconciled').remove_move_reconcile()
            # a cost from the bill dated with its GRN is taken back on that date while it is open (stock and ledger agree
            # on every date)
            own = move.date > lock and move.date >= Stock._first_open_day(company)
            controlled(move)._sb_void(move.date if own else day, self.env._('Void of %s', move.ref or move.name))
        self._sb_undo_bill_cost(day)
        if warnings:
            self.message_post(body=Markup('<br/>').join(warnings))

    def _sb_undo_bill_cost(self, day):
        """Void of a bill whose price difference went into the cost of the GRN goods: the receipts cost again what the GRN
        said, and the items are re-costed from the GRN dates, so everything is as before the bill (the entries are
        reversed on their own dates by the caller). A cost in a period closed since then stays: a cost adjustment on the
        void date takes it back."""
        Stock, company = self.env['sb.stock'], self.company_id
        first_open = Stock._first_open_day(company)
        receipts = self.env['stock.move'].sudo().search([('sb_bill_id', '=', self.id)])
        revals = self.env['sb.stock.revaluation'].sudo().search([('voucher_id', '=', self.id), ('value', '!=', 0)])
        back, recost = [], {}
        for m in receipts:
            on = m.date.date()
            if on >= first_open:
                Stock._write_move_amounts({m.id: abs(m.value) - m.sb_bill_cost})
            else:
                on = day
                back.append((m.product_id, -m.sb_bill_cost, m.sb_stock_voucher_id.name or ''))
            recost[m.product_id] = min(recost.get(m.product_id, on), on)
        receipts.write({'sb_bill_cost': 0.0, 'sb_bill_id': False})
        for r in revals:
            on, product = (r.date if r.date >= first_open else day), r.product_id
            recost[product] = min(recost.get(product, on), on)
            if on == r.date:
                r.unlink()
            else:
                back.append((product, -r.value, r.description or ''))
        if back:
            self.env['sb.stock.revaluation'].sudo().create([{
                'company_id': company.id, 'product_id': p.id, 'date': day, 'value': value, 'reference': self.name,
                'voucher_id': self.id, 'description': self.env._('Void of %(bill)s (%(what)s)', bill=self.name, what=what)}
                for p, value, what in back])
        for product in sorted(recost, key=lambda p: p.id):
            Stock._recost(product, company, recost[product], reason=self.env._('Void %s', self.name))


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    @api.onchange('product_id')
    def _onchange_product(self):
        res = super()._onchange_product()
        v = self.voucher_id
        if self.product_id.is_storable and v.voucher_type in PURCHASE_IN + ('purchase_return',) \
                and not (v.voucher_type == 'bill' and (v.sb_grn_id or v.sb_grn_ids)):
            self.account_id = self.env['sb.stock']._valuation_account(self.product_id, v.company_id or self.env.company)
        return res


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _sb_documents(self):
        """The cost of sales entry of a sales voucher, and the entry of a goods received / issued voucher, are posted and
        reversed through their document too."""
        docs = super()._sb_documents()
        moves = self | self.reversed_entry_id
        found = {}
        for v in self.env['sb.voucher'].sudo().search([('sb_cost_move_ids', 'in', moves.ids)]):
            for m in v.sb_cost_move_ids & moves:
                found[m] = v
        for v in self.env['sb.stock.voucher'].sudo().search([('move_id', 'in', moves.ids)]):
            found[v.move_id] = v
        for move in self.filtered(lambda m: m not in docs):
            doc = found.get(move) or found.get(move.reversed_entry_id)
            if doc:
                docs[move] = doc
        return docs
