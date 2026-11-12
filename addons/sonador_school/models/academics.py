"""Attendance and marks. Every subject is out of 100, made of the school's assessments (tests, assignment,
mid-term, final); a student's semester average and rank in the class follow from the marks."""
from odoo import fields, models


class SchoolAttendance(models.Model):
    _name = 'school.attendance'
    _description = 'Daily register'
    _order = 'date desc'

    section_id = fields.Many2one('school.section', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, index=True)
    taken_by = fields.Many2one('res.users', default=lambda s: s.env.user)
    line_ids = fields.One2many('school.attendance.line', 'attendance_id')

    _one_a_day = models.Constraint('unique(section_id, date)', 'One register per class and day.')


class SchoolAttendanceLine(models.Model):
    _name = 'school.attendance.line'
    _description = 'Student in the register'

    attendance_id = fields.Many2one('school.attendance', required=True, ondelete='cascade', index=True)
    date = fields.Date(related='attendance_id.date', store=True)
    student_id = fields.Many2one('school.student', required=True, index=True, ondelete='cascade')
    status = fields.Selection([('present', 'Present'), ('absent', 'Absent'), ('late', 'Late'), ('excused', 'Excused')],
                              default='present', required=True)
    note = fields.Char()


class SchoolAssessment(models.Model):
    _name = 'school.assessment'
    _description = 'Assessment (part of the 100 marks)'
    _order = 'sequence'

    name = fields.Char(required=True)
    name_am = fields.Char('Name (Amharic)')
    sequence = fields.Integer(default=10)
    max_mark = fields.Float(required=True)
    active = fields.Boolean(default=True)


class SchoolMark(models.Model):
    _name = 'school.mark'
    _description = 'Mark'

    term_id = fields.Many2one('school.term', required=True, ondelete='cascade', index=True)
    section_id = fields.Many2one('school.section', required=True, ondelete='cascade', index=True)
    student_id = fields.Many2one('school.student', required=True, ondelete='cascade', index=True)
    subject_id = fields.Many2one('school.subject', required=True, index=True)
    assessment_id = fields.Many2one('school.assessment', required=True)
    score = fields.Float()

    _one = models.Constraint('unique(term_id, student_id, subject_id, assessment_id)', 'One mark per assessment.')


class SchoolTermResult(models.Model):
    _name = 'school.term.result'
    _description = 'Semester remarks'

    term_id = fields.Many2one('school.term', required=True, ondelete='cascade')
    student_id = fields.Many2one('school.student', required=True, ondelete='cascade', index=True)
    conduct = fields.Selection([('A', 'A - Excellent'), ('B', 'B - Good'), ('C', 'C - Satisfactory'), ('D', 'D - Needs improvement')])
    comment = fields.Text('Homeroom teacher\'s comment')

    _one = models.Constraint('unique(term_id, student_id)', 'One remark per semester.')
