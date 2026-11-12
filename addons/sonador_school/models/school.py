"""The school's structure: academic years and semesters (Ethiopian calendar), grades, sections, subjects, students."""
from odoo import api, fields, models
from odoo.exceptions import UserError

LEVELS = [('kg', 'Kindergarten'), ('primary', 'Primary (1-6)'), ('middle', 'Middle (7-8)'), ('secondary', 'Secondary (9-12)')]


class SchoolYear(models.Model):
    _name = 'school.year'
    _description = 'Academic year'
    _order = 'date_start desc'

    name = fields.Char(required=True, help='e.g. 2019 E.C.')
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    current = fields.Boolean()
    term_ids = fields.One2many('school.term', 'year_id', 'Semesters')

    @api.model
    def get_current(self):
        y = self.search([('current', '=', True)], limit=1) or self.search([], limit=1)
        if not y:
            raise UserError(self.env._('Set up the academic year first.'))
        return y


class SchoolTerm(models.Model):
    _name = 'school.term'
    _description = 'Semester'
    _order = 'year_id, sequence'

    year_id = fields.Many2one('school.year', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    name_am = fields.Char('Name (Amharic)')
    sequence = fields.Integer(default=1)
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)


class SchoolGrade(models.Model):
    _name = 'school.grade'
    _description = 'Grade'
    _order = 'sequence'

    name = fields.Char(required=True)
    name_am = fields.Char('Name (Amharic)')
    sequence = fields.Integer()
    level = fields.Selection(LEVELS, required=True, default='primary')
    subject_ids = fields.Many2many('school.subject', string='Subjects')
    next_grade_id = fields.Many2one('school.grade', 'Promoted to')


class SchoolSubject(models.Model):
    _name = 'school.subject'
    _description = 'Subject'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    name_am = fields.Char('Name (Amharic)')
    code = fields.Char()
    sequence = fields.Integer(default=10)


class SchoolSection(models.Model):
    _name = 'school.section'
    _description = 'Section (class)'
    _order = 'year_id desc, grade_sequence, name'

    name = fields.Char('Section', required=True, help='e.g. A')
    year_id = fields.Many2one('school.year', required=True, ondelete='cascade')
    grade_id = fields.Many2one('school.grade', required=True)
    grade_sequence = fields.Integer(related='grade_id.sequence', store=True)
    homeroom_id = fields.Many2one('hr.employee', 'Homeroom teacher')
    room = fields.Char()
    capacity = fields.Integer(default=40)
    student_ids = fields.One2many('school.student', 'section_id', 'Students')
    teacher_ids = fields.One2many('school.section.teacher', 'section_id', 'Subject teachers')

    @api.depends('grade_id.name', 'name')
    def _compute_display_name(self):
        for s in self:
            s.display_name = '%s%s' % (s.grade_id.name or '', s.name or '')


class SchoolSectionTeacher(models.Model):
    _name = 'school.section.teacher'
    _description = 'Who teaches a subject in a section'

    section_id = fields.Many2one('school.section', required=True, ondelete='cascade')
    subject_id = fields.Many2one('school.subject', required=True)
    teacher_id = fields.Many2one('hr.employee', 'Teacher')


class SchoolStudent(models.Model):
    _name = 'school.student'
    _description = 'Student'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char(required=True, tracking=True)
    name_am = fields.Char('Name (Amharic)')
    student_no = fields.Char('Student no.', readonly=True, copy=False, index=True)
    sex = fields.Selection([('m', 'Male'), ('f', 'Female')], required=True, default='m')
    birth_date = fields.Date()
    photo = fields.Image(max_width=512, max_height=512)
    state = fields.Selection([('applicant', 'Applicant'), ('admitted', 'Admitted'), ('enrolled', 'Enrolled'),
                              ('left', 'Left'), ('graduated', 'Graduated')], default='applicant', required=True, tracking=True, index=True)
    applied_grade_id = fields.Many2one('school.grade', 'Applying for')
    section_id = fields.Many2one('school.section', 'Class', tracking=True, index=True)
    grade_id = fields.Many2one(related='section_id.grade_id', store=True)
    payer_id = fields.Many2one('res.partner', 'Parent / guardian (pays)', tracking=True, index=True,
                               help='The family account the fees are billed to; brothers and sisters share it.')
    guardian_phone = fields.Char(related='payer_id.phone', readonly=False)
    second_guardian = fields.Char()
    second_phone = fields.Char()
    scholarship_pct = fields.Float('Scholarship %')
    transport = fields.Boolean('Uses school transport')
    joined = fields.Date(default=fields.Date.context_today)
    previous_school = fields.Char()
    medical = fields.Text('Medical notes')
    left_reason = fields.Char()
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if not v.get('student_no'):
                v['student_no'] = self.env['ir.sequence'].next_by_code('school.student') or '/'
        return super().create(vals_list)

    def siblings(self):
        self.ensure_one()
        if not self.payer_id:
            return self.browse()
        return self.search([('payer_id', '=', self.payer_id.id), ('state', '=', 'enrolled'), ('id', '!=', self.id)])
