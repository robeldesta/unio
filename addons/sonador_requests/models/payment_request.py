"""Payment requests (general payments, petty cash, driver per diem).

The requester asks for an amount for a payee and a purpose; the department head approves; finance checks it and codes
the accounts it is paid to (the payment order: one or more lines of account, partner, vehicle and amount, which must add
up to the amount asked); the general manager approves above the company's limit. The cashier then makes the payment
voucher from the request in one click (petty cash requests: paid from the petty cash box) and posts it in Accounting;
the request follows the vouchers posted against it: partly paid, paid."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

REQUEST_TYPES = [('payment', 'Payment request'), ('petty', 'Petty cash request'), ('perdiem', 'Driver per diem')]
SEQ = {'payment': 'sb.payment.request', 'petty': 'sb.payment.request.petty', 'perdiem': 'sb.payment.request.perdiem'}


class SbRequestCategory(models.Model):
    _name = 'sb.request.category'
    _description = 'Payment request category'
    _order = 'parent_id, sequence, name'
    _parent_store = False

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    parent_id = fields.Many2one('sb.request.category', 'Group', ondelete='restrict', index=True)
    company_id = fields.Many2one('res.company', 'Company', help='Empty: every company.')
    account_id = fields.Many2one('account.account', 'Expense / payable account',
                                 help='Account the payment is coded to unless finance codes it otherwise.')
    request_type = fields.Selection(REQUEST_TYPES, 'Only for', help='Empty: any kind of request.')

    @api.depends('name', 'parent_id.name')
    def _compute_display_name(self):
        for c in self:
            c.display_name = '%s / %s' % (c.parent_id.name, c.name) if c.parent_id else c.name


class SbPaymentRequest(models.Model):
    _name = 'sb.payment.request'
    _description = 'Payment request'
    _inherit = ['sb.request.mixin']
    _order = 'id desc'

    state = fields.Selection(selection_add=[('partial', 'Partly paid'), ('paid', 'Paid'), ('cancel',)],
                             ondelete={'partial': 'set default', 'paid': 'set default'})
    request_type = fields.Selection(REQUEST_TYPES, 'Kind', required=True, default='payment', tracking=True, index=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    partner_id = fields.Many2one('res.partner', 'Pay to', tracking=True, check_company=True,
                                 help='Vendor, employee or anyone the money goes to.')
    paid_to = fields.Char('Pay to (name)', help='When the payee is not in the contacts (petty cash: the person who receives the cash).')
    category_id = fields.Many2one('sb.request.category', 'Category', tracking=True,
                                  domain="['|', ('request_type', '=', False), ('request_type', '=', request_type), "
                                         "'|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    description = fields.Text('Purpose', required=True, tracking=True)
    amount = fields.Monetary('Amount', currency_field='currency_id', tracking=True)
    line_ids = fields.One2many('sb.payment.request.line', 'request_id', 'Payment order', copy=True,
                               help='Finance codes the payment: accounts, partner and amounts (they add up to the amount asked).')
    lines_total = fields.Monetary(compute='_compute_lines_total', currency_field='currency_id', string='Coded')
    order_ref = fields.Char('Payment order no.', copy=False, help='Number of the payment order in the previous system.')
    attachment_note = fields.Char('Supporting documents', help='Invoice, quotation, agreement ... (attach them in the chatter).')
    # per diem
    trip_id = fields.Many2one('sb.trip', 'Trip', tracking=True, check_company=True,
                              domain="[('perdiem_voucher_id', '=', False), ('state', '!=', 'cancel')]")
    vehicle_id = fields.Many2one('sb.vehicle', 'Vehicle', tracking=True)
    driver_id = fields.Many2one('hr.employee', 'Driver', tracking=True)
    date_from = fields.Datetime('From')
    date_to = fields.Datetime('To')
    destination = fields.Char()
    days = fields.Float('Days', digits=(16, 2))
    rate = fields.Monetary('Rate per day', currency_field='currency_id')
    extra_amount = fields.Monetary('Other receipts', currency_field='currency_id', help='Fuel, parking, loading ... with receipts.')
    extra_reason = fields.Char('Other receipts for')
    # payment
    voucher_ids = fields.One2many('sb.voucher', 'sb_request_id', 'Payment vouchers', readonly=True,
                                  groups='account.group_account_invoice,account.group_account_readonly')
    voucher_count = fields.Integer(compute='_compute_paid', compute_sudo=True)
    amount_paid = fields.Monetary('Paid', compute='_compute_paid', currency_field='currency_id', compute_sudo=True)
    amount_due = fields.Monetary('Still to pay', compute='_compute_paid', currency_field='currency_id', compute_sudo=True)
    draft_voucher_id = fields.Many2one('sb.voucher', compute='_compute_paid', string='Voucher being prepared', compute_sudo=True)

    _SB_LOCKED = ('amount', 'requester_id', 'company_id', 'request_type')

    def _sb_kind(self):
        return self.request_type or 'payment'

    def _sb_sequence_code(self):
        return SEQ.get(self.request_type or 'payment')

    @api.depends('voucher_ids.state', 'voucher_ids.amount_total', 'amount', 'state')
    def _compute_paid(self):
        for r in self:
            posted = r.voucher_ids.filtered(lambda v: v.state == 'posted')
            r.amount_paid = sum(posted.mapped('amount_total'))
            r.amount_due = max(r.amount - r.amount_paid, 0.0) if r.state not in ('cancel',) else 0.0
            r.voucher_count = len(r.voucher_ids)
            r.draft_voucher_id = r.voucher_ids.filtered(lambda v: v.state == 'draft')[:1]

    @api.depends('line_ids.amount')
    def _compute_lines_total(self):
        for r in self:
            r.lines_total = sum(r.line_ids.mapped('amount'))

    @api.onchange('trip_id')
    def _onchange_trip(self):
        t = self.trip_id.sudo()
        if not t:
            return
        self.vehicle_id = t.vehicle_id
        self.driver_id = t.driver_id
        self.date_from = t.out_at
        self.date_to = t.back_at
        self.destination = t.destination or t.purpose
        self.days = t.days or self.days
        self.rate = t.perdiem_rate or t.company_id.sb_fleet_perdiem_rate
        if t.driver_id.work_contact_id:
            self.partner_id = t.driver_id.work_contact_id

    @api.onchange('request_type')
    def _onchange_request_type(self):
        if self.request_type == 'perdiem' and not self.rate:
            self.rate = self.company_id.sb_fleet_perdiem_rate

    @api.onchange('days', 'rate', 'extra_amount')
    def _onchange_perdiem_amount(self):
        if self.request_type == 'perdiem':
            self.amount = round((self.days or 0.0) * (self.rate or 0.0) + (self.extra_amount or 0.0), 2)

    @api.onchange('driver_id')
    def _onchange_driver(self):
        if self.driver_id.work_contact_id and self.request_type == 'perdiem':
            self.partner_id = self.driver_id.work_contact_id

    @api.depends('partner_id', 'paid_to', 'amount')
    def _compute_display_name(self):
        for r in self:
            r.display_name = r.name or '/'

    def _sb_payee(self):
        return self.partner_id.name or self.paid_to or ''

    # ------------------------------------------------------------------ checks of the steps
    def _sb_check_submit(self):
        super()._sb_check_submit()
        if self.request_type == 'perdiem' and not (self.driver_id or self.partner_id):
            raise UserError(self.env._('%s: choose the driver.', self.name))
        if not (self.partner_id or self.paid_to):
            raise UserError(self.env._('%s: say who is paid.', self.name))
        trip = self.trip_id.sudo()
        if trip and (trip.perdiem_voucher_id or self.search_count([('trip_id', '=', trip.id), ('id', '!=', self.id),
                                                                   ('state', 'not in', ('draft', 'cancel'))])):
            raise UserError(self.env._('The per diem of trip %s is asked or paid already.', trip.name))

    def _sb_check_approve(self, step):
        if step == 'finance' or (step == 'gm' and 'finance' not in self._sb_steps()):
            if self.line_ids and self.currency_id.compare_amounts(self.lines_total, self.amount):
                raise UserError(self.env._('%(n)s: the payment order lines add up to %(t)s, the request is for %(a)s.',
                                           n=self.name, t=self.lines_total, a=self.amount))
            if not self.line_ids and not self._sb_default_account():
                raise UserError(self.env._('%s: code the account it is paid to (payment order lines or a category with an '
                                           'account).', self.name))

    def _sb_on_approved(self):
        for r in self.filtered(lambda x: x.trip_id and x.request_type == 'perdiem'):
            trip = r.trip_id.sudo()
            if trip.perdiem_state in ('none', 'requested', False):
                trip.write({'perdiem': r.amount, 'days': r.days or trip.days, 'perdiem_state': 'approved'})

    def _sb_check_cancel(self):
        if self.sudo().voucher_ids.filtered(lambda v: v.state == 'posted'):
            raise UserError(self.env._('%s is paid (partly): void the payment voucher first.', self.name))

    # ------------------------------------------------------------------ paying it
    def _sb_default_account(self):
        if self.request_type == 'perdiem' and self.company_id.sb_fleet_perdiem_account_id:
            return self.category_id.account_id or self.company_id.sb_fleet_perdiem_account_id
        return self.category_id.account_id

    def _sb_voucher_lines(self, amount):
        """The distribution of a payment of this amount: the payment order lines (pro rata for a part payment), else
        the category's account."""
        self.ensure_one()
        lines = []
        if self.line_ids:
            total = sum(self.line_ids.mapped('amount')) or 1.0
            left = amount
            for i, l in enumerate(self.line_ids):
                part = left if i == len(self.line_ids) - 1 else self.currency_id.round(amount * l.amount / total)
                left -= part
                vals = {'account_id': l.account_id.id, 'name': l.name or self.description[:120],
                        'partner_id': l.partner_id.id or False, 'amount': part}
                if l.vehicle_id:
                    vals['vehicle_id'] = l.vehicle_id.id
                lines.append(Command.create(vals))
        else:
            acc = self._sb_default_account()
            if not acc:
                raise UserError(self.env._('%s: finance has not coded the account it is paid to.', self.name))
            vals = {'account_id': acc.id, 'name': (self.description or '')[:120], 'amount': amount}
            if self.vehicle_id:
                vals['vehicle_id'] = self.vehicle_id.id
            lines.append(Command.create(vals))
        return lines

    def action_create_voucher(self):
        """The cashier's button: a payment voucher for what is still to pay, ready to post."""
        self.ensure_one()
        if not self.env.user.has_group('sonador_requests.group_request_cashier'):
            raise UserError(self.env._('Only a cashier makes the payment voucher.'))
        if not self.env.user.has_group('account.group_account_invoice'):
            raise UserError(self.env._('Making and posting the payment voucher needs an Accounting role (Clerk or higher) '
                                       'besides the cashier role. Ask the administrator to give it to you.'))
        if self.state not in ('approved', 'partial'):
            raise UserError(self.env._('%s is not approved for payment yet.', self.name))
        if self.draft_voucher_id:
            return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': self.draft_voucher_id.id,
                    'views': [[False, 'form']], 'target': 'current'}
        due = self.amount_due
        if self.currency_id.compare_amounts(due, 0.0) <= 0:
            raise UserError(self.env._('%s is paid already.', self.name))
        V = self.env['sb.voucher'].with_context(default_voucher_type='payment')
        vals = {'voucher_type': 'payment', 'company_id': self.company_id.id, 'date': fields.Date.context_today(self),
                'partner_id': self.partner_id.id or False, 'reference': self.name, 'sb_request_id': self.id,
                'memo': ('%s: %s' % (self._sb_payee(), self.description or ''))[:250] if not self.partner_id else (self.description or '')[:250],
                'payer_type': 'employee' if self.request_type in ('perdiem', 'petty') else 'vendor',
                'line_ids': self._sb_voucher_lines(due)}
        if self.request_type == 'petty':
            box = V._sb_petty_cash()
            if box:
                vals['cash_account_id'] = box.id
                vals['payment_method'] = 'cash'
                vals['cash_line_ids'] = [Command.create({'account_id': box.id, 'method': 'cash'})]
            # a petty cash request is paid on the petty cash payment pad (PCPV), whichever petty box pays it
            Series = self.env['sb.doc.series']
            pads = Series.search([('doc_type', '=', 'payment'), ('company_id', '=', self.company_id.id), ('active', '=', True),
                                  ('sb_cash_account_id', '!=', False)])
            pad = pads.filtered(lambda p: box and p.sb_cash_account_id == box)[:1] or pads[:1]
            if pad:
                vals['sb_series_id'] = pad.id
        v = V.create(vals)
        # a supplier is withheld what the law says: the voucher starts with it, the cashier sees it before posting
        if hasattr(v, '_sb_wht_expected') and 'withholding' in v._fields and not v.withholding:
            try:
                wht_due = v._sb_wht_expected()[0]
            except Exception:
                wht_due = 0.0
            if self.currency_id.compare_amounts(wht_due, 0.0) > 0:
                v.withholding = True
        if hasattr(v, '_onchange_sync_single_deposit'):
            v._onchange_sync_single_deposit()
        wht = v.wht_amount if 'withholding' in v._fields and v.withholding else 0.0
        self._sb_log(self.env._('Payment voucher prepared by %(u)s for %(a)s%(w)s.', u=self.env.user.name,
                                a='{:,.2f}'.format(due),
                                w=self.env._(' (withholding %s, paid out %s)', '{:,.2f}'.format(wht), '{:,.2f}'.format(due - wht))
                                if wht else ''))
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': v.id, 'views': [[False, 'form']],
                'target': 'current', 'context': {'default_voucher_type': 'payment'}}

    def action_view_vouchers(self):
        self.ensure_one()
        act = {'type': 'ir.actions.act_window', 'name': self.env._('Payment vouchers'), 'res_model': 'sb.voucher',
               'domain': [('sb_request_id', '=', self.id)], 'context': {'default_voucher_type': 'payment'},
               'views': [[False, 'list'], [False, 'form']]}
        if len(self.voucher_ids) == 1:
            act.update(res_id=self.voucher_ids.id, views=[[False, 'form']])
        return act

    def _sb_sync_paid(self):
        """After a voucher of the request is posted, voided or deleted: approved, partly paid or paid."""
        for r in self.sudo():
            if r.state not in ('approved', 'partial', 'paid'):
                continue
            r.invalidate_recordset(['voucher_ids'])
            paid = sum(r.voucher_ids.filtered(lambda v: v.state == 'posted').mapped('amount_total'))
            cmp = r.currency_id.compare_amounts(paid, r.amount)
            new = 'paid' if cmp >= 0 else ('partial' if r.currency_id.compare_amounts(paid, 0.0) > 0 else 'approved')
            if new != r.state:
                r.write({'state': new})
                r._sb_log(self.env._('%(s)s: %(p)s of %(a)s paid.', s=dict(r._fields['state']._description_selection(self.env))[new],
                                     p='{:,.2f}'.format(paid), a='{:,.2f}'.format(r.amount)))
            if r.trip_id and r.request_type == 'perdiem':
                last = r.voucher_ids.filtered(lambda v: v.state == 'posted')[:1]
                r.trip_id.write({'perdiem_state': 'paid' if new == 'paid' else 'approved', 'perdiem_voucher_id': last.id or False})


