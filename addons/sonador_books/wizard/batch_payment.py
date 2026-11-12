from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class SbBatchPayment(models.TransientModel):
    _name = 'sb.batch.payment'
    _description = 'Pay Several Bills'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date('Payment date', required=True, default=fields.Date.context_today)
    due_by = fields.Date('Bills due by', required=True, default=fields.Date.context_today)
    account_id = fields.Many2one('account.account', string='Pay from', required=True,
                                 domain="[('account_type', '=', 'asset_cash')]",
                                 default=lambda s: s.env['sb.voucher']._default_cash_account())
    method = fields.Selection([('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('cash', 'Cash')], default='transfer', required=True)
    withholding = fields.Boolean('Withhold tax where it applies', default=True,
                                 help='Each bill is tested on its own, as on a single payment: its value before VAT against the '
                                      'goods or services threshold, at the rate for the vendor.')
    wht_exempt_reason = fields.Char('Reason not withheld',
                                    help='Paying without withholding where it is due: the exemption certificate number, or why. '
                                         'The payments are listed in the withholding declaration with it.')
    line_ids = fields.One2many('sb.batch.payment.line', 'batch_id')
    total = fields.Monetary(compute='_compute_total')
    count = fields.Integer(compute='_compute_total')
    wht_total = fields.Monetary('Withheld', compute='_compute_total')
    wht_due_total = fields.Monetary('Withholding due', compute='_compute_total')
    net_total = fields.Monetary('Paid out', compute='_compute_total')
    created_ids = fields.Many2many('sb.voucher', string='Payments created', readonly=True)

    @api.depends('line_ids.pay', 'line_ids.amount', 'line_ids.wht_amount', 'line_ids.wht_due')
    def _compute_total(self):
        for b in self:
            paid = b.line_ids.filtered('pay')
            b.total = sum(paid.mapped('amount'))
            b.count = len(paid.partner_id)
            b.wht_total = sum(paid.mapped('wht_amount'))
            b.wht_due_total = sum(paid.mapped('wht_due'))
            b.net_total = b.total - b.wht_total

    def action_load(self):
        self.ensure_one()
        lines = self.env['account.move.line'].search([
            ('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
            ('account_id.account_type', '=', 'liability_payable'), ('amount_residual', '<', 0),
            ('move_id.move_type', '=', 'in_invoice'), '|', ('date_maturity', '<=', self.due_by), ('date_maturity', '=', False),
        ], order='partner_id, date_maturity')
        self.line_ids = [Command.clear()] + [Command.create({
            'move_line_id': l.id, 'pay': True, 'amount': -l.amount_residual}) for l in lines]
        return self._reopen()

    def action_pay(self):
        """One payment per vendor and supply (goods / services), withheld bill by bill as a single payment is."""
        self.ensure_one()
        chosen = self.line_ids.filtered(lambda l: l.pay and l.amount > 0)
        if not chosen:
            raise UserError(self.env._('Tick at least one bill.'))
        unknown = chosen.filtered(lambda l: not l.wht_supply and l.wht_needs_supply)
        if unknown:
            raise UserError(self.env._('Choose goods or services for %s: withholding depends on it.',
                                       ', '.join(unknown.mapped('move_id.name'))))
        reason = (self.wht_exempt_reason or '').strip()
        if not self.withholding and not reason and any(chosen.mapped('wht_due')):
            due = chosen.filtered('wht_due')
            raise UserError(self.env._(
                'Withholding of %(due)s is due on %(bills)s. To pay without it, enter the reason: the exemption certificate '
                'number, or a note.', due='{:,.2f}'.format(sum(due.mapped('wht_due'))), bills=', '.join(due.mapped('move_id.name'))))
        groups = defaultdict(lambda: self.env['sb.batch.payment.line'])
        for l in chosen:
            groups[l.partner_id, l.wht_supply] |= l
        vouchers = self.env['sb.voucher']
        for (partner, supply), lines in groups.items():
            v = self.env['sb.voucher'].create(dict(lines[0]._sb_voucher_vals(lines), **{
                'payer_type': 'vendor', 'memo': self.env._('Payment of %s bills', len(lines)),
                'wht_exempt_reason': reason if not self.withholding and any(lines.mapped('wht_due')) else False,
                'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': self.method})],
            }))
            v._onchange_sync_single_deposit()  # withholding ticks itself where the rule asks for it (no reason given)
            v.action_post()
            vouchers |= v
        self.created_ids = vouchers
        return {
            'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'view_mode': 'list,form',
            'domain': [('id', 'in', vouchers.ids)], 'name': self.env._('Payments created'),
            'context': {'default_voucher_type': 'payment'},
        }

    @api.depends('date')
    def _compute_display_name(self):
        for b in self:
            b.display_name = self.env._('Pay Several Bills')

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Pay Several Bills')}


class SbBatchPaymentLine(models.TransientModel):
    _name = 'sb.batch.payment.line'
    _description = 'Bill to pay'
    _order = 'partner_id, date_due'

    batch_id = fields.Many2one('sb.batch.payment', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='batch_id.currency_id')
    move_line_id = fields.Many2one('account.move.line', required=True)
    move_id = fields.Many2one(related='move_line_id.move_id', string='Bill')
    partner_id = fields.Many2one(related='move_line_id.partner_id', store=True, string='Vendor')
    ref = fields.Char(related='move_id.ref', string='Their ref.')
    date_due = fields.Date(related='move_line_id.date_maturity', store=True, string='Due')
    residual = fields.Monetary(compute='_compute_residual', string='Balance due')
    pay = fields.Boolean(default=True)
    amount = fields.Monetary('Pay now')
    wht_supply = fields.Selection([('goods', 'Goods'), ('service', 'Services'), ('professional', 'Professional fees')], 'Goods or services',
                                  compute='_compute_wht_supply', store=True, readonly=False)
    wht_needs_supply = fields.Boolean(compute='_compute_wht')
    wht_amount = fields.Monetary('Withholding', compute='_compute_wht', help='Withheld on this bill when the payments are posted.')
    wht_due = fields.Monetary('Withholding due', compute='_compute_wht', help='What the rule asks on this bill.')
    wht_rule = fields.Char('Rule', compute='_compute_wht')

    @api.depends('move_line_id')
    def _compute_residual(self):
        for l in self:
            l.residual = -l.move_line_id.amount_residual

    def _sb_voucher_vals(self, lines=None):
        """A payment to the vendor of these bills (this one bill by default) for the amounts to pay now."""
        lines = lines or self
        first = lines[0]
        applies = []
        for l in lines:
            ml = l.move_line_id
            amount = l.amount
            if ml.currency_id and ml.currency_id != ml.company_currency_id and ml.amount_residual:
                amount = ml.currency_id.round(l.amount * ml.amount_residual_currency / ml.amount_residual)  # in the bill's currency
            applies.append(Command.create({'line_id': ml.id, 'amount_applied': amount, 'pay': True}))
        return {'voucher_type': 'payment', 'company_id': first.batch_id.company_id.id, 'date': first.batch_id.date,
                'partner_id': first.partner_id.id, 'wht_supply': first.wht_supply, 'apply_ids': applies}

    @api.depends('move_line_id')
    def _compute_wht_supply(self):
        for l in self:
            l.wht_supply = self.env['sb.voucher'].new(l._sb_voucher_vals())._sb_wht_guess_supply() if l.move_line_id else False

    @api.depends('move_line_id', 'amount', 'pay', 'wht_supply', 'batch_id.date', 'batch_id.withholding')
    def _compute_wht(self):
        """Withholding on each bill as a single payment of it works it out (_sb_wht_split): before VAT, its own threshold."""
        for l in self:
            l.wht_needs_supply, l.wht_amount, l.wht_due, l.wht_rule = False, 0.0, 0.0, False
            if not (l.pay and l.amount > 0 and l.move_line_id):
                continue
            v = self.env['sb.voucher'].new(l._sb_voucher_vals())
            if not v.sb_wht_country:
                continue
            rate, _threshold, rule = v._sb_wht_rule()
            due = v.currency_id.round(v._sb_wht_parts()[0] * rate / 100.0)
            l.wht_needs_supply = not l.wht_supply and bool(due)
            if l.wht_supply:
                l.wht_due, l.wht_rule = due, rule
                l.wht_amount = due if l.batch_id.withholding else 0.0
