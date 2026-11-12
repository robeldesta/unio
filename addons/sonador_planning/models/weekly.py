"""The weekly plan and its one score.

Every line of a plan has a priority (Must, Should, Could: weights 3, 2, 1), a target and where its actual comes from: a
system measure (the same measures as Performance KPIs: litres sold, collections, deliveries ...) or the person's own
check-ins. A week's plan score is the weighted average of its lines' achievement (actual / target, at most 1.2), out of
100. A Must line missed without a reason scores 0. Must lines not done carry over to the next week; carried twice, the
general manager sees them.

Roles hold the standard lines of a job: a role line "every week" is put in each week's plan by itself, with its target."""
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

PRIORITY = [('must', 'Must'), ('should', 'Should'), ('could', 'Could')]
WEIGHT = {'must': 3, 'should': 2, 'could': 1}
CAP = 1.2


class SbPlanTask(models.Model):
    _inherit = 'sb.plan.task'

    priority = fields.Selection(PRIORITY, default='should', required=True)
    measure = fields.Selection('_measure_selection', string='Actual from',
                               help='Where the actual comes from. Empty: from the check-ins (achieved amount, done or progress).')
    measure_args = fields.Json('Measure options', help='e.g. {"product_id": 12} for one product.')
    carried_from_id = fields.Many2one('sb.plan.task', 'Carried from', ondelete='set null', index='btree_not_null')
    carry_count = fields.Integer('Times carried', help='How many weeks in a row this Must line was not done.')
    miss_reason = fields.Char('Why it was missed')
    measured_qty = fields.Float('Actual (measured)', help='Last actual read from the system.')

    @api.model
    def _measure_selection(self):
        return self.env['sb.perf.kpi']._measure_selection()


class SbPlanRoleItem(models.Model):
    _inherit = 'sb.plan.role.item'

    priority = fields.Selection(PRIORITY, default='should', required=True)
    measure = fields.Selection('_measure_selection', string='Actual from')
    measure_args = fields.Json('Measure options')
    per = fields.Selection([('week', 'Every week'), ('month', 'In the month plan')], default='month', required=True,
                           help='Every week: put in each week of the person\'s plan by itself, with this target.')

    @api.model
    def _measure_selection(self):
        return self.env['sb.perf.kpi']._measure_selection()


