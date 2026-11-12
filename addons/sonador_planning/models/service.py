"""Calls of the Planning screens: my week (today's check-ins, the week's tasks, the Friday report), my month plan
(edit, live plan checker, submit), my year (goals from Performance), my team (approve line by line), overview and
settings."""
from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import (MONTHS, fiscal_periods, fiscal_year_of, format_ethiopian,
                                                                 to_ethiopian)


def _s(d):
    return fields.Date.to_string(d) if d else False


def month_bounds(d):
    d = fields.Date.to_date(d)
    for _no, name, a, b in fiscal_periods(fiscal_year_of(d)):
        if a <= d <= b:
            return a, b, name
    return d, d, ''


def week_start(d, company):
    start = int(company.sb_pl_week_start or '5')
    return d - timedelta(days=(d.weekday() - start) % 7)


def grade(score):
    if score is None:
        return ''
    return 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 65 else 'D' if score >= 50 else 'E'


class SbPlanning(models.AbstractModel):
    _name = 'sb.planning'
    _description = 'Planning'

    # ------------------------------------------------------------------ people and access
    def _me(self):
        return self.env['sb.people']._me()

    def _is_hr(self):
        return self.env['sb.people']._is_hr()

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    def _team(self, me=None):
        me = me or self._me()
        if not me:
            return self._E()
        return self._E().search([('id', 'child_of', me.id), ('id', '!=', me.id), ('active', '=', True)])

    def _can_review(self, e):
        return self._is_hr() or e in self._team()

    def _who(self, employee_id):
        e = self._E().browse(int(employee_id)) if employee_id else self._me()
        if not e:
            raise UserError(self.env._('Your login is not linked to a person in People.'))
        if e != self._me() and not self._can_review(e):
            raise AccessError(self.env._('Not your team.'))
        return e

    def _today(self):
        return fields.Date.context_today(self)

    def _approver(self, e):
        return e.parent_id if e.parent_id and e.parent_id != e else self._E()

    # ------------------------------------------------------------------ calendar
    def _off_days(self, e, df, dt):
        hol = self.env['sb.leave.service'].sudo()._holiday_dates(df, dt, e.company_id)
        off = set(hol)
        for lv in self.env['sb.leave'].sudo().search([('employee_id', '=', e.id), ('state', '=', 'approved'),
                                                      ('date_from', '<=', dt), ('date_to', '>=', df)]):
            d = max(lv.date_from, df)
            while d <= min(lv.date_to, dt):
                off.add(d)
                d += timedelta(days=1)
        cal = e.resource_calendar_id or e.company_id.resource_calendar_id
        workdays = {int(a.dayofweek) for a in cal.attendance_ids} if cal else {0, 1, 2, 3, 4, 5}
        d = df
        while d <= dt:
            if d.weekday() not in workdays:
                off.add(d)
            d += timedelta(days=1)
        return off, hol

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        me = self._me()
        c = self.env.company
        cats = self.env['sb.plan.category'].sudo().search([('company_id', '=', c.id), '|', ('department_id', '=', False),
                                                           ('department_id', '=', me.department_id.id if me else False)])
        today = self._today()
        team = self._team()
        return {'me': me.id or False, 'me_name': me.name or '', 'gated': bool(me and me.sudo().sb_pl_gated), 'is_hr': self._is_hr(),
                'has_team': bool(team), 'team_size': len(team), 'today': _s(today), 'today_ec': format_ethiopian(today),
                'categories': [{'id': x.id, 'name': x.name} for x in cats],
                'goals': self._goals(me) if me else [],
                'indicators': [{'id': i.id, 'name': i.name, 'requires_document': i.requires_document}
                               for i in self.env['sb.plan.indicator'].sudo().search([('company_id', 'in', (c.id, False))])],
                'role': self._role_info(me) if me else False,
                'gate': self.env['sb.plan.gate'].state(self.env.user),
                'config': {'mode': c.sb_pl_mode, 'checkin': c.sb_pl_checkin, 'require_goal': c.sb_pl_require_goal,
                           'week_start': c.sb_pl_week_start, 'report_day': c.sb_pl_report_day}}

    def _role_info(self, e):
        role = e.sudo().sb_pl_role_id
        if not role:
            return False
        return {'id': role.id, 'name': role.name,
                'items': [{'id': i.id, 'name': i.name, 'done_when': i.done_when or '', 'target_qty': i.target_qty or '',
                           'unit': i.unit or '', 'output': i.output or '', 'indicator_id': i.indicator_id.id or '',
                           'mandatory': i.mandatory} for i in role.item_ids]}

    def _goals(self, e):
        G = self.env['sb.perf.goal'].sudo()
        today = self._today()
        gs = G.search([('company_id', '=', e.company_id.id), '|', '|', ('employee_id', '=', e.id),
                       '&', ('level', '=', 'department'), ('department_id', '=', e.department_id.id), ('level', '=', 'company'),
                       '|', ('date_to', '=', False), ('date_to', '>=', today - timedelta(days=60))])
        return [{'id': g.id, 'name': g.name, 'level': g.level, 'progress': g.progress} for g in gs]

    # ------------------------------------------------------------------ month plan
    def _plan(self, e, month, create=False):
        m0, m1, label = month_bounds(month)
        P = self.env['sb.plan'].sudo()
        plan = P.search([('employee_id', '=', e.id), ('month', '=', m0)])
        if not plan and create:
            plan = P.create({'employee_id': e.id, 'company_id': e.company_id.id, 'month': m0, 'month_end': m1, 'label': label,
                             'approver_id': self._approver(e).id or False})
        return plan

    def _task_row(self, t):
        return {'id': t.id, 'key': 't%d' % t.id, 'name': t.name, 'done_when': t.done_when or '', 'detail': t.detail or '', 'target_qty': t.target_qty or '',
                'unit': t.unit or '', 'date_from': _s(t.date_from), 'date_to': _s(t.date_to), 'output': t.output or '',
                'category_id': t.category_id.id or '', 'category': t.category_id.name or '', 'goal_id': t.goal_id.id or '',
                'goal': t.goal_id.name or '', 'review': t.review, 'review_note': t.review_note or '', 'status': t.status,
                'progress': t.progress, 'done_qty': t.done_qty, 'added_later': t.added_later,
                'from_ec': format_ethiopian(t.date_from), 'to_ec': format_ethiopian(t.date_to), 'flags': t.flags or [],
                'indicator_id': t.indicator_id.id or '', 'indicator': t.indicator_id.name or '',
                'requires_document': t.indicator_id.requires_document, 'role_item_id': t.role_item_id.id or ''}

    @api.model
    def month(self, month=False, employee_id=False):
        e = self._who(employee_id)
        today = self._today()
        m0, m1, label = month_bounds(month or today)
        plan = self._plan(e, m0)
        nxt0 = month_bounds(m1 + timedelta(days=1))[0]
        prv0 = month_bounds(m0 - timedelta(days=1))[0]
        mine = e == self._me()
        cur0, cur1, _cl = month_bounds(today)
        open_day = cur0 + timedelta(days=(e.company_id.sb_pl_next_from_day or 20) - 1)
        opens = m0 <= today or (m0 == cur1 + timedelta(days=1) and today >= open_day)
        editable = mine and opens and (not plan or plan.state in ('draft', 'returned', 'submitted'))
        changes = plan.change_ids.filtered(lambda c: c.state == 'pending') if plan else self.env['sb.plan.change']
        weeks = []
        ws = week_start(m0, e.company_id)
        while ws <= m1:
            weeks.append({'from': _s(max(ws, m0)), 'to': _s(min(ws + timedelta(days=6), m1)),
                          'label': '%s – %s' % (format_ethiopian(max(ws, m0)).rsplit(',', 1)[0], format_ethiopian(min(ws + timedelta(days=6), m1)).rsplit(',', 1)[0])})
            ws += timedelta(days=7)
        return {'employee': {'id': e.id, 'name': e.name}, 'mine': mine, 'month': _s(m0), 'month_end': _s(m1), 'label': label,
                'range': '%s – %s' % (format_ethiopian(m0), format_ethiopian(m1)),
                'next': _s(nxt0), 'prev': _s(prv0),
                'opens': opens, 'opens_on': format_ethiopian(open_day) if not opens else '',
                'deadline': format_ethiopian(m0 + timedelta(days=(e.company_id.sb_pl_deadline_day or 3) - 1)),
                'weeks': weeks, 'editable': editable, 'can_propose': bool(mine and plan and plan.state == 'approved'),
                'changes': [self._change_row(c) for c in changes],
                'plan': plan and {'id': plan.id, 'state': plan.state, 'score': plan.score, 'manager_note': plan.manager_note or '',
                                  'approver': plan.approver_id.name or '', 'submitted': plan.submitted_at and format_ethiopian(plan.submitted_at.date()) or '',
                                  'revisions': plan.revisions} or False,
                'tasks': [self._task_row(t) for t in plan.task_ids] if plan else [],
                'approver': self._approver(e).name or ''}

    def _change_row(self, c):
        return {'id': c.id, 'kind': c.kind, 'summary': c.summary or '', 'reason': c.reason or '', 'state': c.state,
                'task': c.task_id.name or '', 'note': c.decision_note or ''}

    def _rows_to_vals(self, rows, plan):
        out = []
        cats = {c.name.lower(): c.id for c in self.env['sb.plan.category'].sudo().search([])}
        for r in rows:
            name = (r.get('name') or '').strip()
            if not name and not (r.get('done_when') or '').strip():
                continue
            cat = r.get('category_id')
            if not cat and r.get('category'):
                cat = cats.get(r['category'].strip().lower())
            out.append({'id': int(r['id']) if r.get('id') else False, 'name': name or '?', 'done_when': (r.get('done_when') or '').strip() or False,
                        'target_qty': float(r.get('target_qty') or 0), 'unit': (r.get('unit') or '').strip() or False,
                        'date_from': r.get('date_from') or plan.month, 'date_to': r.get('date_to') or r.get('date_from') or plan.month_end,
                        'output': (r.get('output') or '').strip() or False, 'category_id': int(cat) if cat else False,
                        'detail': (r.get('detail') or '').strip() or False,
                        'goal_id': int(r['goal_id']) if r.get('goal_id') else False,
                        'indicator_id': int(r['indicator_id']) if r.get('indicator_id') else False,
                        'role_item_id': int(r['role_item_id']) if r.get('role_item_id') else False})
        return out

    @api.model
    def check(self, month, rows, employee_id=False):
        """Live plan checker on unsaved rows."""
        e = self._who(employee_id)
        tasks = [dict(r, key=r.get('key') or ('r%d' % i)) for i, r in enumerate(rows)]
        return self.env['sb.plan.coach'].check(e, month, tasks)

    @api.model
    def save(self, month, rows, submit=False):
        e = self._me()
        if not e:
            raise UserError(self.env._('Your login is not linked to a person in People.'))
        plan = self._plan(e, month, create=True)
        if plan.state == 'approved':
            raise UserError(self.env._('The %s plan is approved: propose the change instead (your manager accepts it in one click).', plan.label))
        was_submitted = plan.state == 'submitted'
        if was_submitted:
            submit = True  # still with the manager: the edit replaces what they see, so it is checked like a submission
        vals = self._rows_to_vals(rows, plan)
        T = self.env['sb.plan.task'].sudo()
        keep = []
        for i, v in enumerate(vals):
            tid = v.pop('id')
            v['sequence'] = i
            t = T.browse(tid) if tid and T.browse(tid).plan_id == plan else T.create(dict(v, plan_id=plan.id))
            if tid:
                changed = any((t[k].id if hasattr(t[k], 'id') else t[k]) != (fields.Date.to_date(v[k]) if k in ('date_from', 'date_to') else v[k])
                              for k in v if k != 'sequence')
                t.write(v)
                if changed and t.review != 'pending':
                    t.write({'review': 'pending'})
            keep.append(t.id)
        plan.task_ids.filtered(lambda t: t.id not in keep).unlink()
        res = self.env['sb.plan.coach'].check(e, plan.month, [dict(self._task_row(t), key=t.id) for t in plan.task_ids])
        for t in plan.task_ids:
            t.flags = res['tasks'].get(t.id, [])
        if submit:
            if res['blocking']:
                items = ['• %s' % f['msg'] for f in res['plan'] if f['level'] == 'block']
                items += ['• %s: %s' % (t.name[:60], f['msg']) for t in plan.task_ids for f in (t.flags or []) if f['level'] == 'block']
                raise ValidationError(self.env._('Fix the red items first:') + '\n' + '\n'.join(items[:12]) +
                                      ('\n…' if len(items) > 12 else ''))
            plan.write({'state': 'submitted', 'submitted_at': fields.Datetime.now(), 'score': res['score'],
                        'first_submitted': plan.first_submitted or self._today(),
                        'approver_id': self._approver(e).id or False, 'revisions': plan.revisions + (1 if plan.state == 'returned' else 0)})
            approver = plan.approver_id
            if approver.user_id:
                body = self.env._('%(n)s updated the %(m)s plan waiting for your approval.', n=e.name, m=plan.label) if was_submitted else \
                    self.env._('%(n)s submitted the %(m)s plan for approval.', n=e.name, m=plan.label)
                e.message_post(body=body, partner_ids=approver.user_id.partner_id.ids)
        self.env['sb.plan.gate'].forget(e.user_id)
        out = self.month(plan.month)
        out['check'] = res
        return out

    @api.model
    def copy_last(self, month):
        """Start from the tasks of the previous month (dates moved to this month) - the checker will flag what is copied."""
        e = self._me()
        m0, m1, _l = month_bounds(month)
        prev = self._plan(e, m0 - timedelta(days=1))
        if not prev:
            raise UserError(self.env._('There is no plan for last month.'))
        shift = m0 - prev.month
        rows = []
        for t in prev.task_ids:
            rows.append({'name': t.name, 'done_when': t.done_when or '', 'target_qty': t.target_qty or '', 'unit': t.unit or '',
                         'output': t.output or '', 'category_id': t.category_id.id or '', 'goal_id': t.goal_id.id or '',
                         'indicator_id': t.indicator_id.id or '', 'role_item_id': t.role_item_id.id or '',
                         'date_from': _s(min(max(t.date_from + shift, m0), m1)), 'date_to': _s(min(max(t.date_to + shift, m0), m1))})
        return rows

    # ------------------------------------------------------------------ the week
    def _week_tasks(self, e, ws, we):
        return self.env['sb.plan.task'].sudo().search([('employee_id', '=', e.id), ('plan_id.state', 'in', ('submitted', 'approved')),
                                                       ('date_from', '<=', we), ('date_to', '>=', ws)], order='date_to, sequence')

    def _execution(self, e, ws, we, tasks=None):
        tasks = tasks if tasks is not None else self._week_tasks(e, ws, we)
        upto = min(self._today(), we)
        C = self.env['sb.plan.checkin'].sudo()
        scores = []
        for t in tasks:
            if t.date_from > upto:
                continue
            span = (t.date_to - t.date_from).days + 1
            expected = min(1.0, ((upto - t.date_from).days + 1) / span) * 100
            last = C.search([('task_id', '=', t.id), ('date', '<=', upto)], limit=1)
            actual = 100 if (last.status == 'done' if last else t.status == 'done') else (last.progress if last else 0)
            scores.append(min(actual / expected, 1.0) if expected else 1.0)
        return round(100 * sum(scores) / len(scores)) if scores else None

    @api.model
    def week(self, day=False, employee_id=False):
        e = self._who(employee_id)
        today = self._today()
        d = fields.Date.to_date(day) if day else today
        ws = week_start(d, e.company_id)
        we = ws + timedelta(days=6)
        tasks = self._week_tasks(e, ws, we)
        C = self.env['sb.plan.checkin'].sudo()
        cks = C.search([('task_id', 'in', tasks.ids), ('date', '>=', ws), ('date', '<=', we)])
        by = {(c.task_id.id, c.date): c for c in cks}
        off, hol = self._off_days(e, ws, we)
        days = []
        for i in range(7):
            dd = ws + timedelta(days=i)
            active = tasks.filtered(lambda t: t.date_from <= dd <= t.date_to and not (t.status == 'done' and t.checkin_ids.filtered(
                lambda c: c.status == 'done' and c.date < dd)))
            done = [t.id for t in active if (t.id, dd) in by]
            days.append({'date': _s(dd), 'name': dd.strftime('%a'), 'ec': format_ethiopian(dd), 'n': to_ethiopian(dd)[2],
                         'off': dd in off, 'holiday': hol.get(dd, ''), 'today': dd == today, 'future': dd > today,
                         'due': len(active), 'checked': len(done)})
        sel = fields.Date.to_date(day) if day and ws <= fields.Date.to_date(day) <= we else (today if ws <= today <= we else we)
        strip = []
        for t in tasks.filtered(lambda t: t.date_from <= sel <= t.date_to):
            c = by.get((t.id, sel))
            prev = C.search([('task_id', '=', t.id), ('date', '<', sel)], limit=1)
            if prev.status == 'done' and not c:
                continue
            strip.append(dict(self._task_row(t), checkin=c and {'status': c.status, 'progress': c.progress, 'note': c.note or '',
                                                                 'done_qty': c.done_qty, 'late': c.late} or False,
                              last_progress=prev.progress if prev else 0))
        rep = self.env['sb.plan.week'].sudo().search([('employee_id', '=', e.id), ('week', '=', ws)])
        score = self._execution(e, ws, we, tasks)
        return {'employee': {'id': e.id, 'name': e.name}, 'mine': e == self._me(), 'week': _s(ws), 'week_end': _s(we),
                'label': '%s – %s' % (format_ethiopian(ws), format_ethiopian(we)), 'prev': _s(ws - timedelta(days=7)),
                'next': _s(ws + timedelta(days=7)), 'days': days, 'selected': _s(sel), 'selected_ec': format_ethiopian(sel),
                'selected_off': sel in off, 'strip': strip,
                'tasks': [dict(self._task_row(t), week_checkins=[{'date': _s(c.date), 'status': c.status, 'progress': c.progress}
                                                                 for c in cks.filtered(lambda c, t=t: c.task_id == t).sorted('date')])
                          for t in tasks],
                'score': score, 'grade': grade(score),
                'report': {'state': rep.state if rep else 'none', 'remarks': rep.remarks or '', 'next_focus': rep.next_focus or '',
                           'manager_note': rep.manager_note or '', 'due': format_ethiopian(ws + timedelta(days=(int(e.company_id.sb_pl_report_day) - ws.weekday()) % 7)),
                           'flags': rep.trust_flags or []},
                'plan_missing': not self._plan(e, today) or self._plan(e, today).state in ('draft', 'returned')}

    @api.model
    def checkin(self, task_id, day, status, progress=None, note='', done_qty=None):
        e = self._me()
        t = self.env['sb.plan.task'].sudo().browse(int(task_id))
        if t.employee_id != e:
            raise AccessError(self.env._('This is not your task.'))
        d = fields.Date.to_date(day)
        today = self._today()
        if d > today:
            raise ValidationError(self.env._('That day has not come yet.'))
        if d < week_start(today, e.company_id) - timedelta(days=7):
            raise ValidationError(self.env._('Too far back: ask your manager to note it on the weekly report.'))
        if not (t.date_from <= d <= t.date_to):
            raise ValidationError(self.env._('The task is not planned on that day.'))
        note = (note or '').strip()
        if status == 'blocked' and len(note) < 5:
            raise ValidationError(self.env._('Say what is blocking it.'))
        prog = 100 if status == 'done' else max(0, min(int(progress or 0), 99))
        C = self.env['sb.plan.checkin'].sudo()
        c = C.search([('task_id', '=', t.id), ('date', '=', d)])
        vals = {'status': status, 'progress': prog, 'note': note or False, 'late': d < today,
                'done_qty': float(done_qty) if done_qty not in (None, '') else (t.target_qty if status == 'done' else 0)}
        if c:
            c.write(vals)
        else:
            C.create(dict(vals, task_id=t.id, date=d))
        last = C.search([('task_id', '=', t.id)], order='date desc, id desc', limit=1)
        t.write({'status': last.status, 'progress': last.progress, 'done_qty': last.done_qty})
        self.env['sb.plan.gate'].forget(e.user_id)
        return self.week(_s(d))

    @api.model
    def add_week_task(self, vals):
        """A task that came up during the month (no new approval; the manager sees it marked 'added')."""
        e = self._me()
        d = fields.Date.to_date(vals.get('date_from')) or self._today()
        plan = self._plan(e, d)
        if not plan or plan.state not in ('submitted', 'approved'):
            raise UserError(self.env._('Submit the month plan first, then add tasks that come up.'))
        row = dict(vals, key='new')
        res = self.env['sb.plan.coach'].check(e, plan.month, [row])
        blocks = [f['msg'] for f in res['tasks']['new'] if f['level'] == 'block']
        if blocks:
            raise ValidationError('\n'.join(blocks))
        v = self._rows_to_vals([vals], plan)[0]
        v.pop('id')
        self.env['sb.plan.task'].sudo().create(dict(v, plan_id=plan.id, added_later=True, review='pending',
                                                     flags=res['tasks']['new'], sequence=999))
        return self.week(_s(d))

    @api.model
    def save_report(self, week, remarks, next_focus, confirm=False):
        e = self._me()
        ws = fields.Date.to_date(week)
        W = self.env['sb.plan.week'].sudo()
        rep = W.search([('employee_id', '=', e.id), ('week', '=', ws)]) or W.create({'employee_id': e.id, 'week': ws, 'company_id': e.company_id.id})
        if rep.state == 'confirmed':
            raise UserError(self.env._('This report is already sent.'))
        we = ws + timedelta(days=6)
        vals = {'remarks': (remarks or '').strip() or False, 'next_focus': (next_focus or '').strip() or False}
        if confirm:
            if self._today() < ws + timedelta(days=(int(e.company_id.sb_pl_report_day) - ws.weekday()) % 7):
                raise UserError(self.env._('The report opens on report day.'))
            score = self._execution(e, ws, we)
            vals.update(state='confirmed', confirmed_at=fields.Datetime.now(), score=score or 0, grade=grade(score),
                        trust_flags=self.env['sb.plan.coach'].trust_flags(e, ws, we))
            blocked = self._week_tasks(e, ws, we).filtered(lambda t: t.status == 'blocked')
            if blocked and not vals['remarks']:
                raise ValidationError(self.env._('%s task(s) are blocked: say in the remarks what you need.', len(blocked)))
        rep.write(vals)
        self.env['sb.plan.gate'].forget(e.user_id)
        return self.week(week)

    # ------------------------------------------------------------------ year (Performance goals)
    @api.model
    def year(self, employee_id=False):
        e = self._who(employee_id)
        T = self.env['sb.plan.task'].sudo()
        fy = e.company_id.compute_fiscalyear_dates(self._today())
        out = []
        for g in self._goals(e):
            ts = T.search([('goal_id', '=', g['id']), ('employee_id', '=', e.id), ('date_from', '>=', fy['date_from'])])
            out.append(dict(g, tasks=len(ts), done=len(ts.filtered(lambda t: t.status == 'done')),
                            months=sorted({t.plan_id.label for t in ts})))
        unlinked = T.search_count([('employee_id', '=', e.id), ('goal_id', '=', False), ('date_from', '>=', fy['date_from'])])
        return {'employee': {'id': e.id, 'name': e.name}, 'goals': out, 'unlinked': unlinked,
                'fy': '%s – %s' % (format_ethiopian(fy['date_from']), format_ethiopian(fy['date_to']))}

    # ------------------------------------------------------------------ team (line manager)
    def _person_row(self, e, today, ws, we):
        plan = self._plan(e, today)
        tasks = self._week_tasks(e, ws, we)
        rep = self.env['sb.plan.week'].sudo().search([('employee_id', '=', e.id), ('week', '=', ws - timedelta(days=7))])
        score = self._execution(e, ws, we, tasks)
        gate = self.env['sb.plan.gate'].reasons(e)
        return {'id': e.id, 'name': e.name, 'job': e.job_title or '', 'department': e.department_id.name or '', 'gated': e.sb_pl_gated,
                'avatar': '/web/image/hr.employee.public/%d/avatar_128' % e.id,
                'plan': plan and {'id': plan.id, 'state': plan.state, 'score': plan.score, 'tasks': len(plan.task_ids),
                                  'returned': len(plan.task_ids.filtered(lambda t: t.review == 'returned')),
                                  'added': len(plan.task_ids.filtered(lambda t: t.added_later and t.review == 'pending')),
                                  'changes': len(plan.change_ids.filtered(lambda c: c.state == 'pending'))} or False,
                'week_score': score, 'grade': grade(score), 'blocked': len(tasks.filtered(lambda t: t.status == 'blocked')),
                'done': len(tasks.filtered(lambda t: t.status == 'done')), 'week_tasks': len(tasks),
                'last_report': rep.state if rep else 'none', 'last_grade': rep.grade or '', 'flags': rep.trust_flags or [],
                'missing': [r['msg'] for r in gate]}

    @api.model
    def team(self):
        me = self._me()
        people = self._team(me) if not self._is_hr() else self._team(me)
        today = self._today()
        ws = week_start(today, self.env.company)
        return {'rows': [self._person_row(e, today, ws, ws + timedelta(days=6)) for e in people.sorted('name')],
                'month_label': month_bounds(today)[2]}

    @api.model
    def overview(self):
        if not self._is_hr():
            raise AccessError(self.env._('The overview is for HR and management.'))
        today = self._today()
        ws = week_start(today, self.env.company)
        people = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True), ('sb_pl_gated', '=', True)])
        rows = [self._person_row(e, today, ws, ws + timedelta(days=6)) for e in people.sorted('name')]
        blockers = []
        C = self.env['sb.plan.checkin'].sudo()
        for c in C.search([('employee_id', 'in', people.ids), ('status', '=', 'blocked'), ('date', '>=', ws - timedelta(days=7))], order='date desc'):
            if c.task_id.status == 'blocked':
                blockers.append({'who': c.employee_id.name, 'task': c.task_id.name, 'note': c.note or '', 'ec': format_ethiopian(c.date)})
        return {'rows': rows, 'blockers': blockers[:30], 'week': '%s – %s' % (format_ethiopian(ws), format_ethiopian(ws + timedelta(days=6)))}

    @api.model
    def review(self, plan_id):
        plan = self.env['sb.plan'].sudo().browse(int(plan_id))
        if not self._can_review(plan.employee_id):
            raise AccessError(self.env._('Not your team.'))
        out = self.month(plan.month, plan.employee_id.id)
        out['check'] = self.env['sb.plan.coach'].check(plan.employee_id, plan.month, [dict(self._task_row(t), key=t.id) for t in plan.task_ids])
        out['can_decide'] = plan.state == 'submitted' or bool(plan.task_ids.filtered(lambda t: t.added_later and t.review == 'pending'))
        out['changes'] = [self._change_row(c) for c in plan.change_ids.filtered(lambda c: c.state == 'pending')]
        out['can_send_back'] = plan.state in ('submitted', 'approved') and plan.employee_id != self._me()
        return out

    @api.model
    def decide(self, plan_id, decisions, note=''):
        """decisions: {task_id: {'review': 'approved'|'returned', 'note': '...'}}. Approved when every task is approved."""
        plan = self.env['sb.plan'].sudo().browse(int(plan_id))
        e = plan.employee_id
        if not self._can_review(e):
            raise AccessError(self.env._('Not your team.'))
        if e == self._me() and not self.env.su:
            raise AccessError(self.env._('Your own plan is approved by your manager.'))
        for tid, d in (decisions or {}).items():
            t = plan.task_ids.filtered(lambda x: x.id == int(tid))
            if d.get('review') == 'returned' and not (d.get('note') or '').strip():
                raise ValidationError(self.env._('Say what to change on "%s".', t.name))
            t.write({'review': d.get('review') or 'approved', 'review_note': (d.get('note') or '').strip() or False})
        returned = plan.task_ids.filtered(lambda t: t.review == 'returned')
        pending = plan.task_ids.filtered(lambda t: t.review == 'pending')
        if returned:
            plan.write({'state': 'returned', 'manager_note': note or False})
            msg = self.env._('%(m)s plan: %(n)s task(s) returned for changes by %(by)s.', m=plan.label, n=len(returned), by=self.env.user.name)
        elif not pending:
            plan.write({'state': 'approved', 'approved_at': fields.Datetime.now(), 'manager_note': note or False})
            msg = self.env._('%(m)s plan approved by %(by)s.', m=plan.label, by=self.env.user.name)
        else:
            msg = False
        if msg:
            e.message_post(body=msg, partner_ids=e.user_id.partner_id.ids if e.user_id else [])
        self.env['sb.plan.gate'].forget(e.user_id)
        return self.review(plan.id)

    @api.model
    def report_note(self, week_id, note):
        rep = self.env['sb.plan.week'].sudo().browse(int(week_id))
        if not self._can_review(rep.employee_id):
            raise AccessError(self.env._('Not your team.'))
        rep.manager_note = note
        return True

    # ------------------------------------------------------------------ after approval: small change requests
    CHANGE_FIELDS = ('name', 'done_when', 'target_qty', 'unit', 'date_from', 'date_to', 'output', 'category_id', 'goal_id',
                     'detail', 'indicator_id', 'role_item_id')

    def _cur(self, t, k):
        v = t[k]
        if hasattr(v, '_name'):
            return v.id or False
        if k in ('date_from', 'date_to'):
            return _s(v)
        return v or False

    def _describe(self, kind, t, v):
        if kind == 'add':
            return self.env._('Add "%s"', v.get('name'))
        if kind == 'remove':
            return self.env._('Drop "%s"', t.name)
        parts = []
        for k, new in v.items():
            if k in ('date_from', 'date_to'):
                parts.append('%s → %s' % ({'date_from': self.env._('from'), 'date_to': self.env._('due')}[k], format_ethiopian(fields.Date.to_date(new))))
            elif k in ('name', 'done_when', 'output', 'unit', 'detail'):
                parts.append('%s: "%s"' % ({'name': self.env._('what'), 'done_when': self.env._('done when'), 'output': self.env._('evidence'),
                                             'unit': self.env._('unit'), 'detail': self.env._('how')}[k], (new or '')[:60]))
            elif k == 'target_qty':
                parts.append(self.env._('target %s', '%g' % (new or 0)))
        return '"%s": %s' % (t.name[:50], ', '.join(parts) or self.env._('details'))

    @api.model
    def propose_change(self, month, rows, reason=''):
        """The owner of an approved plan proposes changes; nothing changes until the manager accepts."""
        e = self._me()
        plan = self._plan(e, month) if e else False
        if not plan or plan.state != 'approved':
            raise UserError(self.env._('Only an approved plan takes change requests; edit the plan directly.'))
        reason = (reason or '').strip()
        if len(reason) < 5:
            raise ValidationError(self.env._('Say in a few words why the plan changes.'))
        vals = self._rows_to_vals(rows, plan)
        res = self.env['sb.plan.coach'].check(e, plan.month, [dict(r, key=('r%d' % i), date_from=_s(fields.Date.to_date(r['date_from'])),
                                                                    date_to=_s(fields.Date.to_date(r['date_to'])))
                                                              for i, r in enumerate(rows) if (r.get('name') or '').strip()])
        if res['blocking']:
            items = ['• %s' % f['msg'] for f in res['plan'] if f['level'] == 'block']
            items += ['• %s' % f['msg'] for fs in res['tasks'].values() for f in fs if f['level'] == 'block']
            raise ValidationError(self.env._('Fix the red items first:') + '\n' + '\n'.join(items[:12]))
        Ch = self.env['sb.plan.change'].sudo()
        plan.change_ids.filtered(lambda c: c.state == 'pending').unlink()  # the latest proposal replaces an unanswered one
        seen = set()
        made = Ch.browse()
        for v in vals:
            tid = v.pop('id')
            t = plan.task_ids.filtered(lambda x: x.id == tid) if tid else False
            if t:
                seen.add(t.id)
                diff = {}
                for k in self.CHANGE_FIELDS:
                    new = v.get(k)
                    new = _s(fields.Date.to_date(new)) if k in ('date_from', 'date_to') and new else new
                    if (new or False) != self._cur(t, k) and not (k == 'target_qty' and float(new or 0) == float(t.target_qty or 0)):
                        diff[k] = new
                if diff:
                    made |= Ch.create({'plan_id': plan.id, 'task_id': t.id, 'kind': 'edit', 'vals': diff, 'reason': reason,
                                       'summary': self._describe('edit', t, diff)})
            else:
                v = {k: (_s(fields.Date.to_date(x)) if k in ('date_from', 'date_to') and x else x) for k, x in v.items()}
                made |= Ch.create({'plan_id': plan.id, 'kind': 'add', 'vals': v, 'reason': reason, 'summary': self._describe('add', None, v)})
        for t in plan.task_ids.filtered(lambda x: x.id not in seen):
            made |= Ch.create({'plan_id': plan.id, 'task_id': t.id, 'kind': 'remove', 'reason': reason, 'summary': self._describe('remove', t, {})})
        if not made:
            raise UserError(self.env._('Nothing changed.'))
        if plan.approver_id.user_id:
            e.message_post(body=self.env._('%(n)s proposes %(c)s change(s) to the approved %(m)s plan: %(r)s', n=e.name, c=len(made),
                                           m=plan.label, r=reason), partner_ids=plan.approver_id.user_id.partner_id.ids)
        out = self.month(plan.month)
        out['check'] = res
        return out

    def _apply_change(self, c):
        plan = c.plan_id
        if c.kind == 'add':
            v = dict(c.vals or {})
            self.env['sb.plan.task'].sudo().create(dict(v, plan_id=plan.id, review='approved', added_later=True, sequence=900))
        elif c.kind == 'edit' and c.task_id:
            c.task_id.write(dict(c.vals or {}, review='approved'))
        elif c.kind == 'remove' and c.task_id:
            t = c.task_id
            if t.checkin_ids:  # keep what was already reported; it just stops here
                t.write({'date_to': max(t.date_from, min(t.date_to, self._today())), 'review_note': self.env._('Dropped by agreed change')})
            else:
                t.unlink()

    @api.model
    def decide_changes(self, plan_id, accept_ids=None, refuse_ids=None, note=''):
        """One click: accept (and apply) or refuse the waiting change requests of a plan."""
        plan = self.env['sb.plan'].sudo().browse(int(plan_id))
        e = plan.employee_id
        if not self._can_review(e) or (e == self._me() and not self.env.su):
            raise AccessError(self.env._('Changes to your own plan are accepted by your manager.'))
        pending = plan.change_ids.filtered(lambda c: c.state == 'pending')
        acc = pending.filtered(lambda c: c.id in [int(x) for x in (accept_ids or [])])
        ref = pending.filtered(lambda c: c.id in [int(x) for x in (refuse_ids or [])])
        note = (note or '').strip()
        if ref and len(note) < 3:
            raise ValidationError(self.env._('Say why the change is refused.'))
        now = fields.Datetime.now()
        for c in acc:
            self._apply_change(c)
        acc.write({'state': 'accepted', 'decided_by': self.env.user.id, 'decided_at': now, 'decision_note': note or False})
        ref.write({'state': 'refused', 'decided_by': self.env.user.id, 'decided_at': now, 'decision_note': note})
        if acc or ref:
            e.message_post(body=self.env._('%(m)s plan: %(a)s change(s) accepted, %(r)s refused by %(by)s.%(n)s', m=plan.label, a=len(acc),
                                           r=len(ref), by=self.env.user.name, n=(' ' + note) if note else ''),
                           partner_ids=e.user_id.partner_id.ids if e.user_id else [])
        self.env['sb.plan.gate'].forget(e.user_id)
        return self.review(plan.id)

    @api.model
    def send_back(self, plan_id, reason):
        """The manager can always send a plan back with a reason; every line stays as it was."""
        plan = self.env['sb.plan'].sudo().browse(int(plan_id))
        e = plan.employee_id
        if not self._can_review(e) or (e == self._me() and not self.env.su):
            raise AccessError(self.env._('Not your team.'))
        reason = (reason or '').strip()
        if len(reason) < 5:
            raise ValidationError(self.env._('Say why the plan goes back, so it can be fixed.'))
        if plan.state not in ('submitted', 'approved'):
            raise UserError(self.env._('The plan is not with you.'))
        plan.change_ids.filtered(lambda c: c.state == 'pending').write({'state': 'refused', 'decision_note': reason,
                                                                       'decided_by': self.env.user.id, 'decided_at': fields.Datetime.now()})
        plan.write({'state': 'returned', 'manager_note': reason})
        e.message_post(body=self.env._('%(m)s plan sent back by %(by)s: %(r)s', m=plan.label, by=self.env.user.name, r=reason),
                       partner_ids=e.user_id.partner_id.ids if e.user_id else [])
        self.env['sb.plan.gate'].forget(e.user_id)
        return self.review(plan.id)

    # ------------------------------------------------------------------ settings
    def _require_hr(self):
        if not self._is_hr():
            raise AccessError(self.env._('Planning settings are for HR.'))

    @api.model
    def settings(self):
        self._require_hr()
        c = self.env.company
        people = self._E().search([('company_id', '=', c.id), ('active', '=', True)], order='name')
        return {'config': {'mode': c.sb_pl_mode, 'deadline_day': c.sb_pl_deadline_day, 'next_from_day': c.sb_pl_next_from_day,
                           'week_start': c.sb_pl_week_start, 'checkin': c.sb_pl_checkin, 'grace': c.sb_pl_grace_hours,
                           'report_day': c.sb_pl_report_day, 'min_tasks': c.sb_pl_min_tasks, 'max_tasks': c.sb_pl_max_tasks,
                           'strictness': c.sb_pl_strictness, 'open_areas': (c.sb_pl_open_areas or '').split(','),
                           'require_goal': c.sb_pl_require_goal,
                           'checkin_from': fields.Date.to_string(c.sb_pl_checkin_from) if c.sb_pl_checkin_from else ''},
                'people': [{'id': e.id, 'name': e.name, 'department': e.department_id.name or '', 'job': e.job_title or '',
                            'gated': e.sb_pl_gated, 'strictness': e.sb_pl_strictness or '', 'manager': e.parent_id.name or '',
                            'role_id': e.sb_pl_role_id.id or '',
                            'login': bool(e.user_id)} for e in people],
                'categories': [{'id': x.id, 'name': x.name, 'department_id': x.department_id.id or ''}
                               for x in self.env['sb.plan.category'].sudo().search([('company_id', '=', c.id)])],
                'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search([('company_id', 'in', (c.id, False))])],
                'roles': [{'id': r.id, 'name': r.name, 'description': r.description or '',
                           'items': [{'id': i.id, 'name': i.name, 'done_when': i.done_when or '', 'target_qty': i.target_qty or '',
                                      'unit': i.unit or '', 'mandatory': i.mandatory} for i in r.item_ids]}
                          for r in self.env['sb.plan.role'].sudo().search([('company_id', '=', c.id)])],
                'indicators': [{'id': i.id, 'name': i.name, 'requires_document': i.requires_document}
                               for i in self.env['sb.plan.indicator'].sudo().search([('company_id', 'in', (c.id, False))])]}

    @api.model
    def save_settings(self, cfg):
        self._require_hr()
        w = {'sb_pl_mode': cfg.get('mode') or 'warn', 'sb_pl_deadline_day': int(cfg.get('deadline_day') or 3),
             'sb_pl_next_from_day': int(cfg.get('next_from_day') or 20), 'sb_pl_week_start': cfg.get('week_start') or '5',
             'sb_pl_checkin': cfg.get('checkin') or 'daily', 'sb_pl_grace_hours': int(cfg.get('grace') or 0),
             'sb_pl_report_day': cfg.get('report_day') or '4', 'sb_pl_min_tasks': int(cfg.get('min_tasks') or 1),
             'sb_pl_max_tasks': int(cfg.get('max_tasks') or 50), 'sb_pl_strictness': max(0, min(int(cfg.get('strictness') or 60), 100)),
             'sb_pl_open_areas': ','.join(cfg.get('open_areas') or []), 'sb_pl_require_goal': bool(cfg.get('require_goal')),
             'sb_pl_checkin_from': cfg.get('checkin_from') or False}
        self.env.company.sudo().write(w)
        self.env['sb.plan.gate'].forget()
        return self.settings()

    @api.model
    def set_person(self, employee_id, vals):
        self._require_hr()
        e = self._E().browse(int(employee_id))
        w = {}
        if 'gated' in vals:
            w['sb_pl_gated'] = bool(vals['gated'])
        if 'strictness' in vals:
            w['sb_pl_strictness'] = max(0, min(int(vals['strictness'] or 0), 100))
        if 'role_id' in vals:
            w['sb_pl_role_id'] = int(vals['role_id']) if vals['role_id'] else False
        e.write(w)
        self.env['sb.plan.gate'].forget(e.user_id)
        return True

    @api.model
    def save_category(self, vals):
        self._require_hr()
        C = self.env['sb.plan.category'].sudo()
        w = {'name': (vals.get('name') or '').strip(), 'department_id': int(vals['department_id']) if vals.get('department_id') else False}
        if not w['name']:
            raise ValidationError(self.env._('Name the category.'))
        if vals.get('id'):
            C.browse(int(vals['id'])).write(w)
        else:
            C.create(w)
        return self.settings()

    @api.model
    def remove_category(self, cat_id):
        self._require_hr()
        self.env['sb.plan.category'].sudo().browse(int(cat_id)).active = False
        return self.settings()

    @api.model
    def save_role(self, vals):
        """vals: {id, name, description, items: [{id, name, done_when, target_qty, unit, mandatory}]}"""
        self._require_hr()
        R = self.env['sb.plan.role'].sudo()
        name = (vals.get('name') or '').strip()
        if not name:
            raise ValidationError(self.env._('Name the role.'))
        role = R.browse(int(vals['id'])) if vals.get('id') else R.create({'name': name})
        role.write({'name': name, 'description': (vals.get('description') or '').strip() or False})
        keep = []
        I = self.env['sb.plan.role.item'].sudo()
        for i, it in enumerate(vals.get('items') or []):
            n = (it.get('name') or '').strip()
            if not n:
                continue
            w = {'name': n, 'done_when': (it.get('done_when') or '').strip() or False, 'target_qty': float(it.get('target_qty') or 0),
                 'unit': (it.get('unit') or '').strip() or False, 'mandatory': it.get('mandatory', True) is not False, 'sequence': i}
            rec = I.browse(int(it['id'])) if it.get('id') and I.browse(int(it['id'])).role_id == role else I.create(dict(w, role_id=role.id))
            rec.write(w)
            keep.append(rec.id)
        role.item_ids.filtered(lambda x: x.id not in keep).unlink()
        return self.settings()

    @api.model
    def remove_role(self, role_id):
        self._require_hr()
        self.env['sb.plan.role'].sudo().browse(int(role_id)).active = False
        return self.settings()
