"""Final settlement on leaving, Labour Proclamation 1156/2019.

Notice: the worker gives 30 days (art. 31); the employer gives 1 month (up to 1 year of service), 2 months (1 to 9
years) or 3 months (over 9 years), 2 months on reduction of workforce (art. 35); without notice the employer pays wages
in lieu (art. 44).
Severance (art. 39), only on the grounds the article lists: (a) closure, (b) termination in violation of the law,
(c) reduction of workforce, (d)(e)(f) resignation for harassment, maltreatment or an ignored safety threat,
(g) disability certified by a medical board, (h) at least five years of service and termination by sickness, death or
the worker's own initiative, (i) resignation because of HIV/AIDS - and not for a worker eligible for pension.
Amount (art. 40): 30 days' wages for the first year (a part year in proportion) plus one third of that for every
additional year, at most 12 months' wages; plus 60 days' wages on reduction of workforce (art. 24(4), 29).
Compensation (art. 41): 30 days' wages when the worker leaves under art. 32(1); 90 days for art. 32(1)(b).
Annual leave not taken is paid (art. 77(4)).
Daily wage = monthly wage / 30 (the company's days a month)."""
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

REASONS = [
    ('resign', 'Resignation'),
    ('resign_cause', 'Resignation for cause: maltreatment, ignored safety threat'),
    ('resign_harassment', 'Resignation for sexual harassment or violence'),
    ('dismiss_notice', 'Termination with notice'),
    ('dismiss_summary', 'Termination without notice'),
    ('dismiss_unlawful', 'Termination found unlawful'),
    ('reduction', 'Reduction of workforce'),
    ('closure', 'Closure of the undertaking'),
    ('disability', 'Disability certified by a medical board'),
    ('sickness', 'Sickness'),
    ('hiv', 'Resignation because of HIV/AIDS'),
    ('death', 'Death'),
    ('fixed_term', 'End of fixed-term contract'),
    ('probation', 'Not confirmed after probation'),
    ('retirement', 'Retirement (pension)'),
    ('mutual', 'Mutual agreement'),
]
SEVERANCE_ALWAYS = {'resign_cause', 'resign_harassment', 'dismiss_unlawful', 'reduction', 'closure', 'disability', 'hiv'}
SEVERANCE_FIVE_YEARS = {'resign', 'sickness', 'death'}


class HrDepartureReason(models.Model):
    _inherit = 'hr.departure.reason'

    sb_code = fields.Selection(REASONS, string='Labour Proclamation ground')


