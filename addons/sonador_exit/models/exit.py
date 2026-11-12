"""Leaving and clearance (Robel, 9 Nov 2026): a resignation or termination is cleared before the person leaves.
Request -> clearance by each department head for what the person holds or owes (vehicle: logistics; laptop, computer,
phone, SIM: IT; staff debtor, loans and customer balance: finance; anything else: HR) -> HR -> general manager -> the
person is paid their days and leaves after the payroll of their last day (Leaves after this payroll)."""
from odoo import api, fields, models

from odoo.addons.sonador_payroll.models.settlement import REASONS

AREAS = [('vehicle', 'Vehicle'), ('it', 'IT equipment'), ('finance', 'Money owed'), ('other', 'Other items')]
KINDS = [('vehicle', 'Vehicle'), ('laptop', 'Laptop'), ('computer', 'Computer'), ('phone', 'Phone'), ('sim', 'SIM card'),
         ('keys', 'Keys'), ('id_card', 'ID card'), ('tools', 'Tools or equipment'), ('other', 'Other')]
KIND_AREA = {'vehicle': 'vehicle', 'laptop': 'it', 'computer': 'it', 'phone': 'it', 'sim': 'it'}
STATES = [('draft', 'Not sent'), ('clearance', 'Clearance'), ('hr', 'With HR'), ('gm', 'With the general manager'),
          ('done', 'Approved: leaves after the last payroll'), ('left', 'Left'), ('cancelled', 'Cancelled')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_exit_vehicle_uid = fields.Many2one('res.users', 'Clears vehicles', help='Empty: the head of the logistics department.')
    sb_exit_it_uid = fields.Many2one('res.users', 'Clears IT equipment', help='Empty: the head of the ICT department.')
    sb_exit_finance_uid = fields.Many2one('res.users', 'Clears money owed', help='Empty: the head of the finance department.')
    sb_exit_gm_uid = fields.Many2one('res.users', 'General manager (last approval)', help='Empty: the head of administration.')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_item_ids = fields.One2many('sb.people.item', 'employee_id', 'Items held', groups='hr.group_hr_user')
    sb_exit_last_day = fields.Date('Last working day (clearance)', groups='hr.group_hr_user', copy=False)


class SbPeopleItem(models.Model):
    _name = 'sb.people.item'
    _description = 'Company item a person holds (laptop, phone, SIM, vehicle, keys ...)'
    _order = 'employee_id, kind, id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    kind = fields.Selection(KINDS, required=True, default='other')
    name = fields.Char('Item', required=True)
    serial = fields.Char('Serial, number or plate')
    issued_on = fields.Date()
    returned_on = fields.Date()
    value = fields.Float('Value')
    note = fields.Char()
    area = fields.Selection(AREAS, compute='_compute_area', store=True)

    @api.depends('kind')
    def _compute_area(self):
        for i in self:
            i.area = KIND_AREA.get(i.kind, 'other')


class SbExit(models.Model):
    _name = 'sb.exit'
    _description = 'Leaving and clearance'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    reason = fields.Selection(REASONS, required=True, default='resign', tracking=True)
    request_date = fields.Date(default=fields.Date.context_today)
    last_day = fields.Date('Last working day', required=True, tracking=True)
    note = fields.Text()
    state = fields.Selection(STATES, default='draft', required=True, tracking=True, index=True)
    requested_by = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    line_ids = fields.One2many('sb.exit.line', 'exit_id')
    hr_by = fields.Many2one('res.users', readonly=True)
    hr_on = fields.Datetime(readonly=True)
    gm_by = fields.Many2one('res.users', readonly=True)
    gm_on = fields.Datetime(readonly=True)
    # Robel, 11 Nov 2026: a payment on leaving by agreement (two, three ... months' salary) is authorised by HR when HR approves
    # the leaving; the payroll of the last day pays it (months x monthly salary), taxed month by month
    pay_months = fields.Float('Separation payment (months of salary)', tracking=True, groups='hr.group_hr_user',
                              help='Months of salary paid on leaving by agreement, set by HR when approving. 0: none.')

    @api.depends('employee_id', 'last_day')
    def _compute_name(self):
        for x in self:
            x.name = '%s, leaving %s' % (x.employee_id.name or '', x.last_day.strftime('%d %b %Y') if x.last_day else '')


class SbExitLine(models.Model):
    _name = 'sb.exit.line'
    _description = 'Clearance line'
    _order = 'exit_id, sequence, id'

    exit_id = fields.Many2one('sb.exit', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    area = fields.Selection(AREAS, required=True)
    name = fields.Char(required=True)
    detail = fields.Char()
    ref_model = fields.Char()
    ref_id = fields.Integer()
    approver_id = fields.Many2one('res.users', 'Clears it')
    state = fields.Selection([('pending', 'Waiting'), ('cleared', 'Cleared'), ('issue', 'Not cleared')], default='pending', required=True)
    note = fields.Char()
    amount_due = fields.Float('Amount owed')
    decided_by = fields.Many2one('res.users', readonly=True)
    decided_on = fields.Datetime(readonly=True)


class SbPayrollLineExit(models.Model):
    _inherit = 'sb.payroll.line'

    def _sb_exit_payment(self):
        """The separation payment HR authorised on the person's leaving, for the payroll that holds their last day."""
        self.ensure_one()
        r, e = self.run_id, self.hr_employee_id
        if not e or not r.date_from or not r.date or r.kind == 'special':
            return None
        x = self.env['sb.exit'].sudo().search([('employee_id', '=', e.id), ('state', 'in', ('gm', 'done', 'left')),
                                               ('hr_by', '!=', False), ('pay_months', '>', 0),
                                               ('last_day', '>=', r.date_from), ('last_day', '<=', r.date)], limit=1)
        if not x:
            return None
        # paid once: not again if another payroll of that leaving already carries it
        other = self.search([('hr_employee_id', '=', e.id), ('run_id', '!=', r.id), ('compensation', '>', 0),
                             ('run_id.date_from', '<=', x.last_day), ('run_id.date', '>=', x.last_day)], limit=1)
        return None if other else x

    def _sb_fill(self):
        res = super()._sb_fill()
        for l in self:
            if l.run_id.sb_migrated or l.run_id.state != 'draft':
                continue
            x = l._sb_exit_payment()
            if x and l.full_basic:
                amount = round(x.pay_months * l.full_basic, 2)
                l.write({'compensation': amount,
                         'note': 'Separation payment: %s months of salary, authorised by %s' % ('%g' % x.pay_months, x.hr_by.name)})
        return res
