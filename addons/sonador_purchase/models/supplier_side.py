"""Paying for and receiving a purchase order.

Paid first (owner's rules, 12 Nov 2026):
    CPV against the order:  Dr Advance to supplier (goods) · Dr Input VAT  /  Cr Bank (and withholding)
    GRN:                    Dr Inventory  /  Cr Advance to supplier, up to what the order's advance still holds
On credit (the standard three steps, chosen 12 Nov 2026):
    GRN:                    Dr Inventory  /  Cr Goods received not invoiced (GRNI), for what the advance did not cover
    Purchase voucher (PV):  Dr GRNI · Dr Input VAT  /  Cr Supplier payable, for the GRNI part of the order's GRNs only
    CPV:                    Dr Supplier payable  /  Cr Bank
A CPV against an order first pays the order's open purchase vouchers (their VAT is already booked), then pays the goods
not yet invoiced ahead (advance, with their VAT), and anything beyond the order is advance with no VAT.
Each order's payments, GRNs and purchase vouchers are matched (reconciled) with each other only."""
from odoo import api, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # ------------------------------------------------------------------ the order's own entries
    def _sb_side_moves(self):
        """Posted entries of this order's payments, GRNs and purchase vouchers (voided ones left out with their reversal)."""
        self.ensure_one()
        docs = self._sb_payments().filtered(lambda v: v.state == 'posted' and v.voucher_type == 'payment') | \
            self._sb_bills().filtered(lambda v: v.state == 'posted' and v.voucher_type == 'bill')
        grns = self.sb_grn_ids.filtered(lambda g: g.state == 'posted' and not g.sb_migrated)
        return (docs.move_id | grns.move_id).filtered(lambda m: m.state == 'posted')

    def _sb_side_lines(self, account, moves=None):
        partner = self.partner_id.commercial_partner_id
        moves = self._sb_side_moves() if moves is None else moves
        return moves.line_ids.filtered(lambda l: l.account_id == account and l.partner_id.commercial_partner_id == partner)

    def _sb_payable_account(self):
        return self.partner_id.commercial_partner_id.with_company(self.company_id).property_account_payable_id

    def _sb_advance_open(self):
        """What the order's payments hold ahead of the goods (before VAT)."""
        bal = sum(self._sb_side_lines(self.company_id.sb_vendor_advance_account_id).mapped('balance'))
        return max(self.company_currency_id.round(bal), 0.0)

    def _sb_payables_open(self):
        """What the order's purchase vouchers still owe (VAT included)."""
        pv = self._sb_bills().filtered(lambda v: v.state == 'posted' and v.voucher_type == 'bill').move_id
        bal = sum(self._sb_side_lines(self._sb_payable_account(), pv | self._sb_payments().move_id).mapped('balance'))
        return max(self.company_currency_id.round(-bal), 0.0)

    def _sb_goods_paid_ahead(self):
        """Goods the order's payments paid ahead (before VAT): their debits to the advance account."""
        lines = self._sb_side_lines(self.company_id.sb_vendor_advance_account_id,
                                    self._sb_payments().filtered(lambda v: v.state == 'posted').move_id)
        return sum(lines.mapped('debit'))

    def _sb_goods_invoiced(self):
        """Goods the order's purchase vouchers invoiced (before VAT): their debits to GRNI."""
        pv = self._sb_bills().filtered(lambda v: v.state == 'posted' and v.voucher_type == 'bill').move_id
        return sum(self._sb_side_lines(self.company_id.sb_grni_account_id, pv).mapped('debit'))

    def _sb_vat_parts(self):
        """[(share of the order's VAT, account)] from the order lines' own taxes."""
        self.ensure_one()
        Rep = self.env['account.tax.repartition.line']
        amounts = {}
        for line in self.order_line.filtered(lambda l: l.tax_ids and l.price_subtotal):
            res = line.tax_ids.compute_all(line.price_unit * (1 - (line.discount or 0.0) / 100.0), self.currency_id,
                                           line.product_qty, product=line.product_id, partner=self.partner_id)
            for t in res['taxes']:
                rep = Rep.browse(t['tax_repartition_line_id'])
                if rep.account_id:
                    amounts[rep] = amounts.get(rep, 0.0) + t['amount']
        total = sum(amounts.values())
        return [(amt / total, rep.account_id) for rep, amt in amounts.items()] if total else []

    def _sb_reconcile_side(self):
        """Match the order's payments, GRNs and purchase vouchers on advance, GRNI and the supplier's payable."""
        for o in self:
            c = o.company_id
            for account in (c.sb_vendor_advance_account_id, c.sb_grni_account_id, o._sb_payable_account()):
                if not account or not account.reconcile:
                    continue
                open_lines = o._sb_side_lines(account).filtered(lambda l: not l.reconciled and l.amount_residual)
                if open_lines.filtered(lambda l: l.balance > 0) and open_lines.filtered(lambda l: l.balance < 0):
                    open_lines.reconcile()

    # ------------------------------------------------------------------ purchase voucher: the GRNI part only
    def _sb_grn_grni_share(self, grn):
        """The part of a GRN that went to GRNI (0 when its order's advance covered all of it), as a share of its value."""
        value = sum(grn.line_ids.mapped('value'))
        grni = -sum(self._sb_side_lines(grn.account_id or self.company_id.sb_grni_account_id, grn.move_id).mapped('balance'))
        return min(max(grni / value, 0.0), 1.0) if value else 0.0

    def _sb_bill_vals(self, grns, services=True):
        """A purchase voucher clears only what the GRNs left on GRNI: goods covered by the order's advance are paid."""
        vals = super()._sb_bill_vals(grns, services=services)
        shares = {g.name: self._sb_grn_grni_share(g) for g in grns}
        kept = []
        for cmd in vals.get('line_ids', []):
            line = cmd[2] if isinstance(cmd, (list, tuple)) and len(cmd) > 2 else cmd
            share = next((s for name, s in shares.items() if name and '(%s)' % name in (line.get('name') or '')), 1.0)
            if share <= 0:
                continue
            if share < 1:
                line['quantity'] = line.get('quantity', 0.0) * share
            kept.append(cmd)
        if not kept:
            raise UserError(self.env._('Nothing to put on a purchase voucher for %s: its goods were paid in advance.', self.name))
        vals['line_ids'] = kept
        return vals

    # ------------------------------------------------------------------ money on the order
    def _compute_sb_money(self):
        """Paid = the order's payments (VAT and withholding included) and cash purchases, or what the old figures say
        for orders paid without being linked to them, whichever is more."""
        super()._compute_sb_money()
        for o in self:
            cur = o.company_currency_id
            pays = o._sb_payments().filtered(lambda v: v.state == 'posted')
            linked = sum((p.received_amount or p.amount_total) for p in pays)
            if cur.compare_amounts(linked, o.sb_amount_paid) <= 0:
                continue
            total = o.currency_id._convert(o.amount_total, cur, o.company_id, o.date_order or o.create_date) \
                if o.state == 'purchase' else 0.0
            adv = o._sb_advance_open() if o.state == 'purchase' else 0.0
            o.sb_amount_paid = linked
            o.sb_amount_advance = adv
            o.sb_amount_due = max(total - linked, 0.0)
            if cur.is_zero(o.sb_amount_due):
                o.sb_payment_status = 'paid'
            elif cur.compare_amounts(adv, 0.0) > 0:
                o.sb_payment_status = 'advance'
            else:
                o.sb_payment_status = 'partial'


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    def _sb_grn_advance_open(self):
        po = self.sb_purchase_id
        if self.voucher_type == 'grn' and po and not self.sb_migrated:
            return po._sb_advance_open()
        return super()._sb_grn_advance_open()

    @api.depends('bill_ids.state', 'bill_many_ids.state', 'move_id')
    def _compute_billed(self):
        """A GRN its order's advance paid in full needs no purchase voucher: it counts as done."""
        super()._compute_billed()
        for g in self.filtered(lambda g: not g.billed and g.voucher_type == 'grn' and g.state == 'posted'
                               and g.sb_purchase_id and g.move_id):
            g.billed = g.sb_purchase_id._sb_grn_grni_share(g) <= 0

    def action_create_bill(self):
        """Against an order: the order's purchase voucher (only the part not paid in advance)."""
        if len(self) == 1 and self.sb_purchase_id and self.voucher_type == 'grn' and self.state == 'posted':
            return self.sb_purchase_id.action_sb_bill()
        return super().action_create_bill()

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda g: g.voucher_type == 'grn' and g.state == 'posted').sb_purchase_id._sb_reconcile_side()
        return res


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def _sb_unapplied_split(self, amount, sign):
        po = self.sb_purchase_id
        if not (self.voucher_type == 'payment' and sign < 0 and po and po.state == 'purchase'):
            return super()._sb_unapplied_split(amount, sign)
        c, cur, ref = self.company_id, self.company_id.currency_id, po.name
        out = []
        # 1. the order's purchase vouchers still open: their VAT is booked already
        to_pv = cur.round(min(amount, po._sb_payables_open()))
        if to_pv:
            out.append((po._sb_payable_account(), to_pv, self.env._('Purchase voucher(s) of %s', ref)))
        rest = cur.round(amount - to_pv)
        # 2. goods not yet invoiced: paid ahead, with their VAT; 3. beyond the order: advance, no VAT
        day = self.date or po.date_order
        untaxed = po.currency_id._convert(po.amount_untaxed, cur, c, day)
        rate = (po.currency_id._convert(po.amount_tax, cur, c, day) / untaxed) if untaxed else 0.0
        left = max(untaxed - po._sb_goods_paid_ahead() - po._sb_goods_invoiced(), 0.0)
        covered = min(rest, cur.round(left * (1 + rate)))
        net = cur.round(covered / (1 + rate)) if rate else covered
        vat = cur.round(covered - net)
        ahead = cur.round(net + rest - covered)
        if ahead:
            out.append((c.sb_vendor_advance_account_id, ahead, self.env._('Advance on %s', ref)))
        if vat:
            parts = po._sb_vat_parts()
            done = 0.0
            for i, (share, account) in enumerate(parts):
                part = cur.round(vat - done) if i == len(parts) - 1 else cur.round(vat * share)
                done += part
                out.append((account, part, self.env._('VAT on %s', ref)))
            if not parts:
                out.append((c.sb_vendor_advance_account_id, vat, self.env._('Advance on %s', ref)))
        return out

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda v: v.voucher_type in ('payment', 'bill') and v.state == 'posted').sb_purchase_id._sb_reconcile_side()
        return res
