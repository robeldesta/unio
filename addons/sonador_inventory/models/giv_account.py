"""Goods issued (GIV): the account charged follows the items issued. Raw materials go to their own consumption account
(the item category's expense account: 5000-0001 Cost of Sales - Direct Raw Material), stickers to theirs, and so on,
instead of one company-wide account for every issue. Only expense, cost and work-in-process accounts can be charged.
An item class with "Issues go to" set (work in process, for materials issued to production) charges that instead."""
from collections import Counter

from odoo import api, fields, models

GIV_ACCOUNT_DOMAIN = ['|', ('account_type', 'in', ('expense', 'expense_direct_cost', 'expense_depreciation')),
                      ('name', 'ilike', 'work in pro')]


class ProductCategory(models.Model):
    _inherit = 'product.category'

    sb_issue_account_id = fields.Many2one(
        'account.account', 'Issues go to', company_dependent=True, domain=GIV_ACCOUNT_DOMAIN,
        help='Goods issued (GIV) of items in this class charge this account instead of the expense account: work in '
             'process for materials issued to production, so they reach the finished goods, not cost of sales.')


class SbStockVoucherGivAccount(models.Model):
    _inherit = 'sb.stock.voucher'

    def _sb_giv_category_accounts(self):
        """{line: the account its item's category charges on issue}, for lines with an item."""
        out = {}
        for line in self.line_ids.filtered('product_id'):
            categ = line.product_id.categ_id.with_company(self.company_id or self.env.company)
            account = categ.property_account_expense_categ_id
            if categ.sb_issue_account_id:
                out[line] = categ.sb_issue_account_id
            elif account and account.account_type in ('expense', 'expense_direct_cost', 'expense_depreciation'):
                out[line] = account
        return out

    @api.onchange('line_ids')
    def _onchange_giv_category_account(self):
        """While the account is the one set automatically (the company's issue account, or a category's), it follows the
        items: the category most lines belong to sets the voucher's account, other items' lines carry their own."""
        if self.voucher_type != 'giv' or self.state not in ('draft', False):
            return
        by_line = self._sb_giv_category_accounts()
        if not by_line:
            return
        auto = {self.company_id.sb_giv_account_id, *by_line.values()}
        if self.account_id and self.account_id not in auto:
            return  # chosen by the user: kept
        main = Counter(by_line.values()).most_common(1)[0][0]
        self.account_id = main
        for line, account in by_line.items():
            if not line.account_id or line.account_id in auto:
                line.account_id = account if account != main else False

    @api.model
    def default_get(self, fields_list):
        """A goods received note is received by the storekeeper entering it, unless changed."""
        res = super().default_get(fields_list)
        vtype = res.get('voucher_type') or self.env.context.get('default_voucher_type')
        if 'received_by' in fields_list and vtype == 'grn' and not res.get('received_by'):
            res['received_by'] = self.env.user.name
        return res
