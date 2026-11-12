"""Payroll takes from Attendance: approved overtime hours by art. 68 rate, and absent days HR marked unpaid
(art. 54(1): wages are paid for work done). Hours typed on the payroll line by hand are kept.
With the company setting "pays days worked and overtime from the attendance records", the records of the month give the
paid days and the overtime hours instead (Attendance > Days worked), and people with no days and no overtime are left out."""
from odoo import fields, models

CATS = (('ot_day', 'ot_day'), ('ot_night', 'ot_night'), ('ot_rest', 'ot_rest'), ('ot_holiday', 'ot_holiday'))


class SbPayrollLine(models.Model):
    _inherit = 'sb.payroll.line'

    sb_ot_from_att = fields.Boolean('Overtime from Attendance', help='The overtime hours were filled from approved attendance.')
    sb_absent_unpaid = fields.Float('Unpaid absent days', help='Absent days marked unpaid in Attendance.')

    def _sb_month(self):
        """Days worked and overtime of the run's month for this person, from the records."""
        r = self.run_id
        return self.env['sb.att.month'].rows(r.company_id, r.date_from, r.date, self.hr_employee_id).get(self.hr_employee_id.id)

    def _sb_att_days(self):
        r = self.run_id
        return self.env['sb.att.day'].sudo().search([('employee_id', '=', self.hr_employee_id.id),
                                                     ('date', '>=', r.date_from), ('date', '<=', r.date)])

    def _sb_fill(self):
        for l in self:
            if not l.hr_employee_id or l.run_id.sb_migrated:
                continue
            if l.run_id.company_id.sb_att_sheet_pay:
                m = l._sb_month() or {}
                if l.ot_hand and not m.get('ot_sheet'):  # hours typed on the line stay, unless HR approved the month's OT sheet
                    l.sb_absent_unpaid = 0.0
                    continue
                l.write(dict({f: m.get(f, 0.0) for f, _s in CATS}, sb_ot_from_att=True, sb_absent_unpaid=0.0, ot_hand=False))
                continue
            days = l._sb_att_days()
            ok = days.filtered(lambda d: d.ot_state == 'approved')
            hours = {f: round(sum(ok.mapped(src)) / 60.0, 2) for f, src in CATS}
            typed = l.ot_hand or any(l[f] for f, _s in CATS)
            if any(hours.values()) and (l.sb_ot_from_att or not typed):
                l.write(dict(hours, sb_ot_from_att=True))
            elif l.sb_ot_from_att and not any(hours.values()):
                l.write(dict({f: 0.0 for f, _s in CATS}, sb_ot_from_att=False))
            l.sb_absent_unpaid = len(days.filtered(lambda d: d.status == 'absent' and d.absence == 'unpaid'))
        return super()._sb_fill()

    def _sb_holiday_cap(self, run):
        """Holiday overtime: at most 8 hours for each holiday of the month."""
        return 8.0 * len(self.env['sb.att.engine']._holidays(run.company_id, run.date_from, run.date, {}))

    def _sb_pay_days(self, e, df, dt, worked, days_month):
        if self.run_id.company_id.sb_att_sheet_pay:
            m = self._sb_month()
            return min(m['paid_days'], days_month) if m else 0.0, 0.0
        return super()._sb_pay_days(e, df, dt, worked, days_month)

    def _period_leave(self, e, df, dt):
        unpaid, half = super()._period_leave(e, df, dt)
        extra = self.env['sb.att.day'].sudo().search_count([('employee_id', '=', e.id), ('date', '>=', df), ('date', '<=', dt),
                                                            ('status', '=', 'absent'), ('absence', '=', 'unpaid')])
        return unpaid + extra, half


class SbPayrollRun(models.Model):
    _inherit = 'sb.payroll.run'

    def action_load(self):
        res = super().action_load()
        for r in self.filtered(lambda r: r.company_id.sb_att_sheet_pay and r.kind == 'regular'):
            # as the sheet says: nobody with no paid day and no overtime is paid this month
            r.line_ids.filtered(lambda l: not l.pay_days and not any(l[f] for f, _s in CATS) and not l.commission and not l.bonus).unlink()
        return res
