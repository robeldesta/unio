"""The planning gate: people on the planning list plan before they work.

What locks (each only on working days; approved leave and public holidays never lock):
- the month's plan not submitted by the deadline day (a returned plan counts as not submitted),
- a working day's check-ins missing once the grace hours of the next working day have passed,
- last week's report not sent once the grace hours after report day have passed.
Modes: off, watch (recorded only), warn (banner), lock (only Planning and the open areas work).
The server refuses other data while locked; the screen sends the person to Planning with the reasons."""
import time as _time
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .service import month_bounds, week_start

_CACHE = {}
TTL = 90
ALWAYS = ('sb.plan', 'sb.planning', 'ir.', 'res.users', 'res.lang', 'res.company', 'res.currency', 'bus.', 'web',
          'base', 'res.users.settings', 'hr.employee.public', 'res.partner', 'mail.', 'discuss.', 'sb.people',
          'hr.employee', 'res.groups')
AREAS = {'discuss': ('mail.', 'discuss.', 'res.partner'), 'timeoff': ('sb.leave', 'resource.calendar.leaves', 'sb.holidays'),
         'attendance': ('sb.att',), 'people': ('sb.people', 'hr.employee', 'hr.department', 'hr.job', 'hr.version'),
         'performance': ('sb.perf',)}


