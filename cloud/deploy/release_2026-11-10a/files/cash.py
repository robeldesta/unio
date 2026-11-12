from odoo import Command, api, fields, models
from odoo.exceptions import UserError

METHODS = [
    ('cash', 'Cash'),
    ('deposit', 'Bank deposit'),
    ('transfer', 'Bank transfer'),
    ('cheque', 'Cheque'),
    ('mobile', 'Mobile money (telebirr, M-Pesa, MTN MoMo ...)'),
    ('card', 'POS / card'),
]


class SbVoucherCash(models.Model):
    """One bank / cash / mobile-money leg of a receipt, payment, cash sale, cash purchase or transfer."""
    _name = 'sb.voucher.cash'
    _description = 'Voucher Deposit / Payment Row'
    _order = 'sequence, id'
    _check_company_auto = True

    voucher_id = fields.Many2one('sb.voucher', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related='voucher_id.company_id', store=True)
    currency_id = fields.Many2one(related='voucher_id.currency_id')
    voucher_type = fields.Selection(related='voucher_id.voucher_type')
    voucher_state = fields.Selection(related='voucher_id.state', string='Voucher Status')
    voucher_date = fields.Date(related='voucher_id.date', string='Voucher Date', store=True)
    partner_id = fields.Many2one(related='voucher_id.partner_id', store=True)
    account_id = fields.Many2one(
        'account.account', string='Bank / Cash', check_company=True,
        domain="['|', ('account_type', '=', 'asset_cash'), ('sb_undeposited', '=', True)]",
        help='For a cheque: the bank it will be deposited to (received) or drawn on (issued).')
    method = fields.Selection(METHODS, default='transfer', required=True)
    row_currency_id = fields.Many2one('res.currency', compute='_compute_row_currency', store=True, string='Row Currency')
    amount_currency = fields.Monetary('Amount', currency_field='row_currency_id')
    rate = fields.Float(digits=(12, 6), compute='_compute_rate', store=True, readonly=False,
                        help='ETB per unit of the account currency.')
    amount = fields.Monetary('Amount (company currency)', compute='_compute_amount', store=True)
    charges = fields.Monetary('Bank Charges', currency_field='row_currency_id',
                              help='Deducted by the bank on this row; booked to bank charges.')
    reference = fields.Char('Ref. / Slip / Cheque No.', help='Deposit slip, transfer reference, transaction ID or cheque number.')
    cheque_bank = fields.Char('Drawn on (bank)')
    cheque_date = fields.Date('Cheque Date', help='A post-dated cheque stays on hand until this date.')
    value_date = fields.Date('Value Date')
    cheque_state = fields.Selection([
        ('none', 'Not a cheque'),
        ('open', 'On hand / outstanding'),
        ('cleared', 'Deposited / cleared'),
        ('bounced', 'Bounced / cancelled'),
    ], default='none', readonly=True, copy=False, string='Cheque Status')
    settle_voucher_id = fields.Many2one('sb.voucher', string='Deposited / cleared by', readonly=True, copy=False)
    direction = fields.Selection([('in', 'Received'), ('out', 'Issued')], compute='_compute_direction', store=True)

    @api.depends('account_id')
    def _compute_row_currency(self):
        for row in self:
            row.row_currency_id = row.account_id.currency_id or row.voucher_id.currency_id or row.env.company.currency_id

    @api.depends('row_currency_id', 'voucher_id.date')
    def _compute_rate(self):
        for row in self:
            company_cur = row.voucher_id.currency_id or row.env.company.currency_id
            cur = row.row_currency_id
            if not cur or cur == company_cur:
                row.rate = 1.0
            else:
                row.rate = cur._get_conversion_rate(cur, company_cur, row.voucher_id.company_id or row.env.company,
                                                    row.voucher_id.date or fields.Date.today())

    @api.depends('amount_currency', 'rate')
    def _compute_amount(self):
        for row in self:
            cur = row.voucher_id.currency_id or row.env.company.currency_id
            row.amount = cur.round(row.amount_currency * (row.rate or 1.0))

    @api.depends('voucher_type')
    def _compute_direction(self):
        for row in self:
            row.direction = 'out' if row.voucher_type in ('payment', 'cash_purchase') else 'in'

    @api.onchange('account_id')
    def _onchange_account_method(self):
        journal = self.env['account.journal'].search([('default_account_id', '=', self.account_id.id)], limit=1)
        if journal.type == 'cash' and self.method != 'cheque':
            self.method = 'cash'
        elif self.method == 'cash' and journal.type == 'bank':
            self.method = 'transfer'
        if self.voucher_id and len(self.voucher_id.cash_line_ids) > 1 and not self.amount_currency:
            self.amount_currency = max(self.voucher_id.amount_to_allocate / (self.rate or 1.0), 0.0)

    def _posting_account(self, direction):
        self.ensure_one()
        company = self.voucher_id.company_id
        if self.method == 'cheque' and self.voucher_type != 'transfer':
            return company.sb_cheque_on_hand_account_id if direction > 0 else company.sb_cheque_outstanding_account_id
        return self.account_id

    def _label(self):
        self.ensure_one()
        method = dict(METHODS).get(self.method)
        parts = [method, self.reference]
        if self.method == 'cheque':
            parts += [self.cheque_bank, self.cheque_date and fields.Date.to_string(self.cheque_date)]
        return ' '.join(p for p in parts if p)

    # ------------------------------------------------------------------ cheque life cycle
    def _cheque_wizard(self, action):
        self.ensure_one()
        allowed = ('open', 'cleared') if action == 'bounce' and self.direction == 'in' else ('open',)
        if self.cheque_state not in allowed:
            raise UserError(self.env._('This cheque is not on hand / outstanding.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sb.cheque.wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': dict(self.env['sb.cheque.wizard']._fields['action'].selection).get(action),
            'context': {'default_row_id': self.id, 'default_action': action,
                        'default_account_id': self.account_id.id},
        }

    def action_cheque_clear(self):
        return self._cheque_wizard('clear')

    def action_cheque_bounce(self):
        return self._cheque_wizard('bounce')

    def action_open_voucher(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': self.voucher_id.id,
                'view_mode': 'form'}


class SbChequeWizard(models.TransientModel):
    _name = 'sb.cheque.wizard'
    _description = 'Deposit / Clear / Bounce Cheque'

    row_id = fields.Many2one('sb.voucher.cash', required=True)
    action = fields.Selection([('clear', 'Deposit / Clear cheque'), ('bounce', 'Cheque bounced / cancelled')],
                              required=True)
    direction = fields.Selection(related='row_id.direction')
    date = fields.Date(required=True, default=fields.Date.context_today)
    account_id = fields.Many2one('account.account', string='Bank', domain="[('account_type', '=', 'asset_cash')]")
    charges = fields.Monetary('Bank charges', currency_field='currency_id')
    currency_id = fields.Many2one(related='row_id.currency_id')
    reference = fields.Char('Deposit slip / statement ref.')

    def action_confirm(self):
        self.ensure_one()
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('deposit, clear or bounce cheques'))
        row = self.row_id
        voucher = row.voucher_id
        company = voucher.company_id
        V = self.env['sb.voucher']
        cheque_label = row._label()
        if self.action == 'clear':
            if not self.account_id:
                raise UserError(self.env._('Choose the bank.'))
            if row.direction == 'in' and row.cheque_date and self.date < row.cheque_date:
                raise UserError(self.env._('This cheque is post-dated %s and cannot be deposited yet.', row.cheque_date))
            if row.direction == 'in':   # cheques on hand -> bank
                src, dest = company.sb_cheque_on_hand_account_id, self.account_id
            else:                       # bank -> outstanding cheques (issued cheque presented)
                src, dest = self.account_id, company.sb_cheque_outstanding_account_id
            settle = V.create({
                'voucher_type': 'transfer', 'company_id': company.id, 'date': self.date,
                'reference': self.reference or row.reference,
                'memo': self.env._('%(c)s of %(v)s', c=cheque_label, v=voucher.name),
                'cash_account_id': src.id,
                'cash_line_ids': [Command.create({'account_id': dest.id, 'method': 'transfer',
                                                  'amount_currency': row.amount, 'reference': row.reference})],
                'line_ids': [Command.create({'account_id': company.sb_bank_charges_account_id.id,
                                             'name': self.env._('Cheque charges'), 'amount': self.charges})]
                if self.charges else [],
            })
            settle.action_post()
            row.write({'cheque_state': 'cleared', 'settle_voucher_id': settle.id})
            return True
        # bounce / cancel
        partner = voucher.partner_id.commercial_partner_id
        if not partner:
            raise UserError(self.env._('The voucher has no customer / vendor to charge the cheque back to.'))
        if row.direction == 'in':
            # customer owes us again; the cheque leaves cheques-on-hand
            lines = [
                Command.create({'account_id': partner.property_account_receivable_id.id, 'partner_id': partner.id,
                                'name': self.env._('Bounced %s', cheque_label), 'debit': row.amount}),
                Command.create({'account_id': (company.sb_cheque_on_hand_account_id if row.cheque_state == 'open'
                                               else (self.account_id or row.account_id)).id,
                                'name': self.env._('Bounced %s', cheque_label), 'credit': row.amount}),
            ]
            if self.charges:
                bank = self.account_id or row.account_id
                vat = 0.0
                if company.sb_bank_charges_vat and company.sb_bank_charges_vat_account_id:
                    vat = company.currency_id.round(self.charges - self.charges / (1 + company.sb_bank_charges_vat / 100.0))
                    lines.append(Command.create({'account_id': company.sb_bank_charges_vat_account_id.id,
                                                 'name': self.env._('VAT in bounced cheque charges'), 'debit': vat}))
                lines += [
                    Command.create({'account_id': company.sb_bank_charges_account_id.id,
                                    'name': self.env._('Bounced cheque charges'), 'debit': self.charges - vat}),
                    Command.create({'account_id': bank.id, 'name': self.env._('Bounced cheque charges'),
                                    'credit': self.charges}),
                ]
        else:
            # issued cheque cancelled before it was presented: we owe the vendor again
            lines = [
                Command.create({'account_id': company.sb_cheque_outstanding_account_id.id,
                                'name': self.env._('Cancelled %s', cheque_label), 'debit': row.amount}),
                Command.create({'account_id': partner.property_account_payable_id.id, 'partner_id': partner.id,
                                'name': self.env._('Cancelled %s', cheque_label), 'credit': row.amount}),
            ]
        jv = V.create({'voucher_type': 'journal', 'company_id': company.id, 'date': self.date,
                       'partner_id': partner.id, 'reference': row.reference,
                       'memo': self.env._('%(c)s of %(v)s bounced / cancelled', c=cheque_label, v=voucher.name),
                       'line_ids': lines})
        jv.action_post()
        row.write({'cheque_state': 'bounced', 'settle_voucher_id': jv.id})
        return True
