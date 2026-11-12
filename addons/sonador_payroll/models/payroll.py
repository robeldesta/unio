"""Payroll runs on People: each line is filled from the employee's current job and pay, recurring items, approved leave
(Leave Requests), overtime hours, loans and advances, commission and bonus, then taxed (Income Tax Proclamation 979/2016 as
amended by 1395/2025, the bracket table) and posted to the employee's cost-centre accounts."""
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian, to_ethiopian
from odoo.addons.sonador_books.models.locale import locale_of, overtime_rules

REF_MONTHS = ['MES', 'TIK', 'HID', 'TAH', 'TER', 'YEK', 'MEG', 'MIA', 'GIN', 'SEN', 'HAM', 'NEH', 'PAG']  # as Odoo 18
OT_RATES = {'ot_day': 1.5, 'ot_night': 1.75, 'ot_rest': 2.0, 'ot_holiday': 2.5}  # art. 68(1)


class SbPayrollRun(models.Model):
    _inherit = 'sb.payroll.run'

    label = fields.Char('Name', help='e.g. "Nehase 2018 salaries, factory". Empty: Payroll <month>.')
    kind = fields.Selection([('regular', 'Monthly salaries'), ('final', 'Final settlement'), ('special', 'Other (bonus, arrears ...)')],
                            default='regular', required=True)
    sb_migrated = fields.Boolean('From an earlier system', help='Kept for payslips and history; not posted here.')
    note = fields.Char()
    department_ids = fields.Many2many('hr.department', string='Departments', help='Empty: everyone.')

    @api.depends('period', 'label')
    def _compute_name(self):
        for r in self:
            r.name = r.label or 'Payroll %s' % (r._period()[1] if r.period else '')

    def _employees(self):
        """People paid in the month: current staff, plus those who left during it; of the run's departments (none: all).
        A monthly payroll leaves out whoever is on another monthly payroll of the same month (e.g. factory paid first)."""
        self.ensure_one()
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        dom = [('company_id', '=', self.company_id.id), '|', ('active', '=', True), '&',
               ('departure_date', '>=', self.date_from), ('departure_date', '<=', self.date)]
        if self.department_ids:
            dom.append(('department_id', 'in', self.department_ids.ids))
        people = E.search(dom)
        if self.kind == 'regular':
            people -= self._paid_elsewhere()
        return people

    def _paid_elsewhere(self):
        """People on another monthly payroll of the same month."""
        self.ensure_one()
        return self.search([('company_id', '=', self.company_id.id), ('period', '=', self.period), ('kind', '=', 'regular'),
                            ('id', '!=', self.id)]).mapped('line_ids.hr_employee_id')

    def _reference(self):
        """Odoo 18 numbering of the salary letter: Ethiopian month, year, SAL (Tikimt 2019: TIK19SAL)."""
        self.ensure_one()
        y, m, _d = to_ethiopian(self.date_from)
        return '%s%02dSAL' % (REF_MONTHS[m - 1], y % 100)

    def action_load(self):
        """Add everyone paid this month (People) and fill their lines."""
        for r in self:
            if r.state != 'draft':
                raise UserError(self.env._('Only a draft payroll can be changed.'))
            have = set(r.line_ids.mapped('employee_id').ids)
            for e in r._employees():
                e._sb_sync_payroll()
                if e.sb_payroll_id and e.sb_payroll_id.id not in have and (e.current_version_id.wage or e.sb_benefit_ids):
                    r.line_ids = [Command.create({'employee_id': e.sb_payroll_id.id})]
            r.line_ids.sudo()._sb_fill()  # reads contract fields only HR managers may read; the run itself is the user's
        return True

    def action_refill(self):
        for r in self:
            r.line_ids.sudo()._sb_fill()
        return True

    # ------------------------------------------------------------------ posting by cost centre
    def action_post(self):
        company_ready = self.filtered(lambda r: r.company_id.sb_pay_net_account_id)
        res = super(SbPayrollRun, self - company_ready).action_post() if self - company_ready else True
        for r in company_ready:
            if r.sb_migrated:
                raise UserError(self.env._('%s came from an earlier system: it is already in the books.', r.name))
            if r.state != 'draft':
                continue
            if not r.line_ids:
                raise UserError(self.env._('Load the employees first.'))
            c = r.company_id
            pname = locale_of(c)['pension_name']
            mor, poessa = r._authorities()
            label = r.name
            agg = {}

            def add(account, amount, name, partner=False):
                if not account or abs(amount) < 0.005:
                    return
                key = (account.id, partner.id if partner else False, name)
                agg[key] = agg.get(key, 0.0) + amount
            for l in r.line_ids:
                e = l.hr_employee_id
                prof = e.sb_pay_profile_id or c.sb_pay_profile_id
                if not prof:
                    raise UserError(self.env._('%s has no payroll cost centre (People > Payroll > Settings).', l.employee_id.name))
                emp_partner = l.employee_id.partner_id
                add(prof.salary_account_id, l.basic, self.env._('Basic salary %s', label))
                add(prof.acc('house'), l.house, self.env._('House allowance %s', label))
                add(prof.acc('position'), l.position, self.env._('Position allowance %s', label))
                add(prof.acc('cash_indemnity'), l.cash_indemnity, self.env._('Cash indemnity %s', label))
                add(prof.acc('allowance'), l.taxable_allowance, self.env._('Allowances %s', label))
                add(prof.acc('transport'), l.nontaxable_allowance, self.env._('Transport and non-taxable allowances %s', label))
                add(prof.acc('overtime'), l.overtime, self.env._('Overtime %s', label))
                add(prof.acc('commission'), l.commission, self.env._('Commission %s', label))
                add(prof.acc('bonus'), l.bonus, self.env._('Bonus %s', label))
                add(prof.acc('settlement'), l.leave_payout + l.severance + l.compensation + l.notice_pay,
                    self.env._('Final settlement %s', label))
                add(prof.acc('pension'), l.pension_er, self.env._('Employer %(p)s %(r)s%% %(l)s', p=pname, r='%g' % c.sb_pension_employer, l=label))
                add(c.sb_pay_net_account_id, -l.net, self.env._('Net pay %(m)s - %(e)s', m=label, e=l.employee_id.name), emp_partner)
                add(c.sb_pay_paye_account_id, -l.income_tax, self.env._('Income tax %s', label), mor)
                add(c.sb_pay_pension_account_id, -(l.pension_ee + l.pension_er), self.env._('%(p)s %(a)s%% + %(b)s%% %(l)s', p=pname, a='%g' % c.sb_pension_employee, b='%g' % c.sb_pension_employer, l=label), poessa)
                loan_acc = e.sb_staff_account_id or c.sb_pay_loan_account_id
                if l.loan_deduction:
                    if not loan_acc:
                        raise UserError(self.env._('Set the staff loans account (Payroll settings) or %s\'s staff debtor account.', e.name))
                    add(loan_acc, -l.loan_deduction, self.env._('Loan / advance recovery %(m)s - %(e)s', m=label, e=e.name), emp_partner)
                if l.other_deduction:
                    add(l.deduction_account_id or loan_acc or c.sb_pay_net_account_id, -l.other_deduction,
                        self.env._('Deduction %(m)s - %(e)s', m=label, e=e.name), emp_partner)
                for b in l.benefit_item_ids:
                    add(b.account_id or c.sb_pay_net_account_id, -b.amount, '%s %s - %s' % (b.name, label, e.name), emp_partner)
            lines = [Command.create({'account_id': a, 'partner_id': p, 'name': n, 'debit': round(max(v, 0.0), 2),
                                     'credit': round(max(-v, 0.0), 2)}) for (a, p, n), v in agg.items() if abs(v) >= 0.005]
            v = self.env['sb.voucher'].create({'voucher_type': 'journal', 'company_id': c.id, 'date': r.date,
                                               'reference': r.name, 'memo': r.name, 'line_ids': lines})
            v.action_post()
            r.write({'voucher_id': v.id, 'state': 'posted'})
            # loans paid off by this payroll close by themselves
            self.env['sb.payroll.loan']._sb_close_paid(r.line_ids.hr_employee_id)
            # their first payroll is posted: no longer joining
            r.line_ids.hr_employee_id.filtered('sb_pay_joining').sudo().write({'sb_pay_joining': False})
            # leaving after this payroll: they leave on the month's last day (archived, off attendance and payroll)
            for e in r.line_ids.hr_employee_id.filtered(lambda x: x.sb_pay_leave_after and x.active):
                last = ('sb_exit_last_day' in e._fields and e.sudo().sb_exit_last_day) or r.date
                e.sudo().write({'sb_pay_leave_after': False, 'sb_pay_exiting': False})
                self.env['sb.people'].sudo().depart(e.id, {'date': fields.Date.to_string(min(last, r.date)),
                                                           'note': 'Left after the %s payroll' % r.name})
                if 'sb.exit' in self.env:  # the clearance of that leaving is complete
                    self.env['sb.exit'].sudo().search([('employee_id', '=', e.id), ('state', '=', 'done')]).write({'state': 'left'})
        return res

    def action_pay(self):
        """Net pay account not kept per open item (e.g. Peachtree's Accrued Salary): pay it as a plain debit."""
        direct = self.filtered(lambda r: r.company_id.sb_pay_net_account_id and not r.company_id.sb_pay_net_account_id.reconcile)
        res = super(SbPayrollRun, self - direct).action_pay() if self - direct else True
        for r in direct:
            if r.state != 'posted':
                raise UserError(self.env._('Post the payroll first.'))
            if not r.pay_date or not r.pay_account_id:
                raise UserError(self.env._('Enter the payment date and the bank to pay from.'))
            v = self.env['sb.voucher'].create({
                'voucher_type': 'payment', 'company_id': r.company_id.id, 'date': r.pay_date, 'payer_type': 'employee',
                'payroll_run_id': r.id, 'reference': r.name, 'memo': self.env._('Salaries %s', r.name),
                'line_ids': [Command.create({'account_id': r.company_id.sb_pay_net_account_id.id, 'amount': round(r.total_net, 2),
                                             'name': self.env._('Net salaries %s', r.name)})],
                'cash_line_ids': [Command.create({'account_id': r.pay_account_id.id, 'method': 'transfer', 'reference': r.name})],
            })
            v._onchange_sync_single_deposit()
            v.action_post()
            r.write({'payment_id': v.id, 'state': 'paid'})
        return res

    def _accounts(self):
        res = super()._accounts()
        if self.company_id.sb_pay_net_account_id:
            res['payable'] = self.company_id.sb_pay_net_account_id
        return res


