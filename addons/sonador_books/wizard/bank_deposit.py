from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class AccountAccountUndeposited(models.Model):
    _inherit = 'account.account'

    sb_undeposited = fields.Boolean('Undeposited funds', help='Money received but not yet taken to the bank. '
                                    'Can be used as "Deposited to" on receipts and cash sales; a Bank Deposit moves it to a bank.')


class SbBankDeposit(models.TransientModel):
    _name = 'sb.bank.deposit'
    _description = 'Bank Deposit'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date('Deposit date', required=True, default=fields.Date.context_today)
    account_id = fields.Many2one('account.account', string='Deposit to', required=True,
                                 domain="[('account_type', '=', 'asset_cash')]")
    slip = fields.Char('Deposit slip no.')
    source_id = fields.Many2one('account.account', string='From', required=True,
                                default=lambda s: s.env['account.account'].search([('sb_undeposited', '=', True)], limit=1),
                                domain="[('sb_undeposited', '=', True)]")
    line_ids = fields.One2many('sb.bank.deposit.line', 'deposit_id')
    total = fields.Monetary(compute='_compute_total')
    count = fields.Integer(compute='_compute_total')
    voucher_id = fields.Many2one('sb.voucher', readonly=True)

    @api.depends('line_ids.include', 'line_ids.amount')
    def _compute_total(self):
        for d in self:
            inc = d.line_ids.filtered('include')
            d.total = sum(inc.mapped('amount'))
            d.count = len(inc)

    @api.depends('date')
    def _compute_display_name(self):
        for d in self:
            d.display_name = self.env._('Bank Deposit')

    def action_load(self):
        self.ensure_one()
        lines = self.env['account.move.line'].search([
            ('account_id', '=', self.source_id.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
            ('amount_residual', '>', 0), ('date', '<=', self.date), ('company_id', '=', self.company_id.id)], order='date, id')
        self.line_ids = [Command.clear()] + [Command.create({'move_line_id': l.id, 'include': True}) for l in lines]
        return self._reopen()

    def action_deposit(self):
        self.ensure_one()
        inc = self.line_ids.filtered('include')
        if not inc:
            raise UserError(self.env._('Tick the receipts going to the bank.'))
        names = ', '.join(inc.mapped('move_name'))
        v = self.env['sb.voucher'].create({
            'voucher_type': 'transfer', 'company_id': self.company_id.id, 'date': self.date,
            'reference': self.slip, 'memo': self.env._('Bank deposit %(slip)s: %(n)s receipts', slip=self.slip or '', n=len(inc)),
            'cash_account_id': self.source_id.id,
            'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': 'deposit', 'reference': self.slip,
                                              'amount_currency': self.total})],
        })
        v.action_post()
        credit = v.move_id.line_ids.filtered(lambda l: l.account_id == self.source_id)
        (credit | inc.mapped('move_line_id')).reconcile()
        v.message_post(body=self.env._('Deposited: %s', names))
        self.voucher_id = v
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'res_id': v.id, 'view_mode': 'form',
                'name': self.env._('Bank deposit')}

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Bank Deposit')}


class SbBankDepositLine(models.TransientModel):
    _name = 'sb.bank.deposit.line'
    _description = 'Item to deposit'
    _order = 'date, id'

    deposit_id = fields.Many2one('sb.bank.deposit', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='deposit_id.currency_id')
    move_line_id = fields.Many2one('account.move.line', required=True)
    date = fields.Date(related='move_line_id.date')
    move_name = fields.Char(related='move_line_id.move_id.name', string='Receipt')
    partner_id = fields.Many2one(related='move_line_id.partner_id', string='From')
    name = fields.Char(related='move_line_id.name', string='Description')
    amount = fields.Monetary(compute='_compute_amount')
    include = fields.Boolean(default=True)

    @api.depends('move_line_id')
    def _compute_amount(self):
        for l in self:
            l.amount = l.move_line_id.amount_residual