class SbPlanKpi(models.Model):
    """A department's standard KPI on the board: a system measure over the week, against a weekly target."""
    _name = 'sb.plan.kpi'
    _description = 'Department KPI on the planning board'
    _order = 'department_id, sequence, id'

    department_id = fields.Many2one('hr.department', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    measure = fields.Selection('_measure_selection', string='Measure', required=True)
    target = fields.Float('Weekly target', required=True)
    unit = fields.Char()
    active = fields.Boolean(default=True)

    @api.model
    def _measure_selection(self):
        return self.env['sb.perf.kpi']._measure_selection()

    def value(self, d0, d1):
        self.ensure_one()
        kpi = self.env['sb.perf.kpi'].new({'company_id': self.company_id.id, 'auto_key': self.measure, 'source': 'auto'})
        try:
            with self.env.cr.savepoint():
                return float(kpi._measure(self.env['hr.employee'], d0, d1) or 0.0)
        except Exception:
            return 0.0

    def higher(self):
        return self.env['sb.plan.score']._higher(self.measure)

    def level(self):
        return self.env['sb.plan.score'].is_level(self.measure)


class SbPlanWeek(models.Model):
    _inherit = 'sb.plan.week'

    department_id = fields.Many2one('hr.department', index=True)
    plan_score = fields.Integer('Plan score', help='0-100: the week\'s lines, weighted by priority, against their targets.')
    lines = fields.Json('Lines at sending', help='Target, actual and score of each line when the report was sent.')
    unplanned = fields.Integer('Unplanned tasks', help='Tasks that came up during the week (not in the score).')
    late = fields.Boolean('Sent late')
    review_state = fields.Selection([('none', 'Not reviewed'), ('agreed', 'Agreed'), ('changed', 'Score changed')],
                                    default='none', required=True)
    manager_score = fields.Integer('Score after review')
    manager_reason = fields.Char('Why the score changed')
    reviewed_by = fields.Many2one('res.users')
    reviewed_at = fields.Datetime()

    def _final_score(self):
        self.ensure_one()
        return self.manager_score if self.review_state == 'changed' else self.plan_score


class SbPlanScore(models.AbstractModel):
    _name = 'sb.plan.score'
    _description = 'Plan score'

    # ------------------------------------------------------------------ actuals
    def _measured(self, t, d0, d1):
        """The system's actual of a line over [d0, d1], through the Performance measure of that name."""
        kpi = self.env['sb.perf.kpi'].new({'company_id': t.company_id.id, 'auto_key': t.measure, 'source': 'auto'})
        try:
            with self.env.cr.savepoint():
                return float(kpi.with_context(measure_args=t.measure_args or {})._measure(t.employee_id, d0, d1) or 0.0)
        except Exception:
            return 0.0

    def _mdef(self, key):
        return next((m for m in self.env['sb.perf.kpi']._measures() if m['key'] == key), None) or {}

    def _higher(self, key):
        return self._mdef(key).get('higher', True)

    def _unit(self, key):
        return self._mdef(key).get('unit') or ''

    def is_level(self, key):
        """A level (a %, days of cover, requests waiting) is judged as it stands; an amount (litres, money, orders) builds
        up over the week, so before the week ends it is judged against the share of the week gone."""
        m = self._mdef(key)
        return bool(m.get('level') or m.get('unit') in ('%', 'days'))

    def line(self, t, ws, we, today=None, final=False):
        """One line of the week: its target, actual, achievement and score. While the line runs (and not at sending),
        it is judged against how far along it should be by now."""
        today = today or fields.Date.context_today(self)
        due = ws <= t.date_to <= we
        upto = min(today, we, t.date_to)
        target = t.target_qty or 0.0
        src = 'system' if t.measure else 'check-in'
        actual, ratio = None, 0.0
        level = bool(t.measure and self.is_level(t.measure))
        if t.measure:
            actual = self._measured(t, t.date_from, max(upto, t.date_from))
            if t.measured_qty != actual:
                t.sudo().measured_qty = actual
            if target:
                if self._higher(t.measure):
                    ratio = actual / target
                else:
                    ratio = CAP if actual <= 0 else target / actual
            else:
                ratio = 1.0 if actual else 0.0
        elif target:
            actual = t.done_qty or (target if t.status == 'done' else 0.0)
            ratio = actual / target
        else:
            ratio = 1.0 if t.status == 'done' else (t.progress or 0) / 100.0
        running = today <= t.date_to and not (final and due)
        expected = 1.0
        if running and not level and t.status != 'done':
            span = (t.date_to - t.date_from).days + 1
            expected = min(1.0, max(1, (min(today, we) - t.date_from).days + 1) / span) if span > 0 else 1.0
            if t.measure and not self._higher(t.measure) and target:
                ratio = CAP if actual <= 0 else min(CAP, target * expected / actual)
            else:
                ratio = ratio / expected if expected else 1.0
        ratio = max(0.0, min(ratio, CAP))
        missed = due and ratio < 0.999 and (today > t.date_to or final)
        score = ratio
        if missed and t.priority == 'must' and not (t.miss_reason or '').strip():
            score = 0.0
        return {'id': t.id, 'name': t.name, 'priority': t.priority, 'weight': WEIGHT[t.priority], 'due': due,
                'target': target, 'unit': t.unit or (self._unit(t.measure) if t.measure else ''), 'actual': actual,
                'source': src, 'measure': t.measure or '', 'ratio': round(ratio, 3), 'score': round(score, 3),
                'pct': round(ratio * 100), 'running': running and expected < 1.0, 'expected_pct': round(expected * 100),
                'missed': missed, 'reason': t.miss_reason or '', 'status': t.status,
                'unplanned': bool(t.added_later and not t.carried_from_id and not t.role_item_id),
                'carried': t.carry_count or 0, 'done_when': t.done_when or ''}

    def week(self, e, ws, we, tasks, today=None, final=False):
        """The week's lines and its plan score (unplanned tasks are listed, not scored)."""
        lines = [self.line(t, ws, we, today, final) for t in tasks]
        scored = [l for l in lines if not l['unplanned']]
        total = sum(l['weight'] for l in scored)
        score = round(min(100.0, 100.0 * sum(l['weight'] * l['score'] for l in scored) / total)) if total else None
        order = {'must': 0, 'should': 1, 'could': 2}
        lines.sort(key=lambda l: (l['unplanned'], order[l['priority']], not l['due']))
        return {'lines': lines, 'score': score, 'unplanned': len(lines) - len(scored),
                'needs_reason': [l['id'] for l in lines if l['missed'] and l['priority'] == 'must' and not l['reason'].strip()]}

    # ------------------------------------------------------------------ the report window
    def report_window(self, company, ws):
        """When the week's report opens and closes: report day 10:00 to the next day 12:00, local time."""
        tz = pytz.timezone(self.env.user.tz or 'Africa/Addis_Ababa')
        rd = ws + timedelta(days=(int(company.sb_pl_report_day or '4') - ws.weekday()) % 7)
        opens = tz.localize(datetime.combine(rd, time(10, 0))).astimezone(pytz.utc).replace(tzinfo=None)
        closes = tz.localize(datetime.combine(rd + timedelta(days=1), time(12, 0))).astimezone(pytz.utc).replace(tzinfo=None)
        return rd, opens, closes

    # ------------------------------------------------------------------ standard lines and carry-overs
    def ensure_role_lines(self, e, ws, we):
        """The role's every-week lines in this week's plan, once (only in a plan that is with the manager or approved)."""
        role = e.sudo().sb_pl_role_id
        items = role.item_ids.filtered(lambda i: i.per == 'week') if role else self.env['sb.plan.role.item']
        if not items:
            return
        P = self.env['sb.planning']
        T = self.env['sb.plan.task'].sudo()
        plan = P._plan(e, ws)
        if not plan or plan.state not in ('submitted', 'approved'):
            plan = P._plan(e, we)
        if not plan or plan.state not in ('submitted', 'approved'):
            return
        for item in items:
            if T.search_count([('employee_id', '=', e.id), ('role_item_id', '=', item.id), ('date_from', '>=', ws), ('date_to', '<=', we)]):
                continue
            d0, d1 = max(ws, plan.month), min(we, plan.month_end)
            T.create({'plan_id': plan.id, 'name': item.name, 'done_when': item.done_when or False, 'target_qty': item.target_qty,
                      'unit': item.unit or False, 'output': item.output or False, 'indicator_id': item.indicator_id.id or False,
                      'role_item_id': item.id, 'priority': item.priority, 'measure': item.measure or False,
                      'measure_args': item.measure_args or False, 'date_from': d0, 'date_to': d1, 'review': 'approved',
                      'sequence': item.sequence})

    def carry_over(self, e, ws, we, tasks):
        """Must lines due this week and not done go to next week, marked carried."""
        P = self.env['sb.planning']
        T = self.env['sb.plan.task'].sudo()
        nws, nwe = ws + timedelta(days=7), we + timedelta(days=7)
        out = T
        for t in tasks.filtered(lambda t: t.priority == 'must' and t.status != 'done' and ws <= t.date_to <= we and not t.role_item_id):
            if T.search_count([('carried_from_id', '=', t.id)]):
                continue
            plan = P._plan(e, nws, create=True)
            out |= T.create({'plan_id': plan.id, 'name': t.name, 'done_when': t.done_when or False, 'detail': t.detail or False,
                             'target_qty': max(0.0, (t.target_qty or 0) - (t.done_qty or 0)) if t.target_qty and not t.measure else t.target_qty,
                             'unit': t.unit or False, 'output': t.output or False, 'category_id': t.category_id.id or False,
                             'goal_id': t.goal_id.id or False, 'indicator_id': t.indicator_id.id or False, 'priority': 'must',
                             'measure': t.measure or False, 'measure_args': t.measure_args or False,
                             'date_from': max(nws, plan.month), 'date_to': min(nwe, plan.month_end), 'carried_from_id': t.id,
                             'carry_count': (t.carry_count or 0) + 1, 'review': 'approved', 'added_later': True, 'sequence': 5})
        return out
