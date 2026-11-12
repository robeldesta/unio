"""The attendance engine: punches -> worked day.

A day runs from 4 am to 4 am local time (a 1 am check-out closes the day before). Browser, phone and kiosk punches say
in or out and are paired; device punches are read as first in, last out. Hours are measured against the person's
working schedule (Ethiopia: 8 hours a day, 48 a week, art. 61), less the schedule's breaks. Overtime is the work
before the start or after the end of the schedule (only blocks of at least the company minimum), all work on the weekly
rest day or a public holiday, split as art. 68 pays it: 6 am-10 pm 1.5x, 10 pm-6 am 1.75x, rest day 2x, holiday 2.5x.
It counts only once a manager approves it (art. 66(3): overtime is worked on the employer's express instructions)."""
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

DAY_START = 4  # hours: punches before 4 am belong to the previous day
LIMIT_DAY, LIMIT_WEEK = 4 * 60, 12 * 60  # Ethiopia art. 67(2); per country in sonador_books locale.OVERTIME
from odoo.addons.sonador_books.models.locale import overtime_rules  # noqa: E402


class SbAttEngine(models.AbstractModel):
    _name = 'sb.att.engine'
    _description = 'Attendance engine'

    # ------------------------------------------------------------------ calendar
    def _tz(self, e):
        cal = e.resource_calendar_id or e.company_id.resource_calendar_id
        return pytz.timezone(e.tz or (cal and cal.tz) or 'Africa/Addis_Ababa')

    def _version(self, e, day):
        try:
            return e._get_version(day) or e.current_version_id
        except Exception:
            return e.current_version_id

    def _windows(self, e, day, cache):
        """Scheduled [(from_h, to_h)] for the day, from the person's working schedule."""
        v = self._version(e, day)
        cal = v.resource_calendar_id or e.resource_calendar_id or e.company_id.resource_calendar_id
        key = ('cal', cal.id, day.weekday())
        if key not in cache:
            cache[key] = sorted((a.hour_from, a.hour_to) for a in cal.attendance_ids
                                if int(a.dayofweek) == day.weekday() and a.day_period != 'lunch') if cal else \
                ([(8.0, 12.0), (13.0, 17.0)] if day.weekday() < 6 else [])
        return cache[key]

    def _holidays(self, company, df, dt, cache):
        key = ('hol', company.id, df, dt)
        if key not in cache:
            cache[key] = self.env['sb.leave.service'].sudo()._holiday_dates(df, dt, company)
        return cache[key]

    def _leaves(self, employees, df, dt):
        out = {}
        for lv in self.env['sb.leave'].sudo().search([('employee_id', 'in', employees.ids), ('state', '=', 'approved'),
                                                      ('date_from', '<=', dt), ('date_to', '>=', df)]):
            d = max(lv.date_from, df)
            while d <= min(lv.date_to, dt):
                out[(lv.employee_id.id, d)] = lv.type_id.name
                d += timedelta(days=1)
        return out

    def _employed(self, e, day):
        start = e.current_version_id.contract_date_start or e.version_id.contract_date_start
        if start and day < start:
            return False
        if e.departure_date and day > e.departure_date:
            return False
        if e.sb_att_since and day < e.sb_att_since:
            return False
        return True

    def machine_days(self, company, df, dt):
        """Days the attendance machines were working: records from at least a third of the people tracked. On other days
        (power cut, machine off or not sending) nobody is absent; HR enters such days by hand."""
        tz = company.resource_calendar_id.tz or 'Africa/Addis_Ababa'
        tracked = self.env['hr.employee'].sudo().search_count([('company_id', '=', company.id), ('sb_att_tracked', '=', True)])
        need = max(3, int(tracked / 3))
        lo = datetime.combine(df, time(0)) - timedelta(days=1)
        hi = datetime.combine(dt, time(0)) + timedelta(days=2)
        self.env.cr.execute("""select ((time at time zone 'UTC' at time zone %s) - %s * interval '1 hour')::date d
                               from sb_att_punch where company_id = %s and source in ('device', 'import') and time >= %s and time < %s
                               and employee_id is not null group by d having count(distinct employee_id) >= %s""",
                            (tz, DAY_START, company.id, lo, hi, need))
        return {r[0] for r in self.env.cr.fetchall()}

    def employment_start(self, e):
        """First day on the payroll: the contract start; without one, the first attendance record (else the day the
        person was added), so nobody is absent before they joined."""
        cache = self.env.cr.cache.setdefault('sb_att_start', {})
        if e.id not in cache:
            start = e.current_version_id.contract_date_start or e.version_id.contract_date_start
            if not start:
                first = self.env['sb.att.punch'].sudo().search([('employee_id', '=', e.id)], order='time', limit=1)
                start = self.day_of(e, first.time) if first else e.create_date.date()
            cache[e.id] = start
        return cache[e.id]

    def _employed_paid(self, e, day):
        """On the payroll that day: from the start (contract, else first record) to the departure date."""
        start = self.employment_start(e)
        return not (start and day < start) and not (e.departure_date and day > e.departure_date)

    def _counts_absence(self, e, day):
        live = e.company_id.sb_att_live
        return bool(e.sb_att_tracked and live and day >= live and self._employed(e, day))

    def day_of(self, e, utc_dt):
        """The attendance day of a UTC time."""
        local = pytz.UTC.localize(utc_dt).astimezone(self._tz(e))
        return (local - timedelta(hours=DAY_START)).date()

    def _bounds(self, e, day):
        tz = self._tz(e)
        start = tz.localize(datetime.combine(day, time(DAY_START))).astimezone(pytz.UTC).replace(tzinfo=None)
        return start, start + timedelta(days=1)

    def _dedup(self, punches, machine_gap=0):
        """Punches of a day without repeats: the same kind within a minute (and, with machine_gap, machine punches within
        that many seconds: pressing twice at the door)."""
        ps = []
        for p in punches:
            if ps and ((p.time - ps[-1].time).total_seconds() < 60 and p.kind == ps[-1].kind or
                       (p.time - ps[-1].time).total_seconds() < machine_gap and p.kind == 'auto' and ps[-1].kind == 'auto'):
                continue
            ps.append(p)
        return ps

    def _overnight(self, e, by_day):
        """Back in the evening and out the next morning (a night shift or overtime through the night): the morning punch
        closes the evening's stay, on the evening's day. Seen as an odd number of machine punches ending in the evening
        and the next day starting before 9 with an odd number too."""
        tz = self._tz(e)
        loc = lambda t: pytz.UTC.localize(t).astimezone(tz)  # noqa: E731
        for d in sorted(by_day):
            ps, nxt = by_day.get(d) or [], by_day.get(d + timedelta(days=1)) or []
            a, b = self._dedup(ps, 600), self._dedup(nxt, 600)
            if not a or not b or any(p.kind != 'auto' for p in a + b):
                continue
            if len(a) % 2 == 1 and len(a) >= 3 and len(b) % 2 == 1 and loc(a[-1].time).hour >= 17 and loc(b[0].time).hour < 9 \
                    and (b[0].time - a[-1].time).total_seconds() <= 16 * 3600:
                moved = [p for p in nxt if p.time < b[0].time + timedelta(minutes=10)]
                by_day[d] = ps + moved
                by_day[d + timedelta(days=1)] = [p for p in nxt if p not in moved]

    # ------------------------------------------------------------------ one day
    def _pairs(self, punches):
        """[(in, out or None, punch_in, punch_out)] in UTC, from punches sorted by time."""
        ps = self._dedup(punches)
        if not ps:
            return []
        if all(p.kind in ('in', 'out') for p in ps):
            pairs, cur = [], None
            for p in ps:
                if p.kind == 'in':
                    if cur is None:
                        cur = p
                elif cur is not None:
                    pairs.append((cur.time, p.time, cur, p))
                    cur = None
            if cur is not None:
                pairs.append((cur.time, None, cur, None))
            if pairs:
                return pairs
        ms = self._dedup(punches, 600)
        if len(ms) >= 4 and len(ms) % 2 == 0 and all(p.kind == 'auto' for p in ms) and all(
                (ms[i + 1].time - ms[i].time).total_seconds() >= 1800 for i in range(0, len(ms), 2)) and all(
                (ms[i + 1].time - ms[i].time).total_seconds() >= 3600 for i in range(1, len(ms) - 1, 2)):
            # machine punches in and out more than once (left and came back): each pair is a stay
            return [(ms[i].time, ms[i + 1].time, ms[i], ms[i + 1]) for i in range(0, len(ms), 2)]
        first, last = ps[0], ps[-1]
        if len(ps) == 1 or (last.time - first.time).total_seconds() < 120:
            return [(first.time, None, first, None)]
        return [(first.time, last.time, first, last)]

    def compute(self, e, day, punches, ctx):
        """Values of sb.att.day for one person and day (punches: that day's, sorted)."""
        company = e.company_id
        tz = self._tz(e)
        grace = company.sb_att_grace or 0
        ot_min = company.sb_att_ot_min or 0
        hol = self._holidays(company, ctx['df'], ctx['dt'], ctx)
        holiday = day in hol
        windows = [] if holiday else self._windows(e, day, ctx)
        leave = ctx['leaves'].get((e.id, day))
        tracked = e.sb_att_tracked
        now = fields.Datetime.now()
        today = self.day_of(e, now)

        def loc(utc):
            return pytz.UTC.localize(utc).astimezone(tz).replace(tzinfo=None)

        base = datetime.combine(day, time(0))
        win = [(base + timedelta(hours=a), base + timedelta(hours=b)) for a, b in windows]
        gaps = [(win[i][1], win[i + 1][0]) for i in range(len(win) - 1) if win[i + 1][0] > win[i][1]]
        start, end = (win[0][0], win[-1][1]) if win else (None, None)
        vals = {'employee_id': e.id, 'company_id': company.id, 'date': day, 'expected': int(sum((b - a).total_seconds() for a, b in win) // 60),
                'first_in': False, 'last_out': False, 'open': False, 'worked': 0, 'late': 0, 'early': 0,
                'ot_day': 0, 'ot_night': 0, 'ot_rest': 0, 'ot_holiday': 0, 'outside': any(p.outside for p in punches)}
        pairs = self._pairs(punches)
        if not pairs:
            if holiday:
                vals['status'] = 'holiday'
            elif leave:
                vals['status'] = 'leave'
            elif not win:
                vals['status'] = 'rest'
            else:
                vals['status'] = 'absent'
            return vals
        vals['first_in'] = pairs[0][0]
        closed = [(loc(a), loc(b)) for a, b, _x, _y in pairs if b]
        is_open = pairs[-1][1] is None
        vals['open'] = is_open
        if not is_open:
            vals['last_out'] = pairs[-1][1]
        spans = list(closed)
        if is_open and day == today:
            spans.append((loc(pairs[-1][0]), loc(now)))
        worked = 0.0
        for a, b in spans:
            worked += (b - a).total_seconds()
            for ga, gb in gaps:
                o = (min(b, gb) - max(a, ga)).total_seconds()
                if o > 0:
                    worked -= o
        vals['worked'] = int(max(worked, 0) // 60)
        if tracked and start:
            fi = loc(pairs[0][0])
            late_after = e.sb_att_late_after or company.sb_att_late_after
            if late_after:  # a clock time (e.g. 8:15): late minutes count from it
                thr = base + timedelta(minutes=round(late_after * 60))
                if fi > thr:
                    vals['late'] = int((fi - thr).total_seconds() // 60)
            elif (fi - start).total_seconds() > grace * 60:
                vals['late'] = int((fi - start).total_seconds() // 60)
            if not is_open:
                lo = loc(pairs[-1][1])
                if (end - lo).total_seconds() > grace * 60:
                    vals['early'] = int((end - lo).total_seconds() // 60)
        # overtime, minute by minute over the closed spans
        pre, post = {'day': 0, 'night': 0}, {'day': 0, 'night': 0}
        whole = 0
        for a, b in closed:
            m = a.replace(second=0, microsecond=0)
            while m < b:
                if holiday or not win:
                    in_gap = any(ga <= m < gb for ga, gb in gaps)
                    if not in_gap:
                        whole += 1
                else:
                    bucket = pre if m < start else (post if m >= end else None)
                    if bucket is not None:
                        bucket['night' if (m.hour >= 22 or m.hour < 6) else 'day'] += 1
                m += timedelta(minutes=1)
        if holiday:
            vals['ot_holiday'] = whole if whole >= ot_min else 0
        elif not win:
            vals['ot_rest'] = whole if whole >= ot_min else 0
        else:
            for b in (pre, post):
                if b['day'] + b['night'] >= ot_min:
                    vals['ot_day'] += b['day']
                    vals['ot_night'] += b['night']
        if is_open and day < today:
            vals['status'] = 'missing'
        elif vals['late']:
            vals['status'] = 'late'
        else:
            vals['status'] = 'present'
        return vals

    # ------------------------------------------------------------------ rebuild
    @api.model
    def rebuild(self, employees, df, dt, create_empty=False):
        """Recompute the stored days of these people between two dates (absent days are stored only with create_empty)."""
        Day = self.env['sb.att.day'].sudo()
        P = self.env['sb.att.punch'].sudo()
        ctx = {'df': df, 'dt': dt, 'leaves': self._leaves(employees, df, dt)}
        existing = {(d.employee_id.id, d.date): d for d in Day.search([('employee_id', 'in', employees.ids),
                                                                       ('date', '>=', df), ('date', '<=', dt)])}
        # a device marked "overtime: manual only" (the factory) governs the people who work there: their home device is the one
        # with most of their punches over the last 90 days. Someone from the office who punches once at the factory keeps
        # the overtime of that day.
        home = {}
        for emp, dev, cnt in P._read_group([('employee_id', 'in', employees.ids), ('device_id', '!=', False),
                                            ('time', '>=', fields.Datetime.to_datetime(df) - timedelta(days=90)),
                                            ('time', '<', fields.Datetime.to_datetime(dt) + timedelta(days=2))],
                                           ['employee_id', 'device_id'], ['__count']):
            if cnt > home.get(emp.id, (None, 0))[1]:
                home[emp.id] = (dev, cnt)
        n = 0
        for e in employees:
            lo, _x = self._bounds(e, df)
            _y, hi = self._bounds(e, dt)
            by_day = {}
            for p in P.search([('employee_id', '=', e.id), ('time', '>=', lo), ('time', '<', hi + timedelta(days=1))], order='time, id'):
                by_day.setdefault(self.day_of(e, p.time), []).append(p)
            self._overnight(e, by_day)
            d = df
            while d <= dt:
                ps = by_day.get(d, [])
                row = existing.get((e.id, d))
                if ps or row or (create_empty and self._counts_absence(e, d)):
                    vals = self.compute(e, d, ps, ctx)
                    hd = home.get(e.id, (None, 0))[0]
                    if hd.ot_manual if hd else any(p.device_id.ot_manual for p in ps):  # overtime entered by hand only
                        vals.update(ot_day=0, ot_night=0, ot_rest=0, ot_holiday=0)
                    if vals['status'] != 'absent':
                        vals['absence'] = False
                    elif not (row and row.absence):
                        vals['absence'] = 'unexcused'
                    tot = vals['ot_day'] + vals['ot_night'] + vals['ot_rest'] + vals['ot_holiday']
                    if not tot:
                        vals['ot_state'] = 'none'
                    elif not row or row.ot_state == 'none' or (row.ot_day, row.ot_night, row.ot_rest, row.ot_holiday) != (
                            vals['ot_day'], vals['ot_night'], vals['ot_rest'], vals['ot_holiday']):
                        vals['ot_state'] = 'pending'
                    if row:
                        if not ps and not create_empty and vals['status'] in ('rest', 'holiday') and not row.note:
                            row.unlink()
                        else:
                            row.write(vals)
                    elif ps or vals['status'] in ('absent', 'leave'):
                        Day.create(vals)
                    n += 1
                d += timedelta(days=1)
        return n

    @api.model
    def rebuild_punch_days(self, punches):
        """Recompute the days touched by these punches."""
        todo = {}
        for p in punches.filtered('employee_id'):
            day = self.day_of(p.employee_id, p.time)
            a, b = todo.get(p.employee_id, (day - timedelta(days=1), day))  # the evening before may close with this punch
            todo[p.employee_id] = (min(a, day), max(b, day))
        for e, (a, b) in todo.items():
            self.rebuild(e, a, b)

    @api.model
    def settle(self, df, dt, company=None):
        company = company or self.env.company
        E = self.env['hr.employee'].sudo().search([('company_id', '=', company.id), ('sb_att_tracked', '=', True)])
        return self.rebuild(E, df, dt, create_empty=True)

    @api.model
    def limits(self, days):
        """Art. 67(2) overtime limits broken: {day_id: text} for days over 4 hours or weeks over 12."""
        out, week = {}, {}
        for d in days:
            if d.ot_state == 'refused':
                continue
            k = (d.employee_id.id, d.date.isocalendar()[:2])
            week[k] = week.get(k, 0) + d.ot_total
        for d in days:
            msgs = []
            rules = overtime_rules(d.employee_id.company_id)
            if d.ot_total > rules['limit_day']:
                msgs.append(self.env._('over %s hours of overtime in a day', rules['limit_day'] // 60))
            if week.get((d.employee_id.id, d.date.isocalendar()[:2]), 0) > rules['limit_week']:
                msgs.append(self.env._('over %s hours of overtime that week', rules['limit_week'] // 60))
            if msgs:
                out[d.id] = ', '.join(msgs) + ' (%s)' % rules['law']
        return out
