"""Replacement: goods given to a customer in place of goods returned (Peachtree's REP + OFF SET REP).

After a sales return is posted the customer is owed its value (the credit note). "Give replacement" makes a sales
invoice for the replacement goods through the normal sales path (warehouse, delivery order and batches when the
company delivers later), at the returned value, on the Sales returns and allowances account instead of a sales
account, and settles it against the credit note (no cash). Its entry is exactly Peachtree's pair:
    REP           Dr  cost of sales (5000-0007)  /  Cr  inventory (1504-0000)       the new goods, at today's average cost
    OFF SET REP   Dr  customer (1400-1000)       /  Cr  Sales returns and allowances (4000-0005)   the replaced value
So for a like-for-like replacement: sales returns and allowances back to nil (revenue unchanged by the return and its
replacement), the customer back to nil, cost of sales = the cost of the new goods, the damaged stock keeps the goods
returned. Partial replacements are allowed; what is left stays owed (or is refunded with a payment). Numbered in the
company's Replacement series when it has one (Eco Green: REP/, as Odoo 18's Inventory Replacement), else as a sales
invoice."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('replacement', 'Replacement (REP)')], ondelete={'replacement': 'cascade'})

    @api.model
    def _sb_extra_name_tables(self):
        return dict(super()._sb_extra_name_tables(), replacement=('sb_voucher', 'name'))

    def _sb_fiscal_registers(self, company):
        """A replacement is not a sale: no fiscal receipt is issued, it carries its REP number instead."""
        from odoo.addons.sonador_books.models.series import _letters
        registers = super()._sb_fiscal_registers(company)
        for s in self.with_context(active_test=False).sudo().search([('company_id', '=', company.id), ('doc_type', '=', 'replacement')]):
            registers.setdefault((_letters(s.prefix), _letters(s.suffix)), False)
        return registers

    @api.model
    def _used_numbers(self, series, key):
        res = super()._used_numbers(series, key)
        if series.doc_type == 'replacement':
            for v in self.env['sb.voucher'].sudo().search([('sb_replacement_of_id', '!=', False), ('state', 'in', ('posted', 'void')),
                                                           ('company_id', '=', series.company_id.id)]):
                if v.name and v.name.startswith(series.prefix or ''):
                    res.setdefault(series._parse(v.name), []).append(v.name)
        return res


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_replacement_of_id = fields.Many2one('sb.stock.voucher', 'Replacement for', copy=False, readonly=True, index='btree_not_null',
                                           help='The sales return whose credit this invoice uses: goods given in place of the goods returned.')

    @api.depends('sb_replacement_of_id')
    def _compute_sb_printable(self):
        # a replacement is not a tax invoice: it prints as the Replacement (REP) document, as Odoo 18's Inventory Replacement
        super()._compute_sb_printable()
        for v in self.filtered('sb_replacement_of_id'):
            v.sb_printable = True

    def action_sb_print(self):
        reps = self.filtered('sb_replacement_of_id')
        if reps and reps != self:
            raise UserError(self.env._('Print replacements on their own.'))
        if reps:
            return self.env.ref('sonador_returns.action_report_replacement').report_action(reps)
        return super().action_sb_print()

    def _sb_check_same_items(self):
        """A replacement gives the same items as were returned, no more of each than is not replaced yet."""
        ret = self.sb_replacement_of_id
        left = {}
        for l in ret.line_ids.filtered('product_id'):
            left[l.product_id] = left.get(l.product_id, 0.0) + l.quantity
        for r in ret.sb_replacement_ids.filtered(lambda x: x.state == 'posted' and x != self):
            for l in r.line_ids.filtered('product_id'):
                left[l.product_id] = left.get(l.product_id, 0.0) - l.quantity
        asked = {}
        for l in self.line_ids:
            if not l.product_id:
                raise UserError(self.env._('%s: every line of a replacement is an item returned on %s.', self.display_name, ret.name))
            asked[l.product_id] = asked.get(l.product_id, 0.0) + l.quantity
        for product, qty in asked.items():
            if product not in left:
                raise UserError(self.env._('%(doc)s: %(item)s was not returned on %(ret)s. A replacement gives the same items as were returned.',
                                           doc=self.display_name, item=product.display_name, ret=ret.name))
            if qty - left[product] > 1e-6:
                raise UserError(self.env._('%(doc)s: %(qty)s of %(item)s, but only %(left)s are still to be replaced on %(ret)s.',
                                           doc=self.display_name, qty='{:,.2f}'.format(qty), item=product.display_name,
                                           left='{:,.2f}'.format(max(left[product], 0.0)), ret=ret.name))

    def _reconcile_return(self):
        # the credit of a sales return (RET) stays with the customer: it is replaced or refunded, not taken off the invoice
        if self.sb_return_id:
            return
        return super()._reconcile_return()

    def action_post(self):
        reps = self.filtered(lambda v: v.state == 'draft' and v.sb_replacement_of_id)
        for v in reps:
            ret = v.sb_replacement_of_id
            if ret.state != 'posted':
                raise UserError(self.env._('%s is not posted: a replacement is given after the return is posted.', ret.display_name))
            v._sb_check_same_items()
            left = ret.sudo().sb_credit_note_id.amount_residual
            if v.currency_id.compare_amounts(v.amount_total, left) > 0:
                raise UserError(self.env._('%(doc)s: the replacement (%(amt)s) is more than the customer is still owed on %(ret)s (%(left)s).',
                                           doc=v.display_name, amt='{:,.2f}'.format(v.amount_total), ret=ret.name, left='{:,.2f}'.format(left)))
            if v.sb_fiscal_only and not v.sb_pad_number:
                series = self.env['sb.doc.series']._for('replacement', company=v.company_id)
                if series:
                    v.sb_pad_number = series._take(v.date, what=self.env._('Replacement'))
        res = super().action_post()
        for v in reps.filtered(lambda x: x.state == 'posted'):
            note = v.sb_replacement_of_id.sudo().sb_credit_note_id
            mine = v.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
            theirs = note.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
            for line in mine:
                other = theirs.filtered(lambda t: t.account_id == line.account_id and t.partner_id.commercial_partner_id ==
                                        line.partner_id.commercial_partner_id)
                if other:
                    (line | other).reconcile()
            v.sb_replacement_of_id.sudo().message_post(body=self.env._('Replacement %(doc)s given: %(amt)s.', doc=v.name,
                                                                        amt='{:,.2f}'.format(v.amount_total)))
        return res


    def action_void(self):
        # a replacement settled only against its return's credit: unmatched, so the customer is owed again
        for v in self.filtered(lambda x: x.state == 'posted' and x.sb_replacement_of_id):
            note = v.sb_replacement_of_id.sudo().sb_credit_note_id.move_id
            lines = v.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
            partials = lines.matched_debit_ids | lines.matched_credit_ids
            others = (partials.debit_move_id | partials.credit_move_id).move_id - v.move_id
            if partials and others == note:
                lines.remove_move_reconcile()
        return super().action_void()


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_replacement_ids = fields.One2many('sb.voucher', 'sb_replacement_of_id', string='Replacements')
    sb_owed = fields.Monetary('Owed to the customer', compute='_compute_sb_owed')
    sb_replaced = fields.Monetary('Replaced', compute='_compute_sb_owed')
    sb_refunded = fields.Monetary('Refunded / applied', compute='_compute_sb_owed',
                                  help='Paid back to the customer, or applied to their invoices.')
    sb_remaining = fields.Monetary('Still owed', compute='_compute_sb_owed')

    @api.depends('sb_credit_note_id.amount_total', 'sb_credit_note_id.amount_residual', 'sb_credit_note_id.state',
                 'sb_replacement_ids.state', 'sb_replacement_ids.amount_total')
    def _compute_sb_owed(self):
        for v in self:
            note = v.sudo().sb_credit_note_id
            posted = note.state == 'posted'
            v.sb_owed = note.amount_total if posted else 0.0
            v.sb_replaced = sum(v.sudo().sb_replacement_ids.filtered(lambda r: r.state == 'posted').mapped('amount_total'))
            v.sb_remaining = note.amount_residual if posted else 0.0
            v.sb_refunded = max(v.sb_owed - v.sb_replaced - v.sb_remaining, 0.0)

    def action_sb_give_replacement(self):
        """A sales invoice for the replacement goods: what was returned and not replaced yet, at the returned price, on the
        sales returns and allowances account. The same items only; fewer of them may be given (a partial replacement
        leaves the rest owed)."""
        self.ensure_one()
        if self.voucher_type != 'customer_return' or self.state != 'posted':
            raise UserError(self.env._('A replacement is given for a posted sales return.'))
        if self.currency_id.is_zero(self.sb_remaining):
            raise UserError(self.env._('Nothing is owed to the customer on %s any more.', self.name))
        replaced = {}
        for r in self.sb_replacement_ids.filtered(lambda x: x.state == 'posted'):
            for l in r.line_ids:
                replaced[l.product_id] = replaced.get(l.product_id, 0.0) + l.quantity
        account = self.company_id.sb_sales_return_account_id
        lines = []
        for l in self.line_ids.filtered('product_id'):
            take = min(replaced.get(l.product_id, 0.0), l.quantity)
            replaced[l.product_id] = replaced.get(l.product_id, 0.0) - take
            qty = l.quantity - take
            if qty > 1e-9:
                lines.append(Command.create({'product_id': l.product_id.id, 'name': self.env._('Replacement: %s', l.product_id.display_name),
                                             'quantity': qty, 'price_unit': l.sb_price, 'account_id': account.id,
                                             'tax_ids': [Command.clear()]}))
        vals = {'voucher_type': 'sale', 'partner_id': self.partner_id.id, 'date': fields.Date.context_today(self),
                'sb_warehouse_id': self.warehouse_id.id, 'reference': self.name, 'sb_replacement_of_id': self.id,
                'memo': self.env._('Replacement for %s', self.name), 'line_ids': lines}
        V = self.env['sb.voucher']
        if 'sb_deliver_later' in V._fields and getattr(self.company_id, 'sb_delivery_strict', False):
            vals['sb_deliver_later'] = True  # out with a delivery order and its batches, as every sale
        rep = V.create(vals)
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': rep.id, 'view_mode': 'form',
                'name': self.env._('Replacement')}

    def action_void(self):
        given = self.filtered(lambda v: v.voucher_type == 'customer_return' and v.sb_replacement_ids.filtered(lambda r: r.state == 'posted'))
        if given:
            raise UserError(self.env._('%s has replacements given: void them first.', ', '.join(given.mapped('name'))))
        return super().action_void()


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _sb_records(self, report_ref, res_ids, data):
        # the Replacement printout lists the goods given, with no prices: not a tax invoice, so not held to the fiscal register
        if self._get_report(report_ref).report_name == 'sonador_returns.report_replacement':
            return []
        return super()._sb_records(report_ref, res_ids, data)
