from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class SbFinanceCharge(models.TransientModel):
    _name = 'sb.finance.charge'
    _description = 'Finance Charges'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date('Charge as at', required=True, default=fields.Date.context_today)
    rate = fields.Float('Interest per month (%)', default=1.5, required=True)
    grace_days = fields.Integer('Grace days after due date', default=30)
    minimum = fields.Monetary('Do not charge below', default=50.0)
    account_id = fields.Many2one('account.account', string='Income account', required=True,
                                 default=lambda s: s.env['account.account']._sb_find('120400')[:1]
                                 or s.env['account.account']._sb_find('120200')[:1])
    line_ids = fields.One2many('sb.finance.charge.line', 'wizard_id')
    total = fields.Monetary(compute='_compute_total')

    @api.depends('line_ids.charge', 'line_ids.apply')
    def _compute_total(self):
        for w in self:
            w.total = sum(w.line_ids.filtered('apply').mapped('charge'))

    def action_compute(self):
        self.ensure_one()
        lines = self.env['account.move.line'].search([
            ('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
            ('account_id.account_type', '=', 'asset_receivable'), ('amount_residual', '>', 0)])
        per = defaultdict(lambda: {'overdue': 0.0, 'charge': 0.0, 'days': 0})
        for l in lines:
            due = l.date_maturity or l.date
            days = (self.date - due).days - self.grace_days
            if days <= 0:
                continue
            p = per[l.partner_id.id]
            p['overdue'] += l.amount_residual
            p['charge'] += l.amount_residual * self.rate / 100.0 * days / 30.0
            p['days'] = max(p['days'], days + self.grace_days)
        rows = []
        for pid, v in per.items():
            charge = self.currency_id.round(v['charge'])
            if charge < self.minimum or not pid:
                continue
            rows.append(Command.create({'partner_id': pid, 'overdue': v['overdue'], 'days': v['days'], 'charge': charge, 'apply': True}))
        self.line_ids = [Command.clear()] + rows
        return self._reopen()

    def action_invoice(self):
        self.ensure_one()
        chosen = self.line_ids.filtered(lambda l: l.apply and l.charge > 0)
        if not chosen:
            raise UserError(self.env._('Nothing to charge.'))
        vouchers = self.env['sb.voucher']
        from ..models.voucher_controls import FINANCE_CHARGE
        for l in chosen:
            # interest on late payment goes to its income account (other income), not a sales account: allowed on these
            # invoices only (voucher_controls._sb_check_document_accounts)
            v = self.env['sb.voucher'].with_context(sb_finance_charge=FINANCE_CHARGE).create({
                'voucher_type': 'sale', 'company_id': self.company_id.id, 'date': self.date, 'partner_id': l.partner_id.id,
                'memo': self.env._('Finance charge'), 'reference': self.env._('Late payment interest'),
                'line_ids': [Command.create({
                    'name': self.env._('Finance charge: %(rate)s%% per month on %(amt)s overdue (up to %(days)s days)',
                                       rate=self.rate, amt='{:,.2f}'.format(l.overdue), days=l.days),
                    'account_id': self.account_id.id, 'price_unit': l.charge, 'quantity': 1,
                    'tax_ids': [Command.clear()]})],
            })
            v.action_post()
            vouchers |= v
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.voucher', 'view_mode': 'list,form',
                'domain': [('id', 'in', vouchers.ids)], 'name': self.env._('Finance charge invoices'),
                'context': {'default_voucher_type': 'sale'}}

    @api.depends('date')
    def _compute_display_name(self):
        for w in self:
            w.display_name = self.env._('Finance Charges')

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Finance Charges')}


class SbFinanceChargeLine(models.TransientModel):
    _name = 'sb.finance.charge.line'
    _description = 'Finance charge per customer'

    wizard_id = fields.Many2one('sb.finance.charge', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Customer')
    overdue = fields.Monetary('Overdue amount')
    days = fields.Integer('Oldest (days)')
    charge = fields.Monetary('Charge')
    apply = fields.Boolean(default=True)
