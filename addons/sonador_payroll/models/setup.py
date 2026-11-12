"""Payroll set-up: cost-centre profiles (which accounts each part of pay goes to), the company's payroll liabilities,
recurring benefits and deductions, staff loans and salary advances."""
import math

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

LOAN_STATES = [('hr', 'Waiting for HR'), ('gm', 'Waiting for the general manager'), ('open', 'Being recovered'),
               ('hold', 'On hold'), ('done', 'Recovered'), ('refused', 'Refused')]

ACC = "[('company_ids', 'in', company_id)]"


class SbPayrollProfile(models.Model):
    _name = 'sb.payroll.profile'
    _description = 'Payroll cost centre'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    salary_account_id = fields.Many2one('account.account', 'Basic salary', required=True, domain=ACC)
    overtime_account_id = fields.Many2one('account.account', 'Overtime', domain=ACC)
    house_account_id = fields.Many2one('account.account', 'House allowance', domain=ACC)
    position_account_id = fields.Many2one('account.account', 'Position allowance', domain=ACC)
    cash_indemnity_account_id = fields.Many2one('account.account', 'Cash indemnity', domain=ACC)
    allowance_account_id = fields.Many2one('account.account', 'Other taxable allowances', domain=ACC)
    transport_account_id = fields.Many2one('account.account', 'Transport / non-taxable allowances', domain=ACC)
    commission_account_id = fields.Many2one('account.account', 'Commission', domain=ACC)
    bonus_account_id = fields.Many2one('account.account', 'Bonus / incentive', domain=ACC)
    pension_account_id = fields.Many2one('account.account', 'Employer pension', domain=ACC)
    settlement_account_id = fields.Many2one('account.account', 'Severance and leave pay-out', domain=ACC)

    def acc(self, key):
        """An account of the profile, falling back to the basic salary account."""
        self.ensure_one()
        return self['%s_account_id' % key] or self.salary_account_id


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_pay_net_account_id = fields.Many2one('account.account', 'Net salaries payable')
    sb_pay_paye_account_id = fields.Many2one('account.account', 'Employment income tax payable')
    sb_pay_pension_account_id = fields.Many2one('account.account', 'Pension payable (employee + employer)')
    sb_pay_loan_account_id = fields.Many2one('account.account', 'Staff loans and advances',
                                             help='Used when the employee has no staff debtor account of their own.')
    sb_pay_profile_id = fields.Many2one('sb.payroll.profile', 'Default cost centre')
    sb_pay_hours = fields.Float('Hours a month for the hourly rate', default=208.0,
                                help='Ordinary hourly rate = basic salary / these hours (48 hours x 52 weeks / 12 = 208).')
    sb_pay_days = fields.Float('Days a month for the daily rate', default=30.0)
    sb_cash_indemnity_taxable = fields.Boolean('Tax cash indemnity', default=True,
                                               help='Employment income is taxed unless the law exempts it (Income Tax '
                                                    'Proclamation 979/2016): untick only on your tax adviser\'s word.')
    sb_settlement_taxable = fields.Boolean('Tax severance and compensation', default=True,
                                           help='Leave pay-out is always taxed as employment income.')
    sb_pay_allow_full = fields.Boolean('House and position allowances in full when any day is paid',
                                       help='Untick: they follow the paid days like the basic salary.')
    sb_pay_pension_full = fields.Boolean('Pension on the full monthly basic',
                                         help='Once a person has been paid here before, pension is on the full basic salary even '
                                              'in a month with unpaid days; the first and the last month are on the basic paid. '
                                              'Untick: always on the basic paid.')
    # the salary transfer letter to the bank (printed, signed and stamped)
    sb_pay_bank_name = fields.Char('Bank (transfer letter)', help='e.g. Commercial Bank of Ethiopia')
    sb_pay_bank_branch = fields.Char('Branch (transfer letter)')
    sb_pay_bank_city = fields.Char('City (transfer letter)')
    sb_pay_bank_account = fields.Char('Our account number (transfer letter)')
    sb_pay_loan_gm_id = fields.Many2one('res.users', 'Approves loans and advances',
                                        help='The general manager: gives the final approval after HR has set the terms.')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_pay_profile_id = fields.Many2one('sb.payroll.profile', 'Payroll cost centre', groups='hr.group_hr_user')
    sb_staff_account_id = fields.Many2one('account.account', 'Staff debtor account', groups='hr.group_hr_user',
                                          help='Loans and advances to this employee (e.g. 1200-0014 Staff Debtor - ...).')
    sb_benefit_ids = fields.One2many('sb.payroll.benefit', 'employee_id', 'Recurring pay items', groups='hr.group_hr_user')
    sb_loan_ids = fields.One2many('sb.payroll.loan', 'employee_id', 'Loans and advances', groups='hr.group_hr_user')
    sb_pay_exiting = fields.Boolean('Leaving after this payroll', groups='hr.group_hr_user',
                                    help='Pension on the basic paid this month, not the full basic.')
    sb_pay_leave_after = fields.Boolean('Leaves after this payroll', groups='hr.group_hr_user',
                                        help='Set on the payroll line by HR: paid the days worked, pension on that pay, and '
                                             'when the payroll is posted the person leaves on the month\'s last day.')
    sb_pay_joining = fields.Boolean('Joining (first payroll)', default=True, groups='hr.group_hr_user',
                                    help='New employees are joining until their first payroll is posted: pension on the basic '
                                         'paid for the days worked, not the full basic. Cleared by itself when that payroll is posted.')


