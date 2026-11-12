"""Findings: what the controller found, with the records behind it. A finding is never an accusation and never a change
to the books; people investigate it, then resolve or dismiss it with a note. Each nightly run re-checks it."""
from odoo import api, fields, models
from odoo.exceptions import UserError

SEVERITY = [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')]
CONFIDENCE = [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')]
STATES = [('open', 'Open'), ('review', 'In review'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed'),
          ('cleared', 'Cleared')]
RANK = {'high': 3, 'medium': 2, 'low': 1}


class CtlFinding(models.Model):
    _name = 'ctl.finding'
    _description = 'Controller finding'
    _inherit = ['mail.thread']
    _order = 'priority desc, id desc'

    name = fields.Char('Finding', required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    rule = fields.Selection(selection='_rule_selection', string='Check', required=True, index=True)
    category = fields.Char()
    key = fields.Char(required=True, index=True, help='What makes this finding the same one from run to run.')
    summary = fields.Text('What was found')
    advice = fields.Text('Where to look')
    period = fields.Char()
    amount = fields.Monetary('Amount at stake', tracking=True)
    severity = fields.Selection(SEVERITY, required=True, default='medium', tracking=True)
    confidence = fields.Selection(CONFIDENCE, required=True, default='medium')
    confidence_note = fields.Char('Why this confidence')
    metric_label = fields.Char()
    metric_current = fields.Float('This period')
    metric_baseline = fields.Float('Before')
    state = fields.Selection(STATES, default='open', required=True, tracking=True, index=True)
    user_id = fields.Many2one('res.users', 'Assigned to', tracking=True)
    note = fields.Text('Resolution note', tracking=True)
    first_seen = fields.Date(default=fields.Date.context_today)
    last_seen = fields.Date(default=fields.Date.context_today)
    priority = fields.Float(compute='_compute_priority', store=True, index=True)
    evidence_ids = fields.One2many('ctl.evidence', 'finding_id', 'Evidence')
    evidence_count = fields.Integer(compute='_compute_evidence_count')

    _key_unique = models.Constraint('unique(company_id, rule, key)', 'One finding per check and key.')

    @api.model
    def _rule_selection(self):
        return [(c['code'], c['name']) for c in self.env['ctl.engine']._checks()]

    @api.depends('severity', 'amount', 'confidence')
    def _compute_priority(self):
        for f in self:
            f.priority = RANK.get(f.severity, 1) * 1e13 + min(abs(f.amount or 0.0), 9e12)

    def _compute_evidence_count(self):
        for f in self:
            f.evidence_count = len(f.evidence_ids)

    def action_take(self):
        self.write({'user_id': self.env.user.id, 'state': 'review'})

    def action_resolve(self):
        self.write({'state': 'resolved'})

    def action_dismiss(self):
        for f in self:
            if not (f.note or '').strip():
                raise UserError(self.env._('Write in the resolution note why "%s" is not a problem, then dismiss it.', f.name))
        self.write({'state': 'dismissed'})

    def action_reopen(self):
        self.write({'state': 'open'})


class CtlEvidence(models.Model):
    _name = 'ctl.evidence'
    _description = 'Controller evidence'
    _order = 'sequence, id'

    finding_id = fields.Many2one('ctl.finding', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    role = fields.Char('What it is')
    label = fields.Char('Record')
    date = fields.Date()
    partner = fields.Char('Partner')
    amount = fields.Float()
    res_model = fields.Char()
    res_id = fields.Integer()
    drill = fields.Json(help='A report to open instead of a record.')

    def action_open(self):
        self.ensure_one()
        if self.drill:
            ctx = {'report': self.drill.get('report'), 'options': {k: v for k, v in self.drill.items() if k != 'report'},
                   'ui': {'mode': 'custom'}, 'expand_first': True}
            return {'type': 'ir.actions.client', 'tag': 'sonador_books.report', 'name': self.label, 'context': ctx}
        if not self.res_model or self.res_model not in self.env:
            return False
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(self.env._('This record no longer exists.'))
        if self.res_model == 'account.move.line':
            record = record.move_id
        if record._name == 'account.move':
            voucher = self.env['sb.voucher'].search([('move_id', '=', record.id)], limit=1)
            if voucher:
                record = voucher
        return {'type': 'ir.actions.act_window', 'res_model': record._name, 'res_id': record.id, 'views': [(False, 'form')],
                'target': 'current'}


class CtlRun(models.Model):
    _name = 'ctl.run'
    _description = 'Controller run'
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, index=True)
    date = fields.Datetime(default=fields.Datetime.now)
    seconds = fields.Float()
    new = fields.Integer()
    open = fields.Integer()
    cleared = fields.Integer()
    checks = fields.Json(help='Per check: findings or the reason it could not run.')
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user)
