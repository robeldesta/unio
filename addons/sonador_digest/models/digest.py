"""The digest email in Unio's words and figures. Odoo's own figures (users connected, messages sent, invoices of the
Odoo accounting app, CRM) say little to a business on Unio: sales, money in and out, approvals and customers billed come
from Unio's vouchers and approval log instead. No tips and no app adverts."""
from odoo import api, fields, models

SALES = ('sale', 'cash_sale')


class DigestDigest(models.Model):
    _inherit = 'digest.digest'

    kpi_sb_sales = fields.Boolean('Sales')
    kpi_sb_sales_value = fields.Monetary(compute='_compute_kpi_sb_sales_value')
    kpi_sb_money_in = fields.Boolean('Money in')
    kpi_sb_money_in_value = fields.Monetary(compute='_compute_kpi_sb_money_in_value')
    kpi_sb_money_out = fields.Boolean('Money out')
    kpi_sb_money_out_value = fields.Monetary(compute='_compute_kpi_sb_money_out_value')
    kpi_sb_decisions = fields.Boolean('Approvals decided')
    kpi_sb_decisions_value = fields.Integer(compute='_compute_kpi_sb_decisions_value')
    kpi_sb_customers = fields.Boolean('Customers billed')
    kpi_sb_customers_value = fields.Integer(compute='_compute_kpi_sb_customers_value')

    # ------------------------------------------------------------------ figures
    def _sb_vouchers(self, types):
        start, end, companies = self._get_kpi_compute_parameters()
        start, end = fields.Datetime.to_datetime(start).date(), fields.Datetime.to_datetime(end).date()
        return self.env['sb.voucher'].sudo().search([('voucher_type', 'in', types), ('state', '=', 'posted'),
                                                    ('date', '>=', start), ('date', '<', end),
                                                    ('company_id', 'in', companies.ids)])

    def _compute_kpi_sb_sales_value(self):
        for d in self:
            sold = sum(d._sb_vouchers(SALES).mapped('amount_untaxed'))
            back = sum(d._sb_vouchers(('sale_return',)).mapped('amount_untaxed'))
            d.kpi_sb_sales_value = sold - back

    def _compute_kpi_sb_money_in_value(self):
        for d in self:
            d.kpi_sb_money_in_value = sum(d._sb_vouchers(('receipt', 'cash_sale')).mapped('amount_cash'))

    def _compute_kpi_sb_money_out_value(self):
        for d in self:
            d.kpi_sb_money_out_value = sum(d._sb_vouchers(('payment', 'cash_purchase')).mapped('amount_cash'))

    def _compute_kpi_sb_decisions_value(self):
        start, end, companies = self._get_kpi_compute_parameters()
        Log = self.env.get('sb.approval.log')  # approvals (Me) may not be installed
        n = Log.sudo().search_count([('create_date', '>=', start), ('create_date', '<', end)]) if Log is not None else 0
        for d in self:
            d.kpi_sb_decisions_value = n

    def _compute_kpi_sb_customers_value(self):
        # customers who bought (invoice or cash sale), not partners created: an import creates thousands
        for d in self:
            d.kpi_sb_customers_value = len(d._sb_vouchers(SALES).partner_id)

    def _compute_kpis_actions(self, company, user):
        res = super()._compute_kpis_actions(company, user)
        for kpi, xmlid in (('kpi_sb_sales', 'sonador_books.action_sb_sale'),
                           ('kpi_sb_money_in', 'sonador_books.action_sb_receipt'),
                           ('kpi_sb_money_out', 'sonador_books.action_sb_payment')):
            act = self.env.ref(xmlid, raise_if_not_found=False)
            if act:
                res[kpi] = act.id
        return res

    # ------------------------------------------------------------------ no tips, no adverts
    def _compute_tips(self, company, user, tips_count=1, consumed=True):
        return []

    # ------------------------------------------------------------------ defaults
    def _sb_unio_defaults(self):
        """Unio's figures on, Odoo's off; the name without "Odoo"."""
        vals = {f: False for f in ('kpi_res_users_connected', 'kpi_mail_message_total', 'kpi_account_total_revenue',
                                   'kpi_all_sale_total', 'kpi_crm_lead_created', 'kpi_crm_opportunities_won')
                if f in self._fields}
        vals.update(kpi_sb_sales=True, kpi_sb_money_in=True, kpi_sb_money_out=True,
                    kpi_sb_decisions='sb.approval.log' in self.env, kpi_sb_customers=True)
        for d in self:
            v = dict(vals)
            if 'odoo' in (d.name or '').lower():
                v['name'] = 'Your Unio digest'
            d.write(v)
