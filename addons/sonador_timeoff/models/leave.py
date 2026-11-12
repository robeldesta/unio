"""Leave under the Labour Proclamation 1156/2019.

Annual leave (art. 77-79): 16 working days for the first year of service, plus one working day for every additional two
years; a part year earns in proportion (art. 77(5)); leave not taken may be postponed but not for more than two years
(art. 79(4)), so each service year's leave lapses two years after that year ends; leave taken uses the oldest first.
Sick leave (art. 85-86): at most six months in any twelve; the first month at 100% of wages, the next two at 50%,
the next three unpaid, counted over the last twelve months.
Maternity (art. 88): 30 consecutive days pre-natal and 90 consecutive days post-natal, with pay.
Special leave (art. 81): 3 working days with pay for marriage or the death of a close relative; paternity 3 consecutive
days with full pay; leave without pay up to 5 consecutive days, at most twice in a budget year.
Working days follow the employee's working calendar (48-hour week) and skip public holidays."""
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

ANNUAL_FIRST_YEAR = 16


def annual_entitlement(service_year, country='ET'):
    """Working days for service year n (1 = first year). Uganda: 21 working days a year (Employment Act 2006, s. 54)."""
    if country == 'UG':
        return 21
    return ANNUAL_FIRST_YEAR + (service_year - 1) // 2


class SbLeaveType(models.Model):
    _name = 'sb.leave.type'
    _description = 'Leave type'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection([('annual', 'Annual leave'), ('sick', 'Sick leave'), ('maternity_pre', 'Maternity, pre-natal'),
                             ('maternity_post', 'Maternity, post-natal'), ('paternity', 'Paternity'), ('marriage', 'Marriage'),
                             ('bereavement', 'Bereavement'), ('unpaid', 'Leave without pay'), ('union', 'Trade union'),
                             ('court', 'Court / public duty'), ('other', 'Other')], required=True)
    unit = fields.Selection([('working', 'Working days'), ('calendar', 'Consecutive calendar days')], default='working', required=True)
    max_days = fields.Float('Most per request', help='0 = no limit.')
    max_per_year = fields.Integer('Times per budget year', help='0 = no limit.')
    paid = fields.Boolean(default=True)
    needs_document = fields.Boolean('Needs a document', help='e.g. medical certificate for sick leave.')
    gender = fields.Selection([('female', 'Women'), ('male', 'Men')], string='Only for')
    legal_ref = fields.Char('Legal reference')
    color = fields.Char(default='#5D8DA8')
    active = fields.Boolean(default=True)


class SbLeave(models.Model):
    _name = 'sb.leave'
    _description = 'Leave request'
    _order = 'date_from desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    type_id = fields.Many2one('sb.leave.type', required=True)
    kind = fields.Selection(related='type_id.kind', store=True)
    date_from = fields.Date(required=True, index=True)
    date_to = fields.Date(required=True, index=True)
    days = fields.Float(compute='_compute_days', store=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting approval'), ('approved', 'Approved'),
                              ('refused', 'Refused'), ('cancelled', 'Cancelled')], default='draft', required=True, index=True)
    reason = fields.Char()
    attachment_id = fields.Many2one('ir.attachment', 'Document')
    approver_id = fields.Many2one('hr.employee', 'Decided by')
    manager_ok_id = fields.Many2one('hr.employee', 'Approved by the department manager',
                                    help='First approval; HR approves it then (department manager, then HR).')
    decided_on = fields.Date()
    refuse_reason = fields.Char()
    # sick leave pay (art. 86), set when approved
    days_full_pay = fields.Float()
    days_half_pay = fields.Float()
    days_unpaid = fields.Float()

    @api.depends('date_from', 'date_to', 'type_id.unit', 'employee_id')
    def _compute_days(self):
        for l in self:
            l.days = l._count_days() if l.date_from and l.date_to and l.type_id else 0.0

    def _count_days(self, date_from=None, date_to=None):
        self.ensure_one()
        df, dt = date_from or self.date_from, date_to or self.date_to
        if dt < df:
            return 0.0
        if self.type_id.unit == 'calendar':
            return float((dt - df).days + 1)
        return self.env['sb.leave.service'].working_days(self.employee_id, df, dt)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for l in self:
            if l.date_to < l.date_from:
                raise ValidationError(self.env._('The leave ends before it starts.'))


class SbLeaveAdjust(models.Model):
    _name = 'sb.leave.adjust'
    _description = 'Annual leave adjustment'
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today,
                       help='Days added count as earned on this date (they lapse two years later); days removed count as taken.')
    days = fields.Float(required=True, help='Positive adds annual leave (e.g. opening balance), negative removes it.')
    note = fields.Char(required=True)
