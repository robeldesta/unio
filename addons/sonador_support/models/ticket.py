"""A problem with the system, reported by someone at work, answered and fixed by IT / the admin, confirmed by the person who
reported it. The screen and record it was reported from come with it. Response and fix times are counted in working
hours against the priority's targets."""
from datetime import timedelta

from odoo import api, fields, models

PRIORITY = [('urgent', 'Urgent: work has stopped'), ('high', 'High: work is hard'), ('normal', 'Normal'), ('low', 'Low: when you can')]
# priority: (first answer within, fixed within) in working hours (8 a day)
TARGETS = {'urgent': (2, 8), 'high': (4, 16), 'normal': (8, 40), 'low': (16, 80)}
STATES = [('new', 'New'), ('progress', 'Being worked on'), ('waiting', 'Waiting for the reporter'), ('resolved', 'Fixed, to confirm'),
          ('closed', 'Closed'), ('cancelled', 'Cancelled')]
KINDS = [('broken', 'Something does not work'), ('howto', 'How do I…?'), ('data', 'Wrong data to correct'),
         ('access', 'Login or access'), ('request', 'Something new')]
OPEN = ('new', 'progress', 'waiting', 'resolved')


def work_hours(a, b):
    """Working hours between two datetimes (Monday to Saturday, 8 hours a day, counted on whole days)."""
    if not a or not b or b <= a:
        return 0.0
    total, d = 0.0, a
    while d < b:
        nxt = min(b, (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if d.weekday() != 6:
            total += min((nxt - d).total_seconds() / 3600.0, 8.0) if (nxt - d).total_seconds() < 86400 else 8.0
        d = nxt
    return round(total, 1)


class SbSupportTicket(models.Model):
    _name = 'sb.support.ticket'
    _description = 'Problem with the system'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char('Number', default='New', readonly=True, copy=False)
    subject = fields.Char(required=True, tracking=True)
    description = fields.Text('What happened')
    kind = fields.Selection(KINDS, default='broken', required=True, string='Kind')
    priority = fields.Selection(PRIORITY, default='normal', required=True, tracking=True)
    state = fields.Selection(STATES, default='new', required=True, tracking=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    reporter_id = fields.Many2one('res.users', default=lambda s: s.env.user, required=True, readonly=True, index=True)
    assignee_id = fields.Many2one('res.users', string='Taken by', tracking=True, index=True)
    url = fields.Char('Screen')
    screen = fields.Char('Screen name')
    res_model = fields.Char()
    res_id = fields.Integer()
    record_label = fields.Char('Record')
    resolution = fields.Text('What was done')
    first_response_at = fields.Datetime(readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)
    reopened = fields.Integer(default=0, readonly=True)
    source = fields.Char(default='screen')  # screen, telegram, odoo18
    legacy_ref = fields.Char(index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', 'New') == 'New':
                v['name'] = self.env['ir.sequence'].sudo().next_by_code('sb.support.ticket') or 'New'
        return super().create(vals_list)

    def _targets(self):
        return TARGETS.get(self.priority, TARGETS['normal'])

    def _times(self):
        """(hours to first answer or so far, hours to fix or so far, answer late, fix late)."""
        self.ensure_one()
        now = fields.Datetime.now()
        ans, fix = self._targets()
        h_ans = work_hours(self.create_date, self.first_response_at or now)
        h_fix = work_hours(self.create_date, self.resolved_at or now)
        return h_ans, h_fix, h_ans > ans, h_fix > fix and self.state != 'cancelled'
