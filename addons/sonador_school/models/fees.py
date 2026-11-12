"""School fees on the school's own books. A bill is a journal entry (Dr the family's receivable, Cr tuition income);
tuition is VAT-exempt and a fee bill is not a tax invoice. Payments are ordinary receipts (CRV) applied to the
oldest open bills of the family, so the books, the customer ledger and the ageing all stay right."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

KINDS = [('monthly', 'Every month'), ('term', 'Every semester'), ('annual', 'Once a year'), ('once', 'Once (on admission)')]


class SchoolFeeItem(models.Model):
    _name = 'school.fee.item'
    _description = 'Fee'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    name_am = fields.Char('Name (Amharic)')
    sequence = fields.Integer(default=10)
    kind = fields.Selection(KINDS, required=True, default='monthly')
    amount = fields.Float('Amount (all grades)', help='Used when the grade has no amount of its own.')
    price_ids = fields.One2many('school.fee.price', 'item_id', 'Amount per grade')
    transport_only = fields.Boolean('Only students using transport')
    discountable = fields.Boolean('Sibling discount and scholarships apply', default=True)
    account_id = fields.Many2one('account.account', 'Income account')
    active = fields.Boolean(default=True)

    def amount_for(self, grade):
        self.ensure_one()
        p = self.price_ids.filtered(lambda x: x.grade_id == grade)[:1]
        return p.amount if p else self.amount


class SchoolFeePrice(models.Model):
    _name = 'school.fee.price'
    _description = 'Fee amount for a grade'

    item_id = fields.Many2one('school.fee.item', required=True, ondelete='cascade')
    grade_id = fields.Many2one('school.grade', required=True)
    amount = fields.Float(required=True)


class SchoolBill(models.Model):
    _name = 'school.bill'
    _description = 'Fee bill'
    _order = 'date desc, id desc'

    name = fields.Char(readonly=True, copy=False, default='/')
    student_id = fields.Many2one('school.student', required=True, index=True, ondelete='restrict')
    payer_id = fields.Many2one('res.partner', required=True, index=True)
    period = fields.Char(required=True, help='e.g. Tikimt 2019, Semester 1 2019')
    date = fields.Date(required=True, default=fields.Date.context_today)
    due_date = fields.Date()
    line_ids = fields.One2many('school.bill.line', 'bill_id')
    amount = fields.Float(compute='_compute_amount', store=True)
    move_id = fields.Many2one('account.move', readonly=True, copy=False)
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Billed'), ('cancel', 'Cancelled')], default='draft', index=True)
    residual = fields.Float('Still owed', compute='_compute_residual')

    @api.depends('line_ids.amount', 'line_ids.discount')
    def _compute_amount(self):
        for b in self:
            b.amount = round(sum(l.amount - l.discount for l in b.line_ids), 2)

    def _compute_residual(self):
        for b in self:
            rec = b.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
            b.residual = sum(rec.mapped('amount_residual')) if b.state == 'posted' else 0.0

    def _journal(self):
        J = self.env['account.journal'].sudo()
        j = J.search([('code', '=', 'FEES'), ('company_id', '=', self.env.company.id)], limit=1)
        return j or J.create({'name': 'School fees', 'code': 'FEES', 'type': 'general', 'company_id': self.env.company.id})

    def _receivable(self, partner):
        acc = partner.with_company(self.env.company).property_account_receivable_id
        return acc or self.env['account.account']._sb_find(('221100',))[:1]

    def _income(self, item):
        A = self.env['account.account'].sudo()
        if item.account_id:
            return item.account_id
        acc = A.search([('name', '=', 'Tuition and school fees'), ('company_ids', 'in', self.env.company.id)], limit=1)
        if not acc:
            acc = A.create({'code': A._sb_new_code('110400'), 'name': 'Tuition and school fees', 'account_type': 'income',
                            'company_ids': [Command.set(self.env.company.ids)]})
        return acc

    def action_post(self):
        for b in self.filtered(lambda x: x.state == 'draft'):
            if b.amount <= 0:
                b.state = 'cancel'
                continue
            lines = [Command.create({'account_id': b._receivable(b.payer_id).id, 'partner_id': b.payer_id.id, 'debit': b.amount,
                                     'name': '%s · %s' % (b.student_id.name, b.period), 'date_maturity': b.due_date or b.date})]
            for l in b.line_ids:
                net = round(l.amount - l.discount, 2)
                if net:
                    lines.append(Command.create({'account_id': b._income(l.item_id).id, 'partner_id': b.payer_id.id, 'credit': net,
                                                 'name': '%s · %s · %s' % (l.name, b.student_id.name, b.period)}))
            seq = self.env['ir.sequence'].next_by_code('school.bill') or '/'
            m = self.env['account.move'].sudo().create({'move_type': 'entry', 'journal_id': b._journal().id, 'date': b.date,
                                                        'ref': '%s %s' % (seq, b.student_id.name), 'line_ids': lines})
            m.action_post()
            b.write({'name': seq, 'move_id': m.id, 'state': 'posted'})
        return True

    def action_cancel(self):
        for b in self.filtered(lambda x: x.state == 'posted'):
            if b.residual < b.amount - 0.01:
                raise UserError(self.env._('%s is partly paid: cancel the payment first.', b.name))
            # the entry was made by the system when the bill was posted (sudo): it is reversed the same way
            b.move_id.sudo()._reverse_moves(default_values_list=[{'date': fields.Date.context_today(self), 'ref': 'Cancel %s' % b.name}], cancel=True)
            b.state = 'cancel'
        return True


class SchoolBillLine(models.Model):
    _name = 'school.bill.line'
    _description = 'Fee bill line'

    bill_id = fields.Many2one('school.bill', required=True, ondelete='cascade')
    item_id = fields.Many2one('school.fee.item')
    name = fields.Char(required=True)
    amount = fields.Float()
    discount = fields.Float()
    discount_note = fields.Char()
