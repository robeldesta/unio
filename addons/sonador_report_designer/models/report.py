"""A person's report for one week, month, quarter, half year or year: the template's sections with their own words and blocks. Draft: the blocks
show the books as they are now. Sent: the figures are frozen as they stood (a later correction shows in the next
report, never by rewriting a sent one); the manager accepts it or returns it with a note."""
from odoo import api, fields, models

from .periods import KINDS

STATES = [('draft', 'Draft'), ('sent', 'Sent'), ('accepted', 'Accepted'), ('returned', 'Returned')]


class SbRepReport(models.Model):
    _name = 'sb.rep.report'
    _description = 'Report'
    _order = 'date_from desc, id desc'
    _inherit = ['mail.thread']

    name = fields.Char(compute='_compute_name', store=True)
    template_id = fields.Many2one('sb.rep.template', required=True, ondelete='restrict')
    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', required=True, index=True, default=lambda s: s.env.user)
    manager_id = fields.Many2one('res.users', 'Sent to', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    period = fields.Selection(KINDS, default='week', required=True, index=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection(STATES, default='draft', required=True, tracking=True, index=True)
    late = fields.Boolean(help='Started after its time had closed (a missed period): it stays open to write and send.')
    section_ids = fields.One2many('sb.rep.report.section', 'report_id', copy=True)
    snapshot = fields.Json(help='The blocks as they stood when the report was sent.')
    sent_at = fields.Datetime(readonly=True)
    decided_at = fields.Datetime(readonly=True)
    manager_note = fields.Text()

    @api.depends('template_id.name', 'employee_id.name')
    def _compute_name(self):
        for r in self:
            r.name = '%s · %s' % (r.template_id.name or '', r.employee_id.name or '')

    _one = models.Constraint('unique(template_id, employee_id, period, date_from)', 'One report per template, person and period.')


class SbRepReportSection(models.Model):
    _name = 'sb.rep.report.section'
    _description = 'Report section'
    _order = 'sequence, id'

    report_id = fields.Many2one('sb.rep.report', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    title = fields.Char(required=True)
    prompt = fields.Char()
    required = fields.Boolean()
    narrative = fields.Text()
    block_codes = fields.Char()
