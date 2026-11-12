"""Performance app service for the OWL screens. Who sees what:
HR sees and runs everything; a manager sees and scores their team (everyone below them); an employee sees their own
goals, scorecard, reviews and check-ins, the public goals and the praise wall."""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

from .goal import STATUS
from .kpi import FREQUENCY
from .review import rating_label


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbPerf(models.AbstractModel):
    _name = 'sb.perf'
    _description = 'Performance app'

    # ------------------------------------------------------------------ access
    def _is_hr(self):
        return self.env.su or self.env.user.has_group('sonador_books.group_sb_hr')

    def _me(self):
        return self.env['sb.people']._me()

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    def _team(self, me=None):
        """Everyone below me (not me)."""
        me = me or self._me()
        if not me:
            return self._E().browse()
        return self._E().search([('id', 'child_of', me.id), ('id', '!=', me.id), ('active', '=', True)])

    def _can_manage(self, employee):
        return self._is_hr() or employee in self._team()

    def _can_see(self, employee):
        return self._can_manage(employee) or employee == self._me()

    def _card(self, e):
        return self.env['sb.people']._card(e) if e else False

    # ------------------------------------------------------------------ periods (Ethiopian months)
    def _months(self, around=None):
        today = around or fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        out = []
        for year in (fy - 1, fy):
            periods = fiscal_periods(year)
            starts = [p[2] for p in periods]
            for no, name, start, end in periods:
                if start <= today:
                    out.append({'key': _s(start), 'start': start, 'end': end, 'label': name, 'no': no, 'fy_starts': starts})
        return out[-15:]

    def _month_of(self, start):
        """The period that starts on this date (any fiscal year), or None."""
        start = fields.Date.to_date(start)
        periods = fiscal_periods(fiscal_year_of(start))
        starts = [p[2] for p in periods]
        for no, name, s, end in periods:
            if s == start:
                return {'key': _s(s), 'start': s, 'end': end, 'label': name, 'no': no, 'fy_starts': starts}
        return None

    def _month(self, key):
        for m in self._months(fields.Date.to_date(key) + timedelta(days=40) if key else None):
            if m['key'] == key:
                return m
        m = self._months()[-1]
        return m

    @api.model
    def ctx(self):
        me = self._me()
        team = self._team(me)
        is_hr = self._is_hr()
        people = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)], order='name') if is_hr else (team | me)
        months = self._months()
        return {
            'is_hr': is_hr, 'me': me.id or False, 'is_manager': bool(team), 'team_ids': team.ids,
            'people': [{'id': e.id, 'name': e.name, 'department': e.department_id.name or '', 'job': e.job_title or ''}
                       for e in people],
            'everyone': [{'id': e.id, 'name': e.name} for e in self._E().search(
                [('company_id', '=', self.env.company.id), ('active', '=', True)], order='name')],
            'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search(
                [('company_id', 'in', (self.env.company.id, False))], order='name')],
            'months': [{'key': m['key'], 'label': m['label']} for m in reversed(months)],
            'status': [{'id': k, 'name': v} for k, v in STATUS],
            'auto': [{'id': m['key'], 'name': m['name'], 'scope': m['scope'], 'higher': m['higher'], 'unit': m.get('unit') or ''}
                     for m in self.env['sb.perf.kpi']._measures()],
            'frequency': [{'id': k, 'name': v} for k, v in FREQUENCY],
            'kpi_list': [{'id': k.id, 'name': k.name, 'unit': k.unit or ''} for k in self.env['sb.perf.kpi'].sudo().search(
                self._kpi_setup_domain() + [('active', '=', True)])],
            'jobs': [{'id': j.id, 'name': j.name} for j in self.env['hr.job'].sudo().search(
                [('company_id', 'in', (self.env.company.id, False))], order='name')] if is_hr else [],
            'today': _s(fields.Date.context_today(self)),
            'week': _s(self._week_start()),
        }

    def _week_start(self, d=None):
        d = d or fields.Date.context_today(self)
        return d - timedelta(days=d.weekday())

    # ------------------------------------------------------------------ overview
    @api.model
    def overview(self):
        me = self._me()
        team = self._team(me)
        out = {'me': self._card(me), 'team_size': len(team)}
        months = self._months()
        cur = months[-1]
        prev = months[-2] if len(months) > 1 else cur
        if me:
            out['my_goals'] = [self._goal_dict(g) for g in self._goals_of(me)][:6]
            out['my_score'] = self._scorecard(me, prev)['total']
            out['my_score_month'] = prev['label']
            out['my_reviews'] = [self._review_brief(r) for r in self.env['sb.perf.review'].sudo().search([
                ('employee_id', '=', me.id), ('cycle_id.state', 'in', ('open', 'calibration', 'closed'))], limit=6)]
            out['todo'] = [self._review_brief(r) for r in self.env['sb.perf.review'].sudo().search([
                ('reviewer_id', '=', me.id), ('state', '=', 'draft'), ('cycle_id.state', '=', 'open')])]
            ci = self.env['sb.perf.checkin'].sudo().search([('employee_id', '=', me.id), ('week', '=', self._week_start())], limit=1)
            out['checkin_done'] = bool(ci)
        if team:
            scores = [(e, self._scorecard(e, prev)['total']) for e in team]
            out['team'] = [dict(self._card(e), score=s) for e, s in sorted(scores, key=lambda x: -(x[1] or 0))][:12]
            out['team_checkins'] = self.env['sb.perf.checkin'].sudo().search_count([
                ('employee_id', 'in', team.ids), ('week', '=', self._week_start())])
        out['company_goals'] = [self._goal_dict(g) for g in self.env['sb.perf.goal'].sudo().search([
            ('company_id', '=', self.env.company.id), ('level', '=', 'company'), ('status', '!=', 'dropped')])]
        out['wall'] = self.wall(limit=8)
        if self._is_hr():
            cycles = self.env['sb.perf.cycle'].sudo().search([('company_id', '=', self.env.company.id), ('state', '!=', 'closed')])
            out['cycles'] = [self._cycle_dict(c) for c in cycles]
            out['dept_scores'] = self._dept_scores(prev)
        return out

    def _dept_scores(self, month):
        by = defaultdict(list)
        for sc in self.env['sb.perf.kpi.score'].sudo().search([('company_id', '=', self.env.company.id), ('month', '=', month['start']),
                                                                ('results', '>', 0), ('employee_id.active', '=', True)]):
            by[sc.employee_id.department_id.name or 'No department'].append(sc.percent)
        return sorted([{'name': k, 'score': round(sum(v) / len(v), 1), 'people': len(v)} for k, v in by.items()],
                      key=lambda x: -x['score'])

    # ------------------------------------------------------------------ goals
    def _goal_dict(self, g, deep=False):
        d = {'id': g.id, 'name': g.name, 'level': g.level, 'status': g.status, 'progress': g.progress,
             'owner': self._card(g.employee_id) if g.employee_id else False, 'department': g.department_id.name or '',
             'department_id': g.department_id.id, 'parent_id': g.parent_id.id, 'parent': g.parent_id.name or '',
             'date_from': _s(g.date_from), 'date_to': _s(g.date_to),
             'due_ec': format_ethiopian(g.date_to) if g.date_to else '', 'private': g.private,
             'description': g.description or '', 'progress_manual': g.progress_manual,
             'krs': [{'id': k.id, 'name': k.name, 'start_value': k.start_value, 'target_value': k.target_value,
                      'current_value': k.current_value, 'unit': k.unit or '', 'progress': k.progress,
                      'measure': k.measure, 'kpi_id': k.kpi_id.id or '', 'kpi': k.kpi_id.name or '', 'rollup': k.rollup,
                      'source_note': k.source_note or ''} for k in g.kr_ids],
             'can_edit': self._goal_can_edit(g)}
        return d

    def _goal_can_edit(self, g):
        if self._is_hr():
            return True
        me = self._me()
        if g.level == 'person':
            return g.employee_id == me or (g.employee_id and g.employee_id in self._team(me))
        if g.level == 'department':
            return bool(me and g.department_id and g.department_id.manager_id == me)
        return False

    def _goal_visible(self, g):
        if not g.private:
            return True
        return self._is_hr() or (g.employee_id and self._can_see(g.employee_id))

    def _goals_of(self, e):
        return self.env['sb.perf.goal'].sudo().search([('employee_id', '=', e.id), ('status', '!=', 'dropped')], order='date_to')

    @api.model
    def goals(self, scope='all', employee_id=None):
        G = self.env['sb.perf.goal'].sudo()
        dom = [('company_id', '=', self.env.company.id)]
        if scope == 'mine':
            dom.append(('employee_id', '=', self._me().id or -1))
        elif scope == 'team':
            dom.append(('employee_id', 'in', self._team().ids or [-1]))
        elif scope == 'person' and employee_id:
            dom.append(('employee_id', '=', int(employee_id)))
        goals = G.search(dom).filtered(self._goal_visible)
        return {'goals': [self._goal_dict(g) for g in goals],
                'parents': [{'id': g.id, 'name': g.name, 'level': g.level} for g in G.search(
                    [('company_id', '=', self.env.company.id), ('level', 'in', ('company', 'department')),
                     ('status', 'not in', ('dropped',))])]}

    @api.model
    def goal_save(self, vals):
        G = self.env['sb.perf.goal'].sudo()
        goal = G.browse(int(vals['id'])) if vals.get('id') else G
        me = self._me()
        level = vals.get('level') or (goal.level if goal else 'person')
        owner = self._E().browse(int(vals['employee_id'])) if vals.get('employee_id') else (goal.employee_id if goal else me)
        if goal and not self._goal_can_edit(goal):
            raise AccessError(self.env._('You cannot change this goal.'))
        if level == 'company' and not self._is_hr():
            raise AccessError(self.env._('Company goals are set by HR / management.'))
        if level == 'person' and owner != me and not self._can_manage(owner):
            raise AccessError(self.env._('You can set goals for yourself and your team.'))
        if not (vals.get('name') or '').strip():
            raise ValidationError(self.env._('Name the goal.'))
        data = {'name': vals['name'].strip(), 'description': vals.get('description') or False, 'level': level,
                'employee_id': owner.id if level == 'person' else False,
                'department_id': int(vals['department_id']) if vals.get('department_id') else (
                    owner.department_id.id if level == 'person' and owner else False),
                'parent_id': int(vals['parent_id']) if vals.get('parent_id') else False,
                'date_from': vals.get('date_from') or False, 'date_to': vals.get('date_to') or False,
                'status': vals.get('status') or 'on_track', 'private': bool(vals.get('private')),
                'progress_manual': float(vals.get('progress_manual') or 0.0)}
        if goal:
            goal.write(data)
        else:
            goal = G.create(data)
        krs = vals.get('krs') or []
        keep = self.env['sb.perf.kr']
        for k in krs:
            if not (k.get('name') or '').strip():
                continue
            kv = {'name': k['name'].strip(), 'start_value': float(k.get('start_value') or 0), 'target_value': float(k.get('target_value') or 0),
                  'current_value': float(k.get('current_value') or 0), 'unit': k.get('unit') or False,
                  'measure': k.get('measure') or 'manual', 'rollup': k.get('rollup') or 'sum',
                  'kpi_id': int(k['kpi_id']) if k.get('kpi_id') and (k.get('measure') == 'kpi') else False}
            if kv['measure'] != 'manual':
                kv.pop('current_value')
            kr = goal.kr_ids.filtered(lambda x: x.id == k.get('id'))
            if kr:
                kr.write(kv)
            else:
                kr = self.env['sb.perf.kr'].sudo().create(dict(kv, goal_id=goal.id))
            keep |= kr
        (goal.kr_ids - keep).unlink()
        goal.kr_ids.filtered(lambda x: x.measure != 'manual')._refresh()
        return self._goal_dict(goal)

    @api.model
    def goal_delete(self, goal_id):
        g = self.env['sb.perf.goal'].sudo().browse(int(goal_id))
        if not self._goal_can_edit(g):
            raise AccessError(self.env._('You cannot remove this goal.'))
        g.status = 'dropped'
        return True

    # ------------------------------------------------------------------ KPI scorecards
    def _assignments(self, e):
        A = self.env['sb.perf.kpi.assign'].sudo()
        dom = [('kpi_id.active', '=', True), ('kpi_id.company_id', '=', e.company_id.id), '|', '|',
               ('employee_id', '=', e.id),
               '&', ('employee_id', '=', False), ('job_id', '=', e.job_id.id or -1),
               '&', '&', ('employee_id', '=', False), ('job_id', '=', False), ('department_id', '=', e.department_id.id or -1)]
        res = {}
        for a in A.search(dom):  # a personal assignment wins over the position and department ones
            rank = 0 if a.employee_id else 1 if a.job_id else 2
            if a.kpi_id.id not in res or rank < res[a.kpi_id.id][0]:
                res[a.kpi_id.id] = (rank, a)
        return [a for _r, a in res.values()]

    def _scorecard(self, e, month, compute=False):
        """Rows of the person's KPIs for the period, and the total: sum(score x weight) / (5 x sum(weight)) over the KPIs
        that have a result (quarterly / yearly KPIs only in the last month of the quarter / year)."""
        V = self.env['sb.perf.kpi.value'].sudo()
        rows, total_w, total, ach_sum = [], 0.0, 0.0, 0.0
        for a in sorted(self._assignments(e), key=lambda a: (a.kpi_id.sequence, a.kpi_id.id)):
            k = a.kpi_id
            if not k._applies_in(month.get('no')):
                continue
            v = V.search([('kpi_id', '=', k.id), ('employee_id', '=', e.id), ('month', '=', month['start'])], limit=1)
            target = a.target or k.target
            if compute and k.source == 'auto':
                actual = k._measure(e, *k._window(month))
                if v:
                    v.write({'actual': actual, 'target': target, 'weight': a.weight, 'source': 'auto'})
                else:
                    v = V.create({'kpi_id': k.id, 'employee_id': e.id, 'month': month['start'], 'actual': actual,
                                  'target': target, 'weight': a.weight, 'source': 'auto'})
            row = {'kpi_id': k.id, 'name': k.name, 'unit': k.unit or '', 'source': k.source, 'auto_key': k.auto_key or '',
                   'frequency': k.frequency, 'higher_is_better': k.higher_is_better, 'how': k.how_measured or '',
                   'weight': a.weight, 'target': v.target if v else target, 'actual': v.actual if v else None,
                   'achievement': round(v.achievement * 100.0, 1) if v and v.target else None, 'score': v.score if v else None,
                   'level': (v.level or '') if v else '', 'level_color': (v.level_color or '') if v else '',
                   'note': (v.note or '') if v else '', 'value_id': v.id if v else False}
            rows.append(row)
            if v and v.target:
                total_w += a.weight
                total += v.score * a.weight
                ach_sum += v.achievement * a.weight
        pct = round(total / (5.0 * total_w) * 100.0, 1) if total_w else None
        return {'rows': rows, 'total': pct, 'rating': rating_label(pct) if pct is not None else '',
                'weight': sum(r['weight'] for r in rows), 'points': total, 'max_points': 5.0 * total_w,
                'results': sum(1 for r in rows if r['value_id'] and r['target']),
                'achievement': round(ach_sum / total_w * 100.0, 1) if total_w else None}

    @api.model
    def scorecard(self, employee_id, month_key=None):
        e = self._E().browse(int(employee_id))
        if not self._can_see(e):
            raise AccessError(self.env._('Not allowed.'))
        month = self._month(month_key) if month_key else self._months()[-1]
        sc = self._scorecard(e, month)
        trend = []
        for m in self._months()[-12:]:
            trend.append({'label': m['label'].split(' ')[0], 'score': self._scorecard(e, m)['total']})
        return dict(sc, employee=self._card(e), month={'key': month['key'], 'label': month['label']}, trend=trend,
                    can_edit=self._can_manage(e))

    @api.model
    def scorecard_save(self, employee_id, month_key, rows):
        e = self._E().browse(int(employee_id))
        if not self._can_manage(e):
            raise AccessError(self.env._('Results are entered by the manager or HR.'))
        month = self._month(month_key)
        V = self.env['sb.perf.kpi.value'].sudo()
        assigns = {a.kpi_id.id: a for a in self._assignments(e)}
        for r in rows:
            a = assigns.get(int(r['kpi_id']))
            if not a or a.kpi_id.source == 'auto' or r.get('actual') in (None, ''):
                continue
            if not a.kpi_id._applies_in(month.get('no')):
                continue
            vals = {'actual': float(r['actual']), 'target': float(r.get('target') or a.target or a.kpi_id.target),
                    'weight': a.weight, 'note': r.get('note') or False, 'source': 'manual'}
            v = V.search([('kpi_id', '=', a.kpi_id.id), ('employee_id', '=', e.id), ('month', '=', month['start'])], limit=1)
            if v:
                v.write(vals)
            else:
                V.create(dict(vals, kpi_id=a.kpi_id.id, employee_id=e.id, month=month['start']))
        return self.scorecard(e.id, month['key'])

    @api.model
    def scorecard_compute(self, month_key=None, employee_id=None):
        """Automatic KPIs for the month (everyone, or one person)."""
        month = self._month(month_key) if month_key else self._months()[-1]
        emps = self._E().browse(int(employee_id)) if employee_id else self._E().search(
            [('company_id', '=', self.env.company.id), ('active', '=', True)])
        if not self._is_hr() and any(not self._can_see(e) for e in emps):
            raise AccessError(self.env._('Not allowed.'))
        n = 0
        for e in emps:
            if any(a.kpi_id.source == 'auto' for a in self._assignments(e)):
                self._scorecard(e, month, compute=True)
                n += 1
        return n

    def _kpi_setup_domain(self):
        """KPIs in use: assigned, or made here; the rest of the library (only in templates) lives under KPI Library."""
        in_templates = self.env['sb.perf.kpi.template.line'].sudo().search([]).mapped('kpi_id').ids
        return [('company_id', '=', self.env.company.id), '|', ('assignment_ids', '!=', False), ('id', 'not in', in_templates)]

    @api.model
    def kpis(self):
        K = self.env['sb.perf.kpi'].sudo()
        out = []
        for k in K.search(self._kpi_setup_domain()):
            out.append({'id': k.id, 'name': k.name, 'department_id': k.department_id.id, 'department': k.department_id.name or '',
                        'unit': k.unit or '', 'target': k.target, 'source': k.source, 'auto_key': k.auto_key or '',
                        'bands': k.bands, 'higher_is_better': k.higher_is_better, 'active': k.active,
                        'code': k.code or '', 'frequency': k.frequency, 'how_measured': k.how_measured or '',
                        'levels': [{'name': lv.name, 'min_pct': lv.min_pct, 'score': lv.score, 'color': lv.color or ''}
                                   for lv in k.level_ids.sorted(lambda l: -l.min_pct)],
                        'assign': [{'id': a.id, 'employee_id': a.employee_id.id, 'employee': a.employee_id.name or '',
                                    'department_id': a.department_id.id, 'department': a.department_id.name or '',
                                    'job_id': a.job_id.id, 'job': a.job_id.name or '', 'weight': a.weight, 'target': a.target}
                                   for a in k.assignment_ids]})
        return out

    @api.model
    def kpi_save(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('KPIs are set by HR.'))
        K = self.env['sb.perf.kpi'].sudo()
        k = K.browse(int(vals['id'])) if vals.get('id') else K
        data = {'name': vals['name'], 'department_id': int(vals['department_id']) if vals.get('department_id') else False,
                'unit': vals.get('unit') or False, 'target': float(vals.get('target') or 0), 'source': vals.get('source') or 'manual',
                'auto_key': vals.get('auto_key') or False, 'bands': vals.get('bands') or K._fields['bands'].default(K),
                'higher_is_better': bool(vals.get('higher_is_better', True)), 'active': bool(vals.get('active', True)),
                'code': vals.get('code') or False, 'frequency': vals.get('frequency') or 'monthly',
                'how_measured': vals.get('how_measured') or False}
        if k and k.level_ids:
            data.pop('bands')  # the KPI's own performance levels set its bands
        k = k.write(data) and k if k else K.create(data)
        keep = self.env['sb.perf.kpi.assign']
        for a in vals.get('assign') or []:
            av = {'employee_id': int(a['employee_id']) if a.get('employee_id') else False,
                  'department_id': int(a['department_id']) if a.get('department_id') else False,
                  'job_id': int(a['job_id']) if a.get('job_id') else False,
                  'weight': float(a.get('weight') or 1.0), 'target': float(a.get('target') or 0)}
            if not (av['employee_id'] or av['department_id'] or av['job_id']):
                continue
            rec = k.assignment_ids.filtered(lambda x: x.id == a.get('id'))
            if rec:
                rec.write(av)
            else:
                rec = self.env['sb.perf.kpi.assign'].sudo().create(dict(av, kpi_id=k.id))
            keep |= rec
        (k.assignment_ids - keep).unlink()
        return self.kpis()

    # ------------------------------------------------------------------ review cycles
    def _cycle_dict(self, c):
        revs = c.review_ids
        mgr = revs.filtered(lambda r: r.role == 'manager')
        return {'id': c.id, 'name': c.name, 'kind': c.kind, 'state': c.state, 'date_from': _s(c.date_from), 'date_to': _s(c.date_to),
                'period_ec': '%s - %s' % (format_ethiopian(c.date_from), format_ethiopian(c.date_to)),
                'deadline': _s(c.deadline), 'template': c.template_id.name, 'template_id': c.template_id.id,
                'people': len(set(revs.mapped('employee_id').ids)), 'done': len(revs.filtered(lambda r: r.state != 'draft')),
                'total': len(revs), 'avg': round(sum(mgr.filtered('score_pct').mapped('score_pct')) / len(mgr.filtered('score_pct')), 1)
                if mgr.filtered('score_pct') else None, 'self_review': c.self_review, 'peer_review': c.peer_review}

    def _review_brief(self, r):
        return {'id': r.id, 'cycle': r.cycle_id.name, 'cycle_id': r.cycle_id.id, 'role': r.role, 'state': r.state,
                'employee': self._card(r.employee_id), 'reviewer': r.reviewer_id.name or '', 'score': r.score_pct or None,
                'rating': r.rating or '', 'potential': r.potential or False, 'raise': r.pay_raise_pct}

    @api.model
    def cycles(self):
        C = self.env['sb.perf.cycle'].sudo()
        dom = [('company_id', '=', self.env.company.id)]
        if not self._is_hr():
            dom.append(('state', '!=', 'draft'))
        return {'cycles': [self._cycle_dict(c) for c in C.search(dom)],
                'templates': [{'id': t.id, 'name': t.name, 'criteria': len(t.criterion_ids)} for t in
                              self.env['sb.perf.template'].sudo().search([])]}

    @api.model
    def cycle_create(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('Review cycles are run by HR.'))
        c = self.env['sb.perf.cycle'].sudo().create({
            'name': vals['name'], 'kind': vals.get('kind') or 'bi_annual', 'date_from': vals['date_from'], 'date_to': vals['date_to'],
            'deadline': vals.get('deadline') or False, 'template_id': int(vals['template_id']),
            'self_review': bool(vals.get('self_review', True)), 'peer_review': bool(vals.get('peer_review'))})
        emps = self._E().search([('company_id', '=', c.company_id.id), ('active', '=', True)])
        if vals.get('department_id'):
            emps = emps.filtered(lambda e: e.department_id.id == int(vals['department_id']))
        for e in emps:
            self._make_review(c, e, 'manager', e.parent_id)
            if c.self_review:
                self._make_review(c, e, 'self', e)
        return self._cycle_dict(c)

    def _make_review(self, cycle, employee, role, reviewer):
        managerial = bool(employee.child_ids.filtered('active'))
        lines = [{'criterion_id': cr.id, 'name': cr.name, 'weight': cr.weight, 'sequence': cr.sequence}
                 for cr in cycle.template_id.criterion_ids if cr.applies == 'all' or cr.applies == ('manager' if managerial else 'staff')]
        return self.env['sb.perf.review'].sudo().create({
            'cycle_id': cycle.id, 'employee_id': employee.id, 'role': role, 'reviewer_id': reviewer.id if reviewer else False,
            'line_ids': [(0, 0, l) for l in lines]})

    @api.model
    def cycle_state(self, cycle_id, state):
        if not self._is_hr():
            raise AccessError(self.env._('Review cycles are run by HR.'))
        c = self.env['sb.perf.cycle'].sudo().browse(int(cycle_id))
        c.state = state
        return self.cycle(c.id)

    @api.model
    def cycle(self, cycle_id):
        c = self.env['sb.perf.cycle'].sudo().browse(int(cycle_id))
        me = self._me()
        revs = c.review_ids
        if not self._is_hr():
            revs = revs.filtered(lambda r: r.employee_id == me or r.reviewer_id == me or r.employee_id in self._team(me))
        by_emp = defaultdict(dict)
        for r in revs:
            by_emp[r.employee_id][r.role if r.role != 'peer' else 'peer_%d' % r.id] = self._review_brief(r)
        rows = []
        for e, d in sorted(by_emp.items(), key=lambda kv: kv[0].name):
            rows.append({'employee': self._card(e), 'manager': d.get('manager'), 'self': d.get('self'),
                         'peers': [v for k, v in d.items() if k.startswith('peer_')]})
        return {'cycle': self._cycle_dict(c), 'rows': rows, 'nine_box': self._nine_box(c, revs), 'is_hr': self._is_hr()}

    def _nine_box(self, cycle, revs):
        cells = defaultdict(list)
        for r in revs.filtered(lambda r: r.role == 'manager' and r.score_pct and r.potential):
            perf = 3 if r.score_pct >= 80 else 2 if r.score_pct >= 60 else 1
            cells['%s%s' % (r.potential, perf)].append(self._card(r.employee_id) | {'score': r.score_pct})
        return cells

    @api.model
    def review(self, review_id):
        r = self.env['sb.perf.review'].sudo().browse(int(review_id))
        me = self._me()
        can_score = r.state == 'draft' and (self._is_hr() or r.reviewer_id == me or (r.role == 'manager' and r.employee_id in self._team(me)))
        if not (can_score or self._can_see(r.employee_id) or r.reviewer_id == me):
            raise AccessError(self.env._('Not allowed.'))
        if r.role == 'self' and r.employee_id != me and r.state == 'draft' and not self._is_hr():
            raise AccessError(self.env._('The self review is visible once it is submitted.'))
        others = r.cycle_id.review_ids.filtered(lambda x: x.employee_id == r.employee_id and x != r and x.state != 'draft')
        kpi = self._scorecard(r.employee_id, self._months(r.cycle_id.date_to)[-1]) if r.cycle_id.date_to else None
        return dict(self._review_brief(r), cycle_state=r.cycle_id.state, can_score=can_score,
                    can_acknowledge=r.role == 'manager' and r.state == 'submitted' and r.employee_id == me,
                    lines=[{'id': l.id, 'name': l.name, 'weight': l.weight, 'score': l.score or 0, 'comment': l.comment or ''}
                           for l in r.line_ids],
                    strengths=r.strengths or '', improvements=r.improvements or '', comment=r.comment or '',
                    employee_comment=r.employee_comment or '', source=r.source or '',
                    others=[dict(self._review_brief(o), lines=[{'name': l.name, 'score': l.score} for l in o.line_ids]) for o in others],
                    goals=[self._goal_dict(g) for g in self._goals_of(r.employee_id)],
                    kpi=kpi)

    @api.model
    def review_save(self, review_id, vals, submit=False):
        r = self.env['sb.perf.review'].sudo().browse(int(review_id))
        me = self._me()
        if r.state != 'draft' or not (self._is_hr() or r.reviewer_id == me or (r.role == 'manager' and r.employee_id in self._team(me))):
            raise AccessError(self.env._('This review cannot be changed.'))
        for l in vals.get('lines') or []:
            line = r.line_ids.filtered(lambda x: x.id == l['id'])
            score = int(l.get('score') or 0)
            if score and not 1 <= score <= 5:
                raise ValidationError(self.env._('Scores are 1 to 5.'))
            line.write({'score': score, 'comment': l.get('comment') or False})
        data = {k: vals.get(k) or False for k in ('strengths', 'improvements', 'comment') if k in vals}
        if r.role == 'manager':
            if 'potential' in vals:
                data['potential'] = vals.get('potential') or False
            if 'raise' in vals:
                data['pay_raise_pct'] = float(vals.get('raise') or 0)
        r.write(data)
        if submit:
            if any(not l.score for l in r.line_ids):
                raise ValidationError(self.env._('Score every line (1 to 5) before submitting.'))
            r.write({'state': 'submitted', 'submitted_on': fields.Date.context_today(self)})
        return self.review(r.id)

    @api.model
    def review_acknowledge(self, review_id, comment=''):
        r = self.env['sb.perf.review'].sudo().browse(int(review_id))
        if r.employee_id != self._me() or r.state != 'submitted':
            raise AccessError(self.env._('Only the employee acknowledges their review.'))
        r.write({'state': 'acknowledged', 'acknowledged_on': fields.Date.context_today(self), 'employee_comment': comment or False})
        return self.review(r.id)

    @api.model
    def apply_raises(self, cycle_id, effective):
        """Pay review: the proposed raises of the cycle become new pay versions from the date (People history)."""
        if not self._is_hr():
            raise AccessError(self.env._('Pay changes are made by HR.'))
        c = self.env['sb.perf.cycle'].sudo().browse(int(cycle_id))
        n = 0
        for r in c.review_ids.filtered(lambda r: r.role == 'manager' and r.pay_raise_pct):
            v = r.employee_id.current_version_id
            new = round((v.wage or 0.0) * (1 + r.pay_raise_pct / 100.0), 2)
            self.env['sb.people'].job_change(r.employee_id.id, {'date': effective, 'wage': new, 'reason': 'salary'})
            n += 1
        return n

    # ------------------------------------------------------------------ check-ins
    @api.model
    def checkins(self, week=None):
        week = fields.Date.to_date(week) if week else self._week_start()
        me = self._me()
        C = self.env['sb.perf.checkin'].sudo()
        mine = C.search([('employee_id', '=', me.id or -1), ('week', '=', week)], limit=1)
        team = self._team(me) if not self._is_hr() else self._team(me)
        rows = []
        for e in team:
            c = C.search([('employee_id', '=', e.id), ('week', '=', week)], limit=1)
            rows.append({'employee': self._card(e), 'checkin': self._ci(c) if c else False})
        history = [self._ci(c) for c in C.search([('employee_id', '=', me.id or -1)], limit=8)]
        return {'week': _s(week), 'week_ec': format_ethiopian(week), 'mine': self._ci(mine) if mine else False,
                'team': rows, 'history': history}

    def _ci(self, c):
        return {'id': c.id, 'week': _s(c.week), 'week_ec': format_ethiopian(c.week), 'mood': c.mood, 'wins': c.wins or '',
                'blockers': c.blockers or '', 'priorities': c.priorities or '', 'reply': c.manager_reply or '',
                'replied_by': c.replied_by.name or ''}

    @api.model
    def checkin_save(self, vals):
        me = self._me()
        if not me:
            raise UserError(self.env._('Your login is not linked to an employee.'))
        week = fields.Date.to_date(vals.get('week')) if vals.get('week') else self._week_start()
        C = self.env['sb.perf.checkin'].sudo()
        c = C.search([('employee_id', '=', me.id), ('week', '=', week)], limit=1)
        data = {k: vals.get(k) or False for k in ('mood', 'wins', 'blockers', 'priorities')}
        if c:
            c.write(data)
        else:
            C.create(dict(data, employee_id=me.id, week=week))
        return self.checkins(_s(week))

    @api.model
    def checkin_reply(self, checkin_id, text):
        c = self.env['sb.perf.checkin'].sudo().browse(int(checkin_id))
        if not self._can_manage(c.employee_id):
            raise AccessError(self.env._('Only their manager replies.'))
        c.write({'manager_reply': text, 'replied_by': self._me().id or False})
        return self.checkins(_s(c.week))

    # ------------------------------------------------------------------ one-to-ones
    @api.model
    def one2ones(self):
        me = self._me()
        M = self.env['sb.perf.one2one'].sudo()
        meetings = M.search(['|', ('employee_id', '=', me.id or -1), ('manager_id', '=', me.id or -1)], limit=40)
        return {'meetings': [self._mtg(m) for m in meetings],
                'with': [self._card(e) for e in (self._E().search([('parent_id', '=', me.id or -1), ('active', '=', True)]) | me.parent_id)]}

    def _mtg(self, m):
        return {'id': m.id, 'date': _s(m.date), 'date_ec': format_ethiopian(m.date), 'done': m.done, 'notes': m.notes or '',
                'employee': self._card(m.employee_id), 'manager': self._card(m.manager_id),
                'items': [{'id': i.id, 'kind': i.kind, 'name': i.name, 'done': i.done, 'owner': i.owner_id.name or ''}
                          for i in m.item_ids]}

    @api.model
    def one2one_save(self, vals):
        me = self._me()
        M = self.env['sb.perf.one2one'].sudo()
        m = M.browse(int(vals['id'])) if vals.get('id') else M
        if m and me not in (m.employee_id | m.manager_id) and not self._is_hr():
            raise AccessError(self.env._('Not your one-to-one.'))
        if not m:
            other = self._E().browse(int(vals['with_id']))
            if other.parent_id == me:
                emp, mgr = other, me
            elif me.parent_id == other:
                emp, mgr = me, other
            else:
                raise AccessError(self.env._('One-to-ones are between a person and their manager.'))
            m = M.create({'employee_id': emp.id, 'manager_id': mgr.id, 'date': vals.get('date') or fields.Date.context_today(self)})
        m.write({'notes': vals.get('notes') or False, 'done': bool(vals.get('done')), 'date': vals.get('date') or m.date})
        keep = self.env['sb.perf.one2one.item']
        for i in vals.get('items') or []:
            if not (i.get('name') or '').strip():
                continue
            rec = m.item_ids.filtered(lambda x: x.id == i.get('id'))
            iv = {'kind': i.get('kind') or 'agenda', 'name': i['name'].strip(), 'done': bool(i.get('done'))}
            if rec:
                rec.write(iv)
            else:
                rec = self.env['sb.perf.one2one.item'].sudo().create(dict(iv, meeting_id=m.id))
            keep |= rec
        (m.item_ids - keep).unlink()
        return self._mtg(m)

    # ------------------------------------------------------------------ praise and feedback
    @api.model
    def wall(self, limit=30, employee_id=None):
        F = self.env['sb.perf.feedback'].sudo()
        me = self._me()
        dom = [('company_id', '=', self.env.company.id)]
        if employee_id:
            dom.append(('to_id', '=', int(employee_id)))
        items = F.search(dom, limit=200)
        out = []
        for f in items:
            if f.visibility == 'private' and not (self._is_hr() or me in (f.to_id | f.from_id) or self._can_manage(f.to_id)):
                continue
            out.append({'id': f.id, 'kind': f.kind, 'value': dict(f._fields['value'].selection).get(f.value, '') if f.value else '',
                        'text': f.text, 'visibility': f.visibility, 'from': self._card(f.from_id), 'to': self._card(f.to_id),
                        'date': _s(f.create_date.date()), 'date_ec': format_ethiopian(f.create_date.date())})
            if len(out) >= limit:
                break
        return out

    @api.model
    def give_feedback(self, vals):
        me = self._me()
        if not me:
            raise UserError(self.env._('Your login is not linked to an employee.'))
        to = self._E().browse(int(vals['to_id']))
        if to == me:
            raise ValidationError(self.env._('Praise someone else.'))
        if not (vals.get('text') or '').strip():
            raise ValidationError(self.env._('Write something.'))
        kind = vals.get('kind') or 'praise'
        self.env['sb.perf.feedback'].sudo().create({
            'kind': kind, 'from_id': me.id, 'to_id': to.id, 'value': vals.get('value') or False, 'text': vals['text'].strip(),
            'visibility': 'public' if kind == 'praise' and vals.get('visibility') != 'private' else 'private'})
        return self.wall()
