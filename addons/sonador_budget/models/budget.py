"""Budgets: one per year (the company's fiscal year), an amount per account and month; filled from last year's actuals
with a growth rate, edited on a grid, approved. Income and expense accounts by default (balance sheet accounts may be added)."""
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, fy_label

MONTHS = ['m%02d' % i for i in range(1, 13)]


class SbBudget(models.Model):
    _name = 'sb.budget'
    _description = 'Budget'
    _inherit = ['mail.thread']
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    fiscal_year = fields.Integer(required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved')], default='draft', tracking=True)
    approved_by = fields.Many2one('res.users', readonly=True)
    line_ids = fields.One2many('sb.budget.line', 'budget_id')
    note = fields.Text()

    def periods(self):
        self.ensure_one()
        return [(no, name, a, b) for no, name, a, b in fiscal_periods(self.fiscal_year)][:12]


class SbBudgetLine(models.Model):
    _name = 'sb.budget.line'
    _description = 'Budget line'
    _order = 'budget_id, account_code'

    budget_id = fields.Many2one('sb.budget', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='budget_id.company_id', store=True)
    account_id = fields.Many2one('account.account', required=True)
    account_code = fields.Char(related='account_id.code', store=True)
    total = fields.Float(compute='_compute_total', store=True)
    for _m in MONTHS:
        locals()[_m] = fields.Float()
    del _m

    _uniq = models.Constraint('unique(budget_id, account_id)', 'An account appears once in a budget.')

    @api.depends(*MONTHS)
    def _compute_total(self):
        for l in self:
            l.total = sum(l[m] for m in MONTHS)


class SbBudgets(models.AbstractModel):
    """Calls of the budget screen."""
    _name = 'sb.budgets'
    _description = 'Budgets (screen)'

    def _require(self, write=False):
        if not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise AccessError(self.env._('Budgets are kept by the accountants.'))

    def _accounts(self):
        return self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('internal_group', 'in', ('income', 'expense'))],
                                                  order='code')

    def _actuals(self, accounts, periods):
        """{account_id: [12 amounts]} posted in each period (income positive, expenses positive), closing entries left out."""
        AML = self.env['account.move.line']
        out = {}
        for i, (_no, _n, a, b) in enumerate(periods):
            rows = AML._read_group([('account_id', 'in', accounts.ids), ('parent_state', '=', 'posted'), ('date', '>=', a), ('date', '<=', b),
                                    ('move_id.sb_closing', '=', False)], ['account_id'], ['balance:sum'])
            for acc, bal in rows:
                sign = -1 if acc.internal_group == 'income' else 1
                out.setdefault(acc.id, [0.0] * 12)[i] = sign * (bal or 0.0)
        return out

    @api.model
    def ctx(self):
        self._require()
        c = self.env.company
        today = fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        B = self.env['sb.budget']
        return {'company': c.name, 'currency': c.currency_id.symbol or c.currency_id.name, 'fy': fy,
                'years': [{'fy': y, 'label': fy_label(y), 'budget_id': B.search([('company_id', '=', c.id), ('fiscal_year', '=', y)], limit=1).id or False}
                          for y in (fy - 1, fy, fy + 1)]}

    @api.model
    def load(self, fy):
        """The budget of the year with last year's actuals and this year's actuals beside it."""
        self._require()
        c = self.env.company
        fy = int(fy)
        periods = fiscal_periods(fy)[:12]
        b = self.env['sb.budget'].search([('company_id', '=', c.id), ('fiscal_year', '=', fy)], limit=1)
        accounts = self._accounts()
        actual = self._actuals(accounts, periods)
        last = self._actuals(accounts, fiscal_periods(fy - 1)[:12])
        lines = {l.account_id.id: l for l in b.line_ids}
        rows = []
        for a in accounts:
            l = lines.get(a.id)
            amounts = [l[m] for m in MONTHS] if l else [0.0] * 12
            if not l and not any(actual.get(a.id, [])) and not any(last.get(a.id, [])):
                continue
            rows.append({'account_id': a.id, 'code': a.code, 'name': a.name, 'group': 'income' if a.internal_group == 'income' else 'expense',
                         'budget': amounts, 'actual': actual.get(a.id, [0.0] * 12), 'last_year': sum(last.get(a.id, [0.0] * 12))})
        return {'id': b.id or False, 'name': b.name or 'Budget %s' % fy_label(fy), 'state': b.state or 'draft',
                'approved_by': b.approved_by.name or '', 'fy': fy, 'months': [p[1][:3] for p in periods], 'rows': rows}

    @api.model
    def save(self, fy, rows, name=None):
        self._require()
        c = self.env.company
        fy = int(fy)
        periods = fiscal_periods(fy)[:12]
        B = self.env['sb.budget']
        b = B.search([('company_id', '=', c.id), ('fiscal_year', '=', fy)], limit=1)
        if b and b.state == 'approved':
            raise UserError(self.env._('The %s budget is approved: reopen it to change it.', b.name))
        if not b:
            b = B.create({'name': name or 'Budget %s' % fy_label(fy), 'fiscal_year': fy, 'date_from': periods[0][2],
                          'date_to': periods[-1][3], 'company_id': c.id})
        elif name:
            b.name = name
        have = {l.account_id.id: l for l in b.line_ids}
        for r in rows:
            vals = {m: float(v or 0) for m, v in zip(MONTHS, r['budget'])}
            l = have.get(int(r['account_id']))
            if l:
                l.write(vals)
            elif any(vals.values()):
                self.env['sb.budget.line'].create(dict(vals, budget_id=b.id, account_id=int(r['account_id'])))
        return self.load(fy)

    @api.model
    def fill(self, fy, growth):
        """Last year's actuals month by month, times (1 + growth %)."""
        self._require()
        fy = int(fy)
        accounts = self._accounts()
        last = self._actuals(accounts, fiscal_periods(fy - 1)[:12])
        k = 1 + float(growth or 0) / 100.0
        rows = [{'account_id': aid, 'budget': [round(x * k, -3) for x in amounts]} for aid, amounts in last.items() if any(amounts)]
        return self.save(fy, rows)

    @api.model
    def set_state(self, fy, state):
        self._require()
        b = self.env['sb.budget'].search([('company_id', '=', self.env.company.id), ('fiscal_year', '=', int(fy))], limit=1)
        if not b:
            raise UserError(self.env._('Save the budget first.'))
        if state == 'approved' and not self.env.user.has_group('sonador_books.group_sb_manager'):
            raise AccessError(self.env._('A manager approves the budget.'))
        b.write({'state': state, 'approved_by': self.env.uid if state == 'approved' else False})
        return self.load(b.fiscal_year)
