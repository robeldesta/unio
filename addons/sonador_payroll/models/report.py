"""Payroll Journal: the payroll entries and everything posted to the payroll liability accounts (net pay, income tax,
pension) in the period - salary, tax and pension payments included."""
from odoo import fields, models


class SbReportPayroll(models.AbstractModel):
    _inherit = 'sb.report'

    def _report_payroll_journal(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        c = self.env.company
        runs = self.env['sb.payroll.run'].search([('company_id', '=', c.id), ('voucher_id', '!=', False)])
        moves = set(runs.mapped('voucher_id.move_id').ids)
        accs = (c.sb_pay_net_account_id | c.sb_pay_paye_account_id | c.sb_pay_pension_account_id).ids
        if accs:
            aml = self.env['account.move.line'].search([('company_id', '=', c.id), ('account_id', 'in', accs), ('parent_state', '=', 'posted'),
                                                        ('date', '>=', df), ('date', '<=', dt)])
            moves |= set(aml.mapped('move_id').ids)
        doc = self._report_journal(dict(opts, move_ids=list(moves), _title='Payroll Journal'))
        doc['key'] = 'payroll_journal'
        return doc

    def _report_lst_return(self, opts):
        """Local Service Tax (Uganda) deducted on payslips in the period, per employee."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        items = self.env['sb.payroll.line.benefit'].search([('line_id.run_id.company_id', '=', self.env.company.id),
                                                             ('line_id.run_id.date', '>=', df), ('line_id.run_id.date', '<=', dt),
                                                             ('line_id.run_id.state', 'in', ('posted', 'paid')), ('name', 'like', 'Local Service Tax')])
        per = {}
        for it in items:
            e = it.line_id.employee_id
            row = per.setdefault(e.id, {'name': e.name, 'tin': getattr(it.line_id.hr_employee_id, 'sb_tin', '') or '', 'gross': 0.0, 'amount': 0.0, 'n': 0})
            row['gross'] = max(row['gross'], it.line_id.gross)
            row['amount'] += it.amount
            row['n'] += 1
        rows, total = [], 0.0
        for eid, r in sorted(per.items(), key=lambda x: x[1]['name']):
            if not self._search_ok(opts, r['name']):
                continue
            total += r['amount']
            rows.append({'id': 'e%d' % eid, 'parent': None, 'level': 0, 'kind': 'line', 'label': r['name'],
                         'values': {'name': r['name'], 'tin': r['tin'], 'gross': r['gross'], 'n': r['n'], 'amount': r['amount']}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'name': 'Total', 'amount': total}})
        cols = [{'key': 'name', 'label': 'Employee', 'type': 'text'}, {'key': 'tin', 'label': 'TIN', 'type': 'text'},
                {'key': 'gross', 'label': 'Monthly gross', 'type': 'monetary'}, {'key': 'n', 'label': 'Instalments', 'type': 'number'},
                {'key': 'amount', 'label': 'Local Service Tax', 'type': 'monetary'}]
        return self._doc('lst_return', opts, cols, rows, self._period_name(df, dt), 'Local Service Tax Schedule')

    def _report_payroll_cheques(self, opts):
        """Salary payments: each payroll run's payment, from which bank, with its reference."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        runs = self.env['sb.payroll.run'].search([('company_id', '=', self.env.company.id), ('pay_date', '>=', df), ('pay_date', '<=', dt)], order='pay_date')
        rows, grand = [], 0.0
        for r in runs:
            if not self._search_ok(opts, r.name):
                continue
            net = sum(r.line_ids.mapped('net'))
            grand += net
            rows.append({'id': 'r%d' % r.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': r.name,
                         'values': {'date': fields.Date.to_string(r.pay_date), 'run': r.name, 'bank': r.pay_account_id.display_name or '',
                                    'people': len(r.line_ids), 'state': dict(r._fields['state']._description_selection(self.env)).get(r.state), 'amount': net},
                         'open': {'model': 'sb.payroll.run', 'id': r.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Paid on', 'type': 'date'}, {'key': 'run', 'label': 'Payroll', 'type': 'text'},
                {'key': 'bank', 'label': 'Paid from', 'type': 'text'}, {'key': 'people', 'label': 'Employees', 'type': 'number_int'},
                {'key': 'state', 'label': 'Status', 'type': 'text'}, {'key': 'amount', 'label': 'Net pay', 'type': 'monetary'}]
        return self._doc('payroll_cheques', opts, cols, rows, self._period_name(df, dt), 'Payroll Payment Register')