class SbPayrollBenefit(models.Model):
    _name = 'sb.payroll.benefit'
    _description = 'Recurring pay item'
    _order = 'employee_id, kind, id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    name = fields.Char(required=True)
    kind = fields.Selection([('house', 'House allowance (taxable)'), ('position', 'Position allowance (taxable)'),
                             ('allowance', 'Other taxable allowance'), ('transport', 'Transport / non-taxable allowance'),
                             ('deduction', 'Deduction from pay'), ('employer', 'Employer-paid benefit (cost, not in pay)')],
                            required=True, default='allowance')
    amount = fields.Float(required=True)
    date_from = fields.Date('From')
    date_to = fields.Date('Until')
    account_id = fields.Many2one('account.account', 'Account',
                                 help='Deductions: where the money goes (e.g. union dues, cost sharing, court order). '
                                      'Employer-paid: the expense (e.g. medical insurance).')
    note = fields.Char()


class SbPayrollLoan(models.Model):
    """A staff loan or salary advance: requested, HR sets the terms (monthly deduction, first month) and checks them
    against the law, the general manager approves, then payroll deducts it every month and closes it when paid off.
    No interest. Labour Proclamation 1156/2019 art. 59: what is deducted from the wage in a month (all loans and
    advances together) may not be more than one third of it."""
    _name = 'sb.payroll.loan'
    _description = 'Staff loan or salary advance'
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    kind = fields.Selection([('loan', 'Loan'), ('advance', 'Salary advance')], default='loan', required=True)
    date = fields.Date('Requested on', required=True, default=fields.Date.context_today)
    amount = fields.Float(required=True)
    months = fields.Integer('Months to repay')
    installment = fields.Float('Monthly deduction')
    start = fields.Date('First deduction in the month of')
    note = fields.Char('Reason')
    recovered = fields.Float('Deducted by payroll', compute='_compute_recovered')
    deposited = fields.Float('Paid in (receipts)', compute='_compute_recovered',
                             help='What the person paid into the company\'s bank or cash: receipts (CRV) on their staff debtor account.')
    remaining = fields.Float(compute='_compute_recovered')
    state = fields.Selection(LOAN_STATES, default='open', required=True, index=True)
    requested_by = fields.Many2one('res.users', default=lambda s: s.env.user)
    hr_by = fields.Many2one('res.users', 'Terms set by', readonly=True)
    hr_at = fields.Datetime('Terms set on', readonly=True)
    gm_by = fields.Many2one('res.users', 'Approved by', readonly=True)
    gm_at = fields.Datetime('Approved on', readonly=True)
    refuse_reason = fields.Char('Refused because', readonly=True)
    payout_move_id = fields.Many2one('account.move', 'Paid out by (CPV)', index=True,
                                     help='The payment (CPV) that gave the money: found by itself on the staff debtor account.')

    def _compute_recovered(self):
        Item = self.env['sb.payroll.line.loan'].sudo()
        by_payroll = lambda x: sum(Item.search([('loan_id', '=', x.id), ('line_id.run_id.state', 'in', ('posted', 'paid'))]).mapped('amount'))  # noqa: E731
        for l in self:
            l.recovered, l.deposited, l.remaining = 0.0, 0.0, l.amount
        # money paid in by receipt (CRV) clears the person's oldest loans first
        for e in self.mapped('employee_id'):
            loans = e.sudo().sb_loan_ids.filtered(lambda x: x.state in ('open', 'hold', 'done')).sorted(lambda x: (x.start or x.date, x.id))
            paid = loans[:1]._sb_receipts() if loans else 0.0
            for x in loans:
                rec = by_payroll(x)
                dep = min(max(x.amount - rec, 0.0), paid)
                paid -= dep
                if x in self:
                    x.recovered, x.deposited, x.remaining = rec, dep, max(x.amount - rec - dep, 0.0)
        for l in self.filtered(lambda x: x.state not in ('open', 'hold', 'done')):
            l.recovered = by_payroll(l) if l.id else 0.0

    def _sb_find_payout(self):
        """The payment (CPV) that paid the loan out: a posted entry putting the loan amount on the person's staff debtor
        account, nearest to the request date."""
        for l in self.filtered(lambda x: not x.payout_move_id and x.state in ('open', 'hold', 'done')):
            e = l.employee_id.sudo()
            acc = e.sb_staff_account_id or e.company_id.sb_pay_loan_account_id
            if not acc:
                continue
            dom = [('account_id', '=', acc.id), ('parent_state', '=', 'posted'), ('debit', '>', 0)]
            if not e.sb_staff_account_id:
                dom.append(('partner_id', '=', e.work_contact_id.id))
            lines = self.env['account.move.line'].sudo().search(dom)
            taken = self.sudo().search([('payout_move_id', '!=', False)]).payout_move_id
            paid = {}
            for m in lines.filtered(lambda m: m.move_id not in taken):
                paid[m.move_id] = paid.get(m.move_id, 0.0) + m.debit
            ref = l.date or fields.Date.today()
            hits = sorted((mv for mv, amt in paid.items() if abs(amt - l.amount) <= 0.5), key=lambda mv: abs((mv.date - ref).days))
            if hits:
                l.sudo().payout_move_id = hits[0]

    def _sb_payout_label(self):
        self.ensure_one()
        mv = self.payout_move_id
        if not mv:
            return ''
        v = self.env['sb.voucher'].sudo().search([('move_id', '=', mv.id)], limit=1)
        return (v.name if v else mv.name) or ''

    def _sb_receipts(self):
        """Receipts (CRV) crediting the person's staff debtor account since their first loan."""
        self.ensure_one()
        e = self.employee_id.sudo()
        acc = e.sb_staff_account_id or e.company_id.sb_pay_loan_account_id
        if not acc:
            return 0.0
        dom = [('account_id', '=', acc.id), ('parent_state', '=', 'posted'), ('credit', '>', 0), ('date', '>=', self.start or self.date)]
        if not e.sb_staff_account_id:
            dom.append(('partner_id', '=', e.work_contact_id.id))
        lines = self.env['account.move.line'].sudo().search(dom)
        crv = self.env['sb.voucher'].sudo().search([('move_id', 'in', lines.move_id.ids), ('voucher_type', '=', 'receipt'),
                                                    ('state', '=', 'posted')]).move_id
        return sum(lines.filtered(lambda m: m.move_id in crv).mapped('credit'))

    @api.constrains('installment', 'amount', 'state')
    def _check(self):
        for l in self:
            if l.amount <= 0:
                raise ValidationError(self.env._('Enter the amount.'))
            if l.state in ('gm', 'open', 'hold') and l.installment <= 0:
                raise ValidationError(self.env._('Set the monthly deduction.'))

    # ------------------------------------------------------------------ the rules
    def _sb_month_pay(self):
        """The regular monthly wage the one-third rule is on: basic salary and the fixed allowances (People)."""
        self.ensure_one()
        v = self.employee_id.sudo().current_version_id
        return sum(v[f] or 0.0 for f in ('wage', 'sb_house_allowance', 'sb_position_allowance', 'sb_taxable_allowance',
                                          'sb_cash_indemnity', 'sb_nontaxable_allowance') if f in v._fields)

    def _sb_others(self):
        """What the person's other loans and advances already take each month."""
        self.ensure_one()
        return sum(self.employee_id.sudo().sb_loan_ids.filtered(
            lambda x: x.id != self.id and x.state in ('gm', 'open')).mapped('installment'))

    def _sb_rules(self):
        """The rule figures shown to HR and the general manager."""
        self.ensure_one()
        pay, others = self._sb_month_pay(), self._sb_others()
        return {'pay': pay, 'third': round(pay / 3.0, 2), 'others': others, 'room': round(pay / 3.0 - others, 2)}

    def _sb_check_terms(self):
        """The terms HR sets, checked before the general manager sees them (and again when they approve)."""
        for l in self:
            e = l.employee_id.sudo()
            if l.installment <= 0 or not l.start:
                raise UserError(self.env._('%s: set the monthly deduction and the first month before approving.', e.name))
            if l.installment > l.amount + 0.005:
                raise UserError(self.env._('%s: the monthly deduction is more than the amount.', e.name))
            r = l._sb_rules()
            if not r['pay']:
                raise UserError(self.env._('%s has no salary in People: the one-third rule cannot be checked.', e.name))
            total = round(l.installment + r['others'], 2)
            if total > r['third'] + 0.005:
                raise UserError(self.env._(
                    '%(e)s: %(t)s a month would be deducted for loans and advances, more than one third of the monthly wage '
                    '(%(p)s / 3 = %(m)s). The law does not allow it (Labour Proclamation 1156/2019, art. 59): make the '
                    'monthly deduction smaller and the months longer.',
                    e=e.name, t='{:,.2f}'.format(total), p='{:,.2f}'.format(r['pay']), m='{:,.2f}'.format(r['third'])))
            months = math.ceil(round(l.amount / l.installment, 6))
            v = e.current_version_id
            end = v.contract_date_end if 'contract_date_end' in v._fields else False
            last = l.start + relativedelta(months=months - 1)
            if end and last > end:
                raise UserError(self.env._(
                    '%(e)s: the last deduction (%(l)s) would be after the contract ends (%(c)s). Make the monthly deduction '
                    'larger, or renew the contract first.', e=e.name, l=last, c=end))
            if l.months != months:
                l.sudo().months = months

    # ------------------------------------------------------------------ who decides
    def _sb_is_hr(self, user=None):
        user = user or self.env.user
        return user.has_group('sonador_books.group_sb_hr')

    @api.model
    def _sb_gm_users(self, company=None):
        company = company or self.env.company
        if company.sb_pay_loan_gm_id:
            return company.sb_pay_loan_gm_id
        g = self.env.ref('sonador_requests.group_request_gm', raise_if_not_found=False)
        return g.sudo().user_ids.filtered(lambda u: not u.share) if g else self.env['res.users']

    def _sb_is_gm(self, user=None):
        user = user or self.env.user
        return user in self._sb_gm_users(self[:1].company_id or self.env.company)

    # ------------------------------------------------------------------ the steps
    def action_hr_approve(self):
        for l in self:
            if l.state != 'hr':
                raise UserError(self.env._('This request is no longer waiting for HR.'))
            if not l._sb_is_hr():
                raise AccessError(self.env._('HR sets the terms of loans and advances.'))
            l._sb_check_terms()
            l.sudo().write({'state': 'gm', 'hr_by': self.env.uid, 'hr_at': fields.Datetime.now()})
        return True

    def action_gm_approve(self):
        for l in self:
            if l.state != 'gm':
                raise UserError(self.env._('This request is no longer waiting for the general manager.'))
            if not l._sb_is_gm():
                raise AccessError(self.env._('The general manager approves loans and advances.'))
            l._sb_check_terms()
            l.sudo().write({'state': 'open', 'gm_by': self.env.uid, 'gm_at': fields.Datetime.now()})
        return True

    def action_refuse(self, reason):
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._('Say why: the person who asked will see it.'))
        for l in self:
            if not ((l.state == 'hr' and l._sb_is_hr()) or (l.state == 'gm' and l._sb_is_gm())):
                raise AccessError(self.env._('This request is not waiting for you.'))
            l.sudo().write({'state': 'refused', 'refuse_reason': reason})
        return True

    @api.model
    def _sb_close_paid(self, employees=None):
        """Loans paid off by posted payrolls close by themselves."""
        dom = [('state', '=', 'open')] + ([('employee_id', 'in', employees.ids)] if employees else [])
        for l in self.sudo().search(dom):
            if l.remaining <= 0.005:
                l.state = 'done'
