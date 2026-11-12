"""Audit Room: an internal auditor's visit (a session from arrival to sign-off), the checks looked at during it, the
findings raised, and the re-checks of earlier findings. The auditor only reads the books; findings are answered by
the people who keep them."""
import json

from odoo import api, fields, models

AREAS = [('cash', 'Cash and bank'), ('sales', 'Sales and receivables'), ('purchases', 'Purchases and payables'),
         ('payroll', 'Payroll'), ('tax', 'Tax'), ('assets', 'Fixed assets'), ('stock', 'Stock'),
         ('journals', 'Journals and adjustments'), ('controls', 'Controls and access'), ('other', 'Other')]
SEVERITY = [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')]
FINDING_STATES = [('open', 'Waiting for an answer'), ('answered', 'Answered'), ('fixed', 'Fixed, to verify'),
                  ('closed', 'Closed'), ('accepted', 'Risk accepted')]
VERDICTS = [('clean', 'All in order'), ('minor', 'Minor points'), ('serious', 'Serious issues')]


class AuditVisit(models.Model):
    _name = 'sb.audit.visit'
    _description = 'Audit visit'
    _inherit = ['mail.thread']
    _order = 'started_at desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    auditor_id = fields.Many2one('res.users', required=True, default=lambda s: s.env.user, readonly=True)
    state = fields.Selection([('open', 'In progress'), ('closed', 'Ended')], default='open', required=True, tracking=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
    ended_at = fields.Datetime(readonly=True)
    since = fields.Datetime('Changes since', required=True,
                            help='The checks look at what was entered or changed after this moment: the start of the last visit.')
    previous_id = fields.Many2one('sb.audit.visit', readonly=True)
    check_ids = fields.One2many('sb.audit.check', 'visit_id')
    finding_ids = fields.One2many('sb.audit.finding', 'visit_id', string='Findings raised')
    recheck_ids = fields.One2many('sb.audit.recheck', 'visit_id')
    verdict = fields.Selection(VERDICTS, tracking=True)
    summary = fields.Text('Summary for management')
    duration = fields.Char(compute='_compute_duration')

    @api.depends('started_at', 'auditor_id')
    def _compute_name(self):
        for v in self:
            v.name = '%s, %s' % (fields.Datetime.context_timestamp(v, v.started_at).strftime('%d %b %Y') if v.started_at else '',
                                 v.auditor_id.name or '')

    def _compute_duration(self):
        now = fields.Datetime.now()
        for v in self:
            secs = int(((v.ended_at or now) - v.started_at).total_seconds()) if v.started_at else 0
            h, m = divmod(secs // 60, 60)
            v.duration = '%dh %02dm' % (h, m) if h else '%d min' % m


class AuditCheck(models.Model):
    _name = 'sb.audit.check'
    _description = 'Audit check of a visit'
    _order = 'sequence, id'

    visit_id = fields.Many2one('sb.audit.visit', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='visit_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    key = fields.Char(required=True)
    sequence = fields.Integer()
    area = fields.Selection(AREAS)
    title = fields.Char(required=True)
    why = fields.Char()
    count = fields.Integer()
    amount = fields.Monetary()
    detail = fields.Text()  # one line per item, shown under the check
    res_model = fields.Char()
    res_ids = fields.Text(default='[]')
    state = fields.Selection([('todo', 'To look at'), ('ok', 'In order'), ('issue', 'Issue found'), ('na', 'Not applicable')],
                             default='todo', required=True)
    note = fields.Text()
    # cash count
    book_balance = fields.Monetary()
    counted = fields.Monetary()
    counted_set = fields.Boolean()
    account_id = fields.Many2one('account.account')

    def ids_list(self):
        try:
            return [int(x) for x in json.loads(self.res_ids or '[]')]
        except ValueError:
            return []


class AuditFinding(models.Model):
    _name = 'sb.audit.finding'
    _description = 'Audit finding'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char('Number', readonly=True, default='New', copy=False)
    title = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    visit_id = fields.Many2one('sb.audit.visit', string='Raised in', readonly=True, index=True)
    auditor_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    area = fields.Selection(AREAS, default='other', required=True)
    severity = fields.Selection(SEVERITY, default='medium', required=True, tracking=True)
    found = fields.Text('What I found', required=True)
    recommendation = fields.Text('What should be done')
    amount = fields.Monetary('Amount involved')
    voucher_ids = fields.Many2many('sb.voucher', string='Vouchers')
    owner_id = fields.Many2one('res.users', string='Who answers', tracking=True)
    due_date = fields.Date('Answer by', tracking=True)
    state = fields.Selection(FINDING_STATES, default='open', required=True, tracking=True)
    response = fields.Text('Management answer')
    response_by = fields.Many2one('res.users', readonly=True)
    response_at = fields.Datetime(readonly=True)
    closed_visit_id = fields.Many2one('sb.audit.visit', string='Closed in', readonly=True)
    closed_note = fields.Text(readonly=True)
    check_key = fields.Char()
    overdue = fields.Boolean(compute='_compute_overdue')

    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for f in self:
            f.overdue = bool(f.state == 'open' and f.due_date and f.due_date < today)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('sb.audit.finding') or 'New'
        return super().create(vals_list)


class AuditRecheck(models.Model):
    _name = 'sb.audit.recheck'
    _description = 'Re-check of an earlier finding during a visit'

    visit_id = fields.Many2one('sb.audit.visit', required=True, ondelete='cascade', index=True)
    finding_id = fields.Many2one('sb.audit.finding', required=True, ondelete='cascade')
    result = fields.Selection([('closed', 'Fixed, closed'), ('open', 'Still open'), ('accepted', 'Risk accepted')], required=True)
    note = fields.Text()
