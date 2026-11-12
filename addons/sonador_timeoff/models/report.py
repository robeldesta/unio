"""Leave balances on the report engine."""
from collections import defaultdict

from odoo import models


class SbReportLeave(models.AbstractModel):
    _inherit = 'sb.report'

    def _report_leave(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        S = self.env['sb.leave.service'].sudo()
        L = self.env['sb.leave'].sudo()
        rows, grand = [], defaultdict(float)
        for e in self.env['hr.employee'].sudo().search([('company_id', '=', self.env.company.id)], order='name'):
            if not self._search_ok(opts, e.name, e.department_id.name):
                continue
            a = S.annual(e, dt)
            taken_here = L.search([('employee_id', '=', e.id), ('state', '=', 'approved'), ('date_from', '>=', df), ('date_from', '<=', dt)])
            by_kind = defaultdict(float)
            for l in taken_here:
                by_kind[l.kind] += l.days
            v = {'name': e.name, 'dept': e.department_id.name or '', 'earned': a['earned'], 'taken_all': a['taken'], 'lapsed': a['lapsed'],
                 'available': a['available'], 'annual': by_kind.get('annual', 0.0), 'sick': by_kind.get('sick', 0.0),
                 'other': sum(x for k, x in by_kind.items() if k not in ('annual', 'sick'))}
            for k in ('earned', 'taken_all', 'lapsed', 'available', 'annual', 'sick', 'other'):
                grand[k] += v[k]
            rows.append({'id': 'e%d' % e.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': e.name, 'values': v,
                         'warning': v['available'] < 0, 'open': {'model': 'hr.employee', 'id': e.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, name='Total')})
        cols = [{'key': 'name', 'label': 'Employee', 'type': 'text'}, {'key': 'dept', 'label': 'Department', 'type': 'text'},
                {'key': 'annual', 'label': 'Annual leave taken (period)', 'type': 'number'}, {'key': 'sick', 'label': 'Sick (period)', 'type': 'number'},
                {'key': 'other', 'label': 'Other leave (period)', 'type': 'number'}, {'key': 'earned', 'label': 'Annual earned to date', 'type': 'number'},
                {'key': 'taken_all', 'label': 'Annual taken to date', 'type': 'number'}, {'key': 'lapsed', 'label': 'Lapsed', 'type': 'number'},
                {'key': 'available', 'label': 'Available', 'type': 'number'}]
        return self._doc('leave', opts, cols, rows, '%s  |  balances at %s' % (self._period_name(df, dt), dt), 'Leave Balances')
