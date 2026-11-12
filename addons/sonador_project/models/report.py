"""Job reports on the report engine: list, profitability, ledger, costs by type, phases, cost codes, unbilled
expenses, estimates against actuals, time tickets. Figures come from the Projects service (the same actuals the screens show)."""
from collections import defaultdict

from odoo import fields, models

from .job import COST_KINDS, JOB_KINDS, JOB_STATES


class SbReportJobs(models.AbstractModel):
    _inherit = 'sb.report'

    def _jobs(self, opts):
        J = self.env['sb.job'].search([('company_id', '=', self.env.company.id)], order='code')
        return J.filtered(lambda j: self._search_ok(opts, j.code, j.name, j.partner_id.name))

    def _job_fin(self, jobs):
        P = self.env['sb.projects'].sudo()
        acts = P._actuals(jobs)
        return {j.id: (P._fin(j, acts[j.id]), acts[j.id]) for j in jobs}

    def _report_job_list(self, opts):
        rows = []
        for j in self._jobs(opts):
            rows.append({'id': 'j%d' % j.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': j.name,
                         'values': {'code': j.code, 'name': j.name, 'customer': j.partner_id.name or '', 'kind': dict(JOB_KINDS).get(j.kind),
                                    'state': dict(JOB_STATES).get(j.state), 'manager': j.manager_id.name or '', 'start': fields.Date.to_string(j.date_start) or '',
                                    'end': fields.Date.to_string(j.date_end) or '', 'contract': j.contract_value},
                         'open': {'model': 'sb.job', 'id': j.id}})
        cols = [{'key': 'code', 'label': 'Job', 'type': 'text'}, {'key': 'name', 'label': 'Name', 'type': 'text'}, {'key': 'customer', 'label': 'Customer', 'type': 'text'},
                {'key': 'kind', 'label': 'Type', 'type': 'text'}, {'key': 'state', 'label': 'Status', 'type': 'text'}, {'key': 'manager', 'label': 'Manager', 'type': 'text'},
                {'key': 'start', 'label': 'Start', 'type': 'date'}, {'key': 'end', 'label': 'Planned finish', 'type': 'date'},
                {'key': 'contract', 'label': 'Contract value', 'type': 'monetary'}]
        return self._doc('job_list', opts, cols, rows, 'All jobs', 'Job List')

    def _report_job_master(self, opts):
        doc = self._report_job_list(opts)
        doc.update(key='job_master', title='Job Master File List')
        return doc

    def _report_job_profit(self, opts):
        jobs = self._jobs(opts)
        fin = self._job_fin(jobs)
        rows, grand = [], defaultdict(float)
        for j in jobs:
            f, _a = fin[j.id]
            v = {'code': j.code, 'name': j.name, 'contract': f['contract'], 'budget': f['budget'], 'cost': f['cost'], 'revenue': f['revenue'],
                 'margin': f['revenue'] - f['cost'], 'eac': f['eac'], 'margin_eac': f['margin_eac'] or 0.0, 'pct': f['pct'] if f['pct'] is not None else ''}
            for k in ('contract', 'budget', 'cost', 'revenue', 'margin', 'eac', 'margin_eac'):
                grand[k] += v[k]
            rows.append({'id': 'j%d' % j.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': j.name, 'values': v, 'open': {'model': 'sb.job', 'id': j.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, code='Total')})
        cols = [{'key': 'code', 'label': 'Job', 'type': 'text'}, {'key': 'name', 'label': 'Name', 'type': 'text'},
                {'key': 'contract', 'label': 'Contract', 'type': 'monetary'}, {'key': 'budget', 'label': 'Budget', 'type': 'monetary'},
                {'key': 'revenue', 'label': 'Revenue to date', 'type': 'monetary'}, {'key': 'cost', 'label': 'Cost to date', 'type': 'monetary'},
                {'key': 'margin', 'label': 'Margin to date', 'type': 'monetary'}, {'key': 'eac', 'label': 'Forecast cost', 'type': 'monetary'},
                {'key': 'margin_eac', 'label': 'Forecast margin', 'type': 'monetary'}, {'key': 'pct', 'label': '% complete', 'type': 'number'}]
        return self._doc('job_profit', opts, cols, rows, 'To date', 'Job Profitability')

    def _report_job_est_exp(self, opts):
        doc = self._report_job_profit(opts)
        doc.update(key='job_est_exp', title='Estimated Job Expenses', columns=[c for c in doc['columns'] if c['key'] in ('code', 'name', 'budget', 'cost', 'eac')])
        return doc

    def _report_job_est_rev(self, opts):
        doc = self._report_job_profit(opts)
        doc.update(key='job_est_rev', title='Estimated Job Revenue', columns=[c for c in doc['columns'] if c['key'] in ('code', 'name', 'contract', 'revenue', 'pct')])
        return doc

    def _report_job_ledger(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        jobs = self._jobs(opts)
        fin = self._job_fin(jobs)
        rows = []
        for j in jobs:
            _f, act = fin[j.id]
            lines = [l for l in act['lines'] if not l['date'] or df <= fields.Date.to_date(l['date']) <= dt]
            if not lines:
                continue
            rows.append({'id': 'j%d' % j.id, 'parent': None, 'level': 0, 'kind': 'group', 'label': '%s %s' % (j.code, j.name), 'expandable': True,
                         'values': {'date': '', 'ref': '%s %s' % (j.code, j.name), 'cost': sum(l['cost'] for l in lines), 'revenue': sum(l['revenue'] for l in lines)}})
            for i, l in enumerate(sorted(lines, key=lambda x: x['date'] or '')):
                rows.append({'id': 'j%d_%d' % (j.id, i), 'parent': 'j%d' % j.id, 'level': 1, 'kind': 'line', 'label': l['ref'],
                             'values': {'date': l['date'] or '', 'ref': l['ref'], 'type': l['type'], 'what': l['what'], 'code': l['code'],
                                        'cost': l['cost'], 'revenue': l['revenue']},
                             'open': {'model': l['model'], 'id': l['res_id']} if l.get('model') else None})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Document', 'type': 'text'}, {'key': 'type', 'label': 'Type', 'type': 'text'},
                {'key': 'what', 'label': 'What', 'type': 'text'}, {'key': 'code', 'label': 'Cost code', 'type': 'text'},
                {'key': 'cost', 'label': 'Cost', 'type': 'monetary'}, {'key': 'revenue', 'label': 'Revenue', 'type': 'monetary'}]
        return self._doc('job_ledger', opts, cols, rows, self._period_name(df, dt), 'Job Ledger')

    def _report_job_register(self, opts):
        doc = self._report_job_ledger(opts)
        doc.update(key='job_register', title='Job Register', rows=[r for r in doc['rows'] if r['kind'] == 'group'])
        return doc

    def _report_job_cost_type(self, opts):
        jobs = self._jobs(opts)
        fin = self._job_fin(jobs)
        rows = []
        for j in jobs:
            f, _a = fin[j.id]
            if not f['by_code']:
                continue
            rows.append({'id': 'j%d' % j.id, 'parent': None, 'level': 0, 'kind': 'group', 'label': '%s %s' % (j.code, j.name), 'expandable': True,
                         'values': {'code': '%s %s' % (j.code, j.name), 'budget': f['budget'], 'actual': f['cost'], 'eac': f['eac']}})
            for c in f['by_code']:
                rows.append({'id': 'j%d_%d' % (j.id, c['id']), 'parent': 'j%d' % j.id, 'level': 1, 'kind': 'line', 'label': c['name'],
                             'values': {'code': c['name'], 'kind': dict(COST_KINDS).get(c['kind']), 'budget': c['budget'], 'actual': c['actual'],
                                        'eac': c['eac'], 'var': c['budget'] - c['eac']}})
        cols = [{'key': 'code', 'label': 'Job / cost code', 'type': 'text'}, {'key': 'kind', 'label': 'Type', 'type': 'text'},
                {'key': 'budget', 'label': 'Budget', 'type': 'monetary'}, {'key': 'actual', 'label': 'Actual', 'type': 'monetary'},
                {'key': 'eac', 'label': 'Forecast', 'type': 'monetary'}, {'key': 'var', 'label': 'Variance', 'type': 'monetary'}]
        return self._doc('job_cost_type', opts, cols, rows, 'To date', 'Job Costs by Type')

    def _report_job_estimates(self, opts):
        rows = []
        for j in self._jobs(opts):
            if not j.budget_ids:
                continue
            rows.append({'id': 'j%d' % j.id, 'parent': None, 'level': 0, 'kind': 'group', 'label': '%s %s' % (j.code, j.name), 'expandable': True,
                         'values': {'what': '%s %s' % (j.code, j.name), 'amount': sum(j.budget_ids.mapped('amount'))}})
            for b in j.budget_ids:
                rows.append({'id': 'b%d' % b.id, 'parent': 'j%d' % j.id, 'level': 1, 'kind': 'line', 'label': b.name or '',
                             'values': {'what': b.name or '', 'phase': b.phase_id.name or '', 'code': b.cost_code_id.display_name, 'qty': b.qty,
                                        'unit': b.unit or '', 'rate': b.rate, 'amount': b.amount}})
        cols = [{'key': 'what', 'label': 'Job / estimate line', 'type': 'text'}, {'key': 'phase', 'label': 'Phase', 'type': 'text'},
                {'key': 'code', 'label': 'Cost code', 'type': 'text'}, {'key': 'qty', 'label': 'Units', 'type': 'number'}, {'key': 'unit', 'label': 'Unit', 'type': 'text'},
                {'key': 'rate', 'label': 'Rate', 'type': 'monetary'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('job_estimates', opts, cols, rows, 'Budgets', 'Job Estimates')

    def _report_phase_list(self, opts):
        rows = []
        for j in self._jobs(opts):
            for p in j.phase_ids:
                rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name,
                             'values': {'job': '%s %s' % (j.code, j.name), 'phase': p.name, 'start': fields.Date.to_string(p.date_start) or '',
                                        'end': fields.Date.to_string(p.date_end) or '', 'progress': p.progress}})
        cols = [{'key': 'job', 'label': 'Job', 'type': 'text'}, {'key': 'phase', 'label': 'Phase', 'type': 'text'}, {'key': 'start', 'label': 'Start', 'type': 'date'},
                {'key': 'end', 'label': 'End', 'type': 'date'}, {'key': 'progress', 'label': '% complete', 'type': 'number'}]
        return self._doc('phase_list', opts, cols, rows, 'All jobs', 'Phase List')

    def _report_cost_codes(self, opts):
        rows = [{'id': 'c%d' % c.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': c.name,
                 'values': {'code': c.code, 'name': c.name, 'kind': dict(COST_KINDS).get(c.kind), 'active': 'Yes' if c.active else 'No'}}
                for c in self.env['sb.cost.code'].with_context(active_test=False).search([])]
        cols = [{'key': 'code', 'label': 'Code', 'type': 'text'}, {'key': 'name', 'label': 'Name', 'type': 'text'},
                {'key': 'kind', 'label': 'Type', 'type': 'text'}, {'key': 'active', 'label': 'In use', 'type': 'text'}]
        return self._doc('cost_codes', opts, cols, rows, '', 'Cost Code List')

    def _report_unbilled_job(self, opts):
        """Approved hours on time-and-materials jobs not yet on a bill to the customer."""
        rows, grand = [], 0.0
        T = self.env['sb.timesheet'].search([('company_id', '=', self.env.company.id), ('state', '=', 'approved'), ('billed_id', '=', False),
                                             ('job_id.kind', '=', 'tm')], order='job_id, date')
        for t in T:
            amt = t.hours * (t.bill_rate or t.job_id.bill_rate or 0.0)
            grand += amt
            rows.append({'id': 't%d' % t.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': t.job_id.code,
                         'values': {'job': t.job_id.display_name, 'date': fields.Date.to_string(t.date), 'who': t.employee_id.name, 'hours': t.hours,
                                    'rate': t.bill_rate or t.job_id.bill_rate, 'amount': amt}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'job': 'Total', 'amount': grand}})
        cols = [{'key': 'job', 'label': 'Job', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'who', 'label': 'Who', 'type': 'text'},
                {'key': 'hours', 'label': 'Hours', 'type': 'number'}, {'key': 'rate', 'label': 'Rate', 'type': 'monetary'}, {'key': 'amount', 'label': 'To bill', 'type': 'monetary'}]
        return self._doc('unbilled_job', opts, cols, rows, 'Approved, not billed', 'Unbilled Job Expense')

    def _report_time_tickets(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows, grand = [], defaultdict(float)
        for t in self.env['sb.timesheet'].search([('company_id', '=', self.env.company.id), ('date', '>=', df), ('date', '<=', dt)], order='employee_id, date'):
            if not self._search_ok(opts, t.employee_id.name, t.job_id.name, t.job_id.code):
                continue
            grand['hours'] += t.hours
            grand['cost'] += t.hours * t.cost_rate
            rows.append({'id': 't%d' % t.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': t.employee_id.name,
                         'values': {'who': t.employee_id.name, 'date': fields.Date.to_string(t.date), 'job': t.job_id.display_name,
                                    'customer': t.job_id.partner_id.name or '', 'task': t.task_id.name or '', 'hours': t.hours,
                                    'state': dict(t._fields['state'].selection).get(t.state), 'cost': t.hours * t.cost_rate}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, who='Total')})
        cols = [{'key': 'who', 'label': 'Employee', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'job', 'label': 'Job', 'type': 'text'},
                {'key': 'customer', 'label': 'Customer', 'type': 'text'}, {'key': 'task', 'label': 'Task', 'type': 'text'},
                {'key': 'hours', 'label': 'Hours', 'type': 'number'}, {'key': 'state', 'label': 'Status', 'type': 'text'}, {'key': 'cost', 'label': 'Cost', 'type': 'monetary'}]
        return self._doc('time_tickets', opts, cols, rows, self._period_name(df, dt), 'Time Ticket Register')
