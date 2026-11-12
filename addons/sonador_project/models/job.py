"""Jobs (projects) and what hangs on them: phases, tasks, cost codes, budget, bill of quantities, subcontracts,
timesheets. The books carry the job (and phase / cost code) on every voucher line and store issue line, so actual costs
and revenue come from what is really posted."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

JOB_KINDS = [('fixed', 'Fixed price'), ('tm', 'Time and materials'), ('construction', 'Construction contract (BOQ)'),
             ('internal', 'Internal / overhead')]
JOB_STATES = [('draft', 'Tender / quote'), ('active', 'Running'), ('hold', 'On hold'), ('done', 'Completed'), ('cancel', 'Lost / cancelled')]
COST_KINDS = [('labour', 'Labour'), ('material', 'Materials'), ('subcontract', 'Subcontract'), ('equipment', 'Equipment and vehicles'),
              ('other', 'Other costs')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_job_income_account_id = fields.Many2one('account.account', 'Job revenue account')
    sb_job_retention_account_id = fields.Many2one('account.account', 'Retention receivable account')
    sb_job_advance_account_id = fields.Many2one('account.account', 'Customer advances (mobilisation) account')
    sb_job_vat_on = fields.Selection([('net', 'The amount due (after retention and advance recovery)'),
                                      ('gross', 'The gross work certified')], default='net', string='VAT on certificates',
                                     help='Confirm with your tax adviser how your contracts are taxed.')


class SbCostCode(models.Model):
    _name = 'sb.cost.code'
    _description = 'Cost code'
    _order = 'code, name'

    code = fields.Char(required=True)
    name = fields.Char(required=True)
    kind = fields.Selection(COST_KINDS, required=True, default='other')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    active = fields.Boolean(default=True)

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for c in self:
            c.display_name = '%s %s' % (c.code, c.name)


class SbJob(models.Model):
    _name = 'sb.job'
    _description = 'Job / project'
    _inherit = ['mail.thread']
    _order = 'state, code desc'

    code = fields.Char(required=True, copy=False, default='/', index=True)
    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', 'Customer', tracking=True)
    kind = fields.Selection(JOB_KINDS, default='fixed', required=True, tracking=True)
    state = fields.Selection(JOB_STATES, default='active', required=True, tracking=True, index=True)
    manager_id = fields.Many2one('hr.employee', 'Project manager', tracking=True)
    member_ids = fields.Many2many('hr.employee', string='Team')
    site = fields.Char('Site / location')
    date_start = fields.Date('Start')
    date_end = fields.Date('Planned finish')
    date_done = fields.Date('Completed on')
    contract_value = fields.Monetary('Contract value (excl. VAT)', tracking=True)
    vat = fields.Boolean('Charge VAT', default=True)
    retention_pct = fields.Float('Retention %', default=5.0)
    advance_amount = fields.Monetary('Advance / mobilisation received')
    advance_recovery_pct = fields.Float('Advance recovered from each certificate %')
    bill_rate = fields.Monetary('Default billing rate per hour', help='Time and materials jobs.')
    description = fields.Text()
    color = fields.Integer()
    phase_ids = fields.One2many('sb.job.phase', 'job_id')
    task_ids = fields.One2many('sb.job.task', 'job_id')
    budget_ids = fields.One2many('sb.job.budget', 'job_id')
    boq_ids = fields.One2many('sb.job.boq', 'job_id')
    bill_ids = fields.One2many('sb.job.bill', 'job_id')
    sub_ids = fields.One2many('sb.job.sub', 'job_id')
    timesheet_ids = fields.One2many('sb.timesheet', 'job_id')

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('code', '/') == '/':
                v['code'] = self.env['ir.sequence'].next_by_code('sb.job') or 'JOB-NEW'
        return super().create(vals_list)

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for j in self:
            j.display_name = '%s %s' % (j.code, j.name)

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        domain = domain or []
        if name:
            domain = ['|', ('code', operator, name), ('name', operator, name)] + domain
        return self._search(domain, limit=limit, order=order)


class SbJobPhase(models.Model):
    _name = 'sb.job.phase'
    _description = 'Job phase'
    _order = 'job_id, sequence, id'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    date_start = fields.Date()
    date_end = fields.Date()
    progress = fields.Float('% complete')

    @api.depends('name', 'job_id.code')
    def _compute_display_name(self):
        for p in self:
            p.display_name = '%s / %s' % (p.job_id.code, p.name) if p.job_id else p.name


class SbJobBudget(models.Model):
    _name = 'sb.job.budget'
    _description = 'Job budget line'
    _order = 'job_id, phase_id, cost_code_id'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    phase_id = fields.Many2one('sb.job.phase', ondelete='cascade')
    cost_code_id = fields.Many2one('sb.cost.code', required=True)
    kind = fields.Selection(related='cost_code_id.kind', store=True)
    name = fields.Char('Description')
    qty = fields.Float(default=1.0)
    unit = fields.Char()
    rate = fields.Float()
    amount = fields.Float(compute='_compute_amount', store=True)

    @api.depends('qty', 'rate')
    def _compute_amount(self):
        for b in self:
            b.amount = round(b.qty * b.rate, 2)


class SbJobTask(models.Model):
    _name = 'sb.job.task'
    _description = 'Job task'
    _order = 'job_id, sequence, id'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    phase_id = fields.Many2one('sb.job.phase', ondelete='set null')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Text()
    employee_id = fields.Many2one('hr.employee', 'Assigned to')
    date_start = fields.Date()
    date_end = fields.Date('Due')
    state = fields.Selection([('todo', 'To do'), ('doing', 'In progress'), ('blocked', 'Blocked'), ('done', 'Done')], default='todo', required=True)
    planned_hours = fields.Float()
    priority = fields.Boolean('Urgent')
    milestone = fields.Boolean(help='A milestone: billing may depend on it.')


class SbJobBoq(models.Model):
    """Bill of quantities line: what the contract prices, and how much of it is measured done."""
    _name = 'sb.job.boq'
    _description = 'Bill of quantities line'
    _order = 'job_id, sequence, id'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    section = fields.Char(help='e.g. Substructure, Superstructure, Finishing')
    item_no = fields.Char('Item')
    name = fields.Char('Description', required=True)
    unit = fields.Char()
    qty = fields.Float('Contract qty', digits=(16, 3))
    rate = fields.Float(digits=(16, 2))
    amount = fields.Float(compute='_compute_amount', store=True)
    qty_done = fields.Float('Measured to date', digits=(16, 3), help='From the last approved certificate.')
    phase_id = fields.Many2one('sb.job.phase', ondelete='set null')

    @api.depends('qty', 'rate')
    def _compute_amount(self):
        for b in self:
            b.amount = round(b.qty * b.rate, 2)


class SbJobSub(models.Model):
    _name = 'sb.job.sub'
    _description = 'Subcontract'
    _order = 'job_id, id'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', 'Subcontractor', required=True)
    name = fields.Char('Scope', required=True)
    value = fields.Float('Agreed value')
    retention_pct = fields.Float('Retention %', default=5.0)
    phase_id = fields.Many2one('sb.job.phase', ondelete='set null')
    note = fields.Char()


class SbTimesheet(models.Model):
    _name = 'sb.timesheet'
    _description = 'Timesheet line'
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True)
    company_id = fields.Many2one(related='job_id.company_id', store=True)
    job_id = fields.Many2one('sb.job', required=True, index=True, ondelete='cascade')
    task_id = fields.Many2one('sb.job.task', ondelete='set null')
    phase_id = fields.Many2one('sb.job.phase', ondelete='set null')
    date = fields.Date(required=True, index=True)
    hours = fields.Float(required=True)
    note = fields.Char()
    billable = fields.Boolean(default=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting approval'), ('approved', 'Approved'), ('rejected', 'Rejected')],
                             default='draft', required=True, index=True)
    cost_rate = fields.Float('Cost per hour', help='The person\'s cost per hour when the hours were approved.')
    bill_rate = fields.Float('Billing rate per hour')
    approved_by = fields.Many2one('hr.employee')
    reject_note = fields.Char()
    billed_id = fields.Many2one('sb.job.bill', 'Billed on', ondelete='set null')

    @api.constrains('hours')
    def _check_hours(self):
        for t in self:
            if t.hours <= 0 or t.hours > 24:
                raise ValidationError(self.env._('Hours must be between 0 and 24.'))


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    job_id = fields.Many2one('sb.job', 'Job', index=True, check_company=True)
    job_phase_id = fields.Many2one('sb.job.phase', 'Phase', domain="[('job_id', '=', job_id)]")
    cost_code_id = fields.Many2one('sb.cost.code', 'Cost code')


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    job_id = fields.Many2one('sb.job', 'Job', index=True)
    job_phase_id = fields.Many2one('sb.job.phase', 'Phase', domain="[('job_id', '=', job_id)]")
    cost_code_id = fields.Many2one('sb.cost.code', 'Cost code')
