"""Salespeople see the customer side of the books, through the API too (Robel, 12 Nov 2026).

Sales Clerk carries Odoo's invoicing right so that salespeople post sales invoices, cash sales and returns. That right
alone opens every journal entry, journal item, payment and voucher of the company (Odoo's rule "all invoices" for
invoicing users). For a Sales Clerk who holds no other role touching the books (accounting, store, purchase, petty cash),
the global rules in security/books_side.xml keep it to: customer invoices and credit notes, the sales journals, entries on
customer accounts (receipts, cash sales applied), sale-side vouchers and receipts, customer payments, and whatever the
salesperson created themselves (the stock entries of their own invoices and deliveries). Bank reconciliations: none."""
from odoo import api, fields, models

OTHER_BOOK_ROLES = ('account.group_account_readonly', 'sonador_inventory.group_sb_store_user',
                    'sonador_purchase.group_sb_purchase_user', 'sonador_books.group_sb_petty_cash')


class ResUsers(models.Model):
    _inherit = 'res.users'

    sb_books_customer_only = fields.Boolean(
        'Books: customer side only', compute='_compute_sb_books_customer_only',
        help='A Sales Clerk with no accounting, store, purchase or petty cash role: sees only the customer side of the books.')

    @api.depends('all_group_ids')
    def _compute_sb_books_customer_only(self):
        for u in self:
            u.sb_books_customer_only = not u._is_superuser() and u.has_group('sonador_sales.group_sb_sales_user') \
                and not any(self.env.ref(g, raise_if_not_found=False) and u.has_group(g) for g in OTHER_BOOK_ROLES)
