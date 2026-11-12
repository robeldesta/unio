"""Goals / OKRs (company -> department -> person, aligned to a parent goal), with key results; weekly check-ins;
one-to-ones with a shared agenda and action items; praise and feedback."""
from odoo import api, fields, models

KR_MEASURE = [('manual', 'Entered'), ('kpi', 'A KPI'), ('plan', 'Monthly plan tasks')]
STATUS = [('on_track', 'On track'), ('at_risk', 'At risk'), ('off_track', 'Off track'), ('done', 'Achieved'), ('dropped', 'Dropped')]


class SbPerfGoal(models.Model):
    _name = 'sb.perf.goal'
    _description = 'Goal'
    _order = 'level, date_to, id'

    name = fields.Char(required=True)
    description = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    level = fields.Selection([('company', 'Company'), ('department', 'Department'), ('person', 'Individual')], default='person', required=True)
    employee_id = fields.Many2one('hr.employee', 'Owner', index=True)
    department_id = fields.Many2one('hr.department')
    parent_id = fields.Many2one('sb.perf.goal', 'Aligned to', ondelete='set null', index=True)
    child_ids = fields.One2many('sb.perf.goal', 'parent_id', 'Aligned goals')
    date_from = fields.Date('Start')
    date_to = fields.Date('Due')
    status = fields.Selection(STATUS, default='on_track', required=True)
    kr_ids = fields.One2many('sb.perf.kr', 'goal_id', 'Key results')
    progress_manual = fields.Float('Progress % (no key results)')
    progress = fields.Float(compute='_compute_progress', store=True, recursive=True)
    private = fields.Boolean(help='Visible only to the owner, their managers and HR.')

    @api.depends('kr_ids.progress', 'progress_manual', 'child_ids.progress')
    def _compute_progress(self):
        for g in self:
            if g.kr_ids:
                g.progress = round(sum(g.kr_ids.mapped('progress')) / len(g.kr_ids), 1)
            elif g.child_ids:
                g.progress = round(sum(g.child_ids.mapped('progress')) / len(g.child_ids), 1)
            else:
                g.progress = g.progress_manual