class SbPlanGate(models.AbstractModel):
    _name = 'sb.plan.gate'
    _description = 'Planning gate'

    def forget(self, user=None):
        if user:
            for k in [k for k in _CACHE if k[1] == user.id]:
                _CACHE.pop(k, None)
        else:
            _CACHE.clear()

    def _now_local(self, e):
        tz = pytz.timezone(e.tz or 'Africa/Addis_Ababa')
        return pytz.UTC.localize(fields.Datetime.now()).astimezone(tz).replace(tzinfo=None)

    @api.model
    def reasons(self, e):
        """[{'code', 'msg', 'page'}] of what this person still owes (empty when nothing)."""
        e = e.sudo()
        c = e.company_id
        if not e.sb_pl_gated or c.sb_pl_mode == 'off':
            return []
        S = self.env['sb.planning'].sudo()
        now = self._now_local(e)
        today = now.date()
        off, _hol = S._off_days(e, today - timedelta(days=14), today)
        out = []
        # on leave today: nothing locks
        on_leave = self.env['sb.leave'].sudo().search_count([('employee_id', '=', e.id), ('state', '=', 'approved'),
                                                            ('date_from', '<=', today), ('date_to', '>=', today)])
        if on_leave:
            return []
        m0, m1, label = month_bounds(today)
        deadline = m0 + timedelta(days=max((c.sb_pl_deadline_day or 3) - 1, 0))
        plan = self.env['sb.plan'].sudo().search([('employee_id', '=', e.id), ('month', '=', m0)])
        if today >= deadline and (not plan or plan.state in ('draft', 'returned')):
            out.append({'code': 'month', 'page': 'month',
                        'msg': self.env._('Submit your %(m)s plan (it was due %(d)s).', m=label, d=format_ethiopian(deadline))
                        if not plan or plan.state == 'draft' else
                        self.env._('Fix the returned tasks of your %s plan and send it back.', label)})
        # check-ins of past working days
        if c.sb_pl_checkin != 'weekly' and plan and plan.state in ('submitted', 'approved'):
            grace = timedelta(hours=c.sb_pl_grace_hours or 0)
            # lines the system counts (litres sold, deliveries ...) need no check-in
            tasks = self.env['sb.plan.task'].sudo().search([('employee_id', '=', e.id), ('plan_id.state', 'in', ('submitted', 'approved')),
                                                            ('date_from', '<=', today), ('date_to', '>=', today - timedelta(days=10)),
                                                            ('measure', '=', False)])
            C = self.env['sb.plan.checkin'].sudo()
            missing_days = []
            since = min(p.first_submitted or today for p in self.env['sb.plan'].sudo().search(
                [('employee_id', '=', e.id), ('state', 'in', ('submitted', 'approved')), ('month_end', '>=', today - timedelta(days=10))]))
            d = max(week_start(today, c) - timedelta(days=7), today - timedelta(days=10), since, c.sb_pl_checkin_from or since)
            step = 2 if c.sb_pl_checkin == 'two' else 1
            workdays = []
            while d < today:
                if d not in off:
                    workdays.append(d)
                d += timedelta(days=1)
            for i, d in enumerate(workdays):
                if step == 2 and i % 2:
                    continue
                nxt = next((x for x in workdays[i + 1:]), today)
                if now < datetime.combine(nxt, time(0)) + grace:
                    continue
                due = tasks.filtered(lambda t: t.date_from <= d <= t.date_to)
                due = due.filtered(lambda t: not C.search_count([('task_id', '=', t.id), ('date', '<', d), ('status', '=', 'done')]))
                have = C.search_count([('task_id', 'in', due.ids), ('date', '=', d)]) if step == 1 else \
                    C.search_count([('task_id', 'in', due.ids), ('date', 'in', [d] + workdays[i + 1:i + 2])])
                if due and have < len(due) and (step == 1 or have == 0):
                    missing_days.append((d, len(due) - have))
            for d, n in missing_days[:3]:
                out.append({'code': 'checkin', 'page': 'week', 'date': fields.Date.to_string(d),
                            'msg': self.env._('Check in for %(d)s (%(n)s %(w)s).', d='%s %s' % (d.strftime('%A'), format_ethiopian(d)), n=n,
                                              w=self.env._('task') if n == 1 else self.env._('tasks'))})
        # last week's report
        ws = week_start(today, c)
        prev = ws - timedelta(days=7)
        report_day = prev + timedelta(days=(int(c.sb_pl_report_day) - prev.weekday()) % 7)
        if (not c.sb_pl_checkin_from or prev >= c.sb_pl_checkin_from) and \
                now >= datetime.combine(report_day + timedelta(days=1), time(0)) + timedelta(hours=c.sb_pl_grace_hours or 0) and \
                self.env['sb.plan.task'].sudo().search_count([('employee_id', '=', e.id), ('plan_id.state', 'in', ('submitted', 'approved')),
                                                              ('plan_id.first_submitted', '<=', report_day),
                                                              ('date_from', '<=', prev + timedelta(days=6)), ('date_to', '>=', prev)]):
            rep = self.env['sb.plan.week'].sudo().search([('employee_id', '=', e.id), ('week', '=', prev)])
            if not rep or rep.state != 'confirmed':
                out.append({'code': 'report', 'page': 'week', 'date': fields.Date.to_string(prev),
                            'msg': self.env._('Send the report of the week of %s.', format_ethiopian(prev))})
        return out

    @api.model
    def state(self, user):
        """{'mode', 'locked', 'warn', 'reasons'} for a login (cached a minute and a half)."""
        if not user or user._is_superuser() or user.id == self.env.ref('base.user_admin').id:
            return {'mode': 'off', 'locked': False, 'warn': False, 'reasons': []}
        key = (self.env.cr.dbname, user.id)
        hit = _CACHE.get(key)
        if hit and hit[0] > _time.time():
            return hit[1]
        e = user.sudo().employee_ids.filtered(lambda x: x.company_id == user.company_id)[:1] or user.sudo().employee_ids[:1]
        mode = (e.company_id or user.company_id).sb_pl_mode if e else 'off'
        rs = self.reasons(e) if e else []
        res = {'mode': mode, 'reasons': rs, 'locked': bool(rs) and mode == 'lock', 'warn': bool(rs) and mode in ('warn', 'lock'),
               'areas': (e.company_id.sb_pl_open_areas or '').split(',') if e else []}
        _CACHE[key] = (_time.time() + TTL, res)
        return res

    @api.model
    def allowed(self, user, model):
        st = self.state(user)
        if not st['locked']:
            return True
        prefixes = list(ALWAYS)
        for a in st.get('areas') or []:
            prefixes += list(AREAS.get(a, ()))
        return any(model == p or model.startswith(p) for p in prefixes)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        if self.env.user and not self.env.user._is_public():
            try:
                st = self.env['sb.plan.gate'].sudo().state(self.env.user)
                info['planning_gate'] = {'locked': st['locked'], 'warn': st['warn'], 'reasons': st['reasons'],
                                         'action': 'sonador_planning.action_planning'}
            except Exception:  # never let the gate break the login
                info['planning_gate'] = {'locked': False, 'warn': False, 'reasons': []}
        return info
