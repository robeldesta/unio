"""Filing deadlines: when each return and payment is due, per country. Shipped for Ethiopia, Uganda and Kenya; the
accounting manager can correct a date, add one or switch one off. Months follow the database calendar (Ethiopian months
in Ethiopia, Pagume with Nehase), the same months the books and payroll use."""
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of

AREAS = [('tax', 'Tax'), ('money', 'Money'), ('stock', 'Stock'), ('payroll', 'Payroll'), ('people', 'People'), ('fleet', 'Fleet'),
         ('control', 'Controller'), ('todo', 'To-do')]


class AgendaRule(models.Model):
    _name = 'agenda.rule'
    _description = 'Filing deadline'
    _order = 'country_code, sequence, id'

    name = fields.Char('Return / payment', required=True)
    sequence = fields.Integer(default=10)
    country_code = fields.Char('Country', required=True, help='Two-letter code: ET, UG, KE …')
    area = fields.Selection([('tax', 'Tax'), ('payroll', 'Payroll')], required=True, default='tax',
                            help='Tax: seen by accountants. Payroll: seen by accountants and HR.')
    every = fields.Selection([('month', 'Every month'), ('year', 'Every year')], required=True, default='month')
    due = fields.Selection([('days', 'Days after the month ends'), ('day', 'Day of the next month'),
                            ('fy', 'Months after the year ends')], required=True, default='days')
    value = fields.Integer('Days / day / months', required=True, default=30,
                           help='Days after the month ends; or the day of the next month (0 = its last day); or, for a '
                                'yearly return, the number of months after the year ends (due on the last day of that month).')
    note = fields.Char('What to do', help='Shown under the deadline, e.g. "File and pay on e-tax".')
    active = fields.Boolean(default=True)

    @api.model
    def _deadlines(self, company, date_from, date_to):
        """[(rule, period name, period end, due date)] falling between date_from and date_to."""
        rules = self.search([('country_code', '=', (company.country_id.code or '').upper())])
        if not rules:
            return []
        out = []
        fy0 = fiscal_year_of(date_from)
        periods = []
        for fy in range(fy0 - 2, fiscal_year_of(date_to) + 2):
            periods += [(fy, p) for p in fiscal_periods(fy)]
        for rule in rules:
            if rule.every == 'year':
                for fy in range(fy0 - 2, fiscal_year_of(date_to) + 1):
                    year_end = fiscal_periods(fy)[-1][3]
                    nxt = fiscal_periods(fy + (rule.value - 1) // 12 + 1)
                    due = nxt[(max(rule.value, 1) - 1) % 12][3]
                    if date_from <= due <= date_to:
                        out.append((rule, 'FY %s' % fy, year_end, due))
                continue
            for i, (_fy, (_no, name, _start, end)) in enumerate(periods[:-1]):
                nxt = periods[i + 1][1]
                if rule.due == 'days':
                    due = end + timedelta(days=rule.value)
                elif rule.value <= 0:
                    due = nxt[3]
                else:
                    due = min(nxt[2] + timedelta(days=rule.value - 1), nxt[3])
                if date_from <= due <= date_to:
                    out.append((rule, name, end, due))
        return out
