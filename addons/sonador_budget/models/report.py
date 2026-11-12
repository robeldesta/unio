"""Budget reports on the engine: the budget by month, account variance and the income statement against budget."""
from collections import defaultdict

from odoo import models

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_year_of

from .budget import MONTHS


class SbReportBudget(models.AbstractModel):
    _inherit = 'sb.report'

    def _budget_for(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        b = self.env['sb.budget'].search([('company_id', '=', self.env.company.id), ('date_from', '<=', dt), ('date_to', '>=', df)],
                                         order='state desc, date_from desc', limit=1)
        return b, df, dt

    def _budget_amounts(self, b, df, dt):
        """{account: budget for the months of [df, dt] that fall in the budget's year}."""
        out = defaultdict(float)
        if not b:
            return out
        idx = [i for i, (_no, _n, a, z) in enumerate(b.periods()) if a <= dt and z >= df]
        for l in b.line_ids:
            out[l.account_id] += sum(l[MONTHS[i]] for i in idx)
        return out

    def _actual_amounts(self, accounts, df, dt):
        AML = self.env['account.move.line']
        rows = AML._read_group([('account_id', 'in', accounts.ids), ('parent_state', '=', 'posted'), ('date', '>=', df), ('date', '<=', dt),
                                ('move_id.sb_closing', '=', False)], ['account_id'], ['balance:sum'])
        return {a: (-(bal or 0.0) if a.internal_group == 'income' else (bal or 0.0)) for a, bal in rows}

    def _report_budget(self, opts):
        b, df, dt = self._budget_for(opts)
        rows = []
        if b:
            months = [p[1][:3] for p in b.periods()]
            grand = [0.0] * 12
            for l in b.line_ids.sorted(lambda x: x.account_code or ''):
                if not self._search_ok(opts, l.account_id.code, l.account_id.name):
                    continue
                sign = 1 if l.account_id.internal_group == 'income' else -1
                vals = {'acc': '%s %s' % (l.account_id.code, l.account_id.name), 'total': l.total}
                for i, m in enumerate(MONTHS):
                    vals[m] = l[m]
                    grand[i] += sign * l[m]
                rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': l.account_id.name, 'values': vals})
            rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Budgeted profit',
                         'values': dict({'acc': 'Budgeted profit (income less expenses)', 'total': sum(grand)}, **{m: grand[i] for i, m in enumerate(MONTHS)})})
        else:
            months = ['M%d' % i for i in range(1, 13)]
        cols = [{'key': 'acc', 'label': 'Account', 'type': 'text'}] + [{'key': m, 'label': months[i], 'type': 'monetary'} for i, m in enumerate(MONTHS)] + \
            [{'key': 'total', 'label': 'Year', 'type': 'monetary'}]
        return self._doc('budget', opts, cols, rows, '%s%s' % (b.name if b else 'No budget for the period', ' (approved)' if b.state == 'approved' else ' (draft)' if b else ''),
                         'Budget')

    def _variance_rows(self, opts, grouped):
        b, df, dt = self._budget_for(opts)
        budget = self._budget_amounts(b, df, dt)
        accounts = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('internal_group', 'in', ('income', 'expense'))])
        actual = self._actual_amounts(accounts, df, dt)
        used = sorted(set(budget) | {a for a, v in actual.items() if abs(v) >= 0.5}, key=lambda a: a.code or '')
        sections = [('rev', 'Revenue', lambda a: a.account_type in ('income',)), ('other_inc', 'Other income', lambda a: a.account_type == 'income_other'),
                    ('cos', 'Cost of sales', lambda a: a.account_type == 'expense_direct_cost'),
                    ('opex', 'Operating expenses', lambda a: a.account_type in ('expense',)),
                    ('dep', 'Depreciation', lambda a: a.account_type == 'expense_depreciation')]
        rows, profit = [], {'actual': 0.0, 'budget': 0.0}
        for sid, title, test in sections:
            accs = [a for a in used if test(a) and self._search_ok(opts, a.code, a.name)]
            if not accs:
                continue
            tot = {'actual': sum(actual.get(a, 0.0) for a in accs), 'budget': sum(budget.get(a, 0.0) for a in accs)}
            fav = 1 if sid in ('rev', 'other_inc') else -1
            for k in profit:
                profit[k] += fav * tot[k]
            if grouped:
                rows.append({'id': sid, 'parent': None, 'level': 0, 'kind': 'section', 'label': title, 'expandable': True,
                             'values': self._var_vals(title, tot['actual'], tot['budget'], fav)})
            for a in accs:
                rows.append({'id': 'a%d' % a.id, 'parent': sid if grouped else None, 'level': 1 if grouped else 0, 'kind': 'line', 'label': a.name,
                             'values': self._var_vals('%s %s' % (a.code, a.name), actual.get(a, 0.0), budget.get(a, 0.0), fav),
                             'drill': {'report': 'general_ledger', 'account_ids': [a.id], 'date_from': opts['date_from'], 'date_to': opts['date_to']}})
        rows.append({'id': 'profit', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Profit', 'values': self._var_vals('Profit', profit['actual'], profit['budget'], 1)})
        return b, df, dt, rows

    def _var_vals(self, label, actual, budget, fav):
        var = (actual - budget) * fav
        return {'acc': label, 'actual': actual, 'budget': budget, 'var': var, 'pct': round(100.0 * var / abs(budget), 1) if budget else ''}

    def _var_cols(self):
        return [{'key': 'acc', 'label': 'Account', 'type': 'text'}, {'key': 'actual', 'label': 'Actual', 'type': 'monetary'},
                {'key': 'budget', 'label': 'Budget', 'type': 'monetary'}, {'key': 'var', 'label': 'Better / (worse)', 'type': 'monetary'},
                {'key': 'pct', 'label': '%', 'type': 'number'}]

    def _report_acc_variance(self, opts):
        b, df, dt, rows = self._variance_rows(opts, grouped=False)
        return self._doc('acc_variance', opts, self._var_cols(), rows, '%s  |  %s' % (self._period_name(df, dt), b.name if b else 'no budget'), 'Account Variance')

    def _report_is_budget(self, opts):
        b, df, dt, rows = self._variance_rows(opts, grouped=True)
        return self._doc('is_budget', opts, self._var_cols(), rows, '%s  |  %s' % (self._period_name(df, dt), b.name if b else 'no budget'),
                         'Income Statement / Budget')
