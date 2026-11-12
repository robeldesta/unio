"""Changing an asset that already has depreciation posted: the asset takes the new cost or terms, and its depreciation is
worked out again from the start. What is already posted stays; one catch-up journal voucher posts the difference
(more or less depreciation), and moves the cost and accumulated depreciation when the asset changes class."""
from odoo import Command, fields, models

from .asset import next_period, period_of

TERMS = {'cost', 'category_id', 'date_start', 'method', 'life_months', 'rate', 'salvage', 'opening_depreciation'}


def _money(v):
    return '{:,.2f}'.format(v)


class SbAsset(models.Model):
    _inherit = 'sb.asset'

    def _sb_snapshot(self):
        self.ensure_one()
        return {'category': self.category_id, 'cost': self.cost, 'depreciated': self.depreciated,
                'last': max(self.line_ids.filtered('voucher_id').mapped('date') or [False])}

    def _sb_redo_depreciation(self, old):
        """old: _sb_snapshot() taken before the change. Posts the catch-up voucher; returns it (or nothing)."""
        self.ensure_one()
        cur = self.currency_id
        last = old['last']
        # what the depreciation should be through the last month posted, on the new terms
        state = self.state
        if state == 'done':
            self.state = 'running'  # _amount_for depreciates running assets only
        total = self.opening_depreciation
        d = self._first_month()
        while last and period_of(d)[2] <= last:
            total += self._amount_for(period_of(d)[2], total)
            d = next_period(d)
        diff = cur.round(total - old['depreciated'])
        new_cat, old_cat = self.category_id, old['category']
        lines, notes = [], []
        name = self.env._('Recalculated: %s', self.name)

        def add(account, amount, label):
            if account and not cur.is_zero(amount):
                lines.append(Command.create({'account_id': account.id, 'name': label,
                                             'debit': amount if amount > 0 else 0.0, 'credit': -amount if amount < 0 else 0.0}))
        if not cur.is_zero(diff):
            add(new_cat.expense_account_id, diff, name)
            add(new_cat.depreciation_account_id, -diff, name)
            notes.append(self.env._('%(more)s depreciation %(amt)s', more='more' if diff > 0 else 'less', amt=_money(abs(diff))))
        if new_cat != old_cat:
            label = self.env._('%(a)s moved from %(f)s to %(t)s', a=self.name, f=old_cat.name, t=new_cat.name)
            if new_cat.asset_account_id != old_cat.asset_account_id:
                add(new_cat.asset_account_id, old['cost'], label)
                add(old_cat.asset_account_id, -old['cost'], label)
            if new_cat.depreciation_account_id != old_cat.depreciation_account_id:
                add(old_cat.depreciation_account_id, old['depreciated'], label)
                add(new_cat.depreciation_account_id, -old['depreciated'], label)
            notes.append(label)
        voucher = self.env['sb.voucher']
        if lines:
            lock = self.company_id.fiscalyear_lock_date
            day = last if last and (not lock or last > lock) else fields.Date.context_today(self)
            voucher = voucher.create({
                'voucher_type': 'journal', 'company_id': self.company_id.id, 'date': day,
                'reference': 'DEP-%s' % (self.code or self.id), 'memo': '%s: %s' % (name, '; '.join(notes)), 'line_ids': lines})
            voucher.action_post()
            if not cur.is_zero(diff):
                # the register keeps one line a month: the catch-up joins the month it is dated in
                dep = self.line_ids.filtered(lambda l: l.date == day)[:1]
                if dep:
                    dep.amount = cur.round(dep.amount + diff)
                else:
                    self.env['sb.asset.dep'].create({'asset_id': self.id, 'date': day, 'amount': diff, 'voucher_id': voucher.id})
        self.invalidate_recordset(['depreciated', 'book_value'])
        self.state = 'done' if self.cost - self.salvage - self.depreciated <= 0.5 else 'running'
        body = self.env._('Cost or terms changed after depreciation was posted: depreciation worked out again from the start.')
        if voucher:
            body += ' ' + self.env._('%(v)s posts %(what)s.', v=voucher.name, what='; '.join(notes))
        else:
            body += ' ' + self.env._('Nothing to post: the depreciation already posted is right.')
        if cur.compare_amounts(self.cost, old['cost']):
            body += ' ' + self.env._('The cost went from %(o)s to %(n)s in the register: if the purchase or opening voucher shows '
                                     'the old cost, correct that voucher too.', o=_money(old['cost']), n=_money(self.cost))
        self.message_post(body=body)
        return voucher
