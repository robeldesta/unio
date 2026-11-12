"""Credit limits: per customer (0 = no limit); the company chooses Block (default), Warn or Off.
Checked when a sales order is confirmed and when a sales invoice (on account) is posted."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.client_guard import refuse_client_write


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_credit_check = fields.Selection([('block', 'Block'), ('warn', 'Warn'), ('off', 'Off')], 'Credit limit check',
                                       default='block', required=True)
    sb_overdue_hold_days = fields.Integer('Hold customers overdue by more than (days)', default=0,
                                          help='0 = off. A customer with an invoice unpaid this many days past its due date gets no new order or '
                                               'invoice on account until it is paid or a manager releases the hold.')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sb_hold_released_until = fields.Date('Credit hold released until', help='A manager let this customer buy despite overdue invoices, until this date.')
    sb_hold_note = fields.Char('Why the hold was released')


def _exposure(env, partner, exclude_order=None):
    """What the customer owes now plus confirmed orders not yet invoiced."""
    p = partner.commercial_partner_id
    owed = sum(env['account.move.line'].search([
        ('partner_id', 'child_of', p.id), ('account_id.account_type', '=', 'asset_receivable'),
        ('parent_state', '=', 'posted'), ('reconciled', '=', False)]).mapped('amount_residual'))
    orders = env['sale.order'].search([('partner_id', 'child_of', p.id), ('state', '=', 'sale')])
    if exclude_order:
        orders -= exclude_order
    pending = 0.0
    for so in orders:
        for l in so.order_line.filtered(lambda x: x.product_id and not x.display_type and x.product_uom_qty):
            pending += l.price_total * l.sb_qty_to_invoice / l.product_uom_qty
    return owed, pending


def _overdue(env, partner, company, days):
    from datetime import timedelta
    today = fields.Date.context_today(partner)
    lines = env['account.move.line'].search([
        ('partner_id', 'child_of', partner.commercial_partner_id.id), ('account_id.account_type', '=', 'asset_receivable'),
        ('parent_state', '=', 'posted'), ('reconciled', '=', False), ('amount_residual', '>', 0),
        ('date_maturity', '<', today - timedelta(days=days)), ('company_id', '=', company.id)])
    return lines


def _check(env, record, partner, amount, what):
    company = record.company_id
    p = partner.commercial_partner_id
    days = company.sb_overdue_hold_days
    if days and company.sb_credit_check != 'off' and not (p.sb_hold_released_until and p.sb_hold_released_until >= fields.Date.context_today(record)):
        late = _overdue(env, partner, company, days)
        if late:
            cur = company.currency_id
            oldest = min(late.mapped('date_maturity'))
            msg = env._('%(who)s is on credit hold: %(amt)s overdue by more than %(d)s days (oldest due %(old)s, %(doc)s).',
                        who=p.name, amt=cur.format(sum(late.mapped('amount_residual'))), d=days, old=oldest,
                        doc=late.sorted('date_maturity')[0].move_id.name)
            if company.sb_credit_check == 'block':
                raise UserError(msg + '\n' + env._('Collect the overdue invoices, or a sales manager releases the hold on the customer.'))
            record.message_post(body=msg)
    limit = partner.commercial_partner_id.with_company(company).credit_limit
    if company.sb_credit_check == 'off' or not limit:
        return
    owed, pending = _exposure(env, partner, exclude_order=record if record._name == 'sale.order' else None)
    total = owed + pending + amount
    if total <= limit + 0.005:
        return
    cur = company.currency_id
    msg = env._('%(who)s is over the credit limit: owes %(owed)s, open orders %(pend)s, this %(what)s %(amt)s = %(tot)s against a limit of %(lim)s.',
                who=partner.commercial_partner_id.name, owed=cur.format(owed), pend=cur.format(pending), what=what,
                amt=cur.format(amount), tot=cur.format(total), lim=cur.format(limit))
    if company.sb_credit_check == 'block':
        raise UserError(msg + '\n' + env._('A manager can raise the limit on the customer, or switch the check to Warn in settings.'))
    record.message_post(body=msg)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        for so in self:
            _check(self.env, so, so.partner_id, so.amount_total, self.env._('order'))
        return super().action_confirm()


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def action_post(self):
        for v in self.filtered(lambda x: x.state == 'draft' and x.voucher_type == 'sale' and x.partner_id):
            amount = v.amount_total
            if v.sb_sale_order_id:  # the order's own open amount turns into this invoice: don't count it twice
                so = v.sb_sale_order_id
                for l in so.order_line.filtered(lambda x: x.product_id and not x.display_type and x.product_uom_qty):
                    amount -= l.price_total * min(sum(v.line_ids.filtered(lambda vl: vl.sb_sale_line_id == l).mapped('quantity')),
                                                  l.sb_qty_to_invoice) / l.product_uom_qty
            _check(self.env, v, v.partner_id, max(amount, 0.0), self.env._('invoice'))
        return super().action_post()


class SaleOrderFlowFields(models.Model):
    _inherit = 'sale.order'

    # confirmed, locked and cancelled by the buttons (which check credit, approval and stock), never by editing the order
    _SB_FLOW_FIELDS = ('state',)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            refuse_client_write(self.browse(), vals, self._SB_FLOW_FIELDS, self.env._('Sales order'))
        return super().create(vals_list)

    def write(self, vals):
        refuse_client_write(self, vals, self._SB_FLOW_FIELDS, self.env._('Sales order'))
        return super().write(vals)
