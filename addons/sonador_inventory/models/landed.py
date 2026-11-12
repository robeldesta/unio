"""Landed costs: a supplier's bill for freight, clearing, duty or insurance can be marked as landed cost of one or more
GRNs. When it is posted its amount (before VAT) is spread over the GRN items - by value or by quantity - moved from the
accounts on the bill into the items' inventory accounts by a journal voucher, and added to their average cost from the
bill's date (as a cost adjustment, so stock already sold is not re-costed)."""
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.sonador_books.models.move_guard import controlled


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_landed_grn_ids = fields.Many2many('sb.stock.voucher', 'sb_voucher_landed_grn_rel', 'voucher_id', 'grn_id', string='Landed cost of GRNs',
                                         domain="[('voucher_type', '=', 'grn'), ('state', '=', 'posted')]",
                                         help='Freight, clearing, duty, insurance: the bill becomes part of the cost of the items received on these GRNs.')
    sb_landed_basis = fields.Selection([('value', 'By value'), ('quantity', 'By quantity')], 'Spread', default='value')
    sb_landed_voucher_id = fields.Many2one('sb.voucher', 'Landed cost entry', readonly=True, copy=False)

    def action_post(self):
        res = super().action_post()
        for v in self.filtered(lambda x: x.voucher_type in ('bill', 'cash_purchase') and x.state == 'posted'
                               and x.sb_landed_grn_ids and not x.sb_landed_voucher_id):
            v._sb_post_landed()
        return res

    def _sb_post_landed(self):
        self.ensure_one()
        lines = self.sb_landed_grn_ids.line_ids.filtered(lambda l: l.product_id and l.quantity > 0)
        if not lines:
            raise ValidationError(self.env._('The GRNs chosen as landed cost have no items.'))
        weight = {l: (l.value if self.sb_landed_basis == 'value' else l.quantity) for l in lines}
        total_w = sum(weight.values())
        if total_w <= 0:
            raise ValidationError(self.env._('Nothing to spread the landed cost over.'))
        Stock = self.env['sb.stock']
        cur = self.company_id.currency_id
        by_line = [(l.account_id, l.price_subtotal if 'price_subtotal' in l._fields and l.price_subtotal else l.amount) for l in self.line_ids if l.account_id]
        amount = sum(a for _acc, a in by_line)
        if cur.is_zero(amount):
            return
        per_product = defaultdict(float)
        for l, w in weight.items():
            per_product[l.product_id] += amount * w / total_w
        jl, debit = [], defaultdict(float)
        for p, amt in per_product.items():
            debit[Stock._valuation_account(p, self.company_id)] += cur.round(amt)
        for acc, amt in debit.items():
            jl.append(Command.create({'account_id': acc.id, 'name': self.env._('Landed cost %s', self.name), 'debit': amt, 'credit': 0.0}))
        for acc, amt in by_line:
            jl.append(Command.create({'account_id': acc.id, 'name': self.env._('Landed cost %s', self.name), 'debit': 0.0, 'credit': cur.round(amt)}))
        diff = cur.round(sum(debit.values()) - sum(cur.round(a) for _acc, a in by_line))
        if diff:  # rounding: onto the first debit line
            first = jl[0][2]
            first['debit'] -= diff
        jv = self.env['sb.voucher'].create({'voucher_type': 'journal', 'company_id': self.company_id.id, 'date': self.date,
                                            'reference': self.name, 'memo': self.env._('Landed cost of %s', ', '.join(self.sb_landed_grn_ids.mapped('name'))),
                                            'line_ids': jl})
        jv.action_post()
        R = self.env['sb.stock.revaluation'].sudo()
        for p, amt in per_product.items():
            R.create({'company_id': self.company_id.id, 'product_id': p.id, 'date': self.date, 'value': cur.round(amt),
                      'reference': self.name, 'description': self.env._('Landed cost (%s)', self.partner_id.name or ''), 'move_id': jv.move_id.id})
            Stock._recost(p, self.company_id, self.date, reason=self.name)
        controlled(self).sb_landed_voucher_id = jv  # part of posting the bill