class SbPayrollLineLoan(models.Model):
    _name = 'sb.payroll.line.loan'
    _description = 'Loan recovered on a payslip'

    line_id = fields.Many2one('sb.payroll.line', required=True, ondelete='cascade', index=True)
    loan_id = fields.Many2one('sb.payroll.loan', required=True, ondelete='cascade', index=True)
    amount = fields.Float()


class SbPayrollLineBenefit(models.Model):
    _name = 'sb.payroll.line.benefit'
    _description = 'Deduction item on a payslip'

    line_id = fields.Many2one('sb.payroll.line', required=True, ondelete='cascade', index=True)
    name = fields.Char()
    amount = fields.Float()
    account_id = fields.Many2one('account.account')


class SbPayrollLine(models.Model):
    _inherit = 'sb.payroll.line'

    hr_employee_id = fields.Many2one('hr.employee', compute='_compute_hr_employee', store=True, string='Employee (People)')
    full_basic = fields.Monetary('Monthly basic')
    no_pension = fields.Boolean('No pension (joining month)', help='Joined this month and paid less than the full month: no '
                                'pension this month (Robel, 11 Nov 2026).')
    pay_days = fields.Float('Paid days', default=30.0)
    half_days = fields.Float('Half-pay days', help='Sick leave at 50% (art. 86).')
    house = fields.Monetary('House allowance')
    position = fields.Monetary('Position allowance')
    cash_indemnity = fields.Monetary('Cash indemnity')
    commission = fields.Monetary()
    bonus = fields.Monetary()
    ot_day = fields.Float('OT hours, working day', help='Working day, 6 am to 10 pm. Rates per country (Ethiopia 1.5x, Uganda 1.5x).')
    ot_night = fields.Float('OT hours, night', help='Night, 10 pm to 6 am (Ethiopia 1.75x, Uganda 1.5x).')
    ot_rest = fields.Float('OT hours, rest day', help='Weekly rest day (2x).')
    ot_holiday = fields.Float('OT hours, public holiday', help='Public holiday (Ethiopia 2.5x, Uganda 2x).')
    loan_deduction = fields.Monetary('Loans / advances')
    benefit_deduction = fields.Monetary('Other deductions (recurring)')
    employer_benefit = fields.Monetary('Employer-paid benefits')
    leave_payout = fields.Monetary('Leave pay-out', help='Annual leave not taken, paid on leaving (art. 77(4)).')
    severance = fields.Monetary(help='Art. 39-40.')
    compensation = fields.Monetary(help='Art. 41.')
    notice_pay = fields.Monetary('Pay in lieu of notice', help='Art. 44.')
    loan_item_ids = fields.One2many('sb.payroll.line.loan', 'line_id')
    benefit_item_ids = fields.One2many('sb.payroll.line.benefit', 'line_id')
    note = fields.Char()
    pension_full = fields.Boolean('Pension on the full basic', help='Set when the line is filled (company setting).')
    ot_hand = fields.Boolean('Overtime hours entered by HR', help='Typed or imported: kept when the payroll is recalculated.')
    # payslips of an earlier system keep the figures it paid (e.g. tax on the brackets before Proclamation 1395/2025)
    hist_tax = fields.Float()
    hist_pension_ee = fields.Float()
    hist_pension_er = fields.Float()
    hist_net = fields.Float()
    hist = fields.Boolean()

    @api.depends('employee_id')
    def _compute_hr_employee(self):
        found = {e.sb_payroll_id.id: e for e in self.env['hr.employee'].sudo().with_context(active_test=False).search(
            [('sb_payroll_id', 'in', self.mapped('employee_id').ids)])}
        for l in self:
            l.hr_employee_id = found.get(l.employee_id.id, False)

    @api.depends('basic', 'taxable_allowance', 'overtime', 'nontaxable_allowance', 'other_deduction', 'employee_id.pension',
                 'house', 'position', 'cash_indemnity', 'commission', 'bonus', 'loan_deduction', 'benefit_deduction', 'leave_payout',
                 'severance', 'compensation', 'notice_pay', 'hist', 'hist_tax', 'hist_net', 'pension_full', 'full_basic', 'no_pension')
    def _compute_pay(self):
        for l in self:
            if l.hist:
                l.taxable = l.basic + l.house + l.position + l.cash_indemnity + l.taxable_allowance + l.overtime + l.commission \
                    + l.bonus + l.leave_payout
                l.gross = l.taxable + l.nontaxable_allowance
                l.income_tax, l.pension_ee, l.pension_er, l.net = l.hist_tax, l.hist_pension_ee, l.hist_pension_er, l.hist_net
                continue
            company = l.company_id or self.env.company
            settlement = l.severance + l.compensation + l.notice_pay
            taxed_settlement = settlement if company.sb_settlement_taxable else 0.0
            taxed_cash = l.cash_indemnity if company.sb_cash_indemnity_taxable else 0.0
            l.taxable = l.basic + l.house + l.position + taxed_cash + l.taxable_allowance + l.overtime + l.commission + l.bonus \
                + l.leave_payout + taxed_settlement
            l.gross = l.taxable + l.nontaxable_allowance + (l.cash_indemnity - taxed_cash) + (settlement - taxed_settlement)
            l.income_tax = self._tax(l.taxable, company)
            if taxed_settlement and l.full_basic > 0 and (company.country_id.code or 'ET') == 'ET':
                # Ethiopia (MoR, Income Tax Reg. 410): a payment on leaving (severance, compensation, notice pay, a payment by
                # agreement) is not lumped into the month: each whole month's salary of it is taxed on its own at the
                # schedule, and the part of a month left over at the band it falls in
                months = int(taxed_settlement // l.full_basic)
                rest = round(taxed_settlement - months * l.full_basic, 2)
                l.income_tax = round(self._tax(l.taxable - taxed_settlement, company) + months * self._tax(l.full_basic, company)
                                     + self._tax(rest, company), 2)
            pension = l.employee_id.pension
            # Ethiopia (POESSA): on basic salary; Uganda / Kenya (NSSF): on gross wages, terminal benefits excluded
            base = l.full_basic if l.pension_full and l.basic else l.basic
            if locale_of(company).get('pension_base') == 'gross':
                base = l.basic + l.house + l.position + l.cash_indemnity + l.taxable_allowance + l.nontaxable_allowance + l.overtime \
                    + l.commission + l.bonus
            pension = pension and not l.no_pension
            l.pension_ee = round(base * company.sb_pension_employee / 100.0, 2) if pension else 0.0
            l.pension_er = round(base * company.sb_pension_employer / 100.0, 2) if pension else 0.0
            l.net = round(l.gross - l.income_tax - l.pension_ee - l.other_deduction - l.loan_deduction - l.benefit_deduction, 2)

    def _sb_tax_split(self):
        """(tax on the month's pay, tax on the payment on leaving, months it counts as): how the income tax was worked out
        when a payment on leaving is on the line (Ethiopia: each month's salary of it taxed on its own)."""
        self.ensure_one()
        company = self.company_id or self.env.company
        settlement = (self.severance + self.compensation + self.notice_pay) if company.sb_settlement_taxable else 0.0
        if not settlement or self.full_basic <= 0 or (company.country_id.code or 'ET') != 'ET':
            return self.income_tax, 0.0, 0.0
        on_pay = self._tax(self.taxable - settlement, company)
        return on_pay, round(self.income_tax - on_pay, 2), round(settlement / self.full_basic, 2)

    def _period_leave(self, e, df, dt):
        """Unpaid and half-pay days in the month from approved leave."""
        L = self.env['sb.leave'].sudo().search([('employee_id', '=', e.id), ('state', '=', 'approved'),
                                               ('date_from', '<=', dt), ('date_to', '>=', df)])
        unpaid = half = 0.0
        for lv in L:
            inside = (min(lv.date_to, dt) - max(lv.date_from, df)).days + 1
            span = (lv.date_to - lv.date_from).days + 1
            if lv.kind == 'unpaid':
                unpaid += inside
            elif lv.kind == 'sick' and (lv.days_half_pay or lv.days_unpaid):
                share = inside / float(span)
                unpaid += lv.days_unpaid * share
                half += lv.days_half_pay * share
        return round(unpaid, 2), round(half, 2)

    def _sb_pay_days(self, e, df, dt, worked, days_month):
        """Paid days and half-pay days of the month: the days employed less approved unpaid leave."""
        unpaid, half = self._period_leave(e, df, dt)
        return max(min(worked, days_month) - unpaid, 0.0), half

    def _sb_lst(self, company, run, version, items, day):
        """Local Service Tax instalment (Uganda): the yearly amount for the monthly income, in four equal parts, July - October."""
        loc = locale_of(company)
        if not loc.get('lst') or run.kind != 'regular' or day.month not in loc.get('lst_months', ()):
            return None
        income = (version.wage or 0.0) + (version.sb_house_allowance or 0.0) + (version.sb_position_allowance or 0.0) + \
            (version.sb_taxable_allowance or 0.0) + items['house'] + items['position']
        yearly = next((amt for upto, amt in loc['lst'] if not upto or income <= upto), 0)
        if not yearly:
            return None
        n = len(loc['lst_months'])
        Acc = self.env['account.account'].sudo()
        acc = Acc._sb_find('300810', company)[:1] or Acc.create(
            {'code': Acc._sb_new_code('300810', company), 'sb_legacy_code': '300810', 'name': 'Local Service Tax payable', 'account_type': 'liability_current', 'company_ids': [Command.set(company.ids)]})
        return (self.env._('Local Service Tax (%(i)s of %(n)s)', i=loc['lst_months'].index(day.month) + 1, n=n), round(yearly / n, 0), acc.id)

    def _sb_fill(self):
        """Fill the line from People (pay of the month), Leave Requests and loans. Overtime hours, commission, bonus and
        one-off deductions typed on the line are kept."""
        for l in self:
            r, e = l.run_id, l.hr_employee_id
            if not e or r.sb_migrated:
                continue
            c = r.company_id
            dt = r.date or fields.Date.today()
            df = r.date_from or dt.replace(day=1)
            v = e._get_version(dt) if hasattr(e, '_get_version') else e.current_version_id
            v = v or e.current_version_id
            days_month = c.sb_pay_days or 30.0
            start = v.contract_date_start
            worked = days_month
            if start and start > df:
                worked -= (start - df).days
            if e.departure_date and df <= e.departure_date < dt:
                worked -= (dt - e.departure_date).days
            pay_days, half = l._sb_pay_days(e, df, dt, worked, days_month)
            full = v.wage or 0.0
            daily = full / days_month
            basic = round(full * pay_days / days_month - daily / 2.0 * half, 2)
            items = {'house': 0.0, 'position': 0.0, 'allowance': 0.0, 'transport': 0.0, 'employer': 0.0}
            deductions = []
            for b in e.sb_benefit_ids.filtered(lambda b: (not b.date_from or b.date_from <= dt) and (not b.date_to or b.date_to >= df)):
                if b.kind == 'deduction':
                    deductions.append((b.name, b.amount, b.account_id.id))
                else:
                    items[b.kind] += b.amount
            lst = self._sb_lst(c, r, v, items, dt)
            if lst:
                deductions.append(lst)
            # house, position and cash indemnity are on the job (People); recurring items add to them. The job's "other
            # taxable allowances" is the reference for the rest: what the recurring items do not explain is "other"
            house = (v.sb_house_allowance or 0.0) + items['house']
            position = (v.sb_position_allowance or 0.0) + items['position']
            other_taxable = max((v.sb_taxable_allowance or 0.0) - items['allowance'], 0.0) + items['allowance']
            transport = max(v.sb_nontaxable_allowance or 0.0, items['transport'])
            ratio = pay_days / days_month if days_month else 1.0
            fixed = (1.0 if pay_days else 0.0) if c.sb_pay_allow_full else ratio  # house and position
            hourly = full / (c.sb_pay_hours or 208.0)
            rates = overtime_rules(l.run_id.company_id)['rates']
            overtime = round(sum(l[k] * rate for k, rate in rates.items()) * hourly, 2) if any(l[k] for k in rates) else l.overtime
            l.loan_item_ids.unlink()
            loans = []
            # loans and advances together take at most one third of the month's wage (Labour Proclamation 1156/2019,
            # art. 59): in a short month the rest waits, and the loan simply runs a month longer
            room = (basic + house * fixed + position * fixed + ((v.sb_cash_indemnity or 0.0) + other_taxable + transport) * ratio) / 3.0
            for loan in e.sb_loan_ids.filtered(lambda x: x.state == 'open' and x.start and x.start <= dt).sorted('start'):
                take = max(min(loan.installment, loan.remaining, room), 0.0)
                room -= take
                if take > 0:
                    loans.append(Command.create({'loan_id': loan.id, 'amount': round(take, 2)}))
            l.benefit_item_ids.unlink()
            l.write({
                'full_basic': full, 'pay_days': pay_days, 'half_days': half, 'basic': basic,
                'house': round(house * fixed, 2), 'position': round(position * fixed, 2),
                'cash_indemnity': round((v.sb_cash_indemnity or 0.0) * ratio, 2),
                'taxable_allowance': round(other_taxable * ratio, 2), 'nontaxable_allowance': round(transport * ratio, 2),
                'overtime': overtime, 'employer_benefit': round(items['employer'], 2),
                # joining (first payroll) or leaving (flagged, or leaving date on or before the month's end): pension on the
                # basic paid for the days worked, not the full basic
                'no_pension': bool((e.sb_pay_joining or (start and start >= df)) and pay_days < days_month),
                'pension_full': bool(c.sb_pay_pension_full and pay_days and not e.sb_pay_exiting and not e.sb_pay_joining
                                     and not e.sb_pay_leave_after
                                     and not (e.departure_date and e.departure_date <= dt) and (
                    # not their first payroll: started before this month (paid before, here or in the earlier system),
                    # or an earlier payroll here
                    bool(start and start < df) or self.search_count(
                        [('hr_employee_id', '=', e.id), ('run_id.date_from', '<', df), ('run_id', '!=', r.id)], limit=1))),
                'loan_item_ids': loans,
                'benefit_item_ids': [Command.create({'name': n, 'amount': a, 'account_id': acc}) for n, a, acc in deductions],
            })
            l.write({'loan_deduction': sum(l.loan_item_ids.mapped('amount')),
                     'benefit_deduction': sum(l.benefit_item_ids.mapped('amount'))})
        return True
