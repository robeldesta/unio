"""Recruitment: openings (positions with headcount, salary range, closing date, hiring manager), candidates, applications
moving through stages, interviews with 1-5 scorecards, offers, and hiring into People."""
from odoo import api, fields, models

STAGES = [('new', 'New'), ('screening', 'Screening'), ('interview', 'Interview'), ('second', 'Second interview'),
          ('offer', 'Offer'), ('hired', 'Hired')]


class HrJob(models.Model):
    _inherit = 'hr.job'

    sb_open = fields.Boolean('Recruiting')
    sb_openings = fields.Integer('Openings', default=1)
    sb_description = fields.Text('Job description')
    sb_requirements = fields.Text('Requirements')
    sb_salary_from = fields.Float('Salary from')
    sb_salary_to = fields.Float('Salary to')
    sb_close_date = fields.Date('Applications close')
    sb_hiring_manager_id = fields.Many2one('hr.employee', 'Hiring manager')
    sb_department_id = fields.Many2one('hr.department', 'Department (opening)')


class SbRecruitReason(models.Model):
    _name = 'sb.recruit.reason'
    _description = 'Reason for not continuing'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    by_candidate = fields.Boolean('Candidate withdrew')


class SbRecruitCandidate(models.Model):
    _name = 'sb.recruit.candidate'
    _description = 'Candidate'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    email = fields.Char(index=True)
    phone = fields.Char(index=True)
    linkedin = fields.Char('LinkedIn')
    education = fields.Char()
    experience_years = fields.Float('Years of experience')
    city = fields.Char()
    notes = fields.Text()
    cv_id = fields.Many2one('ir.attachment', 'CV')
    application_ids = fields.One2many('sb.recruit.application', 'candidate_id')
    employee_id = fields.Many2one('hr.employee', 'Hired as')
    source = fields.Char()


class SbRecruitApplication(models.Model):
    _name = 'sb.recruit.application'
    _description = 'Application'
    _order = 'stage_seq, rating desc, applied_on desc, id desc'

    candidate_id = fields.Many2one('sb.recruit.candidate', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='candidate_id.company_id', store=True)
    job_id = fields.Many2one('hr.job', 'Position', index=True)
    stage = fields.Selection(STAGES, default='new', required=True, index=True)
    stage_seq = fields.Integer(compute='_compute_seq', store=True)
    active = fields.Boolean(default=True)
    reason_id = fields.Many2one('sb.recruit.reason', 'Not continuing because')
    rating = fields.Integer('Rating (0-3)')
    expected_salary = fields.Float()
    proposed_salary = fields.Float()
    applied_on = fields.Date(default=fields.Date.context_today)
    stage_date = fields.Date(default=fields.Date.context_today)
    hired_on = fields.Date()
    owner_id = fields.Many2one('res.users', 'Recruiter')
    notes = fields.Text()
    interview_ids = fields.One2many('sb.recruit.interview', 'application_id')
    source_ref = fields.Char()

    @api.depends('stage')
    def _compute_seq(self):
        order = {k: i for i, (k, _v) in enumerate(STAGES)}
        for a in self:
            a.stage_seq = order.get(a.stage, 0)


class SbRecruitInterview(models.Model):
    _name = 'sb.recruit.interview'
    _description = 'Interview'
    _order = 'when desc, id desc'

    application_id = fields.Many2one('sb.recruit.application', required=True, ondelete='cascade', index=True)
    when = fields.Datetime(required=True)
    kind = fields.Selection([('phone', 'Phone screen'), ('panel', 'Panel interview'), ('practical', 'Practical test'),
                             ('final', 'Final interview')], default='panel')
    interviewer_ids = fields.Many2many('hr.employee', string='Interviewers')
    place = fields.Char()
    score_skills = fields.Integer('Skills (1-5)')
    score_experience = fields.Integer('Experience (1-5)')
    score_attitude = fields.Integer('Attitude and fit (1-5)')
    score_communication = fields.Integer('Communication (1-5)')
    score = fields.Float(compute='_compute_score', store=True)
    recommendation = fields.Selection([('strong_yes', 'Strong yes'), ('yes', 'Yes'), ('no', 'No'), ('strong_no', 'Strong no')])
    notes = fields.Text()
    done = fields.Boolean()

    @api.depends('score_skills', 'score_experience', 'score_attitude', 'score_communication')
    def _compute_score(self):
        for i in self:
            s = [x for x in (i.score_skills, i.score_experience, i.score_attitude, i.score_communication) if x]
            i.score = round(sum(s) / len(s), 2) if s else 0.0
