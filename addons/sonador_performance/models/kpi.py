"""KPIs: a library of measures (name, unit, direction, frequency, entered by hand or computed from system data through
a registry of measures), templates per position / department with weights and targets, assignments to people, results
per period, performance levels (bands) and a stored scorecard per person and period.

Measures registry: any module adds measures by inheriting sb.perf.kpi and extending _measures(); each measure is
{'key', 'name', 'scope': 'person' | 'company', 'unit', 'higher': bool, 'fn': callable(kpi, employee|None, date_from, date_to)}.
"""
import re
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

DEFAULT_BANDS = '>=0.95=5; >=0.85=4; >=0.75=3; >=0.6=2; <0.6=1'
# the standard performance levels (same thresholds as DEFAULT_BANDS): (from %, name, score, colour)
DEFAULT_LEVELS = [(95.0, 'Excellent', 5, '#198754'), (85.0, 'Very good', 4, '#0d9488'), (75.0, 'Good', 3, '#ca8a04'),
                  (60.0, 'Fair', 2, '#ea580c'), (0.0, 'Poor', 1, '#dc2626')]
LEVEL_NAME = {s: n for _p, n, s, _c in DEFAULT_LEVELS}
LEVEL_COLOR = {s: c for _p, _n, s, c in DEFAULT_LEVELS}
FREQUENCY = [('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')]
# kept for older callers (the measures registry on sb.perf.kpi is the full list)
AUTO = [('sales_amount', 'Sales (before VAT) by the salesperson'),
        ('sales_litres', 'Litres sold by the salesperson'),
        ('collections', 'Collections from the salesperson\'s customers and van trips'),
        ('company_sales_amount', 'Company sales (before VAT)'),
        ('company_sales_litres', 'Company litres sold'),
        ('company_collections', 'Company collections'),
        ('production_litres', 'Litres produced (finished product received)')]


def litres_of(product):
    """Litres in one unit, from the item name (5 Liter, 120 Liter); commercial bulk is sold by the litre."""
    name = (product.name or '').lower()
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:l\b|lt|liter|litre)', name)
    if m:
        return float(m.group(1))
    if 'commercial' in name:
        return 1.0
    return 0.0


def score_for(achievement, bands):
    """bands '>=0.95=5; >=0.85=4; ...; <0.6=1' (achievement as a fraction)."""
    for part in (bands or DEFAULT_BANDS).split(';'):
        part = part.strip()
        m = re.match(r'(>=|>|<=|<)\s*(-?[\d.]+)\s*=\s*(\d+)', part)
        if not m:
            continue
        op, edge, score = m.group(1), float(m.group(2)), int(m.group(3))
        if (op == '>=' and achievement >= edge) or (op == '>' and achievement > edge) or \
                (op == '<' and achievement < edge) or (op == '<=' and achievement <= edge):
            return score
    return 1


def achievement_of(actual, target, higher_is_better=True):
    """actual / target (higher is better) or target / actual (lower is better; nothing at all counts as fully met)."""
    if not target:
        return 0.0
    if higher_is_better:
        return actual / target
    return target / actual if actual else 1.0


