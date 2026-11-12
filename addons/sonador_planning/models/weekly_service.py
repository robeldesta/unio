"""Screens of the weekly plan: the week with its lines and score, closing the week and planning the next one on one
screen, the manager's review, and the board (every department in the same shape, the differences first)."""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .service import _s, grade, week_start
from .weekly import PRIORITY, WEIGHT


def _band(score):
    if score is None:
        return 'none'
    return 'good' if score >= 85 else ('mid' if score >= 65 else 'bad')


class SbPlanning(models.AbstractModel):
    _inherit = 'sb.planning'

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        out = super().ctx()
        me = self._me()
        out['priorities'] = [{'key': k, 'name': n, 'weight': WEIGHT[k]} for k, n in PRIORITY]
        out['measures'] = [{'key': m['key'], 'name': m['name'], 'unit': m.get('unit') or '', 'scope': m.get('scope')}
                           for m in self.env['sb.perf.kpi']._measures()]
        out['can_board'] = bool(self._is_hr() or (me and self._team(me)))
        return out

    # ------------------------------------------------------------------ the week
    def _next_tasks(self, e, nws, nwe):
        return self.env['sb.plan.task'].sudo().search([('employee_id', '=', e.id), ('date_from', '>=', nws), ('date_to', '<=', nwe)],
                                                      order='sequence, id')

    def _next_row(self, t):
        return {'id': t.id, 'name': t.name, 'priority': t.priority, 'target_qty': t.target_qty or '', 'unit': t.unit or '',
                'measure': t.measure or '', 'done_when': t.done_when or '', 'standard': bool(t.role_item_id),
                'carried': t.carry_count or 0, 'review': t.review, 'review_note': t.review_note or ''}

    @api.model
    def week(self, day=False, employee_id=False):
        e = self._who(employee_id)
        d = fields.Date.to_date(day) if day else self._today()
        ws = week_start(d, e.company_id)
        we = ws + timedelta(days=6)
        S = self.env['sb.plan.score']
        S.ensure_role_lines(e, ws, we)
        out = super().week(day, employee_id)
        tasks = self._week_tasks(e, ws, we)
        # lines the system counts need no check-in: off the day's list and the day counters
        measured = tasks.filtered('measure')
        if measured:
            out['strip'] = [r for r in out['strip'] if r['id'] not in measured.ids]
            for dd in out['days']:
                d0 = fields.Date.to_date(dd['date'])
                n = len(measured.filtered(lambda t: t.date_from <= d0 <= t.date_to))
                dd['due'] = max(0, dd['due'] - n)
                dd['checked'] = min(dd['checked'], dd['due'])
        wk = S.week(e, ws, we, tasks)
        rep = self.env['sb.plan.week'].sudo().search([('employee_id', '=', e.id), ('week', '=', ws)])
        if rep.state == 'confirmed' and rep.lines:
            wk = {'lines': rep.lines, 'score': rep.plan_score, 'unplanned': rep.unplanned, 'needs_reason': []}
        rd, opens, closes = S.report_window(e.company_id, ws)
        now = fields.Datetime.now()
        nws, nwe = ws + timedelta(days=7), we + timedelta(days=7)
        out.update({
            'lines': wk['lines'], 'plan_score': wk['score'], 'band': _band(wk['score']), 'unplanned': wk['unplanned'],
            'needs_reason': wk['needs_reason'], 'score': wk['score'], 'grade': grade(wk['score']),
            'window': {'opens': fields.Datetime.to_string(opens), 'closes': fields.Datetime.to_string(closes),
                       'open': opens <= now, 'late': now > closes,
                       'label': '%s 10:00 – %s 12:00' % (format_ethiopian(rd), format_ethiopian(rd + timedelta(days=1)))},
            'nextweek': {'week': _s(nws), 'label': '%s – %s' % (format_ethiopian(nws), format_ethiopian(nwe)),
                     'rows': [self._next_row(t) for t in self._next_tasks(e, nws, nwe)]},
        })
        out['report'].update({'plan_score': rep.plan_score if rep else None, 'late': rep.late if rep else False,
                              'review_state': rep.review_state if rep else 'none', 'manager_score': rep.manager_score if rep else 0,
                              'manager_reason': rep.manager_reason or '' if rep else '', 'id': rep.id or False,
                              'can_review': bool(rep and rep.state == 'confirmed' and e != self._me() and self._can_review(e))})
        return out

    @api.model
    def save_reasons(self, reasons):
        """Why each missed line was missed (only the person's own lines)."""
        e = self._me()
        T = self.env['sb.plan.task'].sudo()
        for tid, why in (reasons or {}).items():
            t = T.browse(int(tid))
            if t.exists() and t.employee_id == e:
                t.miss_reason = (why or '').strip() or False

    @api.model
    def save_report(self, week, remarks, next_focus, confirm=False, reasons=None):
        e = self._me()
        self.save_reasons(reasons)
        ws = fields.Date.to_date(week)
        we = ws + timedelta(days=6)
        if not confirm:
            return super().save_report(week, remarks, next_focus, False)
        S = self.env['sb.plan.score']
        rd, opens, closes = S.report_window(e.company_id, ws)
        now = fields.Datetime.now()
        if now < opens:
            raise UserError(self.env._('The report opens on %s at 10:00.', format_ethiopian(rd)))
        tasks = self._week_tasks(e, ws, we)
        wk = S.week(e, ws, we, tasks, final=True)
        if wk['needs_reason']:
            names = [l['name'] for l in wk['lines'] if l['id'] in wk['needs_reason']]
            raise ValidationError(self.env._('Say why these Must lines were missed: %s', '; '.join(names)))
        if not self._next_tasks(e, ws + timedelta(days=7), we + timedelta(days=7)).filtered(lambda t: not t.role_item_id and not t.carried_from_id):
            raise ValidationError(self.env._('Plan next week before you send: add at least one line under "Next week".'))
        res = super().save_report(week, remarks, next_focus, True)
        rep = self.env['sb.plan.week'].sudo().search([('employee_id', '=', e.id), ('week', '=', ws)])
        rep.write({'plan_score': wk['score'] or 0, 'score': wk['score'] or 0, 'grade': grade(wk['score']), 'lines': wk['lines'],
                   'unplanned': wk['unplanned'], 'late': now > closes, 'department_id': e.department_id.id or False})
        S.carry_over(e, ws, we, tasks)
        return self.week(week)

    # ------------------------------------------------------------------ next week
    @api.model
    def save_next(self, week, rows):
        """Next week's plan lines (written while closing this week). Lines added to an approved month plan wait for the
        manager's yes, line by line, as tasks added during the month do."""
        e = self._me()
        ws = fields.Date.to_date(week)
        nws, nwe = ws + timedelta(days=7), ws + timedelta(days=13)
        T = self.env['sb.plan.task'].sudo()
        keep = []
        for r in rows or []:
            name = (r.get('name') or '').strip()
            if not name:
                continue
            if r.get('priority') not in WEIGHT:
                raise ValidationError(self.env._('"%s": choose Must, Should or Could.', name))
            if not r.get('measure') and not r.get('target_qty') and not (r.get('done_when') or '').strip():
                raise ValidationError(self.env._('"%s": give a target, or say what "done" looks like.', name))
            vals = {'name': name, 'priority': r['priority'], 'target_qty': float(r.get('target_qty') or 0), 'unit': r.get('unit') or False,
                    'measure': r.get('measure') or False, 'done_when': (r.get('done_when') or '').strip() or False}
            t = T.browse(int(r['id'])) if r.get('id') else T
            if t and t.exists() and t.employee_id == e:
                if t.role_item_id:
                    vals = {'target_qty': vals['target_qty']} if vals['target_qty'] else {}
                t.write(vals)
                keep.append(t.id)
                continue
            plan = self._plan(e, nws, create=True)
            t = T.create(dict(vals, plan_id=plan.id, date_from=max(nws, plan.month), date_to=min(nwe, plan.month_end),
                              review='pending', added_later=False, sequence=50))
            keep.append(t.id)
        # lines taken off the list (not the standard or carried ones)
        for t in self._next_tasks(e, nws, nwe):
            if t.id not in keep and not t.role_item_id and not t.carried_from_id and t.review != 'approved':
                t.unlink()
        return self.week(week)

    # ------------------------------------------------------------------ the manager's review
    @api.model
    def review_week(self, week_id, agree, score=None, reason=''):
        rep = self.env['sb.plan.week'].sudo().browse(int(week_id))
        if not rep.exists() or not self._can_review(rep.employee_id) or rep.employee_id == self._me():
            raise AccessError(self.env._('Only the line manager reviews this report.'))
        if rep.state != 'confirmed':
            raise UserError(self.env._('The report is not sent yet.'))
        if agree:
            rep.write({'review_state': 'agreed', 'manager_score': rep.plan_score, 'manager_reason': False})
        else:
            reason = (reason or '').strip()
            if len(reason) < 5:
                raise ValidationError(self.env._('Say why the score changes: the person will see it.'))
            rep.write({'review_state': 'changed', 'manager_score': max(0, min(100, int(score or 0))), 'manager_reason': reason})
        rep.write({'reviewed_by': self.env.uid, 'reviewed_at': fields.Datetime.now()})
        return self.week(_s(rep.week), rep.employee_id.id)

    # ------------------------------------------------------------------ the board
    def _board_people(self):
        me = self._me()
        E = self._E()
        if self._is_hr():
            return E.search([('company_id', '=', self.env.company.id), ('active', '=', True), ('sb_pl_gated', '=', True)])
        return self._team(me).filtered(lambda e: e.sudo().sb_pl_gated)

    def _dept_kpis(self, dept, ws, we):
        """The department's standard KPIs over the week so far, against their weekly targets."""
        out = []
        today = self._today()
        upto = min(we, today)
        gone = 1.0 if today >= we else max(1, (today - ws).days + 1) / 7.0
        for k in self.env['sb.plan.kpi'].sudo().search([('department_id', '=', dept.id), ('company_id', '=', self.env.company.id)]):
            actual = k.value(ws, upto)
            full = k.target or 0.0
            # an amount that builds up over the week is judged against the share of the week gone
            target = full if k.level() else full * gone
            if target:
                ratio = actual / target if k.higher() else (1.2 if actual <= 0 else target / actual)
            else:
                ratio = 1.0
            ratio = min(ratio, 1.2)
            out.append({'name': k.name, 'actual': round(actual, 2), 'target': round(full, 2), 'unit': k.unit or '',
                        'pct': round(ratio * 100), 'band': _band(ratio * 100), 'higher': k.higher(),
                        'pace': not k.level() and gone < 1.0})
        return out

    @api.model
    def board(self, day=False):
        """Every department in the same shape: its score (its people's plan scores), its KPIs, and the differences first:
        Must lines missed, lines carried twice, KPIs off, reports late or missing."""
        if not (self._is_hr() or self._team()):
            raise AccessError(self.env._('The board is for managers.'))
        d = fields.Date.to_date(day) if day else self._today()
        ws = week_start(d, self.env.company)
        we = ws + timedelta(days=6)
        S = self.env['sb.plan.score']
        W = self.env['sb.plan.week'].sudo()
        T = self.env['sb.plan.task'].sudo()
        people = self._board_people()
        depts = {}
        exceptions = []
        _rd, _opens, closes = S.report_window(self.env.company, ws)
        report_due = fields.Datetime.now() > closes
        for e in people.sorted('name'):
            rep = W.search([('employee_id', '=', e.id), ('week', '=', ws)])
            if rep.state == 'confirmed':
                score, lines = rep._final_score(), rep.lines or []
            else:
                wk = S.week(e, ws, we, self._week_tasks(e, ws, we))
                score, lines = wk['score'], wk['lines']
            dkey = e.department_id.id or 0
            dp = depts.setdefault(dkey, {'id': dkey, 'name': e.department_id.name or 'No department', 'people': [], 'scores': []})
            row = {'id': e.id, 'name': e.name, 'job': e.job_title or '', 'score': score, 'band': _band(score),
                   'sent': rep.state == 'confirmed', 'late': bool(rep.late), 'review': rep.review_state if rep else 'none',
                   'must': len([l for l in lines if l['priority'] == 'must' and not l.get('unplanned')]),
                   'missed': len([l for l in lines if l.get('missed') and l['priority'] == 'must']),
                   'unplanned': len([l for l in lines if l.get('unplanned')])}
            dp['people'].append(row)
            if score is not None:
                dp['scores'].append(score)
            for l in lines:
                if l.get('missed') and l['priority'] == 'must':
                    exceptions.append({'kind': 'missed', 'label': 'Must missed', 'dept': dp['name'], 'who': e.name, 'employee_id': e.id,
                                       'text': '%s: %s%s' % (l['name'], '%s of %s %s' % (_n(l['actual']), _n(l['target']), l['unit'])
                                                             if l.get('target') else 'not done', (' (%s)' % l['reason']) if l.get('reason') else '')})
            if rep.state == 'confirmed' and rep.late:
                exceptions.append({'kind': 'late', 'label': 'Late', 'dept': dp['name'], 'who': e.name, 'employee_id': e.id,
                                   'text': 'Report sent after Saturday 12:00.'})
            elif rep.state != 'confirmed' and report_due:
                exceptions.append({'kind': 'missing', 'label': 'No report', 'dept': dp['name'], 'who': e.name, 'employee_id': e.id,
                                   'text': 'This week\'s report is not sent.'})
        for t in T.search([('employee_id', 'in', people.ids), ('carry_count', '>=', 2), ('date_from', '<=', we), ('date_to', '>=', ws),
                           ('status', '!=', 'done')]):
            exceptions.append({'kind': 'carried', 'label': 'Carried %d times' % t.carry_count, 'dept': t.employee_id.department_id.name or '',
                               'who': t.employee_id.name, 'employee_id': t.employee_id.id, 'text': t.name})
        out = []
        for dp in depts.values():
            dept = self.env['hr.department'].sudo().browse(dp['id']) if dp['id'] else self.env['hr.department']
            sc = round(sum(dp['scores']) / len(dp['scores'])) if dp['scores'] else None
            kpis = self._dept_kpis(dept, ws, we) if dept else []
            for k in kpis:
                if k['band'] == 'bad':
                    same = next((x for x in exceptions if x['kind'] == 'kpi' and x.get('kpi') == k['name']), None)
                    if same:   # one KPI off shows once, with every department that carries it
                        same['dept'] = '%s, %s' % (same['dept'], dp['name'])
                        continue
                    exceptions.append({'kind': 'kpi', 'label': 'KPI off', 'dept': dp['name'], 'who': '', 'employee_id': False, 'kpi': k['name'],
                                       'text': ('%s: %s %s so far, %s%% of where the week should be (target %s for the week)'
                                                % (k['name'], _n(k['actual']), k['unit'], k['pct'], _n(k['target'])))
                                       if k.get('pace') else ('%s: %s against %s %s' % (k['name'], _n(k['actual']), _n(k['target']), k['unit']))})
            out.append(dict(dp, score=sc, band=_band(sc), kpis=kpis))
        out.sort(key=lambda x: (x['score'] is None, x['score'] if x['score'] is not None else 0))
        rank = {'missed': 0, 'carried': 1, 'kpi': 2, 'missing': 3, 'late': 4}
        exceptions.sort(key=lambda x: rank.get(x['kind'], 9))
        scores = [d['score'] for d in out if d['score'] is not None]
        return {'week': _s(ws), 'label': '%s – %s' % (format_ethiopian(ws), format_ethiopian(we)),
                'prev': _s(ws - timedelta(days=7)), 'next': _s(ws + timedelta(days=7)),
                'company': round(sum(scores) / len(scores)) if scores else None, 'departments': out, 'exceptions': exceptions}

    def _person_row(self, e, today, ws, we):
        row = super()._person_row(e, today, ws, we)
        wk = self.env['sb.plan.score'].week(e, ws, we, self._week_tasks(e, ws, we))
        row.update(week_score=wk['score'], grade=grade(wk['score']), band=_band(wk['score']))
        return row


def _n(v):
    if v is None:
        return '–'
    return ('{:,.0f}' if abs(v) >= 100 or float(v).is_integer() else '{:,.2f}').format(v)