class SbPaymentRequestLine(models.Model):
    _name = 'sb.payment.request.line'
    _description = 'Payment order line'
    _order = 'sequence, id'

    request_id = fields.Many2one('sb.payment.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    currency_id = fields.Many2one(related='request_id.currency_id')
    sequence = fields.Integer(default=10)
    name = fields.Char('Description')
    account_id = fields.Many2one('account.account', 'Account', required=True, check_company=True)
    partner_id = fields.Many2one('res.partner', 'Partner')
    vehicle_id = fields.Many2one('sb.vehicle', 'Vehicle')
    amount = fields.Monetary(required=True, currency_field='currency_id')

    @api.constrains('amount')
    def _check_amount(self):
        for l in self:
            if l.amount <= 0:
                raise ValidationError(self.env._('A payment order line is more than zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        ids = {v.get('request_id') for v in vals_list if v.get('request_id')}
        self.env['sb.payment.request'].browse(list(ids))._sb_check_coding()
        return super().create(vals_list)

    def write(self, vals):
        self.request_id._sb_check_coding()
        return super().write(vals)

    def unlink(self):
        self.request_id._sb_check_coding()
        return super().unlink()


class SbPaymentRequestCoding(models.Model):
    _inherit = 'sb.payment.request'

    def _sb_check_coding(self):
        """The payment order is written by the requester in draft or by finance before the final approval."""
        if self.env.su:
            return
        for r in self:
            if r.state == 'draft' and self.env.user in (r.requester_id | r.create_uid):
                continue
            if r.state in ('draft', 'dept', 'finance', 'gm') and self.env.user.has_group('sonador_requests.group_request_finance'):
                continue
            raise UserError(self.env._('%s: the payment order is coded by finance before the final approval.', r.name))
