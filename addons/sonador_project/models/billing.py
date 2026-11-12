"""Job billing: milestone / progress invoices, time and materials (approved hours), interim payment certificates
measured against the bill of quantities (retention, advance recovery, VAT), and retention release. Approving a bill
creates the sales invoice in the books."""
from odoo import fields, models

BILL_KINDS = [('milestone', 'Milestone / progress'), ('tm', 'Time and materials'), ('ipc', 'Interim payment certificate'),
              ('retention', 'Retention release')]


class SbJobBill(models.Model):
    _name = 'sb.job.bill'
    _description = 'Job billing'
    _order = 'job_id, date desc, id desc'

    job_id = fields.Many2one('sb.job', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='job_id.company_id', store=True)
    currency_id = fields.Many2one(related='job_id.currency_id')
    kind = fields.Selection(BILL_KINDS, required=True, default='milestone')
    number = fields.Char()
    date = fields.Date(required=True, default=fields.Date.context_today)
    period_to = fields.Date('Work up to')
    name = fields.Char('Description')
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('invoiced', 'Invoiced'), ('void', 'Void')], default='draft', required=True)
    gross_to_date = fields.Monetary('Work done to date')
    prev_gross = fields.Monetary('Certified before')
    gross = fields.Monetary('This bill (gross)')
    retention = fields.Monetary()
    recovery = fields.Monetary('Advance recovery')
    net = fields.Monetary('Due before VAT')
    vat = fields.Monetary('VAT')
    total = fields.Monetary('Total due')
    line_ids = fields.One2many('sb.job.bill.line', 'bill_id')
    timesheet_ids = fields.One2many('sb.timesheet', 'billed_id')
    voucher_id = fields.Many2one('sb.voucher', 'Sales invoice', readonly=True, copy=False)
    note = fields.Text()


class SbJobBillLine(models.Model):
    _name = 'sb.job.bill.line'
    _description = 'Certificate line'
    _order = 'bill_id, sequence, id'

    bill_id = fields.Many2one('sb.job.bill', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    boq_id = fields.Many2one('sb.job.boq', ondelete='set null')
    name = fields.Char()
    unit = fields.Char()
    rate = fields.Float()
    qty_prev = fields.Float('Before', digits=(16, 3))
    qty_to_date = fields.Float('To date', digits=(16, 3))
    qty_this = fields.Float('This period', digits=(16, 3))
    amount_to_date = fields.Float()
    amount_this = fields.Float()
