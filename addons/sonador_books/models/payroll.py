"""Ethiopian payroll: employees, employment income tax (PAYE), POESSA pension, monthly runs posted to the ledger
and paid by payment voucher."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from .ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_label

# Income Tax Proclamation No. 1395/2025, Article 11 (monthly employment income)
DEFAULT_BRACKETS = [
    (0, 2000, 0, 0),
    (2000, 4000, 15, 300),
    (4000, 7000, 20, 500),
    (7000, 10000, 25, 850),
    (10000, 14000, 30, 1350),
    (14000, 0, 35, 2050),
]


class SbPayrollBracket(models.Model):
    _name = 'sb.payroll.bracket'
    _description = 'Employment Income Tax Bracket'
    _order = 'lower'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    lower = fields.Monetary('Over', required=True)
    upper = fields.Monetary('Up to', help='0 = no upper limit')
    rate = fields.Float('Rate (%)', required=True)
    deduction = fields.Monetary('Deduction', help='Amount subtracted from income x rate (the quick-calculation method).')


class SbEmployee(models.Model):
    _name = 'sb.employee'
    _description = 'Employee'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Contact', readonly=True,
                                 help='Created automatically: carries the salary payable balance.')
    job = fields.Char('Position')
    department = fields.Char()
    tin = fields.Char('TIN')
    pension_no = fields.Char('Pension no.')
    start_date = fields.Date('Start date')
    bank_account = fields.Char('Bank account for salary')
    basic_salary = fields.Monetary('Basic salary', tracking=True)
    taxable_allowance = fields.Monetary('Taxable allowances', tracking=True)
    nontaxable_allowance = fields.Monetary('Non-taxable allowances', tracking=True,
                                           help='e.g. transport allowance within the exempt limit.')
    pension = fields.Boolean('In the pension scheme', default=True)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one('res.users', 'User', help='The login of this employee, if any.')
    manager_id = fields.Many2one('sb.employee', 'Manager')
    work_email = fields.Char('Work email')
    work_phone = fields.Char('Work phone')
    mobile = fields.Char('Mobile')
    employee_type = fields.Selection([('employee', 'Employee'), ('contractor', 'Contractor'), ('freelance', 'Freelance'),
                                      ('student', 'Student / intern'), ('worker', 'Worker')], default='employee', string='Type')
    end_date = fields.Date('Left on')

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for r in recs.filtered(lambda r: not r.partner_id):
            r.partner_id = self.env['res.partner'].create({'name': r.name, 'vat': r.tin, 'comment': 'Employee'})
        return recs

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals or 'tin' in vals:
            for r in self.filtered('partner_id'):
                r.partner_id.write({'name': r.name, 'vat': r.tin})
        return res


class SbPayrollRun(models.Model):
    _name = 'sb.payroll.run'
    _description = 'Payroll Run'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    period = fields.Selection('_period_choices', required=True, string='Month',
                              default=lambda s: s._default_period())
    date_from = fields.Date(compute='_compute_dates', store=True)
    date = fields.Date('Posting date', compute='_compute_dates', store=True, readonly=False,
                       help='Last day of the Ethiopian month: the salaries are an expense of that month.')
    pay_date = fields.Date('Payment date')
    pay_account_id = fields.Many2one('account.account', string='Pay from', domain="[('account_type', '=', 'asset_cash')]",
                                     default=lambda s: s.env['sb.voucher']._default_cash_account())
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Posted'), ('paid', 'Paid')], default='draft', tracking=True)
    line_ids = fields.One2many('sb.payroll.line', 'run_id', string='Employees')
    voucher_id = fields.Many2one('sb.voucher', string='Payroll entry', readonly=True)
    payment_id = fields.Many2one('sb.voucher', string='Salary payment', readonly=True)
    total_gross = fields.Monetary(compute='_compute_totals', store=True)
    total_tax = fields.Monetary('PAYE', compute='_compute_totals', store=True)
    total_pension_ee = fields.Monetary('Pension (employee)', compute='_compute_totals', store=True)
    total_pension_er = fields.Monetary('Pension (employer)', compute='_compute_totals', store=True)
    total_net = fields.Monetary('Net pay', compute='_compute_totals', store=True)
    total_cost = fields.Monetary('Cost to company', compute='_compute_totals', store=True)

    @api.model
    def _period_choices(self):
        today = fields.Date.context_today(self)
        current = fiscal_year_of(today)
        out = []
        for fy in range(current - 2, current + 2):
            for no, name, start, end in fiscal_periods(fy):
                out.append(('%d-%02d' % (fy, no), '%s (%s)' % (name, fy_label(fy))))
        return out

    @api.model
    def _default_period(self):
        today = fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        no = next(p[0] for p in fiscal_periods(fy) if p[2] <= today <= p[3])
        return '%d-%02d' % (fy, no)

    def _period(self):
        fy, no = (int(x) for x in self.period.split('-'))
        return fiscal_periods(fy)[no - 1]

    @api.depends('period')
    def _compute_dates(self):
        for r in self:
            if r.period:
                p = r._period()
                r.date_from, r.date = p[2], p[3]

    @api.depends('period')
    def _compute_name(self):
        for r in self:
            r.name = 'Payroll %s' % (r._period()[1] if r.period else '')

    @api.depends('line_ids.gross', 'line_ids.income_tax', 'line_ids.pension_ee', 'line_ids.pension_er', 'line_ids.net')
    def _compute_totals(self):
        for r in self:
            r.total_gross = sum(r.line_ids.mapped('gross'))
            r.total_tax = sum(r.line_ids.mapped('income_tax'))
            r.total_pension_ee = sum(r.line_ids.mapped('pension_ee'))
            r.total_pension_er = sum(r.line_ids.mapped('pension_er'))
            r.total_net = sum(r.line_ids.mapped('net'))
            r.total_cost = r.total_gross + r.total_pension_er

    # ------------------------------------------------------------------ actions
    def action_load(self):
        for r in self:
            have = set(r.line_ids.mapped('employee_id').ids)
            emps = self.env['sb.employee'].search([('company_id', '=', r.company_id.id)])
            r.line_ids = [Command.create({'employee_id': e.id, 'basic': e.basic_salary, 'taxable_allowance': e.taxable_allowance,
                                          'nontaxable_allowance': e.nontaxable_allowance})
                          for e in emps if e.id not in have]
        return True

    def _accounts(self):
        acc = lambda c: self.env['account.account']._sb_find(c, self.company_id)[:1]  # noqa: E731  (old or renumbered number)
        return {'salary': acc('611100'), 'allowance': acc('612100'), 'pension_exp': acc('613100'),
                'payable': acc('300400'), 'paye': acc('300800'), 'pension': acc('300300')}

    def _authorities(self):
        from .locale import locale_of
        P = self.env['res.partner'].sudo()  # the tax office and pension fund are made once, whoever posts the first run
        loc = locale_of(self.company_id or self.env.company)
        tax, fund = loc.get('tax_authority', 'Ministry of Revenue'), loc.get('pension_fund', 'POESSA (Private Organization Employees Social Security Agency)')
        mor = P.search([('name', '=', tax)], limit=1) or P.create({'name': tax, 'supplier_rank': 1, 'is_company': True})
        poessa = P.search([('name', '=', fund)], limit=1) or P.search([('name', 'ilike', fund.split(' (')[0])], limit=1) or P.create(
            {'name': fund, 'supplier_rank': 1, 'is_company': True})
        return mor, poessa

    def action_post(self):
        for r in self:
            if r.state != 'draft':
                continue
            if not r.line_ids:
                raise UserError(self.env._('Load the employees first.'))
            a = r._accounts()
            mor, poessa = r._authorities()
            label = r._period()[1]
            lines = []

            def add(account, debit, credit, name, partner=False):
                if abs(debit) < 0.005 and abs(credit) < 0.005:
                    return
                lines.append(Command.create({'account_id': account.id, 'debit': round(debit, 2), 'credit': round(credit, 2),
                                             'name': name, 'partner_id': partner.id if partner else False}))
            add(a['salary'], sum(r.line_ids.mapped('basic')) + sum(r.line_ids.mapped('taxable_allowance')) + sum(r.line_ids.mapped('overtime')),
                0, self.env._('Salaries %s', label))
            add(a['allowance'], sum(r.line_ids.mapped('nontaxable_allowance')), 0, self.env._('Non-taxable allowances %s', label))
            add(a['pension_exp'], r.total_pension_er, 0, self.env._('Employer pension 11%% %s', label))
            for l in r.line_ids:
                add(a['payable'], 0, l.net, self.env._('Net pay %(m)s - %(e)s', m=label, e=l.employee_id.name), l.employee_id.partner_id)
                if l.other_deduction:
                    add(l.deduction_account_id or a['payable'], 0, l.other_deduction,
                        self.env._('Deduction %(m)s - %(e)s', m=label, e=l.employee_id.name), l.employee_id.partner_id)
            add(a['paye'], 0, r.total_tax, self.env._('PAYE %s', label), mor)
            add(a['pension'], 0, r.total_pension_ee + r.total_pension_er, self.env._('Pension, employee and employer shares %s', label), poessa)
            v = self.env['sb.voucher'].create({'voucher_type': 'journal', 'company_id': r.company_id.id, 'date': r.date,
                                               'reference': r.name, 'memo': r.name, 'line_ids': lines})
            v.action_post()
            r.write({'voucher_id': v.id, 'state': 'posted'})
        return True

    def action_pay(self):
        for r in self:
            if r.state != 'posted':
                raise UserError(self.env._('Post the payroll first.'))
            if not r.pay_date or not r.pay_account_id:
                raise UserError(self.env._('Enter the payment date and the bank to pay from.'))
            open_lines = r.voucher_id.move_id.line_ids.filtered(
                lambda l: l.account_id == r._accounts()['payable'] and not l.reconciled and l.credit > 0)
            v = self.env['sb.voucher'].create({
                'voucher_type': 'payment', 'company_id': r.company_id.id, 'date': r.pay_date, 'payer_type': 'employee',
                'payroll_run_id': r.id, 'reference': r.name, 'memo': self.env._('Salaries %s', r._period()[1]),
                'apply_ids': [Command.create({'line_id': l.id, 'amount_applied': -l.amount_residual}) for l in open_lines],
                'cash_line_ids': [Command.create({'account_id': r.pay_account_id.id, 'method': 'transfer', 'reference': r.name})],
            })
            v.action_post()
            r.write({'payment_id': v.id, 'state': 'paid'})
        return True

    def action_reset(self):
        for r in self:
            why = self.env._('Payroll %s reset to draft', r.name)
            if r.payment_id and r.payment_id.state == 'posted':
                r.payment_id.with_context(sb_void_reason=why).action_void()
            if r.voucher_id and r.voucher_id.state == 'posted':
                r.voucher_id.with_context(sb_void_reason=why).action_void()
            r.write({'state': 'draft', 'voucher_id': False, 'payment_id': False})
        return True

    def action_payslips(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/sonador_books/payslips/%d' % self.id, 'target': 'new'}


class SbPayrollLine(models.Model):
    _name = 'sb.payroll.line'
    _description = 'Payroll Line'
    _order = 'id'

    run_id = fields.Many2one('sb.payroll.run', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='run_id.company_id', store=True)
    currency_id = fields.Many2one(related='run_id.currency_id')
    date = fields.Date(related='run_id.date', store=True)
    employee_id = fields.Many2one('sb.employee', required=True)
    basic = fields.Monetary('Basic')
    taxable_allowance = fields.Monetary('Taxable allow.')
    overtime = fields.Monetary('Overtime')
    nontaxable_allowance = fields.Monetary('Non-taxable allow.')
    gross = fields.Monetary(compute='_compute_pay', store=True)
    taxable = fields.Monetary('Taxable income', compute='_compute_pay', store=True)
    income_tax = fields.Monetary('PAYE', compute='_compute_pay', store=True)
    pension_ee = fields.Monetary('Pension (employee)', compute='_compute_pay', store=True)
    pension_er = fields.Monetary('Pension (employer)', compute='_compute_pay', store=True)
    other_deduction = fields.Monetary('Other deductions', help='Loan repayment, advance recovery ...')
    deduction_account_id = fields.Many2one('account.account', string='Deduction to',
                                           help='e.g. 220300 Advance to staff for an advance being recovered.')
    net = fields.Monetary('Net pay', compute='_compute_pay', store=True)

    @api.model
    def _tax(self, taxable, company):
        brackets = self.env['sb.payroll.bracket'].search([('company_id', '=', company.id)], order='lower')
        rows = [(b.lower, b.upper, b.rate, b.deduction) for b in brackets] or DEFAULT_BRACKETS
        for lower, upper, rate, deduction in rows:
            if taxable > lower and (not upper or taxable <= upper):
                return max(round(taxable * rate / 100.0 - deduction, 2), 0.0)
        return 0.0

    @api.depends('basic', 'taxable_allowance', 'overtime', 'nontaxable_allowance', 'other_deduction', 'employee_id.pension')
    def _compute_pay(self):
        for l in self:
            company = l.company_id or self.env.company
            l.taxable = l.basic + l.taxable_allowance + l.overtime
            l.gross = l.taxable + l.nontaxable_allowance
            l.income_tax = self._tax(l.taxable, company)
            pension = l.employee_id.pension
            l.pension_ee = round(l.basic * company.sb_pension_employee / 100.0, 2) if pension else 0.0
            l.pension_er = round(l.basic * company.sb_pension_employer / 100.0, 2) if pension else 0.0
            l.net = l.gross - l.income_tax - l.pension_ee - l.other_deduction


class ResCompanyPayroll(models.Model):
    _inherit = 'res.company'

    sb_pension_employee = fields.Float('Employee pension (%)', default=7.0)
    sb_pension_employer = fields.Float('Employer pension (%)', default=11.0)


class SbVoucherPayroll(models.Model):
    _inherit = 'sb.voucher'

    payroll_run_id = fields.Many2one('sb.payroll.run', string='Payroll', readonly=True, copy=False)
