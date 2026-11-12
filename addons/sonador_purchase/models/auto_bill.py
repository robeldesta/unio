"""Goods received on a prepaid purchase order are billed by themselves (a company setting, off by default).

When a GRN is posted against an order with an advance not yet used, the purchase voucher for what this GRN received is made
at the order's prices and taxes (as the order's Bill button makes it), posted, and the advance applied to it: the
supplier's advance and goods received not invoiced clear without anyone doing anything. A partial receipt is billed for
what arrived; what is left of the advance stays on the order. If the bill cannot be posted (a closed period, a missing
account, the user may not post bills), the GRN is posted all the same and the bill waits as a draft for finance, in Me,
with the reason."""
from markupsafe import Markup

from odoo import Command, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_po_auto_bill = fields.Boolean(
        'Bill goods received on prepaid purchase orders automatically',
        help='A GRN posted against a purchase order paid in advance makes and posts the purchase voucher for what it received, '
             'at the order\'s prices, and applies the advance to it. A purchase voucher that cannot be posted waits for finance in Me.')


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    def action_post(self):
        grns = self.filtered(lambda v: v.state == 'draft' and v.voucher_type == 'grn')
        res = super().action_post()
        for g in grns.filtered(lambda v: v.state == 'posted' and v.sb_purchase_id and v.company_id.sb_po_auto_bill
                               and not v.billed and not v.sb_migrated):
            g._sb_auto_bill()
        return res

    def _sb_auto_bill(self):
        self.ensure_one()
        po = self.sb_purchase_id.sudo()
        po.invalidate_recordset(['sb_amount_advance'])
        if po.state != 'purchase' or self.company_id.currency_id.compare_amounts(po.sb_amount_advance, 0.0) <= 0:
            return False  # not paid in advance: billed by hand, as before
        vals = po._sb_bill_vals(self, services=False)
        vals.update(date=self.date, sb_auto_bill=True, company_id=self.company_id.id,
                    memo=self.env._('Purchase voucher for %(po)s (%(grn)s), made automatically', po=po.name, grn=self.name))
        bill = self.env['sb.voucher'].sudo().with_context(default_voucher_type='bill').create(vals)
        bill.message_post(body=self.env._('Made automatically from %(grn)s, posted by %(u)s: %(po)s was paid in advance.',
                                          grn=self.name, u=self.env.user.name, po=po.name))
        error = False
        try:
            with self.env.cr.savepoint():
                # part of posting the GRN (no confirmation of its own), with the rights of whoever posts the GRN
                bill.with_env(self.env).with_context(sb_confirm_inside=True).action_post()
        except Exception as e:  # noqa: BLE001 - whatever stops the bill, the GRN stays posted and finance is told
            error = (e.args[0] if e.args and isinstance(e.args[0], str) else str(e)) or type(e).__name__
            bill.write({'sb_auto_bill_error': error[:500]})
        if error:
            self.message_post(body=self.env._('Purchase voucher made from this GRN but not posted (%(err)s): it waits for finance '
                                              'in Me as a draft.', err=error))
        else:
            self.message_post(body=self.env._('Purchase voucher %(bill)s made and posted automatically; the advance on %(po)s '
                                              'is applied to it.', bill=bill.name, po=po.name))
        return bill


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_auto_bill = fields.Boolean('Made automatically', copy=False, readonly=True,
                                  help='A purchase voucher Unio made from a GRN of a prepaid purchase order.')
    sb_auto_bill_error = fields.Char('Not posted automatically', copy=False, readonly=True,
                                     help='Why the purchase voucher made from the GRN could not be posted: it waits for finance in Me.')

    def action_post(self):
        auto = self.filtered(lambda v: v.state == 'draft' and v.sb_auto_bill)
        res = super().action_post()
        for b in auto.filtered(lambda v: v.state == 'posted'):
            if b.sb_auto_bill_error:
                b.sudo().sb_auto_bill_error = False
            b._sb_apply_po_advance()
        return res

    def _sb_apply_po_advance(self):
        """The advance paid on the bill's order settles the bill, as far as it goes: Dr the supplier's payable, Cr the
        advance (a journal entry of the system's own, in the Journal Vouchers journal, matched on both sides)."""
        self.ensure_one()
        po, company = self.sb_purchase_id.sudo(), self.company_id
        cur = company.currency_id
        bill = self.sudo()
        partner = bill.partner_id.commercial_partner_id
        payable = bill.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.amount_residual < 0)
        if not po or not payable or (bill.move_id.currency_id and bill.move_id.currency_id != cur):
            return False
        adv_acc = company.sb_vendor_advance_account_id
        advances = po._sb_payments().filtered(lambda v: v.state == 'posted').move_id.line_ids.filtered(
            lambda l: l.balance > 0 and l.amount_residual > 0 and l.partner_id.commercial_partner_id == partner and (
                l.account_id == adv_acc or (l.account_id.sb_partner_account and l.account_id.internal_group == 'asset'))
        ).sorted(lambda l: (l.date, l.id))
        left = min(-sum(payable.mapped('amount_residual')), sum(advances.mapped('amount_residual')))
        if cur.compare_amounts(left, 0.0) <= 0:
            return False
        used = {}
        for l in advances:
            take = min(left, l.amount_residual)
            if cur.compare_amounts(take, 0.0) <= 0:
                break
            used.setdefault(l.account_id, [0.0, self.env['account.move.line']])
            used[l.account_id][0] += take
            used[l.account_id][1] |= l
            left = cur.round(left - take)
        total = cur.round(sum(v[0] for v in used.values()))
        label = self.env._('Advance on %(po)s applied to %(bill)s', po=po.name, bill=bill.name)
        Move = self.env['account.move'].sudo()
        journal = Move._sb_reversal_journal(company)
        move = Move.create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': bill.date, 'company_id': company.id, 'ref': label,
            'line_ids': [Command.create({'account_id': payable[:1].account_id.id, 'partner_id': partner.id, 'name': label,
                                         'debit': total, 'credit': 0.0})]
                        + [Command.create({'account_id': acc.id, 'partner_id': partner.id, 'name': label,
                                           'debit': 0.0, 'credit': cur.round(amt)}) for acc, (amt, _l) in used.items()]})
        move.action_post()
        (move.line_ids.filtered(lambda l: l.account_id == payable[:1].account_id and l.debit) | payable[:1]).reconcile()
        for acc, (_amt, lines) in used.items():
            (move.line_ids.filtered(lambda l, a=acc: l.account_id == a and l.credit) | lines).reconcile()
        bill.message_post(body=Markup('%s') % self.env._('Advance of %(amt)s paid on %(pays)s applied (%(entry)s).',
                                                          amt='{:,.2f}'.format(total), entry=move.name,
                                                          pays=', '.join(sorted(set(sum((v[1] for v in used.values()),
                                                                                        self.env['account.move.line']).move_id.mapped('name'))))))
        return move