class SbSettlement(models.AbstractModel):
    _name = 'sb.settlement'
    _description = 'Final settlement'

    @api.model
    def ensure_reasons(self):
        R = self.env['hr.departure.reason'].sudo()
        for i, (code, name) in enumerate(REASONS):
            r = R.search([('sb_code', '=', code)], limit=1) or R.search([('name', '=', name)], limit=1)
            if r:
                r.write({'sb_code': code, 'name': name})
            else:
                R.create({'name': name, 'sb_code': code, 'sequence': 30 + i})

    @api.model
    def compute(self, employee, last_day, code, notice_given=True, pension_eligible=False):
        c = employee.company_id
        v = employee.current_version_id
        wage = v.wage or 0.0
        daily = wage / (c.sb_pay_days or 30.0)
        start = v.contract_date_start
        service = relativedelta(last_day, start) if start else relativedelta()
        years = service.years + service.months / 12.0 + service.days / 365.0 if start else 0.0
        out = {'wage': wage, 'daily': round(daily, 2), 'years': round(years, 2), 'lines': []}

        def line(key, label, days, ref):
            amt = round(days * daily, 2)
            out['lines'].append({'key': key, 'label': label, 'days': round(days, 2), 'amount': amt, 'ref': ref})
            return amt
        if c.country_id.code == 'UG':
            return self._compute_ug(employee, last_day, code, notice_given, out, years, wage, daily, line, start)
        # severance
        eligible = code in SEVERANCE_ALWAYS or (code in SEVERANCE_FIVE_YEARS and years >= 5)
        if eligible and not pension_eligible and start:
            first = min(years, 1.0) * 30.0
            more = max(years - 1.0, 0.0) * 10.0
            days = min(first + more, 12 * (c.sb_pay_days or 30.0))
            line('severance', 'Severance pay: 30 days for the first year + 10 days a further year (cap 12 months)', days, 'art. 39-40')
            if code == 'reduction':
                line('severance', 'Reduction of workforce: 60 days', 60, 'art. 40(4)')
        # compensation
        if code == 'resign_harassment':
            line('compensation', 'Compensation: 90 days', 90, 'art. 41(2)')
        elif code == 'resign_cause':
            line('compensation', 'Compensation: 30 days', 30, 'art. 41(1)')
        # notice by the employer
        if code in ('dismiss_notice', 'reduction', 'closure') and not notice_given:
            months = 2 if code == 'reduction' else (1 if years <= 1 else 2 if years <= 9 else 3)
            line('notice_pay', 'Wages in lieu of %d month%s notice' % (months, 's' if months > 1 else ''), months * (c.sb_pay_days or 30.0), 'art. 35, 44')
        # annual leave not taken
        bal = self.env['sb.leave.service'].annual(employee, last_day)
        if bal['available'] > 0:
            line('leave_payout', 'Annual leave not taken: %s working days' % bal['available'], bal['available'], 'art. 77(4)')
        out['total'] = round(sum(l['amount'] for l in out['lines']), 2)
        out['notice'] = ('The worker gives 30 days notice (art. 31).' if code == 'resign' else
                         'Employer notice: %s (art. 35).' % ('2 months' if code == 'reduction' else '1 month' if years <= 1 else
                                                              '2 months' if years <= 9 else '3 months')
                         if code in ('dismiss_notice', 'reduction', 'closure') else '')
        return out

    def _compute_ug(self, employee, last_day, code, notice_given, out, years, wage, daily, line, start):
        """Uganda, Employment Act 2006. Notice by the employer (s. 58): 2 weeks (6 months to 1 year of service), 1 month
        (1-5 years), 2 months (5-10), 3 months (over 10); paid in lieu when not given (s. 58(5)). Summary dismissal for
        gross misconduct needs no notice (s. 69). Severance (s. 87, 89): after 6 months of service, on unfair dismissal,
        redundancy or closure, incapacity or death - one month's wages for each completed year unless agreed otherwise.
        Leave not taken is paid."""
        employer_ends = code in ('dismiss_notice', 'reduction', 'closure', 'dismiss_unlawful', 'disability', 'probation', 'fixed_term')
        weeks = 0 if years < 0.5 else 2 if years < 1 else 4.33 if years < 5 else 8.67 if years < 10 else 13
        if employer_ends and not notice_given and weeks:
            line('notice_pay', 'Pay in lieu of notice (%s weeks)' % round(weeks, 1), weeks * 7 * (employee.company_id.sb_pay_days or 30.0) / 30.4,
                 'Employment Act s. 58')
        if code in ('dismiss_unlawful', 'reduction', 'closure', 'disability', 'death') and years >= 0.5 and start:
            line('severance', 'Severance: one month for each completed year of service', int(years) * (employee.company_id.sb_pay_days or 30.0),
                 'Employment Act s. 87, 89')
        bal = self.env['sb.leave.service'].annual(employee, last_day)
        if bal['available'] > 0:
            line('leave_payout', 'Annual leave not taken: %s working days' % bal['available'], bal['available'], 'Employment Act s. 54')
        out['total'] = round(sum(l['amount'] for l in out['lines']), 2)
        out['notice'] = ('Notice: %s weeks (Employment Act s. 58).' % round(weeks, 1)) if weeks and code != 'dismiss_summary' else ''
        return out