class SbPerfKpi(models.Model):
    _name = 'sb.perf.kpi'
    _description = 'KPI'
    _order = 'department_id, sequence, id'

    name = fields.Char(required=True)
    code = fields.Char(index=True, help='Short reference (optional).')
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    department_id = fields.Many2one('hr.department', 'Department')
    unit = fields.Char()
    target = fields.Float('Target', required=True, default=100.0, help='Target for one period (month, quarter or year).')
    higher_is_better = fields.Boolean(default=True)
    frequency = fields.Selection(FREQUENCY, default='monthly', required=True,
                                 help='Monthly: a result every month. Quarterly / yearly: one result at the end of each '
                                      'fiscal quarter / year (computed measures then cover the whole quarter / year).')
    bands = fields.Char('Scoring bands', default=DEFAULT_BANDS,
                        help='Achievement (actual / target) to score 1-5, e.g. ">=0.95=5; >=0.85=4; >=0.75=3; >=0.6=2; <0.6=1". '
                             'Written from the performance levels when the KPI has its own.')
    level_ids = fields.One2many('sb.perf.kpi.level', 'kpi_id', 'Performance levels', copy=True)
    source = fields.Selection([('manual', 'Entered'), ('auto', 'Computed from system data')], default='manual', required=True)
    auto_key = fields.Selection('_measure_selection', string='Measure')
    description = fields.Char()
    how_measured = fields.Text('How it is measured', help='For the people entering results: what counts and where the figure comes from.')
    active = fields.Boolean(default=True)
    assignment_ids = fields.One2many('sb.perf.kpi.assign', 'kpi_id', 'Assigned to')
    legacy_ref = fields.Char(index=True, copy=False)

    # ------------------------------------------------------------------ measures registry
    @api.model
    def _measures(self):
        """All computed measures. Other modules extend: out = super()._measures(); out.append({...}); return out."""
        return [
            {'key': 'sales_amount', 'name': 'Sales (before VAT) by the salesperson', 'scope': 'person', 'unit': 'money', 'higher': True,
             'fn': lambda k, e, a, b: k._m_sales(e, a, b, 'amount')},
            {'key': 'sales_litres', 'name': 'Litres sold by the salesperson', 'scope': 'person', 'unit': 'litres', 'higher': True,
             'fn': lambda k, e, a, b: k._m_sales(e, a, b, 'litres')},
            {'key': 'collections', 'name': "Collections from the salesperson's customers and van trips", 'scope': 'person',
             'unit': 'money', 'higher': True, 'fn': lambda k, e, a, b: sum(k._receipts(e, a, b).mapped('amount_total'))},
            {'key': 'company_sales_amount', 'name': 'Company sales (before VAT)', 'scope': 'company', 'unit': 'money', 'higher': True,
             'fn': lambda k, e, a, b: k._m_sales(None, a, b, 'amount')},
            {'key': 'company_sales_litres', 'name': 'Company litres sold', 'scope': 'company', 'unit': 'litres', 'higher': True,
             'fn': lambda k, e, a, b: k._m_sales(None, a, b, 'litres')},
            {'key': 'company_collections', 'name': 'Company collections', 'scope': 'company', 'unit': 'money', 'higher': True,
             'fn': lambda k, e, a, b: sum(k._receipts(None, a, b).mapped('amount_total'))},
            {'key': 'production_litres', 'name': 'Litres produced (finished product received)', 'scope': 'company', 'unit': 'litres',
             'higher': True, 'fn': lambda k, e, a, b: k._m_production(a, b)},
            {'key': 'on_time_delivery', 'name': 'On-time delivery: % of delivery orders fully delivered within the agreed days',
             'scope': 'company', 'unit': '%', 'higher': True, 'fn': lambda k, e, a, b: k._m_on_time(a, b)},
            {'key': 'overdue_receivables', 'name': "Overdue receivables of the salesperson's customers (open balance past due)",
             'scope': 'person', 'unit': 'money', 'higher': False, 'fn': lambda k, e, a, b: k._m_overdue(e, b)},
            {'key': 'company_overdue_receivables', 'name': 'Company overdue receivables (open balance past due)',
             'scope': 'company', 'unit': 'money', 'higher': False, 'fn': lambda k, e, a, b: k._m_overdue(None, b)},
            {'key': 'goal_progress', 'name': "Progress of the person's goals (average %)", 'scope': 'person', 'unit': '%',
             'higher': True, 'fn': lambda k, e, a, b: k._m_goal_progress(e, a, b)},
        ]

    @api.model
    def _measure_selection(self):
        return [(m['key'], m['name']) for m in self._measures()]

    def _measure_def(self):
        self.ensure_one()
        return next((m for m in self._measures() if m['key'] == self.auto_key), None)

    def _is_company_measure(self):
        m = self._measure_def()
        return bool(m and m['scope'] == 'company')

    def _measure(self, employee, date_from, date_to):
        """The computed value of this KPI for one person (or the company) over [date_from, date_to]."""
        self.ensure_one()
        m = self._measure_def()
        if not m:
            return 0.0
        emp = None if m['scope'] == 'company' else employee
        return float(m['fn'](self, emp, date_from, date_to) or 0.0)

    @api.onchange('auto_key')
    def _onchange_auto_key(self):
        m = self.auto_key and self._measure_def()
        if m:
            self.higher_is_better = m['higher']
            if not self.unit and m.get('unit'):
                self.unit = m['unit']

    # ------------------------------------------------------------------ the measures
    def _m_sales(self, employee, date_from, date_to, what):
        vouchers = self._sales_vouchers(employee, date_from, date_to)
        sign = lambda v: -1 if v.voucher_type == 'sale_return' else 1  # noqa: E731
        if what == 'amount':
            return sum(sign(v) * v.amount_untaxed for v in vouchers)
        return sum(sign(v) * l.quantity * litres_of(l.product_id) for v in vouchers for l in v.line_ids if l.product_id)

    def _m_production(self, date_from, date_to):
        if 'sb.stock.voucher.line' not in self.env:
            return 0.0
        lines = self.env['sb.stock.voucher.line'].sudo().search([
            ('voucher_id.voucher_type', '=', 'fprr'), ('voucher_id.state', '=', 'posted'),
            ('voucher_id.date', '>=', date_from), ('voucher_id.date', '<=', date_to),
            ('voucher_id.company_id', '=', self.company_id.id)])
        return sum(l.quantity * litres_of(l.product_id) for l in lines)

    def _m_on_time(self, date_from, date_to):
        """Delivery orders dated in the period: share fully delivered within N days (setting sb_perf.on_time_days, default 3)."""
        if 'sb.delivery.order' not in self.env:
            return 0.0
        days = int(self.env['ir.config_parameter'].sudo().get_param('sb_perf.on_time_days', 3) or 3)
        orders = self.env['sb.delivery.order'].sudo().search([('company_id', '=', self.company_id.id),
                                                               ('date', '>=', date_from), ('date', '<=', date_to)])
        if not orders:
            return 0.0
        good = 0
        for o in orders:
            done = o.batch_ids.filtered(lambda b: b.state == 'done')
            if o.state == 'done' and done and max(done.mapped('date')) <= o.date + timedelta(days=days):
                good += 1
        return round(good * 100.0 / len(orders), 2)

    def _m_overdue(self, employee, date_to):
        dom = [('account_id.account_type', '=', 'asset_receivable'), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
               ('amount_residual', '>', 0), ('date_maturity', '<', date_to), ('company_id', '=', self.company_id.id)]
        if employee is not None:
            customers = self.env['sale.order'].sudo().search([('user_id', '=', employee.user_id.id or -1)]).mapped(
                'partner_id.commercial_partner_id').ids
            dom.append(('partner_id', 'child_of', customers or [-1]))
        return sum(self.env['account.move.line'].sudo().search(dom).mapped('amount_residual'))

    def _m_goal_progress(self, employee, date_from, date_to):
        if employee is None:
            return 0.0
        goals = self.env['sb.perf.goal'].sudo().search([
            ('employee_id', '=', employee.id), ('status', '!=', 'dropped'),
            '|', ('date_from', '=', False), ('date_from', '<=', date_to), '|', ('date_to', '=', False), ('date_to', '>=', date_from)])
        return round(sum(goals.mapped('progress')) / len(goals), 2) if goals else 0.0

    def _sales_vouchers(self, employee, date_from, date_to):
        V = self.env['sb.voucher'].sudo()
        dom = [('voucher_type', 'in', ('sale', 'cash_sale', 'sale_return')), ('state', '=', 'posted'),
               ('date', '>=', date_from), ('date', '<=', date_to), ('company_id', '=', self.company_id.id)]
        if employee is None:
            return V.search(dom)
        partners = (employee.work_contact_id | employee.user_id.partner_id).ids
        dom += ['|', ('sb_sale_order_id.user_id', '=', employee.user_id.id or -1), ('sb_van_trip_id.salesperson_id', 'in', partners or [-1])]
        return V.search(dom)

    def _receipts(self, employee, date_from, date_to):
        V = self.env['sb.voucher'].sudo()
        dom = [('voucher_type', '=', 'receipt'), ('state', '=', 'posted'), ('date', '>=', date_from), ('date', '<=', date_to),
               ('company_id', '=', self.company_id.id)]
        if employee is None:
            return V.search(dom + [('partner_id.customer_rank', '>', 0)])
        trips = self.env['sb.van.trip'].sudo().search([('salesperson_id', 'in', (employee.work_contact_id | employee.user_id.partner_id).ids or [-1])]) \
            if 'sb.van.trip' in self.env else self.env['sb.voucher']
        van_receipts = trips.mapped('receipt_ids') if trips else V
        customers = self.env['sale.order'].sudo().search([('user_id', '=', employee.user_id.id or -1)]).mapped('partner_id').ids
        return V.search(dom + ['|', ('id', 'in', van_receipts.ids), ('partner_id', 'in', customers or [-1])])

    # ------------------------------------------------------------------ levels and scoring
    def _score(self, achievement):
        """(score 1-5, level name, colour) for an achievement ratio: the KPI's own levels, else its bands."""
        self.ensure_one()
        pct = achievement * 100.0
        levels = self.level_ids.sorted(lambda l: -l.min_pct)
        if levels:
            for lv in levels:
                if pct >= lv.min_pct - 1e-9:
                    return lv.score, lv.name, lv.color or LEVEL_COLOR.get(lv.score, '#6c757d')
            lv = levels[-1]
            return lv.score, lv.name, lv.color or LEVEL_COLOR.get(lv.score, '#6c757d')
        s = score_for(achievement, self.bands)
        return s, LEVEL_NAME.get(s, ''), LEVEL_COLOR.get(s, '#6c757d')

    def _sync_bands(self):
        for k in self.filtered('level_ids'):
            parts, levels = [], k.level_ids.sorted(lambda l: -l.min_pct)
            for lv in levels:
                if lv.min_pct > 0:
                    parts.append('>=%g=%d' % (lv.min_pct / 100.0, lv.score))
            low = levels[-1]
            positive = [lv.min_pct for lv in levels if lv.min_pct > 0]
            if low.min_pct <= 0 and positive:
                parts.append('<%g=%d' % (min(positive) / 100.0, low.score))
            bands = '; '.join(parts)
            if bands != k.bands:
                k.bands = bands

    def action_default_levels(self):
        """Give the KPI its own performance levels (the standard five), to adjust."""
        for k in self:
            k.level_ids.unlink()
            self.env['sb.perf.kpi.level'].create([{'kpi_id': k.id, 'sequence': i, 'min_pct': p, 'name': n, 'score': s, 'color': c}
                                                  for i, (p, n, s, c) in enumerate(DEFAULT_LEVELS)])
        return True

    @api.constrains('source', 'auto_key')
    def _check_measure(self):
        for k in self:
            if k.source == 'auto' and not k.auto_key:
                raise ValidationError(self.env._('Pick the measure that computes "%s".', k.name))

    # ------------------------------------------------------------------ periods
    def _applies_in(self, month_no):
        """Does this KPI get a result in fiscal month month_no (1-12)?"""
        self.ensure_one()
        if not month_no:
            return True
        if self.frequency == 'quarterly':
            return month_no % 3 == 0
        if self.frequency == 'yearly':
            return month_no == 12
        return True

    def _window(self, month):
        """Dates a computed measure covers for the period ending with this month."""
        self.ensure_one()
        start = month['start']
        n = {'quarterly': 3, 'yearly': 12}.get(self.frequency, 1)
        if n > 1 and month.get('fy_starts'):
            start = month['fy_starts'][max(0, month['no'] - n)]
        return start, month['end']


