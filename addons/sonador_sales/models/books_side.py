"""Salespeople see the customer side of the books, through the API too (Robel, 12 Nov 2026).

Sales Clerk carries Odoo's invoicing right so that salespeople post sales invoices, cash sales and returns. That right
alone opens every journal entry, journal item, payment and voucher of the company (Odoo's rule "all invoices" for
invoicing users). For a Sales Clerk who holds no other role touching the books (accounting, store, purchase, petty cash),
the global rules in security/books_side.xml keep it to: customer invoices and credit notes, the sales journals, entries on
customer accounts (receipts, cash sales applied), sale-side vouchers and receipts, customer payments, and whatever the
salesperson created themselves (the stock entries of their own invoices and deliveries). Bank reconciliations: none.

Sales Clerk and the store, purchase and petty cash roles are exclusive (Robel, 12 Nov 2026): each of those roles opens the
books, so a person holding both would read everything again. The user form marks the clash and saving it is refused.
Left out of the check: system administrators (they hold every role) and anyone with an accounting role (the general
manager and finance sell, approve purchases and are meant to see the books)."""
import copy

from odoo import api, fields, models
from odoo.exceptions import ValidationError

OTHER_BOOK_ROLES = ('account.group_account_readonly', 'sonador_inventory.group_sb_store_user',
                    'sonador_purchase.group_sb_purchase_user', 'sonador_books.group_sb_petty_cash')


# roles a Sales Clerk may not also hold
EXCLUSIVE_WITH_SALES = ('sonador_inventory.group_sb_store_user', 'sonador_purchase.group_sb_purchase_user',
                        'sonador_books.group_sb_petty_cash')


def _exclusive_groups(env):
    """(Sales Clerk, the roles exclusive with it) as installed in this database."""
    sales = env.ref('sonador_sales.group_sb_sales_user', raise_if_not_found=False)
    others = env['res.groups'].sudo()
    for xmlid in EXCLUSIVE_WITH_SALES:
        others |= env.ref(xmlid, raise_if_not_found=False) or env['res.groups']
    return sales, others


class ResGroups(models.Model):
    _inherit = 'res.groups'

    @api.model
    def _get_view_group_hierarchy(self):
        """The user form's group picker: Sales Clerk and the store, purchase and petty cash roles shown as exclusive."""
        res = super()._get_view_group_hierarchy()
        sales, others = _exclusive_groups(self.env)
        if not sales or not others:
            return res
        res = copy.deepcopy(res)  # the parent's result is cached: never change it in place
        groups = res.get('groups', {})
        if sales.id in groups:
            groups[sales.id]['disjoint_ids'] = sorted(set(groups[sales.id]['disjoint_ids']) | set(others.ids))
        for g in others:
            if g.id in groups:
                groups[g.id]['disjoint_ids'] = sorted(set(groups[g.id]['disjoint_ids']) | {sales.id})
        return res

    @api.constrains('user_ids')
    def _check_user_disjoint_groups(self):
        super()._check_user_disjoint_groups()
        if not self.env.registry.ready:
            return  # installing or upgrading: people who already hold both are listed and settled by a script, not here
        sales, others = _exclusive_groups(self.env)
        if sales and others and self & (sales | others):
            (self & (sales | others)).sudo().user_ids._check_disjoint_groups()


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

    @api.constrains('group_ids')
    def _check_disjoint_groups(self):
        super()._check_disjoint_groups()
        sales, others = _exclusive_groups(self.env)
        if not sales or not others:
            return
        exempt = self.env['res.groups']
        for xmlid in ('base.group_system', 'account.group_account_readonly'):
            exempt |= self.env.ref(xmlid, raise_if_not_found=False) or self.env['res.groups']
        for user in self.sudo():
            if user._is_superuser() or sales not in user.all_group_ids or user.all_group_ids & exempt:
                continue
            clash = user.all_group_ids & others
            if clash:
                raise ValidationError(self.env._(
                    '%(user)s cannot be a Sales Clerk and also %(roles)s. Salespeople see only the customer side of the '
                    'books, and these roles open the rest. Choose one of them for this person.',
                    user=user.name, roles=', '.join(clash.mapped('name'))))
