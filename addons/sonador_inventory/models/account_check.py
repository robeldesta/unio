from odoo import api, models
from odoo.exceptions import UserError


class StockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    def action_post(self):
        """An archived account (the GRNI account archived by a clean-up ...) stops posting with a message naming the
        setting to fix, not the system's own 'account is archived'."""
        labels = {'grn': 'Goods received not invoiced', 'giv': 'Default account for goods issued',
                  'adjustment': 'Stock count differences'}
        for v in self.filtered(lambda r: r.state == 'draft'):
            accounts = (v.account_id | v.line_ids.mapped('account_id')).sudo().with_context(active_test=False)
            for account in accounts.filtered(lambda a: not a.active):
                if account == v.account_id and v.voucher_type in labels:
                    raise UserError(self.env._(
                        'The account %(acc)s is archived, so this voucher cannot be posted. An accountant can restore it '
                        '(Setup > Chart of Accounts, archived) or choose another account in Setup > Settings > Inventory, '
                        '"%(setting)s", then choose it on this voucher.',
                        acc=account.display_name, setting=self.env._(labels[v.voucher_type])))
                raise UserError(self.env._(
                    'The account %(acc)s is archived, so this voucher cannot be posted. Choose another account on the '
                    'voucher, or ask an accountant to restore it (Setup > Chart of Accounts, archived).',
                    acc=account.display_name))
        return super().action_post()


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _sb_fix_giv_default(self):
        """The default "Charge to" of a goods issue (GIV): the account the company's own issues are charged to most,
        when the setting still points to the standard chart's 621800 that it never posted to. Run at every update."""
        for company in self.sudo().search([]):
            current = company.sb_giv_account_id
            if current and self.env['account.move.line'].sudo().search_count(
                    [('account_id', '=', current.id), ('company_id', '=', company.id)], limit=1):
                continue
            self.env.cr.execute("""
                SELECT account_id FROM sb_stock_voucher
                 WHERE company_id = %s AND voucher_type = 'giv' AND state = 'posted' AND account_id IS NOT NULL
                 GROUP BY account_id ORDER BY count(*) DESC LIMIT 1""", (company.id,))
            row = self.env.cr.fetchone()
            if row and row[0] != current.id:
                company.sb_giv_account_id = row[0]
        return True
