"""Onboarding and offboarding: a checklist (journey) per joiner or leaver, from a template chosen by department /
position, with tasks for HR, the manager, IT, finance, the store and the employee, due relative to the first or last
day. Policies are acknowledged in the app. Offboarding adds the assets still with the employee and the loans still
open, an exit interview, and the certificate of service the employer must give free of charge (art. 12(8))."""
from datetime import timedelta

from odoo import api, fields, models

ROLES = [('hr', 'HR'), ('manager', 'Manager'), ('it', 'IT'), ('finance', 'Finance'), ('store', 'Store'), ('employee', 'Employee')]
ACTIONS = [('', 'None'), ('contract', 'Upload signed contract'), ('documents', 'Collect ID / TIN documents'),
           ('policies', 'Acknowledge policies'), ('goals', 'Set first goals'), ('probation', 'Confirm after probation'),
           ('login', 'Create or disable login'), ('assets', 'Issue or return assets'), ('loans', 'Clear loans'),
           ('interview', 'Exit interview'), ('settlement', 'Final settlement'), ('certificate', 'Certificate of service')]


class SbJourneyTemplate(models.Model):
    _name = 'sb.journey.template'
    _description = 'Onboarding / offboarding checklist'
    _order = 'kind, sequence, id'

    name = fields.Char(required=True)
    kind = fields.Selection([('onboarding', 'Onboarding'), ('offboarding', 'Offboarding')], required=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    department_ids = fields.Many2many('hr.department', string='Departments', help='Empty: everyone.')
    job_ids = fields.Many2many('hr.job', string='Positions', help='Empty: every position.')
    task_ids = fields.One2many('sb.journey.template.task', 'template_id', 'Tasks')
    active = fields.Boolean(default=True)


class SbJourneyTemplateTask(models.Model):
    _name = 'sb.journey.template.task'
    _description = 'Checklist task'
    _order = 'offset, sequence, id'

    template_id = fields.Many2one('sb.journey.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Char()
    role = fields.Selection(ROLES, required=True, default='hr')
    offset = fields.Integer('Due (days from the first / last day)', help='Negative = before.')
    action = fields.Selection(ACTIONS, default='')
    legal_ref = fields.Char()


class SbJourney(models.Model):
    _name = 'sb.journey'
    _description = 'Onboarding / offboarding'
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    kind = fields.Selection([('onboarding', 'Onboarding'), ('offboarding', 'Offboarding')], required=True)
    template_id = fields.Many2one('sb.journey.template')
    date = fields.Date('First / last day', required=True)
    state = fields.Selection([('open', 'In progress'), ('done', 'Completed'), ('cancelled', 'Cancelled')], default='open')
    task_ids = fields.One2many('sb.journey.task', 'journey_id', 'Tasks')
    progress = fields.Float(compute='_compute_progress', store=True)
    # exit interview
    exit_reason = fields.Text('Main reasons for leaving')
    exit_rating = fields.Selection([('1', 'Very poor'), ('2', 'Poor'), ('3', 'Fair'), ('4', 'Good'), ('5', 'Very good')],
                                   string='Overall experience')
    exit_feedback = fields.Text('What should we improve?')
    rehire = fields.Selection([('yes', 'Yes'), ('no', 'No'), ('maybe', 'With conditions')], string='Eligible for rehire')
    note = fields.Char()

    @api.depends('task_ids.done')
    def _compute_progress(self):
        for j in self:
            j.progress = round(100.0 * len(j.task_ids.filtered('done')) / len(j.task_ids), 0) if j.task_ids else 0.0


class SbJourneyTask(models.Model):
    _name = 'sb.journey.task'
    _description = 'Onboarding / offboarding task'
    _order = 'due, sequence, id'

    journey_id = fields.Many2one('sb.journey', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='journey_id.employee_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Char()
    role = fields.Selection(ROLES, required=True, default='hr')
    owner_id = fields.Many2one('hr.employee', 'Done by (person)', help='Set from the role: the manager, or the company owner of IT / finance / store.')
    due = fields.Date()
    action = fields.Selection(ACTIONS, default='')
    legal_ref = fields.Char()
    done = fields.Boolean()
    done_on = fields.Date()
    done_by = fields.Many2one('res.users')
    note = fields.Char()
    asset_id = fields.Many2one('sb.people.asset')
    loan_id = fields.Many2one('sb.payroll.loan')


class SbPolicy(models.Model):
    _name = 'sb.policy'
    _description = 'Company policy'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    version = fields.Char(default='1')
    body = fields.Html()
    attachment_id = fields.Many2one('ir.attachment', 'File')
    active = fields.Boolean(default=True)
    ack_ids = fields.One2many('sb.policy.ack', 'policy_id')


class SbPolicyAck(models.Model):
    _name = 'sb.policy.ack'
    _description = 'Policy acknowledgement'

    policy_id = fields.Many2one('sb.policy', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    version = fields.Char()
    date = fields.Datetime(default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_owner_it_id = fields.Many2one('hr.employee', 'IT tasks done by')
    sb_owner_finance_id = fields.Many2one('hr.employee', 'Finance tasks done by')
    sb_owner_store_id = fields.Many2one('hr.employee', 'Store tasks done by')
    sb_owner_hr_id = fields.Many2one('hr.employee', 'HR tasks done by')
