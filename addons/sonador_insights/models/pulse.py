"""Pulse surveys: short, anonymous. Who has answered (to stop answering twice and to remind) is kept apart from the
answers, which carry only the department, and results by department show only from 5 answers."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

MIN_GROUP = 5


class SbPulse(models.Model):
    _name = 'sb.pulse'
    _description = 'Pulse survey'
    _order = 'date_open desc, id desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    date_open = fields.Date(default=fields.Date.context_today)
    date_close = fields.Date()
    state = fields.Selection([('draft', 'Draft'), ('open', 'Open'), ('closed', 'Closed')], default='draft')
    question_ids = fields.One2many('sb.pulse.question', 'pulse_id')
    taken_ids = fields.One2many('sb.pulse.taken', 'pulse_id')
    response_ids = fields.One2many('sb.pulse.response', 'pulse_id')


class SbPulseQuestion(models.Model):
    _name = 'sb.pulse.question'
    _description = 'Pulse question'
    _order = 'sequence, id'

    pulse_id = fields.Many2one('sb.pulse', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection([('enps', 'Recommend us (0-10, eNPS)'), ('scale', 'Agree (1-5)'), ('text', 'Comment')], default='scale', required=True)


class SbPulseTaken(models.Model):
    _name = 'sb.pulse.taken'
    _description = 'Pulse answered by'

    pulse_id = fields.Many2one('sb.pulse', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)


class SbPulseResponse(models.Model):
    _name = 'sb.pulse.response'
    _description = 'Pulse response (anonymous)'

    pulse_id = fields.Many2one('sb.pulse', required=True, ondelete='cascade', index=True)
    department = fields.Char()
    answer_ids = fields.One2many('sb.pulse.answer', 'response_id')


class SbPulseAnswer(models.Model):
    _name = 'sb.pulse.answer'
    _description = 'Pulse answer'

    response_id = fields.Many2one('sb.pulse.response', required=True, ondelete='cascade', index=True)
    question_id = fields.Many2one('sb.pulse.question', required=True, ondelete='cascade')
    value = fields.Integer()
    text = fields.Text()
