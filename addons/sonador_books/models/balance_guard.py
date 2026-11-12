"""Journal vouchers and opening balances must balance: debits equal credits, on saving as on posting, and no line takes
a negative amount (a credit goes in the Credit column). Nothing is ever balanced for the user behind their back.

The one exception is the Beginning Balances screen while it builds the opening voucher piece by piece (customer and
vendor items are entered on their own pages): it checks the opening trial balance itself before an account balance is
saved."""
from odoo import api, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

CHECKED = ('journal', 'opening')


def _money(v):
    return '{:,.2f}'.format(v)


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def _sb_balance_problem(self):
        """A sentence saying what is wrong, or ''."""
        self.ensure_one()
        cur = self.currency_id
        for l in self.line_ids:
            if float_compare(l.debit, 0.0, precision_rounding=cur.rounding) < 0 or \
                    float_compare(l.credit, 0.0, precision_rounding=cur.rounding) < 0:
                amount = l.debit if l.debit < 0 else l.credit
                return self.env._(
                    '%(acc)s has a negative amount (%(amt)s). Enter amounts as positive numbers: put it in the %(col)s '
                    'column instead.', acc=l.account_id.display_name or l.name or '',
                    amt=_money(amount), col='Credit' if l.debit < 0 else 'Debit')
        debit, credit = sum(self.line_ids.mapped('debit')), sum(self.line_ids.mapped('credit'))
        if float_compare(debit, credit, precision_rounding=cur.rounding):
            return self.env._(
                '%(name)s does not balance: debits %(d)s, credits %(c)s, difference %(diff)s. Debits must equal credits.',
                name=self.name if self.name and self.name != 'New' else self.env._('This voucher'),
                d=_money(debit), c=_money(credit), diff=_money(abs(debit - credit)))
        return ''

    def _sb_guard_balance(self):
        if self.env.context.get('sb_bb_screen'):
            return
        for v in self.filtered(lambda v: v.voucher_type in CHECKED and v.line_ids):
            problem = v._sb_balance_problem()
            if problem:
                raise ValidationError(problem)

    @api.constrains('line_ids', 'voucher_type')
    def _sb_check_balanced(self):
        self._sb_guard_balance()

    def _check_before_post(self):
        self._sb_guard_balance()
        return super()._check_before_post()


class SbBeginning(models.AbstractModel):
    _inherit = 'sb.beginning'

    def _post_opening(self, voucher, lines):
        return super(SbBeginning, self.with_context(sb_bb_screen=True))._post_opening(
            voucher.with_context(sb_bb_screen=True), lines)

    def _repost(self, voucher, lines):
        return super(SbBeginning, self.with_context(sb_bb_screen=True))._repost(voucher.with_context(sb_bb_screen=True), lines)

    def _remove(self, voucher):
        return super(SbBeginning, self.with_context(sb_bb_screen=True))._remove(voucher.with_context(sb_bb_screen=True))
