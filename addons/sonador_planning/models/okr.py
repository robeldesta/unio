"""Planning in Performance: key results measured by monthly plan tasks, and plan completion as a KPI measure."""
from odoo import fields, models


def _task_pct(tasks):
    """Average completion of tasks: done = 100, else their progress %."""
    if not tasks:
        return 0.0
    return round(sum(100.0 if t.status == 'done' else float(t.progress or 0) for t in tasks) / len(tasks), 2)


class SbPerfKr(models.Model):
    _inherit = 'sb.perf.kr'

    plan_task_ids = fields.Many2many('sb.plan.task', 'sb_perf_kr_plan_task_rel', 'kr_id', 'task_id', 'Plan tasks',
                                     help='Monthly plan tasks that deliver this key result. Empty: the tasks linked to the goal.')

    def _from_plan(self):
        self.ensure_one()
        tasks = self.plan_task_ids
        if not tasks:
            d0, d1 = self._window()
            dom = [('goal_id', '=', self.goal_id.id), ('date_from', '<=', d1), ('date_to', '>=', d0),
                   ('review', '!=', 'returned')]
            if self.goal_id.employee_id:
                dom.append(('employee_id', '=', self.goal_id.employee_id.id))
            tasks = self.env['sb.plan.task'].sudo().search(dom)
        if not tasks:
            return 0.0, 'no plan tasks linked yet'
        done = len(tasks.filtered(lambda t: t.status == 'done'))
        return _task_pct(tasks), '%d of %d plan tasks done' % (done, len(tasks))

    def _refresh(self):
        for k in self.filtered(lambda x: x.measure == 'plan'):
            if not k.target_value:
                k.target_value = 100.0
            if not k.unit:
                k.unit = '%'
        return super()._refresh()


class SbPerfKpi(models.Model):
    _inherit = 'sb.perf.kpi'

    def _measures(self):
        out = super()._measures()
        out += [
            {'key': 'plan_completion', 'name': "Plan completion: % of the person's monthly plan tasks done", 'scope': 'person',
             'unit': '%', 'higher': True, 'fn': lambda k, e, a, b: k._m_plan(e, a, b)},
            {'key': 'company_plan_completion', 'name': 'Plan completion across the company (% of monthly plan tasks done)',
             'scope': 'company', 'unit': '%', 'higher': True, 'fn': lambda k, e, a, b: k._m_plan(None, a, b)},
        ]
        return out

    def _m_plan(self, employee, date_from, date_to):
        dom = [('company_id', '=', self.company_id.id), ('date_to', '>=', date_from), ('date_to', '<=', date_to),
               ('review', '!=', 'returned')]
        if employee is not None:
            dom.append(('employee_id', '=', employee.id))
        return _task_pct(self.env['sb.plan.task'].sudo().search(dom))
