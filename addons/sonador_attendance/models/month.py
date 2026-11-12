"""Days worked and the OT sheet of a payroll month, from the records only (Robel, 4 Nov 2026):
- exempt from attendance (managers, drivers, field purchasers ...): 30 days (their days employed in the month);
- everyone else: days worked = the working days up to today with a check-in or check-out record (the attendance
  machine, a check-in from the field, the browser or kiosk) plus approved annual and sick leave days (department manager,
  then HR); Sundays and public holidays are not working days. Paid days (what payroll pays, basic / 30 x these) = days
  worked + the Sundays and holidays + the days the machines were off (nobody recorded), at most 30;
- OT sheet: normal, night, weekend and holiday hours of the records (not refused); holiday hours at most 8 for each public
  holiday of the month.
Payroll pays from this when the company says so; nothing is stored, entered or approved here."""
from collections import Counter
from datetime import timedelta

from odoo import api, fields, models

LEAVE_COLS = {'annual': 'annual_leave', 'sick': 'sick_leave', 'maternity_pre': 'other_leave', 'maternity_post': 'other_leave'}
COLS = ('days_worked', 'paid_days', 'annual_leave', 'sick_leave', 'other_leave', 'absences', 'late_days', 'late_min', 'ot_day', 'ot_night', 'ot_rest', 'ot_holiday')


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_att_sheet_pay = fields.Boolean('Payroll pays days worked and overtime from the attendance records',
                                      help='Paid days and overtime hours of each month come from the records '
                                           '(Attendance > Days worked), not from leave alone.')


class SbAttMonth(models.AbstractModel):
    _name = 'sb.att.month'
    _description = 'Days worked and overtime of a month, from the records'

    @api.model
    def people(self, company, df, dt):
        """Paid people of the month: on a salary, employed at some point of it."""
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        people = E.search([('company_id', '=', company.id), '|', ('active', '=', True), '&',
                           ('departure_date', '>=', df), ('departure_date', '<=', dt)], order='name')
        return people.filtered(lambda e: (e.current_version_id.wage or 0.0) > 0)

    @api.model
    def rows(self, company, df, dt, people=None):
        """{employee id: {days_worked, annual_leave, sick_leave, other_leave, absences, late_days, ot_day, ot_night,
        ot_rest, ot_holiday}} for the month df..dt."""
        G = self.env['sb.att.engine']
        P = self.env['sb.att.punch'].sudo()
        Day = self.env['sb.att.day'].sudo()
        people = self.people(company, df, dt) if people is None else people
        today = fields.Date.context_today(self)
        upto = min(dt, today)  # absences only for the days that have passed
        month = company.sb_pay_days or 30.0
        hol = G._holidays(company, df, dt, {})
        present = set()
        if people:
            lo, _x = G._bounds(people[0], df)
            _y, hi = G._bounds(people[0], dt)
            for p in P.search([('employee_id', 'in', people.ids), ('time', '>=', lo), ('time', '<', hi)]):
                present.add((p.employee_id.id, G.day_of(p.employee_id, p.time)))
        leaves = {}
        for lv in self.env['sb.leave'].sudo().search([('employee_id', 'in', people.ids), ('state', '=', 'approved'),
                                                      ('date_from', '<=', dt), ('date_to', '>=', df)]):
            d = max(lv.date_from, df)
            while d <= min(lv.date_to, dt):
                leaves[(lv.employee_id.id, d)] = lv
                d += timedelta(days=1)
        ot, late, late_min = {}, Counter(), Counter()
        tracked = people.filtered('sb_att_tracked')
        for d in Day.search([('employee_id', 'in', tracked.ids), ('date', '>=', df), ('date', '<=', dt)]):
            if d.late:
                late[d.employee_id.id] += 1
                late_min[d.employee_id.id] += d.late
            if d.ot_state == 'refused':
                continue
            o = ot.setdefault(d.employee_id.id, dict.fromkeys(('ot_day', 'ot_night', 'ot_rest', 'ot_holiday'), 0))
            for col in o:
                o[col] += d[col]
        all_days = [df + timedelta(days=i) for i in range((dt - df).days + 1)]
        machine = G.machine_days(company, df, dt)  # a day the machines recorded nobody is no absence (HR enters it by hand)
        out = {}
        for e in people:
            employed = [d for d in all_days if G._employed_paid(e, d)]
            r = dict.fromkeys(COLS, 0.0)
            unpaid = 0.0
            for d in employed:
                lv = leaves.get((e.id, d))
                if not lv or d.weekday() == 6 or d in hol:
                    continue
                r[LEAVE_COLS.get(lv.kind) or 'other_leave'] += 1
                if lv.kind == 'unpaid' or not lv.type_id.paid:
                    unpaid += 1
            if e.sb_att_tracked:
                # today counts once there is a record: the day is not over
                past = [d for d in employed if d < today or (d == today and (e.id, d) in present)] if upto == today else \
                    [d for d in employed if d <= upto]
                working = [d for d in past if d.weekday() != 6 and d not in hol]
                r['absences'] = float(len([d for d in working if (e.id, d) not in leaves and (e.id, d) not in present and d in machine]))
                on_leave = len([d for d in working if (e.id, d) in leaves and (e.id, d) not in present
                                and not (leaves[(e.id, d)].kind == 'unpaid' or not leaves[(e.id, d)].type_id.paid)])
                r['days_worked'] = float(len([d for d in working if (e.id, d) in present]) + on_leave)
                # paid: + Sundays and holidays, + days the machines were off (no record from anyone: nothing to deduct)
                off = len([d for d in working if d not in machine and (e.id, d) not in present and (e.id, d) not in leaves])
                r['paid_days'] = min(r['days_worked'] + len(past) - len(working) + off, month)
                for col, mins in ot.get(e.id, {}).items():
                    r[col] = round(mins / 60.0, 2)
                r['ot_holiday'] = min(r['ot_holiday'], 8.0 * len([d for d in employed if d in hol]))
                r['late_days'] = float(late[e.id])
                r['late_min'] = float(late_min[e.id])
            else:  # exempt: the month's days employed (30)
                r['days_worked'] = r['paid_days'] = max(min(float(len(employed)), month) - unpaid, 0.0)
            out[e.id] = r
        return out
