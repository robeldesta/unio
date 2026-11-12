"""Planning records: the monthly plan (the contract with the line manager), its tasks, daily check-ins and the weekly
report. Months are Ethiopian months (Pagume folds into Nehase); weeks start on the configured day."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

PLAN_STATES = [('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('returned', 'Returned for changes'),
               ('approved', 'Approved')]
TASK_REVIEW = [('pending', 'Not reviewed'), ('approved', 'Approved'), ('returned', 'Returned')]
TASK_STATUS = [('todo', 'Not started'), ('on_track', 'On track'), ('blocked', 'Blocked'), ('done', 'Done')]
WEEKDAYS = [('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'), ('4', 'Friday'), ('5', 'Saturday'),
            ('6', 'Sunday')]
OPEN_AREAS = [('discuss', 'Discuss (chat)'), ('timeoff', 'Leave Requests'), ('attendance', 'Attendance'),
              ('people', 'People profile'), ('performance', 'Performance goals')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_pl_mode = fields.Selection([('off', 'Off'), ('watch', 'Watch only (record, never lock)'),
                                   ('warn', 'Warn (banner, no lock)'), ('lock', 'Lock until planned')],
                                  'Planning gate', default='warn')
    sb_pl_deadline_day = fields.Integer('Monthly plan due by day', default=3,
                                        help='Day of the Ethiopian month by which the plan of the month must be submitted.')
    sb_pl_next_from_day = fields.Integer('Next month can be planned from day', default=20)
    sb_pl_week_start = fields.Selection(WEEKDAYS, 'Week starts on', default='5')
    sb_pl_checkin = fields.Selection([('daily', 'Every working day'), ('two', 'Every second working day'),
                                      ('weekly', 'Once a week (with the report)')], 'Check-ins', default='daily')
    sb_pl_grace_hours = fields.Integer('Grace hours', default=10,
                                       help='Hours into the next working day before a missing check-in or report locks.')
    sb_pl_checkin_from = fields.Date('Check-ins asked from',
                                     help='No check-in or weekly report is asked for days before this (e.g. the day '
                                          'planning started in this system; plans brought over from an earlier one).')
    sb_pl_report_day = fields.Selection(WEEKDAYS, 'Weekly report on', default='4')
    sb_pl_min_tasks = fields.Integer('Fewest tasks a month', default=4)
    sb_pl_max_tasks = fields.Integer('Most tasks a month', default=25)
    sb_pl_strictness = fields.Integer('Plan checker strictness (0-100)', default=60)
    sb_pl_open_areas = fields.Char('Open while locked', default='discuss,timeoff,attendance,people,performance',
                                   help='Comma list of: ' + ', '.join(k for k, _v in OPEN_AREAS))
    sb_pl_require_goal = fields.Boolean('Every task links to a goal', default=False)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_pl_gated = fields.Boolean('Must plan', groups='hr.group_hr_user',
                                 help='On the planning list: monthly plan, check-ins and weekly report; locked when missing.')
    sb_pl_strictness = fields.Integer('Plan checker strictness', groups='hr.group_hr_user',
                                      help='0 = company default. 100 = strictest.')


class SbPlanCategory(models.Model):
    _name = 'sb.plan.category'
    _description = 'Work category'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    department_id = fields.Many2one('hr.department', help='Empty: for everyone.')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    active = fields.Boolean(default=True)


class SbPlan(models.Model):
    _name = 'sb.plan'
    _description = 'Monthly plan'
    _order = 'month desc, employee_id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    month = fields.Date('Month (first day)', required=True, index=True)
    month_end = fields.Date(required=True)
    label = fields.Char('Month')
    state = fields.Selection(PLAN_STATES, default='draft', required=True, index=True)
    approver_id = fields.Many2one('hr.employee', 'Approver')
    submitted_at = fields.Datetime()
    first_submitted = fields.Date('First submitted', help='Check-ins and reports are owed from this day on.')
    approved_at = fields.Datetime()
    score = fields.Integer('Plan quality', help='0-100 from the plan checker at submission.')
    revisions = fields.Integer()
    manager_note = fields.Text()
    task_ids = fields.One2many('sb.plan.task', 'plan_id')
    change_ids = fields.One2many('sb.plan.change', 'plan_id')
    legacy_ref = fields.Char(index=True)

    _unique = models.Constraint('unique(employee_id, month)', 'One plan per person and month.')


class SbPlanTask(models.Model):
    _name = 'sb.plan.task'
    _description = 'Planned task'
    _order = 'plan_id, sequence, id'

    plan_id = fields.Many2one('sb.plan', required=True, index=True, ondelete='cascade')
    employee_id = fields.Many2one(related='plan_id.employee_id', store=True, index=True)
    company_id = fields.Many2one(related='plan_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    category_id = fields.Many2one('sb.plan.category', 'Category')
    name = fields.Char('What', required=True)
    done_when = fields.Char('Done when', help='The measurable result: a number, a date, a deliverable.')
    detail = fields.Text('How', help='Optional: the steps.')
    target_qty = fields.Float('Target')
    unit = fields.Char()
    date_from = fields.Date('From', required=True)
    date_to = fields.Date('To', required=True)
    output = fields.Char('Evidence / output', help='What shows it is done: a document, a report, a count, a photo.')
    goal_id = fields.Many2one('sb.perf.goal', 'Goal', ondelete='set null')
    indicator_id = fields.Many2one('sb.plan.indicator', 'Checked by', ondelete='set null')
    role_item_id = fields.Many2one('sb.plan.role.item', 'Role item', ondelete='set null',
                                   help="The role's must-do item this task covers.")
    added_later = fields.Boolean(help='Added during the month, after approval.')
    review = fields.Selection(TASK_REVIEW, default='pending', required=True)
    review_note = fields.Char('Manager note')
    status = fields.Selection(TASK_STATUS, default='todo', required=True)
    progress = fields.Integer('Progress %')
    done_qty = fields.Float('Achieved')
    flags = fields.Json('Plan checker findings')
    checkin_ids = fields.One2many('sb.plan.checkin', 'task_id')
    legacy_ref = fields.Char(index=True)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for t in self:
            if t.date_to < t.date_from:
                raise ValidationError(self.env._('"%s" ends before it starts.', t.name))


class SbPlanCheckin(models.Model):
    _name = 'sb.plan.checkin'
    _description = 'Task check-in'
    _order = 'date desc, id desc'

    task_id = fields.Many2one('sb.plan.task', required=True, index=True, ondelete='cascade')
    employee_id = fields.Many2one(related='task_id.employee_id', store=True, index=True)
    date = fields.Date(required=True, index=True, help='The working day it is about.')
    status = fields.Selection(TASK_STATUS[1:], required=True, default='on_track')
    progress = fields.Integer('Progress %')
    done_qty = fields.Float('Achieved so far')
    note = fields.Text()
    late = fields.Boolean('Entered after the day', help='Back-filled after the working day ended.')
    legacy_ref = fields.Char(index=True)

    _unique = models.Constraint('unique(task_id, date)', 'One check-in per task and day.')


class SbPlanWeek(models.Model):
    _name = 'sb.plan.week'
    _description = 'Weekly report'
    _order = 'week desc, employee_id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    week = fields.Date('Week starting', required=True, index=True)
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed')], default='draft', required=True)
    remarks = fields.Text('Remarks')
    next_focus = fields.Text('Focus next week')
    score = fields.Integer('Execution %')
    grade = fields.Char()
    trust_flags = fields.Json()
    confirmed_at = fields.Datetime()
    manager_note = fields.Text()
    plan_state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('returned', 'Returned for changes'),
                                   ('approved', 'Approved')], 'Week plan',
                                  help='State of the week plan brought over from the old planning (the week now builds itself).')
    legacy_ref = fields.Char(index=True)

    _unique = models.Constraint('unique(employee_id, week)', 'One report per person and week.')
