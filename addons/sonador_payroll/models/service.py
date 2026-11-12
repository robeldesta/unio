"""Payroll screens (People > Payroll): runs, the run grid, an employee's pay history and items, final settlement,
settings. HR runs payroll; every employee sees their own payslips."""
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

from odoo.addons.sonador_books.models.payroll import DEFAULT_BRACKETS

from .settlement import REASONS
from odoo.addons.sonador_books.models.locale import locale_of, overtime_rules

LINE_FIELDS = ['full_basic', 'pay_days', 'half_days', 'basic', 'house', 'position', 'cash_indemnity', 'taxable_allowance', 'nontaxable_allowance',
               'ot_day', 'ot_night', 'ot_rest', 'ot_holiday', 'overtime', 'commission', 'bonus', 'leave_payout', 'severance',
               'compensation', 'notice_pay', 'taxable', 'gross', 'income_tax', 'pension_ee', 'pension_er', 'loan_deduction',
               'benefit_deduction', 'other_deduction', 'net', 'employer_benefit']
EDITABLE = {'ot_day', 'ot_night', 'ot_rest', 'ot_holiday', 'commission', 'bonus', 'other_deduction', 'note', 'pay_days'}


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbReportPayslip(models.AbstractModel):
    _inherit = 'sb.report'

    def payslip_doc(self, run, lines=None):
        """Payslips with every pay item (People payroll)."""
        label = run.name
        rows = []
        for i, l in enumerate(lines or run.line_ids):
            e = l.employee_id
            he = l.hr_employee_id
            gid = 'p%d' % l.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'section', 'page_break': i > 0,
                         'label': '%s  -  %s' % (e.name, e.job or ''),
                         'values': {'item': '%s  -  %s%s' % (e.name, e.job or '', ('  (%s)' % he.sb_code) if he and he.sb_code else ''),
                                    'info': 'TIN %s  |  Pension no. %s  |  %s paid days' % (e.tin or '-', e.pension_no or '-', l.pay_days)}})
            ot_hours = ', '.join('%s h x%s' % (l[k], r) for k, r in overtime_rules(run.company_id)['rates'].items() if l[k])
            earn = [('Basic salary', l.basic), ('House allowance', l.house), ('Position allowance', l.position),
                    ('Cash indemnity', l.cash_indemnity),
                    ('Other taxable allowances', l.taxable_allowance), ('Overtime' + (' (%s)' % ot_hours if ot_hours else ''), l.overtime),
                    ('Commission', l.commission), ('Bonus', l.bonus), ('Annual leave pay-out', l.leave_payout),
                    ('Severance', l.severance), ('Compensation', l.compensation),
                    ('Pay in lieu of notice', l.notice_pay), ('Transport / non-taxable allowances', l.nontaxable_allowance)]
            for item, amt in earn:
                if amt:
                    rows.append({'id': '%s_%s' % (gid, item), 'parent': gid, 'level': 1, 'kind': 'line', 'label': item,
                                 'values': {'item': item, 'earn': amt}})
            rows.append({'id': gid + '_g', 'parent': gid, 'level': 1, 'kind': 'total', 'label': 'Gross pay',
                         'values': {'item': 'Gross pay (taxable %s)' % '{:,.2f}'.format(l.taxable), 'earn': l.gross}})
            cmp = run.company_id
            pname = locale_of(cmp)['pension_name']
            ded = [('Employment income tax (PAYE)', l.income_tax), ('%s %g%%' % (pname, cmp.sb_pension_employee), l.pension_ee)]
            ded += [('%s %s' % ('Loan' if it.loan_id.kind == 'loan' else 'Salary advance', '(balance %s)' % '{:,.2f}'.format(
                max(it.loan_id.remaining - (it.amount if run.state == 'draft' else 0), 0))), it.amount) for it in l.loan_item_ids]
            ded += [(b.name, b.amount) for b in l.benefit_item_ids]
            ded += [('Other deductions', l.other_deduction)]
            for item, amt in ded:
                if amt:
                    rows.append({'id': '%s_%s' % (gid, item), 'parent': gid, 'level': 1, 'kind': 'line', 'label': item,
                                 'values': {'item': item, 'ded': amt}})
            rows.append({'id': gid + '_n', 'parent': gid, 'level': 1, 'kind': 'total', 'label': 'Net pay',
                         'values': {'item': 'Net pay', 'net': l.net}})
            rows.append({'id': gid + '_er', 'parent': gid, 'level': 1, 'kind': 'note', 'label': 'Employer %s' % pname,
                         'values': {'item': 'Paid by the company: %s %g%% %s%s' % (pname, cmp.sb_pension_employer, '{:,.2f}'.format(l.pension_er),
                                    ('; benefits %s' % '{:,.2f}'.format(l.employer_benefit)) if l.employer_benefit else '')}})
        cols = [{'key': 'item', 'label': 'Payslip', 'type': 'text'}, {'key': 'info', 'label': '', 'type': 'text'},
                {'key': 'earn', 'label': 'Earnings', 'type': 'monetary'}, {'key': 'ded', 'label': 'Deductions', 'type': 'monetary'},
                {'key': 'net', 'label': 'Net', 'type': 'monetary'}]
        doc = self._doc('payslips', {'date_from': fields.Date.to_string(run.date_from), 'date_to': fields.Date.to_string(run.date)},
                        cols, rows, '%s  |  %s  |  paid %s' % (label, '%s - %s' % (format_ethiopian(run.date_from), format_ethiopian(run.date)),
                                                               run.pay_date.strftime('%d %B %Y') if run.pay_date else 'not yet'),
                        'Payslip' if lines and len(lines) == 1 else 'Payslips %s' % label)
        doc['signatures'] = False
        return doc


