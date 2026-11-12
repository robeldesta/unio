"""To-dos (what a person has to do, by when) and marks (a deadline someone filed, or an item someone put off)."""
from odoo import api, fields, models

PRIORITY = [('high', 'High'), ('medium', 'Normal'), ('low', 'Low')]


class AgendaTodo(models.Model):
    _name = 'agenda.todo'
    _description = 'To-do'
    _order = 'done, date, id'

    name = fields.Char('What', required=True)
    date = fields.Date('By', index=True)
    user_id = fields.Many2one('res.users', 'Who', required=True, default=lambda s: s.env.user, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    priority = fields.Selection(PRIORITY, default='medium', required=True)
    note = fields.Text()
    done = fields.Boolean(index=True)
    done_on = fields.Date()
    source_key = fields.Char(index=True, help='The agenda item it came from.')
    res_model = fields.Char()
    res_id = fields.Integer()

    def write(self, vals):
        if 'done' in vals:
            vals['done_on'] = fields.Date.context_today(self) if vals['done'] else False
        return super().write(vals)

    def action_open_source(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env or not self.env[self.res_model].browse(self.res_id).exists():
            return False
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'views': [(False, 'form')]}


class AgendaMark(models.Model):
    """'done': a deadline was met (filed and paid), for everyone in the company.
    'later': one person put an item off until a date; it comes back then."""
    _name = 'agenda.mark'
    _description = 'Agenda mark'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    user_id = fields.Many2one('res.users', index=True)
    key = fields.Char(required=True, index=True)
    kind = fields.Selection([('done', 'Done'), ('later', 'Later')], required=True)
    until = fields.Date()

    @api.model
    def _active(self, company):
        today = fields.Date.context_today(self)
        marks = self.sudo().search([('company_id', '=', company.id), '|', ('kind', '=', 'done'),
                                    '&', ('user_id', '=', self.env.uid), ('until', '>', today)])
        return {m.key for m in marks if m.kind == 'done'}, {m.key: m.until for m in marks if m.kind == 'later'}