class SbPerfKr(models.Model):
    _name = 'sb.perf.kr'
    _description = 'Key result'
    _order = 'sequence, id'

    goal_id = fields.Many2one('sb.perf.goal', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    start_value = fields.Float('Start')
    target_value = fields.Float('Target', default=100.0)
    current_value = fields.Float('Now')
    unit = fields.Char()
    progress = fields.Float(compute='_compute_progress', store=True)
    measure = fields.Selection(KR_MEASURE, string='Measured by', default='manual', required=True,
                               help='Entered by hand, from a KPI (its results or its computed measure over the goal period), '
                                    'or from monthly plan tasks (their completion).')
    kpi_id = fields.Many2one('sb.perf.kpi', 'KPI', ondelete='set null', index=True)
    rollup = fields.Selection([('sum', 'Total over the goal period'), ('avg', 'Monthly average'), ('latest', 'Latest month')],
                              default='sum', required=True)
    source_note = fields.Char('Last refreshed', readonly=True)

    @api.depends('start_value', 'target_value', 'current_value')
    def _compute_progress(self):
        for k in self:
            span = k.target_value - k.start_value
            k.progress = max(0.0, min(100.0, (k.current_value - k.start_value) / span * 100.0)) if span else (
                100.0 if k.current_value >= k.target_value else 0.0)


    # ------------------------------------------------------------------ key results fed from KPIs and plans
    def _window(self):
        """Goal period up to today (default: the current fiscal year)."""
        from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of
        self.ensure_one()
        today = fields.Date.context_today(self)
        g = self.goal_id
        if g.date_from and g.date_to:
            return g.date_from, min(g.date_to, today) if g.date_from <= today else g.date_to
        periods = fiscal_periods(fiscal_year_of(today))
        return g.date_from or periods[0][2], min(g.date_to or periods[-1][3], today)

    def _people(self):
        """Whose results count: the owner, else everyone in the department, else the whole company."""
        g = self.goal_id
        E = self.env['hr.employee'].sudo()
        if g.employee_id:
            return g.employee_id
        if g.department_id:
            return E.search([('department_id', 'child_of', g.department_id.id), ('active', '=', True)])
        return E.search([('company_id', '=', g.company_id.id), ('active', '=', True)])

    def _from_kpi(self):
        """Current value of a key result linked to a KPI, rolled up over the goal period."""
        self.ensure_one()
        k = self.kpi_id
        d0, d1 = self._window()
        if d1 < d0:
            return 0.0, 'goal period not started'
        months = self._months_between(d0, d1)
        if k.source == 'auto':
            people = [None] if k._is_company_measure() or not self.goal_id.employee_id and not self.goal_id.department_id \
                else list(self._people())
            if self.rollup == 'latest' and months:
                m = months[-1]
                val = sum(k._measure(e, m['start'], min(m['end'], d1)) for e in people)
            else:
                val = sum(k._measure(e, d0, d1) for e in people)
                if self.rollup == 'avg' and months:
                    val /= len(months)
            return val, '%s, %s to %s' % (k.name, d0, d1)
        V = self.env['sb.perf.kpi.value'].sudo()
        vals = V.search([('kpi_id', '=', k.id), ('employee_id', 'in', self._people().ids),
                         ('month', '>=', months[0]['start'] if months else d0), ('month', '<=', d1)])
        if not vals:
            return 0.0, 'no results yet'
        by_month = {}
        for v in vals:
            by_month.setdefault(v.month, []).append(v.actual)
        per_month = [sum(xs) for _m, xs in sorted(by_month.items())]
        if self.rollup == 'latest':
            val = per_month[-1]
        elif self.rollup == 'avg':
            val = sum(per_month) / len(per_month)
        else:
            val = sum(per_month)
        return val, '%s results in %d month(s)' % (len(vals), len(per_month))

    def _months_between(self, d0, d1):
        from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of
        out, fy = [], fiscal_year_of(d0)
        while True:
            for no, name, s, e in fiscal_periods(fy):
                if e >= d0 and s <= d1:
                    out.append({'start': s, 'end': e, 'label': name, 'no': no})
            if not out or out[-1]['end'] >= d1 or fy > fiscal_year_of(d1):
                break
            fy += 1
        return out

    def _from_plan(self):
        """Plan completion is computed in Planning (sonador_planning extends this)."""
        return self.current_value, 'Planning is not installed'

    def _refresh(self):
        for k in self:
            if k.measure == 'kpi' and k.kpi_id:
                val, note = k._from_kpi()
            elif k.measure == 'plan':
                val, note = k._from_plan()
            else:
                continue
            k.write({'current_value': round(val, 2), 'source_note': '%s (%s)' % (note, fields.Date.context_today(k))})

    def action_refresh(self):
        self._refresh()
        return True

    @api.model
    def _cron_refresh(self):
        self.search([('measure', '!=', 'manual'), ('goal_id.status', 'not in', ('done', 'dropped'))])._refresh()


class SbPerfCheckin(models.Model):
    _name = 'sb.perf.checkin'
    _description = 'Weekly check-in'
    _order = 'week desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    week = fields.Date('Week of', required=True, index=True)
    mood = fields.Selection([('1', 'Rough'), ('2', 'Meh'), ('3', 'Okay'), ('4', 'Good'), ('5', 'Great')], default='4')
    wins = fields.Text('Wins this week')
    blockers = fields.Text('Blockers / need help')
    priorities = fields.Text('Priorities next week')
    manager_reply = fields.Text()
    replied_by = fields.Many2one('hr.employee')


class SbPerfOneOnOne(models.Model):
    _name = 'sb.perf.one2one'
    _description = 'One-to-one'
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    manager_id = fields.Many2one('hr.employee', required=True, index=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    notes = fields.Text('Shared notes')
    item_ids = fields.One2many('sb.perf.one2one.item', 'meeting_id', 'Agenda and actions')
    done = fields.Boolean('Held')


class SbPerfOneOnOneItem(models.Model):
    _name = 'sb.perf.one2one.item'
    _description = 'One-to-one item'
    _order = 'kind, sequence, id'

    meeting_id = fields.Many2one('sb.perf.one2one', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection([('agenda', 'Talking point'), ('action', 'Action item')], default='agenda', required=True)
    name = fields.Char(required=True)
    owner_id = fields.Many2one('hr.employee', 'Who')
    done = fields.Boolean()


class SbPerfFeedback(models.Model):
    _name = 'sb.perf.feedback'
    _description = 'Praise and feedback'
    _order = 'create_date desc, id desc'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    kind = fields.Selection([('praise', 'Praise'), ('feedback', 'Feedback')], default='praise', required=True)
    from_id = fields.Many2one('hr.employee', 'From', required=True, index=True)
    to_id = fields.Many2one('hr.employee', 'To', required=True, index=True)
    value = fields.Selection([('customer', 'Customer first'), ('quality', 'Quality'), ('teamwork', 'Teamwork'),
                              ('integrity', 'Integrity'), ('ownership', 'Ownership'), ('innovation', 'Innovation')],
                             string='Company value')
    text = fields.Text(required=True)
    visibility = fields.Selection([('public', 'Everyone (praise wall)'), ('private', 'Person and their manager')],
                                  default='public', required=True)