class SbPerfKpiLevel(models.Model):
    _name = 'sb.perf.kpi.level'
    _description = 'Performance level'
    _order = 'kpi_id, min_pct desc, id'

    kpi_id = fields.Many2one('sb.perf.kpi', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char('Level', required=True, help='e.g. Excellent, Very good, Good, Fair, Poor')
    min_pct = fields.Float('From % of target', required=True, help='Achievement from this % up to the next level.')
    score = fields.Integer('Score (1-5)', required=True, default=1)
    color = fields.Char('Colour', default='#6c757d')
    legacy_ref = fields.Char(index=True, copy=False)

    @api.constrains('score', 'min_pct')
    def _check(self):
        for lv in self:
            if not 1 <= lv.score <= 5:
                raise ValidationError(self.env._('A score is 1 to 5.'))
            if lv.min_pct < 0:
                raise ValidationError(self.env._('"From %" cannot be negative.'))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.kpi_id._sync_bands()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.kpi_id._sync_bands()
        return res

    def unlink(self):
        kpis = self.kpi_id
        res = super().unlink()
        kpis.exists()._sync_bands()
        return res


class SbPerfKpiAssign(models.Model):
    _name = 'sb.perf.kpi.assign'
    _description = 'KPI assignment'
    _order = 'kpi_id, id'

    kpi_id = fields.Many2one('sb.perf.kpi', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='kpi_id.company_id', store=True)
    employee_id = fields.Many2one('hr.employee', 'Person', help='Empty: everyone in the department (or position).')
    job_id = fields.Many2one('hr.job', 'Position')
    department_id = fields.Many2one('hr.department', 'Department')
    weight = fields.Float(default=1.0)
    target = fields.Float('Own target', help='Leave 0 to use the KPI target.')
    template_id = fields.Many2one('sb.perf.kpi.template', 'From template', ondelete='set null', index=True)
    legacy_ref = fields.Char(index=True, copy=False)

    @api.constrains('employee_id', 'job_id', 'department_id')
    def _check_scope(self):
        for a in self:
            if not (a.employee_id or a.job_id or a.department_id):
                raise ValidationError(self.env._('Assign "%s" to a person, a position or a department.', a.kpi_id.name))

    def _people(self):
        E = self.env['hr.employee'].sudo()
        emps = E.browse()
        for a in self:
            if a.employee_id:
                emps |= a.employee_id
            elif a.job_id:
                emps |= E.search([('job_id', '=', a.job_id.id)])
            elif a.department_id:
                emps |= E.search([('department_id', '=', a.department_id.id)])
        return emps

    def _touch_scores(self, emps):
        """Weights changed: re-score the stored results of the people concerned (periods that have one)."""
        if self.env.context.get('sb_perf_no_score') or not emps:
            return
        S = self.env['sb.perf.kpi.score'].sudo()
        by_month = defaultdict(set)
        for s in S.search([('employee_id', 'in', emps.ids)]):
            by_month[s.month].add(s.employee_id.id)
        for month, ids in by_month.items():
            S._refresh(emps.browse(list(ids)), month)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._touch_scores(recs._people())
        return recs

    def write(self, vals):
        before = self._people() if {'employee_id', 'job_id', 'department_id'} & set(vals) else self.browse()._people()
        res = super().write(vals)
        if {'employee_id', 'job_id', 'department_id', 'weight', 'target', 'kpi_id'} & set(vals):
            self._touch_scores(before | self._people())
        return res

    def unlink(self):
        emps = self._people()
        res = super().unlink()
        self.browse()._touch_scores(emps)
        return res


class SbPerfKpiTemplate(models.Model):
    """A scorecard for a position (job description) or a department: KPIs with weights and targets, applied as assignments."""
    _name = 'sb.perf.kpi.template'
    _description = 'KPI template'
    _order = 'name, id'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    job_id = fields.Many2one('hr.job', 'Position')
    department_id = fields.Many2one('hr.department', 'Department')
    description = fields.Text()
    active = fields.Boolean(default=True)
    line_ids = fields.One2many('sb.perf.kpi.template.line', 'template_id', 'KPIs', copy=True)
    weight_total = fields.Float('Total weight', compute='_compute_weight_total')
    assignment_count = fields.Integer('Assignments made', compute='_compute_assignment_count')
    legacy_ref = fields.Char(index=True, copy=False)

    @api.depends('line_ids.weight')
    def _compute_weight_total(self):
        for t in self:
            t.weight_total = sum(t.line_ids.mapped('weight'))

    def _compute_assignment_count(self):
        counts = dict(self.env['sb.perf.kpi.assign']._read_group([('template_id', 'in', self.ids)], ['template_id'], ['__count']))
        for t in self:
            t.assignment_count = counts.get(t, 0)

    def action_apply(self):
        """Assign the template's KPIs to its position (or else its department); applying again updates weights and targets."""
        A = self.env['sb.perf.kpi.assign']
        n = 0
        for t in self:
            if not (t.job_id or t.department_id):
                raise UserError(self.env._('Set the position or the department of "%s" first.', t.name))
            dept = False if t.job_id else t.department_id.id
            for line in t.line_ids:
                vals = {'weight': line.weight, 'target': line.target, 'template_id': t.id}
                a = A.search([('kpi_id', '=', line.kpi_id.id), ('employee_id', '=', False),
                              ('job_id', '=', t.job_id.id or False), ('department_id', '=', dept)], limit=1)
                if a:
                    a.write(vals)
                else:
                    A.create(dict(vals, kpi_id=line.kpi_id.id, job_id=t.job_id.id or False, department_id=dept))
                n += 1
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Template applied'), 'type': 'success', 'sticky': False,
                           'message': self.env._('%s KPI assignments made or updated.', n)}}

    def action_assignments(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Assignments'), 'res_model': 'sb.perf.kpi.assign',
                'view_mode': 'list,form', 'domain': [('template_id', '=', self.id)]}


