"""Calls of the Attendance screens: clock in/out, who is in today, timesheets, overtime approval, the monthly report
with the art. 27 lateness/absence counts, kiosk, and setup (sites, devices, rules, device users)."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, format_ethiopian
from odoo.addons.sonador_books.models.locale import overtime_rules

LATE_COUNT, ABSENT_DAYS = 8, 5  # art. 27(1)(a)-(b), in six months, after a written warning



def _hhmm(hours):
    """8.25 -> '08:15'; nothing -> ''."""
    if not hours:
        return ''
    m = int(round(hours * 60))
    return '%02d:%02d' % (m // 60, m % 60)


def _hours(text):
    """'08:15' -> 8.25; '' -> 0."""
    text = (text or '').strip()
    if not text:
        return 0.0
    h, _x, m = text.partition(':')
    return int(h) + int(m or 0) / 60.0

def _s(d):
    return fields.Date.to_string(d) if d else False


def _h(minutes):
    return round((minutes or 0) / 60.0, 2)


def _pay_month(day):
    """The pay month (Ethiopian month of the books, e.g. Tikimt 11 Oct - 9 Nov) holding a date: (name, first, last)."""
    from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_year_of
    fy = fiscal_year_of(day)
    p = next(p for p in fiscal_periods(fy) if p[2] <= day <= p[3])
    return p[1], p[2], p[3]


class SbAttService(models.AbstractModel):
    _name = 'sb.att.service'
    _description = 'Attendance'

    # ------------------------------------------------------------------ access
    def _P(self):
        return self.env['sb.people']

    def _G(self):
        return self.env['sb.att.engine']

    def _is_hr(self):
        return self._P()._is_hr()

    def _me(self):
        return self._P()._me()

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    def _visible(self, include_left=False):
        """People whose attendance I can see: HR everyone, managers their team (and themselves), others themselves."""
        dom = [('company_id', '=', self.env.company.id)]
        if not include_left:
            dom.append(('active', '=', True))
        if self._is_hr():
            return self._E().search(dom, order='name')
        me = self._me()
        if not me:
            return self._E()
        return self._E().search(dom + [('id', 'child_of', me.id)], order='name')

    def _can_manage(self, e):
        me = self._me()
        return self._is_hr() or bool(me and e != me and e in self._E().search([('id', 'child_of', me.id)]))

    def _check_manage(self, e):
        if not self._can_manage(e):
            raise AccessError(self.env._('Only HR or %s\'s manager can change this.', e.name))

    def _require_hr(self):
        if not self._is_hr():
            raise AccessError(self.env._('Only HR officers can do this.'))

    def _local(self, e, utc):
        if not utc:
            return False
        return pytz.UTC.localize(utc).astimezone(self._G()._tz(e)).strftime('%H:%M')

    def _today(self, e=None):
        e = e or self._me()
        return self._G().day_of(e, fields.Datetime.now()) if e else fields.Date.context_today(self)

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        me = self._me()
        c = self.env.company
        team = self._visible()
        return {'is_hr': self._is_hr(), 'is_manager': bool(me and len(team - me)), 'me': me.id or False,
                'ot_approver': self._ot_can_approve(),
                'me_name': me.name or '', 'company': c.name, 'today': _s(self._today()), 'today_ec': format_ethiopian(self._today()),
                'rules': {'grace': c.sb_att_grace, 'ot_min': c.sb_att_ot_min, 'geofence': c.sb_att_geofence, 'need_gps': c.sb_att_need_gps,
                          'live': _s(c.sb_att_live)},
                'ot': overtime_rules(c),
                'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search(
                    [('company_id', 'in', (c.id, False))], order='name')],
                'people': [{'id': e.id, 'name': e.name} for e in team]}

    # ------------------------------------------------------------------ my clock
    def _punch_row(self, p):
        return {'id': p.id, 'time': self._local(p.employee_id, p.time) if p.employee_id else p.time.strftime('%H:%M'),
                'kind': p.kind, 'source': dict(p._fields['source']._description_selection(self.env)).get(p.source),
                'src': p.source, 'site': p.site_id.name or '', 'outside': p.outside, 'distance': p.distance,
                'map': 'https://maps.google.com/?q=%s,%s' % (p.latitude, p.longitude) if (p.latitude or p.longitude) else '',
                'note': p.note or '', 'device': p.device_id.name or '', 'by': p.by_id.name if p.source == 'manual' else ''}

    def _day_punches(self, e, day):
        lo, hi = self._G()._bounds(e, day)
        return self.env['sb.att.punch'].sudo().search([('employee_id', '=', e.id), ('time', '>=', lo), ('time', '<', hi)], order='time, id')

    @api.model
    def my_clock(self, employee_id=False):
        e = self._E().browse(int(employee_id)) if employee_id else self._me()
        if not e:
            return {'none': True}
        if e != self._me() and e not in self._visible():
            raise AccessError(self.env._('Not your team.'))
        today = self._today(e)
        G = self._G()
        G.rebuild(e, today, today)
        Day = self.env['sb.att.day'].sudo()
        row = Day.search([('employee_id', '=', e.id), ('date', '=', today)])
        punches = self._day_punches(e, today)
        last = punches[-1:] if punches else punches
        is_in = bool(row.open) if row else False
        since = False
        if is_in:
            pairs = G._pairs(punches)
            since = fields.Datetime.to_string(pairs[-1][0]) + 'Z' if pairs else False
        monday = today - timedelta(days=today.weekday())
        week_rows = {d.date: d for d in Day.search([('employee_id', '=', e.id), ('date', '>=', monday), ('date', '<=', monday + timedelta(days=6))])}
        week = []
        for i in range(7):
            d = monday + timedelta(days=i)
            r = week_rows.get(d)
            week.append({'date': _s(d), 'label': d.strftime('%a'), 'ec': format_ethiopian(d), 'hours': _h(r.worked) if r else 0,
                         'expected': _h(r.expected) if r else 0, 'status': r.status if r else ('future' if d > today else ''),
                         'today': d == today})
        _l, m0, _m1 = _pay_month(today)
        mrows = Day.search([('employee_id', '=', e.id), ('date', '>=', m0), ('date', '<=', today)])
        return {'employee': {'id': e.id, 'name': e.name}, 'today': _s(today), 'today_ec': format_ethiopian(today),
                'is_in': is_in, 'since': since, 'worked_today': row.worked if row else 0,
                'expected_today': row.expected if row else 0, 'late_today': row.late if row else 0,
                'punches': [self._punch_row(p) for p in punches], 'week': week,
                'week_hours': round(sum(w['hours'] for w in week), 2),
                'month': {'present': len(mrows.filtered(lambda r: r.status in ('present', 'late', 'missing'))),
                          'late': len(mrows.filtered(lambda r: r.late)), 'late_min': sum(mrows.mapped('late')),
                          'absent': len(mrows.filtered(lambda r: r.status == 'absent')),
                          'hours': _h(sum(mrows.mapped('worked'))),
                          'ot_ok': _h(sum(mrows.filtered(lambda r: r.ot_state == 'approved').mapped('ot_total'))),
                          'ot_wait': _h(sum(mrows.filtered(lambda r: r.ot_state == 'pending').mapped('ot_total')))},
                'anywhere': e.sb_att_anywhere, 'last': last and last.kind}

    def _place(self, e, lat, lon, acc):
        c = e.company_id
        vals = {}
        if lat or lon:
            site, dist, inside = self.env['sb.att.site'].nearest(lat, lon, c)
            vals.update({'latitude': lat, 'longitude': lon, 'accuracy': int(acc or 0), 'site_id': site.id if inside else False,
                         'distance': int(dist) if dist is not None else 0,
                         'outside': bool(site) and not inside and not e.sb_att_anywhere and c.sb_att_geofence != 'off'})
            if vals['outside'] and c.sb_att_geofence == 'block':
                raise UserError(self.env._('You are %(d)s m from %(site)s. Clock in at a work site (ask HR if you work in the field).',
                                           d='{:,}'.format(int(dist)), site=site.name))
        elif c.sb_att_need_gps and not e.sb_att_anywhere and self.env['sb.att.site'].sudo().search_count([('company_id', '=', c.id)]):
            raise UserError(self.env._('Allow location in the browser to clock in (the padlock or "aA" next to the address).'))
        return vals

    @api.model
    def clock(self, lat=0.0, lon=0.0, acc=0, mobile=False, note=''):
        e = self._me()
        if not e:
            raise UserError(self.env._('Your login is not linked to a person in People. Ask HR.'))
        today = self._today(e)
        self._G().rebuild(e, today, today)
        row = self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '=', today)])
        kind = 'out' if row and row.open else 'in'
        vals = {'employee_id': e.id, 'company_id': e.company_id.id, 'time': fields.Datetime.now(), 'kind': kind,
                'source': 'mobile' if mobile else 'web', 'note': note or False,
                'ip': request.httprequest.remote_addr if request else False}
        vals.update(self._place(e, lat, lon, acc))
        p = self.env['sb.att.punch'].sudo().create(vals)
        self._G().rebuild(e, today, today)
        res = self.my_clock()
        res['done'] = {'kind': kind, 'site': p.site_id.name or '', 'outside': p.outside, 'distance': p.distance}
        return res

    # ------------------------------------------------------------------ today board
    @api.model
    def board(self, department_id=False):
        people = self._visible()
        if department_id:
            people = people.filtered(lambda e: e.department_id.id == int(department_id))
        people = people.filtered('sb_att_tracked')
        G = self._G()
        if not people:
            return {'groups': [], 'counts': {}}
        today = self._today(people[:1])
        G.rebuild(people, today, today)
        rows = {d.employee_id.id: d for d in self.env['sb.att.day'].sudo().search([('employee_id', 'in', people.ids), ('date', '=', today)])}
        ctx = {'df': today, 'dt': today, 'leaves': G._leaves(people, today, today)}
        now_local = {}
        out = []
        for e in people:
            if not G._employed(e, today):
                continue
            r = rows.get(e.id)
            first = self._day_punches(e, today)[:1] if r else []
            if r:
                st = 'in' if r.open else 'out'
                last = self._local(e, r.last_out)
                worked = r.worked
                # machine scans carry no in/out: before the end of the working day the last scan is not leaving
                if st == 'out' and not self._day_punches(e, today).filtered(lambda p: p.kind == 'out'):
                    tz = G._tz(e)
                    if e.id not in now_local:
                        now_local[e.id] = pytz.UTC.localize(fields.Datetime.now()).astimezone(tz).replace(tzinfo=None)
                    win = G._windows(e, today, ctx)
                    end = datetime.combine(today, time(0)) + timedelta(hours=win[-1][1]) if win else None
                    if end and r.first_in and now_local[e.id] < end:
                        st, last = 'in', ''
                        worked = int((now_local[e.id] - pytz.UTC.localize(r.first_in).astimezone(tz).replace(tzinfo=None)).total_seconds() // 60)
                v = {'status': r.status, 'late': r.late, 'worked': worked, 'first': self._local(e, r.first_in),
                     'last': last, 'site': first and first.site_id.name or '', 'outside': r.outside,
                     'source': first and dict(first._fields['source']._description_selection(self.env)).get(first.source) or ''}
            else:
                v = G.compute(e, today, [], ctx)
                st = v['status']
                if st == 'absent':
                    tz = G._tz(e)
                    if e.id not in now_local:
                        now_local[e.id] = pytz.UTC.localize(fields.Datetime.now()).astimezone(tz).replace(tzinfo=None)
                    win = G._windows(e, today, ctx)
                    start = datetime.combine(today, time(0)) + timedelta(hours=win[0][0]) if win else None
                    st = 'notyet' if start and now_local[e.id] < start + timedelta(minutes=e.company_id.sb_att_grace or 0) else 'missing_in'
                v = {'status': v['status'], 'late': 0, 'worked': 0, 'first': '', 'last': '', 'site': '', 'outside': False, 'source': '',
                     'leave': ctx['leaves'].get((e.id, today), '')}
            card = self._P()._card(e)
            card.update(v)
            card['board'] = st
            out.append(card)
        # the machines' records reach the server in batches: before today's arrive, nobody is "absent"
        last = self.env['sb.att.punch'].sudo().search([('company_id', '=', self.env.company.id), ('source', 'in', ('device', 'import'))],
                                                      order='time desc', limit=1)
        lo, _x = G._bounds(people[0], today)
        waiting = bool(self.env['sb.att.device'].sudo().search_count([('company_id', '=', self.env.company.id)])) and \
            (not last or last.time < lo)
        if waiting:
            for c in out:
                if c['board'] == 'missing_in':
                    c['board'] = 'nodata'
        counts = Counter(c['board'] for c in out)
        counts['late'] = len([c for c in out if c['late']])
        return {'people': out, 'counts': dict(counts), 'today': _s(today), 'today_ec': format_ethiopian(today), 'total': len(out),
                'waiting': waiting, 'last_record': self._local(people[0], last.time) and '%s %s' % (
                    format_ethiopian(G.day_of(people[0], last.time)), self._local(people[0], last.time)) if last else ''}

    # ------------------------------------------------------------------ timesheets
    @api.model
    def grid(self, month, department_id=False, search=''):
        label, m0, m1 = _pay_month(fields.Date.to_date(month))
        people = self._visible(include_left=True).filtered(lambda e: e.active or (e.departure_date and e.departure_date >= m0))
        if department_id:
            people = people.filtered(lambda e: e.department_id.id == int(department_id))
        if search:
            s = search.lower()
            people = people.filtered(lambda e: s in (e.name or '').lower() or s in (e.sb_code or '').lower())
        G = self._G()
        today = self._today()
        rows = defaultdict(dict)
        for d in self.env['sb.att.day'].sudo().search([('employee_id', 'in', people.ids), ('date', '>=', m0), ('date', '<=', m1)]):
            rows[d.employee_id.id][d.date] = d
        ctx = {'df': m0, 'dt': m1, 'leaves': G._leaves(people, m0, m1)}
        hol = G._holidays(self.env.company, m0, m1, ctx)
        days = []
        d = m0
        while d <= m1:
            days.append({'date': _s(d), 'n': d.day, 'wd': d.strftime('%a')[:2], 'ec': format_ethiopian(d),
                         'hol': hol.get(d, ''), 'sun': d.weekday() == 6, 'today': d == today})
            d += timedelta(days=1)
        out = []
        for e in people:
            cells, tot = [], Counter()
            d = m0
            while d <= m1:
                r = rows[e.id].get(d)
                if r:
                    c = {'s': r.status, 'h': _h(r.worked), 'late': r.late, 'ot': _h(r.ot_total), 'ots': r.ot_state,
                         'in': (self._local(e, r.first_in) or '').lstrip('0'),
                         'open': r.open, 'abs': r.absence or '', 'out': r.outside, 'note': bool(r.note)}
                    tot['worked'] += r.worked
                    tot['late'] += 1 if r.late else 0
                    tot['ot'] += r.ot_total if r.ot_state == 'approved' else 0
                    tot['otp'] += r.ot_total if r.ot_state == 'pending' else 0
                    tot['absent'] += 1 if r.status == 'absent' else 0
                    tot['present'] += 1 if r.status in ('present', 'late', 'missing') else 0
                    tot['missing'] += 1 if r.status == 'missing' else 0
                elif d >= today or not G._employed(e, d):
                    c = {'s': 'future' if d >= today else 'na'}
                else:
                    v = G.compute(e, d, [], ctx)
                    c = {'s': v['status'] if (v['status'] != 'absent' or G._counts_absence(e, d)) else ('untracked' if not e.sb_att_tracked else 'na')}
                    if c['s'] == 'absent':
                        c['abs'] = 'unexcused'
                        tot['absent'] += 1
                cells.append(c)
                d += timedelta(days=1)
            out.append({'id': e.id, 'name': e.name, 'code': e.sb_code or '', 'department': e.department_id.name or '',
                        'tracked': e.sb_att_tracked, 'cells': cells,
                        'tot': {'hours': _h(tot['worked']), 'present': tot['present'], 'late': tot['late'], 'absent': tot['absent'],
                                'ot': _h(tot['ot']), 'otp': _h(tot['otp']), 'missing': tot['missing']}})
        return {'month': _s(m0), 'last': _s(m1), 'label': label, 'label_ec': '%s to %s' % (_s(m0), _s(m1)),
                'days': days, 'people': out}

    @api.model
    def day(self, employee_id, day):
        e = self._E().browse(int(employee_id))
        if e != self._me() and e not in self._visible(include_left=True):
            raise AccessError(self.env._('Not your team.'))
        d = fields.Date.to_date(day)
        G = self._G()
        G.rebuild(e, d, d)
        r = self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '=', d)])
        ctx = {'df': d, 'dt': d, 'leaves': G._leaves(e, d, d)}
        v = G.compute(e, d, self._day_punches(e, d), ctx)
        win = G._windows(e, d, ctx) if v['status'] != 'holiday' else []
        hol = G._holidays(e.company_id, d, d, ctx).get(d, '')
        week = self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '>=', d - timedelta(days=d.weekday())),
                                                     ('date', '<=', d - timedelta(days=d.weekday()) + timedelta(days=6))])
        lim = G.limits(week).get(r.id, '') if r else ''
        fmt = lambda x: '%02d:%02d' % (int(x), round((x - int(x)) * 60))
        return {'employee': {'id': e.id, 'name': e.name, 'department': e.department_id.name or ''},
                'date': _s(d), 'ec': format_ethiopian(d), 'weekday': d.strftime('%A'),
                'schedule': ', '.join('%s–%s' % (fmt(a), fmt(b)) for a, b in win) or (hol and self.env._('Public holiday: %s', hol)) or self.env._('Rest day'),
                'leave': ctx['leaves'].get((e.id, d), ''),
                'row': r and {'id': r.id, 'status': r.status, 'worked': r.worked, 'expected': r.expected, 'late': r.late, 'early': r.early,
                              'ot_day': r.ot_day, 'ot_night': r.ot_night, 'ot_rest': r.ot_rest, 'ot_holiday': r.ot_holiday,
                              'ot_total': r.ot_total, 'ot_state': r.ot_state, 'ot_by': r.ot_by_id.name or '', 'ot_note': r.ot_note or '',
                              'absence': r.absence or '', 'note': r.note or '', 'open': r.open} or
                       {'id': False, 'status': v['status'], 'worked': 0, 'expected': v['expected'], 'late': 0, 'early': 0, 'ot_total': 0,
                        'ot_state': 'none', 'absence': 'unexcused' if v['status'] == 'absent' else '', 'note': '', 'open': False},
                'limit': lim, 'punches': [self._punch_row(p) for p in self._day_punches(e, d)],
                'can_edit': self._can_manage(e), 'is_hr': self._is_hr()}

    @api.model
    def add_punch(self, employee_id, day, hhmm, kind, note=''):
        self._require_hr()  # only HR enters attendance by hand
        e = self._E().browse(int(employee_id))
        if not (note or '').strip():
            raise ValidationError(self.env._('Say why the punch is added (e.g. "forgot to clock out", "device down").'))
        d = fields.Date.to_date(day)
        hh, mm = [int(x) for x in hhmm.split(':')]
        local = datetime.combine(d, time(hh, mm))
        if hh < 4:
            local += timedelta(days=1)
        utc = self._G()._tz(e).localize(local).astimezone(pytz.UTC).replace(tzinfo=None)
        if utc > fields.Datetime.now():
            raise ValidationError(self.env._('That time is in the future.'))
        # a device day becomes an in/out day once someone sets it by hand
        punches = self._day_punches(e, d)
        punches.filtered(lambda p: p.kind == 'auto').write({'kind': 'in'})
        if punches.filtered(lambda p: p.source == 'device') and len(punches) > 1:
            dev = punches.sorted('time')
            dev[-1:].filtered(lambda p: p.source == 'device').write({'kind': 'out'})
        self.env['sb.att.punch'].sudo().create({'employee_id': e.id, 'company_id': e.company_id.id, 'time': utc, 'kind': kind,
                                                'source': 'manual', 'note': note.strip(), 'by_id': self.env.user.id})
        e.message_post(body=self.env._('Attendance %(d)s: %(k)s at %(t)s added by %(u)s (%(n)s).', d=format_ethiopian(d),
                                       k=kind, t=hhmm, u=self.env.user.name, n=note.strip()))
        return self.day(e.id, day)

    @api.model
    def remove_punch(self, punch_id):
        self._require_hr()
        p = self.env['sb.att.punch'].sudo().browse(int(punch_id))
        e = p.employee_id
        d = self._G().day_of(e, p.time)
        e.message_post(body=self.env._('Attendance %(d)s: punch at %(t)s (%(s)s) removed by %(u)s.', d=format_ethiopian(d),
                                       t=self._local(e, p.time), s=p.source, u=self.env.user.name))
        p.unlink()
        self._G().rebuild(e, d, d)
        return self.day(e.id, _s(d))

    @api.model
    def set_day(self, employee_id, day, vals):
        """Absence (unexcused / excused / unpaid) and note on a day."""
        e = self._E().browse(int(employee_id))
        self._check_manage(e)
        d = fields.Date.to_date(day)
        Day = self.env['sb.att.day'].sudo()
        r = Day.search([('employee_id', '=', e.id), ('date', '=', d)])
        if not r:
            self._G().rebuild(e, d, d, create_empty=True)
            r = Day.search([('employee_id', '=', e.id), ('date', '=', d)])
        if not r:
            raise UserError(self.env._('Nothing to record on this day.'))
        w = {}
        if 'absence' in vals and r.status == 'absent':
            w['absence'] = vals['absence'] or 'unexcused'
        if 'note' in vals:
            w['note'] = vals['note'] or False
        r.write(w)
        return self.day(e.id, day)

    # ------------------------------------------------------------------ overtime
    @api.model
    def overtime(self, state='pending', month=False):
        people = self._visible(include_left=True)
        me = self._me()
        if not self._is_hr():
            people -= me
        dom = [('employee_id', 'in', people.ids), ('ot_total', '>', 0)]
        if state:
            dom.append(('ot_state', '=', state))
        if month:
            _l, m0, m1 = _pay_month(fields.Date.to_date(month))
            dom += [('date', '>=', m0), ('date', '<=', m1)]
        Day = self.env['sb.att.day'].sudo()
        rows = Day.search(dom, order='date desc', limit=500)
        weeks = Day.search([('employee_id', 'in', rows.employee_id.ids), ('ot_total', '>', 0),
                            ('date', '>=', min(rows.mapped('date')) - timedelta(days=7)) if rows else ('id', '=', 0),
                            ('date', '<=', max(rows.mapped('date')) + timedelta(days=7)) if rows else ('id', '=', 0)])
        lim = self._G().limits(weeks)
        return [{'id': r.id, 'employee_id': r.employee_id.id, 'name': r.employee_id.name, 'department': r.department_id.name or '',
                 'date': _s(r.date), 'ec': format_ethiopian(r.date), 'weekday': r.date.strftime('%a'),
                 'first': self._local(r.employee_id, r.first_in), 'last': self._local(r.employee_id, r.last_out),
                 'day': _h(r.ot_day), 'night': _h(r.ot_night), 'rest': _h(r.ot_rest), 'holiday': _h(r.ot_holiday), 'total': _h(r.ot_total),
                 'state': r.ot_state, 'by': r.ot_by_id.name or '', 'note': r.ot_note or '', 'limit': lim.get(r.id, '')} for r in rows]

    def _ot_can_approve(self):
        """Overtime approver set for the company: only that person decides; else HR and managers."""
        appr = self.env.company.sb_att_ot_approver_id
        return self.env.su or (self.env.user == appr if appr else bool(self._is_hr() or self._me()))

    @api.model
    def decide_overtime(self, day_ids, decision, note=''):
        rows = self.env['sb.att.day'].sudo().browse([int(i) for i in day_ids])
        me = self._me()
        appr = self.env.company.sb_att_ot_approver_id
        if appr and not self.env.su and self.env.user != appr:
            raise AccessError(self.env._('Overtime is approved by %s.', appr.name))
        for r in rows:
            if r.employee_id == me and not self.env.su:
                raise AccessError(self.env._('You cannot approve your own overtime.'))
            if not appr:
                self._check_manage(r.employee_id)
        rows.write({'ot_state': 'approved' if decision == 'approve' else 'refused', 'ot_by_id': me.id or False, 'ot_note': note or False})
        return len(rows)

    # ------------------------------------------------------------------ monthly report
    @api.model
    def report(self, month, department_id=False):
        label, m0, m1 = _pay_month(fields.Date.to_date(month))
        today = self._today()
        upto = min(m1, today)
        six = upto - relativedelta(months=6) + timedelta(days=1)
        people = self._visible(include_left=True).filtered(
            lambda e: e.sb_att_tracked and (e.active or (e.departure_date and e.departure_date >= m0)))
        if department_id:
            people = people.filtered(lambda e: e.department_id.id == int(department_id))
        G = self._G()
        settled = min(m1, today - timedelta(days=1))  # today is not over: nobody is absent yet
        if settled >= m0:
            G.rebuild(people, m0, settled, create_empty=True)
        Day = self.env['sb.att.day'].sudo()
        rows = defaultdict(lambda: Day.browse())
        for d in Day.search([('employee_id', 'in', people.ids), ('date', '>=', m0), ('date', '<=', m1)]):
            rows[d.employee_id.id] |= d
        half = defaultdict(Counter)
        for d in Day.search([('employee_id', 'in', people.ids), ('date', '>=', six), ('date', '<=', upto)]):
            if d.late:
                half[d.employee_id.id]['late'] += 1
            if d.status == 'absent' and d.absence != 'excused':
                half[d.employee_id.id]['absent'] += 1
        warns = defaultdict(list)
        for w in self.env['sb.att.warning'].sudo().search([('employee_id', 'in', people.ids)]):
            warns[w.employee_id.id].append({'date': _s(w.date), 'ec': format_ethiopian(w.date), 'kind': w.kind, 'note': w.note or ''})
        # working days so far and absences as payroll counts them (the records; Sundays and holidays off; leave not absence)
        M = self.env['sb.att.month'].rows(self.env.company, m0, m1, people)
        hol = G._holidays(self.env.company, m0, m1, {})
        work = [m0 + timedelta(days=i) for i in range((upto - m0).days + 1)] if upto >= m0 else []
        work = [d for d in work if d.weekday() != 6 and d not in hol]
        out = []
        for e in people:
            r = rows[e.id]
            wl = [w for w in warns[e.id] if w['date'] >= _s(six)]
            flags = []
            if half[e.id]['late'] >= LATE_COUNT:
                flags.append(self.env._('Late %s times in 6 months', half[e.id]['late']) +
                             ('' if any(w['kind'] == 'late' for w in wl) else self.env._(' (no written warning yet)')))
            if half[e.id]['absent'] >= ABSENT_DAYS:
                flags.append(self.env._('Absent %s days in 6 months', half[e.id]['absent']) +
                             ('' if any(w['kind'] == 'absence' for w in wl) else self.env._(' (no written warning yet)')))
            ok = r.filtered(lambda x: x.ot_state == 'approved')
            out.append({'id': e.id, 'name': e.name, 'code': e.sb_code or '', 'department': e.department_id.name or '',
                        'scheduled': len([d for d in work if G._employed_paid(e, d)]) - int(M[e.id]['annual_leave'] + M[e.id]['sick_leave'] + M[e.id]['other_leave']),
                        'present': len([d for d in work if G._employed_paid(e, d)]) - int(M[e.id]['annual_leave'] + M[e.id]['sick_leave'] + M[e.id]['other_leave'] + M[e.id]['absences']),
                        'rest_worked': len(r.filtered(lambda x: not x.expected and x.status in ('present', 'late', 'missing'))),
                        'late': len(r.filtered('late')), 'late_min': sum(r.mapped('late')),
                        'early': len(r.filtered('early')), 'missing': len(r.filtered(lambda x: x.status == 'missing')),
                        'absent': int(M[e.id]['absences']),
                        'unpaid': len(r.filtered(lambda x: x.status == 'absent' and x.absence == 'unpaid')),
                        'excused': len(r.filtered(lambda x: x.status == 'absent' and x.absence == 'excused')),
                        'leave': len(r.filtered(lambda x: x.status == 'leave')),
                        'hours': _h(sum(r.mapped('worked'))), 'expected': _h(sum(r.filtered(lambda x: x.status != 'leave').mapped('expected'))),
                        'ot_day': _h(sum(ok.mapped('ot_day'))), 'ot_night': _h(sum(ok.mapped('ot_night'))),
                        'ot_rest': _h(sum(ok.mapped('ot_rest'))), 'ot_holiday': _h(sum(ok.mapped('ot_holiday'))),
                        'ot_wait': _h(sum(r.filtered(lambda x: x.ot_state == 'pending').mapped('ot_total'))),
                        'half_late': half[e.id]['late'], 'half_absent': half[e.id]['absent'], 'flags': flags, 'warnings': wl})
        trend = []
        months = [(label, m0, m1)]
        while len(months) < 6:
            months.insert(0, _pay_month(months[0][1] - timedelta(days=1)))
        for name, a, b in months:
            ds = Day.search([('employee_id', 'in', people.ids), ('date', '>=', a), ('date', '<=', b)])
            sched = len(ds.filtered(lambda x: x.expected and x.status not in ('leave', 'holiday')))
            trend.append({'label': name.split(' ')[0], 'late': len(ds.filtered('late')),
                          'absent': len(ds.filtered(lambda x: x.status == 'absent' and x.absence != 'excused')),
                          'ontime': round(100.0 * len(ds.filtered(lambda x: x.status == 'present')) / sched, 1) if sched else 0})
        return {'month': _s(m0), 'last': _s(m1), 'label': label, 'label_ec': '%s to %s' % (_s(m0), _s(m1)),
                'six_from': format_ethiopian(six), 'rows': out, 'trend': trend, 'is_hr': self._is_hr()}

    @api.model
    def warn(self, employee_id, kind, note):
        e = self._E().browse(int(employee_id))
        self._check_manage(e)
        if not (note or '').strip():
            raise ValidationError(self.env._('Write what the warning says.'))
        self.env['sb.att.warning'].sudo().create({'employee_id': e.id, 'kind': kind, 'note': note.strip()})
        e.message_post(body=self.env._('Written warning for %(k)s: %(n)s', k={'late': 'lateness', 'absence': 'absence'}[kind], n=note.strip()))
        return True

    # ------------------------------------------------------------------ a year at a glance (one square a day)
    @api.model
    def year(self, employee_id=False):
        """One row per pay month (the 12 last, as payroll pays them), one square a day: machine on time, machine late, machine with overtime, manual or field,
        sick leave, annual leave, absent, Sunday or holiday. No mark on a day not over, for people exempt from attendance,
        or when the machines recorded (almost) nobody. Hover: in, out, hours, source (and who entered it by hand, when, why)."""
        e = self._E().browse(int(employee_id)) if employee_id else self._me()
        if not e:
            return {}
        if e != self._me() and e not in self._visible(include_left=True):
            raise AccessError(self.env._('Not your team.'))
        G = self._G()
        today = self._today(e)
        months = [_pay_month(today)]
        while len(months) < 12:
            months.insert(0, _pay_month(months[0][1] - timedelta(days=1)))
        start, end = months[0][1], months[-1][2]
        lo, _x = G._bounds(e, start)
        _y, hi = G._bounds(e, today)
        src = defaultdict(set)
        for p in self.env['sb.att.punch'].sudo().search([('employee_id', '=', e.id), ('time', '>=', lo), ('time', '<', hi)]):
            src[G.day_of(e, p.time)].add(p.source)
        rows = {d.date: d for d in self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '>=', start), ('date', '<=', today)])}
        hol = G._holidays(e.company_id, start, today, {})
        leaves = {}
        for lv in self.env['sb.leave'].sudo().search([('employee_id', '=', e.id), ('state', '=', 'approved'),
                                                      ('date_from', '<=', today), ('date_to', '>=', start)]):
            d = max(lv.date_from, start)
            while d <= min(lv.date_to, today):
                leaves[d] = lv
                d += timedelta(days=1)
        machine_days = G.machine_days(e.company_id, start, today)
        manual = defaultdict(list)
        for p in self.env['sb.att.punch'].sudo().search([('employee_id', '=', e.id), ('source', '=', 'manual'),
                                                         ('time', '>=', lo), ('time', '<', hi)]):
            manual[G.day_of(e, p.time)].append(p)
        SRC = {'device': 'machine', 'import': 'field check-in', 'mobile': 'field check-in (phone)', 'web': 'browser',
               'kiosk': 'kiosk', 'manual': 'entered by HR'}
        cells, tot = [], Counter()
        d = start
        while d <= end:
            r = rows.get(d)
            s_ = src.get(d, set())
            k = ''
            if d > today:
                k = 'future'
            elif not G._employed_paid(e, d):
                k = 'out'
            elif s_:
                if 'device' in s_:
                    k = 'late' if r and r.late else 'ot' if r and r.ot_total and r.ot_state != 'refused' else 'ontime'
                else:
                    k = 'manual'
            elif d in leaves:
                k = 'sick' if leaves[d].kind == 'sick' else 'annual'
            elif d in hol or d.weekday() == 6:
                k = 'off'
            elif not e.sb_att_tracked or d == today or d not in machine_days:
                k = 'none'  # exempt, the day not over, or the machines recorded (almost) nobody: no mark
            else:
                k = 'absent'
            tip = []
            if r and r.first_in:
                tip.append('in %s' % self._local(e, r.first_in))
            if r and r.last_out and not r.open:
                tip.append('out %s' % self._local(e, r.last_out))
            if r and r.worked:
                tip.append('%s h' % _h(r.worked))
            if r and r.late:
                tip.append('late %d min' % r.late)
            if r and r.ot_total and r.ot_state != 'refused':
                tip.append('overtime %s h' % _h(r.ot_total))
            if s_ - {'manual'}:
                tip.append(', '.join(sorted(SRC.get(x, x) for x in s_ - {'manual'})))
            for p in manual.get(d, [])[:1]:
                tip.append('entered by %s on %s: %s' % (p.by_id.name or p.create_uid.name, format_ethiopian(p.create_date.date()), p.note or ''))
            if k in ('sick', 'annual'):
                tip.append(leaves[d].type_id.name)
            if k == 'off':
                tip.append(hol.get(d) or 'Sunday')
            tot[k] += 1
            cells.append({'d': _s(d), 'k': k, 'ec': format_ethiopian(d), 'info': ' · '.join(tip), 'wd': d.strftime('%a'),
                          'g': d.strftime('%d %b'), 'in': self._local(e, r.first_in) if r and r.first_in else '',
                          'out': self._local(e, r.last_out) if r and r.last_out and not r.open else '',
                          'h': _h(r.worked) if r and r.worked else 0, 'late': r.late if r else 0,
                          'ot': _h(r.ot_total) if r and r.ot_total and r.ot_state != 'refused' else 0,
                          'src': ', '.join(sorted(SRC.get(x, x) for x in s_)), 'dow': (d.weekday() + 1) % 7})
            d += timedelta(days=1)
        by_day = {c['d']: c for c in cells}
        M = self.env['sb.att.month']
        out = []
        for name, df, dt in months:
            m = M.rows(e.company_id, df, dt, e).get(e.id) or {}
            n = (dt - df).days + 1
            out.append({'name': name, 'from': _s(df), 'to': _s(dt),
                        'cells': [by_day[_s(df + timedelta(days=i))] for i in range(n)],
                        'worked': m.get('days_worked', 0), 'paid': m.get('paid_days', 0), 'absent': m.get('absences', 0),
                        'late': m.get('late_days', 0), 'leave': m.get('annual_leave', 0) + m.get('sick_leave', 0) + m.get('other_leave', 0),
                        'ot': round(sum(m.get(k, 0) for k in ('ot_day', 'ot_night', 'ot_rest', 'ot_holiday')), 2),
                        'employed': any(G._employed_paid(e, df + timedelta(days=i)) for i in range(n))})
        return {'employee_id': e.id, 'name': e.name, 'tracked': e.sb_att_tracked, 'months': out[::-1],
                'tot': dict(tot), 'can_enter': self._is_hr(), 'width': max(len(x['cells']) for x in out)}

    @api.model
    def hr_day(self, employee_id, day, time_in, time_out, note):
        """HR enters a day by hand (e.g. the machine was off in a power cut): in and out times with the reason. HR only."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        if not (note or '').strip():
            raise ValidationError(self.env._('Say why it is entered by hand (e.g. "power cut, machine off").'))
        if not time_in:
            raise ValidationError(self.env._('Enter at least the time in.'))
        self.add_punch(e.id, day, time_in, 'in', note)
        if time_out:
            self.add_punch(e.id, day, time_out, 'out', note)
        return self.year(e.id)

    @api.model
    def hr_day_clear(self, employee_id, day):
        """HR removes what it entered by hand on a day (machine and field records stay)."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        d = fields.Date.to_date(day)
        ps = self._day_punches(e, d).filtered(lambda p: p.source == 'manual')
        ps.unlink()
        self._G().rebuild(e, d, d)
        e.message_post(body=self.env._('Attendance %(d)s: the hand entry removed by %(u)s.', d=format_ethiopian(d), u=self.env.user.name))
        return self.year(e.id)

    # ------------------------------------------------------------------ days worked and the OT sheet (from the records)
    @api.model
    def month(self, period=False):
        """Days worked and the OT sheet of a payroll month, from the records (what payroll pays)."""
        self._require_hr()
        c = self.env.company
        periods = []
        for key, label in self.env['sb.payroll.run']._period_choices():
            fy, no = (int(x) for x in key.split('-'))
            p = fiscal_periods(fy)[no - 1]
            periods.append({'key': key, 'label': label, 'name': p[1], 'from': p[2], 'to': p[3]})
        today = fields.Date.context_today(self)
        p = next((x for x in periods if x['key'] == period), None) or next((x for x in periods if x['from'] <= today <= x['to']), periods[-1])
        M = self.env['sb.att.month']
        people = M.people(c, p['from'], p['to'])
        rows = M.rows(c, p['from'], p['to'], people)
        lines = [dict(rows[e.id], id=e.id, name=e.name, code=e.sb_code or '', department=e.department_id.name or '',
                      exempt=not e.sb_att_tracked) for e in people]
        lines.sort(key=lambda x: x['name'].lower())
        cols = ('days_worked', 'paid_days', 'annual_leave', 'sick_leave', 'other_leave', 'absences', 'late_days', 'late_min', 'ot_day', 'ot_night', 'ot_rest', 'ot_holiday')
        hol = self._G()._holidays(c, p['from'], p['to'], {})
        return {'period': p['key'], 'name': p['name'], 'from': _s(p['from']), 'to': _s(p['to']),
                'from_ec': format_ethiopian(p['from']), 'to_ec': format_ethiopian(p['to']),
                'upto': _s(min(p['to'], today)), 'open_month': today < p['to'], 'sheet_pay': c.sb_att_sheet_pay,
                'periods': [{'key': x['key'], 'label': x['label']} for x in periods],
                'holidays': [{'date': _s(d), 'ec': format_ethiopian(d), 'name': n} for d, n in sorted(hol.items())],
                'lines': lines, 'tot': {k: round(sum(x[k] for x in lines), 2) for k in cols}}

    # ------------------------------------------------------------------ kiosk
    @api.model
    def kiosk(self, code):
        """A shared tablet: the person types their PIN or badge number (set on the profile by HR)."""
        if not self._is_hr() and not self.env.user.has_group('sonador_attendance.group_kiosk'):
            raise AccessError(self.env._('This login is not allowed to run the kiosk.'))
        code = (code or '').strip()
        if not code:
            raise UserError(self.env._('Type your PIN.'))
        e = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True),
                              '|', ('pin', '=', code), ('barcode', '=', code)], limit=2)
        if len(e) != 1:
            raise UserError(self.env._('PIN not recognised.'))
        today = self._today(e)
        self._G().rebuild(e, today, today)
        row = self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '=', today)])
        kind = 'out' if row and row.open else 'in'
        self.env['sb.att.punch'].sudo().create({'employee_id': e.id, 'company_id': e.company_id.id, 'time': fields.Datetime.now(),
                                                'kind': kind, 'source': 'kiosk'})
        self._G().rebuild(e, today, today)
        row = self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('date', '=', today)])
        return {'name': e.name, 'kind': kind, 'time': self._local(e, fields.Datetime.now()), 'late': row.late if kind == 'in' else 0,
                'worked': row.worked, 'avatar': '/web/image/hr.employee.public/%d/avatar_128' % e.id}

    # ------------------------------------------------------------------ setup
    @api.model
    def setup(self):
        self._require_hr()
        c = self.env.company
        E = self._E().search([('company_id', '=', c.id), ('active', '=', True)], order='name')
        P = self.env['sb.att.punch'].sudo()
        counts = {(d.id, b): (n, hi, lo) for d, b, n, hi, lo in P._read_group(
            [('company_id', '=', c.id), ('device_id', '!=', False)], ['device_id', 'bio_id'], ['__count', 'time:max', 'time:min'])}
        bios = self.env['sb.att.bio'].sudo().search([('company_id', '=', c.id), ('device_id.active', '=', True)])
        by_emp = defaultdict(dict)
        for b in bios.filtered('employee_id'):
            by_emp[b.employee_id.id].setdefault(b.device_id.id, []).append(b.bio_id)
        unmapped = []
        for b in bios.filtered(lambda x: not x.employee_id):
            n, hi, lo = counts.get((b.device_id.id, b.bio_id), (0, None, None))
            unmapped.append({'id': b.id, 'device': b.device_id.name, 'bio_id': b.bio_id, 'name': b.name or '', 'punches': n,
                             'first': _s(lo.date()) if lo else '', 'last': _s(hi.date()) if hi else ''})
        unmapped.sort(key=lambda u: (-(u['last'] >= _s(fields.Date.context_today(self) - timedelta(days=60))), -u['punches']))
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        host = base.split('://')[-1].split('/')[0]
        users = self.env['res.users'].sudo().search([('share', '=', False), ('company_ids', 'in', c.id)], order='name')
        return {'rules': {'grace': c.sb_att_grace, 'ot_min': c.sb_att_ot_min, 'geofence': c.sb_att_geofence, 'need_gps': c.sb_att_need_gps,
                          'live': _s(c.sb_att_live), 'ot_approver': c.sb_att_ot_approver_id.id or '', 'sheet_pay': c.sb_att_sheet_pay,
                          'late_after': _hhmm(c.sb_att_late_after)},
                'users': [{'id': u.id, 'name': u.name} for u in users],
                'sites': [{'id': s.id, 'name': s.name, 'latitude': s.latitude, 'longitude': s.longitude, 'radius': s.radius}
                          for s in self.env['sb.att.site'].sudo().search([('company_id', '=', c.id)])],
                'devices': [{'id': d.id, 'name': d.name, 'serial': d.serial or '', 'code': d.code or '', 'token': d.token,
                             'site_id': d.site_id.id or '', 'site': d.site_id.name or '', 'punches': d.punches, 'ot_manual': d.ot_manual,
                             'last_seen': fields.Datetime.to_string(d.last_seen) if d.last_seen else '',
                             'last_punch': fields.Datetime.to_string(d.last_punch) if d.last_punch else ''}
                            for d in self.env['sb.att.device'].sudo().search([('company_id', '=', c.id)])],
                'server': host.split(':')[0], 'port': (host.split(':')[1] if ':' in host else ('443' if base.startswith('https') else '80')),
                'base_url': base,
                'unmapped': unmapped,
                'people': [{'id': e.id, 'name': e.name, 'department': e.department_id.name or '',
                            'bios': {str(k): ', '.join(v) for k, v in by_emp[e.id].items()},
                            'tracked': e.sb_att_tracked, 'anywhere': e.sb_att_anywhere, 'pin': bool(e.pin),
                            'late_after': _hhmm(e.sb_att_late_after)} for e in E]}

    @api.model
    def save_rules(self, vals):
        self._require_hr()
        c = self.env.company.sudo()
        late_after = _hours(vals.get('late_after'))
        relate = abs((c.sb_att_late_after or 0.0) - late_after) > 1e-6 or int(vals.get('grace') or 0) != c.sb_att_grace
        c.write({'sb_att_late_after': late_after})
        c.write({'sb_att_grace': int(vals.get('grace') or 0), 'sb_att_ot_min': int(vals.get('ot_min') or 0),
                 'sb_att_geofence': vals.get('geofence') or 'warn', 'sb_att_need_gps': bool(vals.get('need_gps')),
                 'sb_att_live': vals.get('live') or False,
                 'sb_att_ot_approver_id': int(vals.get('ot_approver') or 0) or False, 'sb_att_sheet_pay': bool(vals.get('sheet_pay'))})
        if relate:  # lateness of the recent weeks is worked out again on the new rule
            self._relate(self._E().search([('company_id', '=', c.id), ('sb_att_tracked', '=', True)]))
        return self.setup()

    def _relate(self, employees):
        today = fields.Date.context_today(self)
        if employees:
            self._G().rebuild(employees, today - timedelta(days=45), today)

    @api.model
    def save_site(self, vals):
        self._require_hr()
        S = self.env['sb.att.site'].sudo()
        w = {'name': (vals.get('name') or '').strip(), 'latitude': float(vals.get('latitude') or 0),
             'longitude': float(vals.get('longitude') or 0), 'radius': int(vals.get('radius') or 200)}
        if not w['name'] or not (w['latitude'] or w['longitude']):
            raise ValidationError(self.env._('Give the site a name and a location.'))
        if vals.get('id'):
            S.browse(int(vals['id'])).write(w)
        else:
            S.create(dict(w, company_id=self.env.company.id))
        return self.setup()

    @api.model
    def delete_site(self, site_id):
        self._require_hr()
        self.env['sb.att.site'].sudo().browse(int(site_id)).active = False
        return self.setup()

    @api.model
    def save_device(self, vals):
        self._require_hr()
        D = self.env['sb.att.device'].sudo()
        w = {'name': (vals.get('name') or '').strip(), 'serial': (vals.get('serial') or '').strip() or False,
             'code': (vals.get('code') or '').strip() or False, 'site_id': int(vals['site_id']) if vals.get('site_id') else False,
             'ot_manual': bool(vals.get('ot_manual'))}
        if not w['name']:
            raise ValidationError(self.env._('Name the device (e.g. "Factory gate").'))
        if vals.get('id'):
            D.browse(int(vals['id'])).write(w)
        else:
            D.create(dict(w, company_id=self.env.company.id))
        return self.setup()

    @api.model
    def delete_device(self, device_id):
        self._require_hr()
        self.env['sb.att.device'].sudo().browse(int(device_id)).active = False
        return self.setup()

    @api.model
    def set_person(self, employee_id, vals):
        self._require_hr()
        e = self._E().browse(int(employee_id))
        w = {}
        for k, f in (('tracked', 'sb_att_tracked'), ('anywhere', 'sb_att_anywhere')):
            if k in vals:
                w[f] = bool(vals[k])
        if 'late_after' in vals:
            w['sb_att_late_after'] = _hours(vals['late_after'])
        if 'pin' in vals and vals['pin']:
            pin = str(vals['pin']).strip()
            if not pin.isdigit() or len(pin) < 4:
                raise ValidationError(self.env._('The kiosk PIN is at least 4 digits.'))
            if self._E().search_count([('company_id', '=', e.company_id.id), ('pin', '=', pin), ('id', '!=', e.id)]):
                raise ValidationError(self.env._('Someone else has this PIN.'))
            w['pin'] = pin
        e.write(w)
        if 'bios' in vals:
            for dev_id, numbers in vals['bios'].items():
                self._set_bios(e, self.env['sb.att.device'].sudo().browse(int(dev_id)), numbers)
        if 'sb_att_late_after' in w:
            self._relate(e)
        if w.get('sb_att_tracked') is False:
            self.env['sb.att.day'].sudo().search([('employee_id', '=', e.id), ('status', '=', 'absent')]).unlink()
        return True

    def _set_bios(self, e, dev, numbers):
        """Make these user numbers of the device (comma separated) this person's, and re-link their punches."""
        Bio = self.env['sb.att.bio'].sudo()
        want = [x.strip() for x in (numbers or '').split(',') if x.strip()]
        for n in want:
            b = Bio.search([('device_id', '=', dev.id), ('bio_id', '=', n)])
            if b.employee_id and b.employee_id != e:
                raise ValidationError(self.env._('%(dev)s user %(n)s is %(who)s.', dev=dev.name, n=n, who=b.employee_id.name))
        drop = Bio.search([('device_id', '=', dev.id), ('employee_id', '=', e.id), ('bio_id', 'not in', want)])
        for b in drop:
            b.employee_id = False
            self._link(b)
        for n in want:
            b = Bio.search([('device_id', '=', dev.id), ('bio_id', '=', n)]) or Bio.create(
                {'company_id': dev.company_id.id, 'device_id': dev.id, 'bio_id': n})
            if b.employee_id != e:
                b.employee_id = e
                self._link(b)

    def _link(self, b):
        """Give the punches of a device user to whoever it now maps to, and rebuild the days touched."""
        P = self.env['sb.att.punch'].sudo()
        ps = P.search([('device_id', '=', b.device_id.id), ('bio_id', '=', b.bio_id)])
        olds = ps.employee_id
        G = self._G()
        spans = {}
        for p in ps.filtered('employee_id'):
            d = G.day_of(p.employee_id, p.time)
            a, z = spans.get(p.employee_id, (d, d))
            spans[p.employee_id] = (min(a, d), max(z, d))
        ps.write({'employee_id': b.employee_id.id or False})
        for e in olds:
            if e in spans:
                G.rebuild(e, *spans[e])
        G.rebuild_punch_days(ps)
        return len(ps)

    @api.model
    def map_bio(self, bio_rec_id, employee_id):
        self._require_hr()
        b = self.env['sb.att.bio'].sudo().browse(int(bio_rec_id))
        b.employee_id = int(employee_id)
        self._link(b)
        return self.setup()