class SbPayrollService(models.AbstractModel):
    _name = 'sb.payroll.service'
    _description = 'Payroll screens'

    def _is_hr(self):
        return self.env['sb.people']._is_hr()

    def _require_hr(self):
        if not self._is_hr():
            raise AccessError(self.env._('Payroll is run by HR.'))

    def _me(self):
        return self.env['sb.people']._me()

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        Run = self.env['sb.payroll.run']
        c = self.env.company
        accounts = self.env['account.account'].sudo().search([('company_ids', 'in', c.id), ('active', '=', True)], order='code')
        return {'is_hr': self._is_hr(), 'me': self._me().id or False, 'periods': [{'id': k, 'name': v} for k, v in Run._period_choices()],
                'default_period': Run._default_period(), 'reasons': [{'id': k, 'name': v} for k, v in REASONS],
                'profiles': [{'id': p.id, 'name': p.name} for p in self.env['sb.payroll.profile'].sudo().search([('company_id', '=', c.id)])],
                'accounts': [{'id': a.id, 'name': '%s %s' % (a.code, a.name)} for a in accounts] if self._is_hr() else [],
                'cash': [{'id': a.id, 'name': '%s %s' % (a.code, a.name)} for a in accounts if a.account_type == 'asset_cash'],
                'today': _s(fields.Date.context_today(self)), 'ot': overtime_rules(c),
                'letter_ready': bool(c.sb_pay_bank_name and c.sb_pay_bank_account),
                'is_gm': self.env['sb.payroll.loan']._sb_is_gm(), 'loans_waiting': self._loans_waiting(),
                'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search(
                    [('company_id', 'in', (c.id, False))], order='name')]}

    # ------------------------------------------------------------------ runs
    def _run_dict(self, r):
        return {'id': r.id, 'name': r.name, 'period': r.period, 'state': r.state, 'kind': r.kind, 'migrated': r.sb_migrated,
                'from_ec': format_ethiopian(r.date_from), 'to_ec': format_ethiopian(r.date), 'date': _s(r.date),
                'people': len(r.line_ids), 'gross': r.total_gross, 'tax': r.total_tax, 'pension': r.total_pension_ee + r.total_pension_er,
                'net': r.total_net, 'cost': r.total_cost, 'pay_date': _s(r.pay_date), 'pay_account_id': r.pay_account_id.id,
                'voucher': r.voucher_id.name or '', 'payment': r.payment_id.name or '', 'note': r.note or '',
                'departments': r.department_ids.mapped('name'), 'department_ids': r.department_ids.ids, 'label': r.label or '',
                'reference': r._reference() if r.date_from else ''}

    @api.model
    def runs(self, year=None):
        self._require_hr()
        R = self.env['sb.payroll.run'].sudo()
        return [self._run_dict(r) for r in R.search([('company_id', '=', self.env.company.id)], limit=200)]

    @api.model
    def run_create(self, period, label='', kind='regular', department_ids=None):
        self._require_hr()
        r = self.env['sb.payroll.run'].sudo().create({'period': period, 'label': label or False, 'kind': kind,
                                                      'department_ids': [Command.set([int(x) for x in department_ids or []])]})
        if kind == 'regular':
            r.action_load()
        return r.id

    @api.model
    def run_save(self, run_id, vals):
        """A draft payroll changed: month, name, kind, departments. People outside it now leave, the missing join,
        a new month fills everyone again."""
        self._require_hr()
        r = self.env['sb.payroll.run'].sudo().browse(int(run_id))
        if r.state != 'draft' or r.sb_migrated or r.kind == 'final':
            raise UserError(self.env._('Only a draft payroll can be changed.'))
        month = vals.get('period') and vals['period'] != r.period
        r.write({'period': vals.get('period') or r.period, 'label': vals.get('label') or False,
                 'kind': vals.get('kind') if vals.get('kind') in ('regular', 'special') else r.kind,
                 'department_ids': [Command.set([int(x) for x in vals.get('department_ids') or []])]})
        if r.kind == 'regular':
            keep = r._employees()
        else:
            keep = r.line_ids.mapped('hr_employee_id').filtered(lambda e: not r.department_ids or e.department_id in r.department_ids)
        gone = r.line_ids.filtered(lambda l: l.hr_employee_id and l.hr_employee_id not in keep)
        removed = len(gone)
        gone.unlink()
        if r.kind == 'regular':
            r.action_load()
        elif month:
            r.action_refill()
        return dict(self.run(r.id), removed=removed)

    @api.model
    def line_remove(self, line_id):
        self._require_hr()
        l = self.env['sb.payroll.line'].sudo().browse(int(line_id))
        r = l.run_id
        if r.state != 'draft' or r.sb_migrated:
            raise UserError(self.env._('Reset the payroll to draft to change it.'))
        l.unlink()
        return self.run(r.id)

    def _line_dict(self, l):
        d = {f: l[f] for f in LINE_FIELDS}
        d.update(id=l.id, note=l.note or '', ot_hand=l.ot_hand, employee=self.env['sb.people']._card(l.hr_employee_id) if l.hr_employee_id else
                 {'id': False, 'name': l.employee_id.name, 'job': l.employee_id.job or '', 'department': l.employee_id.department or '',
                  'avatar': '/web/static/img/placeholder.png'},
                 status='joining' if l.hr_employee_id.sb_pay_joining else ('leaving' if l.hr_employee_id.sb_pay_leave_after or (
                     l.hr_employee_id.departure_date and l.hr_employee_id.departure_date <= l.run_id.date) else ''),
                 loans=[{'name': it.loan_id.kind, 'amount': it.amount} for it in l.loan_item_ids],
                 benefits=[{'name': b.name, 'amount': b.amount} for b in l.benefit_item_ids],
                 warn=[w for w in (
                     not l.employee_id.tin and 'no TIN',
                     l.hr_employee_id and not l.hr_employee_id.primary_bank_account_id and 'no bank account',
                     l.net < 0 and 'net pay below zero',
                     l.hr_employee_id and not (l.hr_employee_id.sb_pay_profile_id or l.run_id.company_id.sb_pay_profile_id) and 'no cost centre') if w])
        return d

    @api.model
    def run(self, run_id):
        self._require_hr()
        r = self.env['sb.payroll.run'].sudo().browse(int(run_id))
        lines = r.line_ids.sorted(lambda l: (l.hr_employee_id.department_id.name or l.employee_id.department or '', l.employee_id.name))
        tot = {f: round(sum(lines.mapped(f)), 2) for f in LINE_FIELDS if f not in ('pay_days', 'half_days', 'full_basic')}
        others = r._paid_elsewhere() if r.kind == 'regular' else []
        return {'run': self._run_dict(r), 'lines': [self._line_dict(l) for l in lines], 'totals': tot,
                'elsewhere': len(others), 'banked': not any(l.hr_employee_id and not l.hr_employee_id.primary_bank_account_id for l in lines)}

    @api.model
    def ot_import(self, run_id, data, filename):
        self._require_hr()
        r = self.env['sb.payroll.run'].sudo().browse(int(run_id))
        res = self.env['sb.payroll.export'].sudo().ot_import(r, data, filename)
        return dict(self.run(r.id), imported=res)

    @api.model
    def line_leaving(self, line_id, leaving):
        """Leaving after this payroll: pension on the basic paid; when the payroll is posted they leave on its last day."""
        self._require_hr()
        l = self.env['sb.payroll.line'].sudo().browse(int(line_id))
        if l.run_id.state != 'draft':
            raise UserError(self.env._('Reset the payroll to draft to change it.'))
        if l.hr_employee_id:
            l.hr_employee_id.sb_pay_leave_after = bool(leaving)
            l.run_id.line_ids.filtered(lambda x: x == l)._sb_fill()
        return self.run(l.run_id.id)

    @api.model
    def line_save(self, line_id, vals):
        self._require_hr()
        l = self.env['sb.payroll.line'].sudo().browse(int(line_id))
        if l.run_id.state != 'draft':
            raise UserError(self.env._('Reset the payroll to draft to change it.'))
        data = {}
        for k, v in vals.items():
            if k not in EDITABLE:
                continue
            data[k] = v if k == 'note' else float(v or 0)
        if {'ot_day', 'ot_night', 'ot_rest', 'ot_holiday'} & set(data):
            data['ot_hand'] = True  # typed hours stay when the payroll is recalculated
        l.write(data)
        if data.get('ot_hand'):
            l._sb_overtime()
        if 'pay_days' in data:
            days = l.run_id.company_id.sb_pay_days or 30.0
            l.basic = round(l.full_basic * l.pay_days / days, 2)
        return self.run(l.run_id.id)

    @api.model
    def run_action(self, run_id, action, vals=None):
        self._require_hr()
        r = self.env['sb.payroll.run'].sudo().browse(int(run_id))
        vals = vals or {}
        if action == 'load':
            r.action_load()
        elif action == 'refill':
            r.action_refill()
        elif action == 'post':
            r.action_post()
        elif action == 'pay':
            r.write({'pay_date': vals.get('pay_date') or fields.Date.context_today(self),
                     'pay_account_id': int(vals['pay_account_id']) if vals.get('pay_account_id') else r.pay_account_id.id})
            r.action_pay()
        elif action == 'ot_reset':
            self.env['sb.payroll.export'].sudo().ot_reset(r)
        elif action == 'reset':
            if r.sb_migrated:
                raise UserError(self.env._('Payrolls from the earlier system stay as they are.'))
            r.action_reset()
        elif action == 'delete':
            if r.state != 'draft' or r.sb_migrated:
                raise UserError(self.env._('Only a draft payroll can be deleted.'))
            r.unlink()
            return False
        return self.run(r.id)

    # ------------------------------------------------------------------ employee pay
    @api.model
    def employee(self, employee_id=None):
        e = self.env['hr.employee'].sudo().browse(int(employee_id)) if employee_id else self._me()
        if not e:
            return {'employee': False}
        if not self._is_hr() and e != self._me():
            raise AccessError(self.env._('You can see your own pay only.'))
        lines = self.env['sb.payroll.line'].sudo().search([('employee_id', '=', e.sb_payroll_id.id or -1)]).sorted(
            lambda l: l.run_id.date, reverse=True)
        if not self._is_hr():
            lines = lines.filtered(lambda l: l.run_id.state in ('posted', 'paid'))
        fy = e.company_id.compute_fiscalyear_dates(fields.Date.context_today(self))
        ytd = lines.filtered(lambda l: fy['date_from'] <= l.run_id.date <= fy['date_to'])
        out = {'employee': self.env['sb.people']._card(e), 'is_hr': self._is_hr(),
               'ytd': {f: round(sum(ytd.mapped(f)), 2) for f in ('gross', 'taxable', 'income_tax', 'pension_ee', 'pension_er', 'net')},
               'fy': '%s - %s' % (format_ethiopian(fy['date_from']), format_ethiopian(fy['date_to'])),
               'slips': [{'id': l.id, 'run': l.run_id.name, 'run_id': l.run_id.id, 'period': format_ethiopian(l.run_id.date), 'state': l.run_id.state,
                          'gross': l.gross, 'tax': l.income_tax, 'pension': l.pension_ee, 'net': l.net, 'migrated': l.run_id.sb_migrated}
                         for l in lines[:36]]}
        if self._is_hr():
            out.update(profile_id=e.sb_pay_profile_id.id, staff_account_id=e.sb_staff_account_id.id,
                       wage=e.current_version_id.wage, taxable=e.current_version_id.sb_taxable_allowance,
                       house=e.current_version_id.sb_house_allowance, position=e.current_version_id.sb_position_allowance,
                       cash=e.current_version_id.sb_cash_indemnity,
                       nontaxable=e.current_version_id.sb_nontaxable_allowance,
                       benefits=[{'id': b.id, 'name': b.name, 'kind': b.kind, 'amount': b.amount, 'date_from': _s(b.date_from),
                                  'date_to': _s(b.date_to), 'account_id': b.account_id.id, 'note': b.note or ''} for b in e.sb_benefit_ids],
                       loans=[{'id': x.id, 'kind': x.kind, 'date': _s(x.date), 'amount': x.amount, 'installment': x.installment,
                               'start': _s(x.start), 'recovered': x.recovered, 'remaining': x.remaining, 'state': x.state,
                               'note': x.note or ''} for x in e.sb_loan_ids if x.state in ('open', 'hold', 'done')])
        return out

    @api.model
    def employee_save(self, employee_id, vals):
        self._require_hr()
        e = self.env['hr.employee'].sudo().browse(int(employee_id))
        e.write({'sb_pay_profile_id': int(vals['profile_id']) if vals.get('profile_id') else False,
                 'sb_staff_account_id': int(vals['staff_account_id']) if vals.get('staff_account_id') else False})
        B = self.env['sb.payroll.benefit'].sudo()
        keep = B
        for b in vals.get('benefits') or []:
            if not b.get('name') or not b.get('amount'):
                continue
            bv = {'name': b['name'], 'kind': b.get('kind') or 'allowance', 'amount': float(b['amount']),
                  'date_from': b.get('date_from') or False, 'date_to': b.get('date_to') or False,
                  'account_id': int(b['account_id']) if b.get('account_id') else False, 'note': b.get('note') or False}
            rec = e.sb_benefit_ids.filtered(lambda x: x.id == b.get('id'))
            rec = rec.write(bv) and rec if rec else B.create(dict(bv, employee_id=e.id))
            keep |= rec
        (e.sb_benefit_ids - keep).unlink()
        for x in vals.get("loans") or []:
            if not x.get('amount') or not x.get('installment'):
                continue
            lv = {'kind': x.get('kind') or 'loan', 'date': x.get('date') or fields.Date.context_today(self), 'amount': float(x['amount']),
                  'installment': float(x['installment']), 'start': x.get('start') or fields.Date.context_today(self),
                  'state': x.get('state') or 'open', 'note': x.get('note') or False}
            # new loans go through the request and its approvals (Loans); here only the ones already approved are kept up
            rec = e.sb_loan_ids.filtered(lambda y: y.id == x.get('id') and y.state in ('open', 'hold', 'done'))
            if rec:
                lv['state'] = lv['state'] if lv['state'] in ('open', 'hold', 'done') else rec.state
                rec.write(lv)
        return self.employee(e.id)

    # ------------------------------------------------------------------ loans and advances
    def _loans_waiting(self):
        Lo = self.env['sb.payroll.loan']
        n = 0
        if self._is_hr():
            n += Lo.sudo().search_count([('state', '=', 'hr'), ('company_id', '=', self.env.company.id)])
        if Lo._sb_is_gm():
            n += Lo.sudo().search_count([('state', '=', 'gm'), ('company_id', '=', self.env.company.id)])
        return n

    def _loan_row(self, x):
        e = x.employee_id
        r = x._sb_rules()
        months = x.months or (int(-(-x.amount // x.installment)) if x.installment else 0)
        return {'id': x.id, 'employee_id': e.id, 'employee': e.name, 'department': e.department_id.name or '',
                'kind': x.kind, 'date': _s(x.date), 'date_ec': format_ethiopian(x.date) if x.date else '',
                'amount': x.amount, 'months': months, 'installment': x.installment, 'start': _s(x.start),
                'start_ec': format_ethiopian(x.start) if x.start else '', 'note': x.note or '', 'state': x.state,
                'state_label': dict(x._fields['state']._description_selection(self.env)).get(x.state),
                'recovered': x.recovered, 'deposited': x.deposited, 'remaining': x.remaining,
                'cpv': x._sb_payout_label(), 'cpv_date': format_ethiopian(x.payout_move_id.date) if x.payout_move_id else '', 'requested_by': x.requested_by.name or '',
                'hr_by': x.hr_by.name or '', 'gm_by': x.gm_by.name or '', 'refuse_reason': x.refuse_reason or '',
                'pay': r['pay'], 'third': r['third'], 'others': r['others'], 'room': r['room'],
                'can_hr': x.state == 'hr' and self._is_hr(), 'can_gm': x.state == 'gm' and x._sb_is_gm()}

    @api.model
    def loans(self):
        """HR and the general manager see everyone's loans; everyone else their own."""
        Lo = self.env['sb.payroll.loan'].sudo()
        Lo._sb_close_paid()  # paid off by payroll or by receipts
        Lo.search([('payout_move_id', '=', False), ('state', 'in', ('open', 'hold', 'done')),
                   ('company_id', '=', self.env.company.id)])._sb_find_payout()
        me = self._me()
        if self._is_hr() or Lo._sb_is_gm():
            recs = Lo.search([('company_id', '=', self.env.company.id)], order='date desc, id desc')
        else:
            recs = Lo.search([('employee_id', '=', me.id)]) if me else Lo
        order = {'hr': 0, 'gm': 1, 'open': 2, 'hold': 3, 'done': 4, 'refused': 5}
        rows = sorted((self._loan_row(x) for x in recs), key=lambda r: order.get(r['state'], 9))
        gm = Lo._sb_gm_users()
        return {'rows': rows, 'gm': ', '.join(gm.mapped('name')) or False, 'me': me.id or False}

    @api.model
    def loan_voucher(self, loan_id):
        x = self.env['sb.payroll.loan'].sudo().browse(int(loan_id))
        if not (self._is_hr() or x._sb_is_gm() or x.employee_id == self._me()) or not x.payout_move_id:
            return False
        v = self.env['sb.voucher'].sudo().search([('move_id', '=', x.payout_move_id.id)], limit=1)
        model, rid = ('sb.voucher', v.id) if v else ('account.move', x.payout_move_id.id)
        return {'type': 'ir.actions.act_window', 'res_model': model, 'res_id': rid, 'views': [[False, 'form']], 'target': 'current'}

    @api.model
    def loan_request(self, vals):
        """A request: the employee for themselves, or HR for anyone. HR sets the terms next."""
        me = self._me()
        emp_id = int(vals.get('employee_id') or 0) if self._is_hr() else me.id
        if not emp_id:
            raise UserError(self.env._('Choose the employee.'))
        amount = float(vals.get('amount') or 0)
        months = max(int(vals.get('months') or 1), 1)
        if amount <= 0:
            raise UserError(self.env._('Enter the amount.'))
        if not (vals.get('note') or '').strip():
            raise UserError(self.env._('Say what it is for.'))
        self.env['sb.payroll.loan'].sudo().create({
            'employee_id': emp_id, 'kind': vals.get('kind') or 'loan', 'amount': amount, 'months': months,
            'installment': round(amount / months, 2), 'note': vals['note'].strip(), 'state': 'hr',
            'requested_by': self.env.uid, 'start': vals.get('start') or False})
        return self.loans()

    @api.model
    def loan_terms(self, loan_id, vals):
        """HR sets the terms while the request waits for them."""
        self._require_hr()
        x = self.env['sb.payroll.loan'].sudo().browse(int(loan_id))
        if x.state != 'hr':
            raise UserError(self.env._('The terms can be changed only while the request waits for HR.'))
        w = {}
        for k in ('amount', 'installment'):
            if vals.get(k) not in (None, ''):
                w[k] = float(vals[k])
        if vals.get('start'):
            w['start'] = vals['start']
        if vals.get('kind'):
            w['kind'] = vals['kind']
        x.write(w)
        if x.installment:
            x.months = int(-(-x.amount // x.installment))
        return self.loans()

    @api.model
    def loan_decide(self, loan_id, approve, note=''):
        x = self.env['sb.payroll.loan'].browse(int(loan_id))
        if not approve:
            x.action_refuse(note)
        elif x.sudo().state == 'hr':
            x.action_hr_approve()
        else:
            x.action_gm_approve()
        return self.loans()

    # ------------------------------------------------------------------ final settlement
    @api.model
    def settlement(self, employee_id, last_day, code, notice_given=True, pension_eligible=False):
        self._require_hr()
        e = self.env['hr.employee'].sudo().with_context(active_test=False).browse(int(employee_id))
        return self.env['sb.settlement'].compute(e, fields.Date.to_date(last_day), code, bool(notice_given), bool(pension_eligible))

    @api.model
    def settlement_make(self, employee_id, last_day, code, notice_given=True, pension_eligible=False):
        """Record the leaving in People and put the settlement on a final-settlement payroll of that month (draft)."""
        self._require_hr()
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        e = E.browse(int(employee_id))
        day = fields.Date.to_date(last_day)
        res = self.env['sb.settlement'].compute(e, day, code, bool(notice_given), bool(pension_eligible))
        if e.active:
            reason = self.env['hr.departure.reason'].sudo().search([('sb_code', '=', code)], limit=1)
            self.env['sb.people'].depart(e.id, {'date': last_day, 'reason_id': reason.id, 'note': res.get('notice')})
        e._sb_sync_payroll()
        Run = self.env['sb.payroll.run'].sudo()
        fy = fiscal_year_of(day)
        no = next(p[0] for p in fiscal_periods(fy) if p[2] <= day <= p[3])
        period = '%d-%02d' % (fy, no)
        run = Run.create({'period': period, 'kind': 'final', 'label': 'Final settlement %s (%s)' % (e.name, format_ethiopian(day))})
        amounts = defaultdict(float)
        for l in res['lines']:
            amounts[l['key']] += l['amount']
        run.line_ids = [Command.create({'employee_id': e.sb_payroll_id.id, 'pay_days': 0, 'basic': 0, **amounts})]
        return {'run_id': run.id, 'settlement': res}

    # ------------------------------------------------------------------ settings
    @api.model
    def settings(self):
        self._require_hr()
        c = self.env.company
        P = self.env['sb.payroll.profile'].sudo()
        keys = ['salary', 'overtime', 'house', 'position', 'cash_indemnity', 'allowance', 'transport', 'commission', 'bonus', 'pension', 'settlement']
        return {'company': {'net': c.sb_pay_net_account_id.id, 'paye': c.sb_pay_paye_account_id.id, 'pension': c.sb_pay_pension_account_id.id,
                            'loan': c.sb_pay_loan_account_id.id, 'profile': c.sb_pay_profile_id.id, 'hours': c.sb_pay_hours,
                            'days': c.sb_pay_days, 'settlement_taxable': c.sb_settlement_taxable,
                            'cash_taxable': c.sb_cash_indemnity_taxable, 'allow_full': c.sb_pay_allow_full,
                            'pension_full': c.sb_pay_pension_full,
                            'pension_ee': c.sb_pension_employee, 'pension_er': c.sb_pension_employer,
                            'bank_name': c.sb_pay_bank_name or '', 'bank_branch': c.sb_pay_bank_branch or '',
                            'bank_city': c.sb_pay_bank_city or '', 'bank_account': c.sb_pay_bank_account or '',
                            'loan_gm': c.sb_pay_loan_gm_id.id or ''},
                'users': [{'id': u.id, 'name': u.name} for u in self.env['res.users'].sudo().search(
                    [('share', '=', False), ('company_ids', 'in', c.id)], order='name')],
                'profiles': [dict({'id': p.id, 'name': p.name}, **{k: p['%s_account_id' % k].id for k in keys}) for p in P.search([('company_id', '=', c.id)])],
                'brackets': [{'lower': b.lower, 'upper': b.upper, 'rate': b.rate, 'deduction': b.deduction} for b in
                             self.env['sb.payroll.bracket'].sudo().search([('company_id', '=', c.id)], order='lower')] or
                            [{'lower': lo, 'upper': up, 'rate': rate, 'deduction': d} for lo, up, rate, d in DEFAULT_BRACKETS],
                'keys': keys}

    @api.model
    def settings_save(self, vals):
        self._require_hr()
        c = self.env.company.sudo()
        cv = vals.get('company') or {}
        ids = lambda k: int(cv[k]) if cv.get(k) else False  # noqa: E731
        c.write({'sb_pay_net_account_id': ids('net'), 'sb_pay_paye_account_id': ids('paye'), 'sb_pay_pension_account_id': ids('pension'),
                 'sb_pay_loan_account_id': ids('loan'), 'sb_pay_profile_id': ids('profile'), 'sb_pay_hours': float(cv.get('hours') or 208),
                 'sb_pay_days': float(cv.get('days') or 30), 'sb_settlement_taxable': bool(cv.get('settlement_taxable')),
                 'sb_cash_indemnity_taxable': bool(cv.get('cash_taxable')), 'sb_pay_allow_full': bool(cv.get('allow_full')),
                 'sb_pay_pension_full': bool(cv.get('pension_full')),
                 'sb_pay_bank_name': cv.get('bank_name') or False, 'sb_pay_bank_branch': cv.get('bank_branch') or False,
                 'sb_pay_bank_city': cv.get('bank_city') or False, 'sb_pay_bank_account': cv.get('bank_account') or False,
                 'sb_pay_loan_gm_id': ids('loan_gm')})
        P = self.env['sb.payroll.profile'].sudo()
        for p in vals.get('profiles') or []:
            pv = {'name': p['name']}
            for k in (vals.get('keys') or []):
                pv['%s_account_id' % k] = int(p[k]) if p.get(k) else False
            if not pv.get('salary_account_id'):
                continue
            rec = P.browse(int(p['id'])) if p.get('id') else P
            if rec:
                rec.write(pv)
            else:
                P.create(dict(pv, company_id=c.id))
        return self.settings()