class SbPerfKpiTemplateLine(models.Model):
    _name = 'sb.perf.kpi.template.line'
    _description = 'KPI template line'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one('sb.perf.kpi.template', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    kpi_id = fields.Many2one('sb.perf.kpi', 'KPI', required=True, ondelete='cascade', index=True)
    unit = fields.Char(related='kpi_id.unit')
    weight = fields.Float(default=1.0)
    target = fields.Float('Target', help='Leave 0 to use the KPI target.')
    legacy_ref = fields.Char(index=True, copy=False)


class SbPerfKpiValue(models.Model):
    _name = 'sb.perf.kpi.value'
    _description = 'KPI result for a period'
    _order = 'month desc, kpi_id'

    kpi_id = fields.Many2one('sb.perf.kpi', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='kpi_id.company_id', store=True)
    month = fields.Date('Period (first day)', required=True, index=True)
    target = fields.Float()
    actual = fields.Float()
    weight = fields.Float(default=1.0)
    achievement = fields.Float(compute='_compute_score', store=True)
    score = fields.Integer(compute='_compute_score', store=True)
    level = fields.Char('Performance level', compute='_compute_score', store=True)
    level_color = fields.Char(compute='_compute_score', store=True)
    note = fields.Char()
    source = fields.Selection([('manual', 'Entered'), ('auto', 'Computed'), ('import', 'Brought over')], default='manual')
    legacy_ref = fields.Char(index=True, copy=False)

    _uniq = models.Constraint('unique(kpi_id, employee_id, month)', 'One result per KPI, person and period.')

    @api.depends('target', 'actual', 'kpi_id.bands', 'kpi_id.higher_is_better', 'kpi_id.level_ids.min_pct',
                 'kpi_id.level_ids.score', 'kpi_id.level_ids.name', 'kpi_id.level_ids.color')
    def _compute_score(self):
        for v in self:
            if not v.target:
                v.achievement, v.score, v.level, v.level_color = 0.0, 0, False, False
                continue
            ach = achievement_of(v.actual, v.target, v.kpi_id.higher_is_better)
            v.achievement = ach
            v.score, v.level, v.level_color = v.kpi_id._score(ach)

    # keep the stored scorecards in step with the results
    def _touch_scores(self, extra=()):
        if self.env.context.get('sb_perf_no_score'):
            return
        pairs = defaultdict(set)
        for v in self:
            pairs[v.month].add(v.employee_id.id)
        for e, m in extra:
            if e:
                pairs[m].add(e.id)
        S = self.env['sb.perf.kpi.score'].sudo()
        for month, emp_ids in pairs.items():
            S._refresh(self.env['hr.employee'].sudo().browse(list(emp_ids)).exists(), month)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._touch_scores()
        return recs

    def write(self, vals):
        before = [(v.employee_id, v.month) for v in self] if {'employee_id', 'month'} & set(vals) else []
        res = super().write(vals)
        if {'actual', 'target', 'weight', 'employee_id', 'month', 'kpi_id'} & set(vals):
            self._touch_scores(before)
        return res

    def unlink(self):
        pairs = [(v.employee_id, v.month) for v in self]
        res = super().unlink()
        self.browse()._touch_scores(pairs)
        return res


class SbPerfKpiScore(models.Model):
    """A person's scorecard result for one period, kept so lists, history and reports need not recompute.
    points = sum(score x weight) over the KPIs with a result; max_points = 5 x sum(weight) of those;
    percent = points / max_points x 100; achievement = weighted average of actual / target."""
    _name = 'sb.perf.kpi.score'
    _description = 'Scorecard result'
    _order = 'month desc, percent desc, id'
    _rec_name = 'employee_id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, index=True)
    department_id = fields.Many2one('hr.department', index=True)
    job_id = fields.Many2one('hr.job', 'Position')
    month = fields.Date('Period (first day)', required=True, index=True)
    period = fields.Char()
    kpis = fields.Integer('KPIs assigned')
    results = fields.Integer('With a result')
    points = fields.Float(digits=(10, 3))
    max_points = fields.Float('Out of', digits=(10, 3))
    percent = fields.Float('Score %', digits=(5, 1))
    achievement = fields.Float('Achieved % (weighted)', digits=(7, 1))
    rating = fields.Char()
    source = fields.Selection([('computed', 'Computed'), ('import', 'Brought over')], default='computed')
    legacy_ref = fields.Char(index=True, copy=False)

    _uniq = models.Constraint('unique(employee_id, month)', 'One scorecard result per person and period.')

    @api.model
    def _refresh(self, employees, month_start):
        if not employees or not month_start:
            return
        P = self.env['sb.perf'].sudo()
        month = P._month_of(month_start)
        if not month:
            return
        for e in employees:
            sc = P._scorecard(e, month)
            rec = self.search([('employee_id', '=', e.id), ('month', '=', month_start)], limit=1)
            if not sc['rows'] and not rec:
                continue
            vals = {'company_id': e.company_id.id or self.env.company.id, 'department_id': e.department_id.id,
                    'job_id': e.job_id.id, 'period': month['label'], 'kpis': len(sc['rows']), 'results': sc['results'],
                    'points': sc['points'], 'max_points': sc['max_points'], 'percent': sc['total'] or 0.0,
                    'achievement': sc['achievement'] or 0.0, 'rating': sc['rating'], 'source': 'computed'}
            if rec:
                rec.write(vals)
            else:
                self.create(dict(vals, employee_id=e.id, month=month_start))

    @api.model
    def refresh_month(self, month_key=None):
        """Every active person, for one period (default: the current one)."""
        P = self.env['sb.perf'].sudo()
        month = P._month(month_key) if month_key else P._months()[-1]
        emps = P._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)])
        self._refresh(emps, month['start'])
        return True
