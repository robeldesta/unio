from odoo import Command, api, models

# Standard Odoo apps that Accounting replaces. Their menus stay in the database,
# visible only to members of group_sb_legacy_apps.
LEGACY_APP_MENUS = (
    'account.menu_finance',
    'stock.menu_stock_root',
    'spreadsheet_dashboard.spreadsheet_dashboard_menu_root',
    'mrp.menu_mrp_root',
)
# Other standard apps our own apps replace (Sales, Purchase, People...), and Odoo's developer screens
STOCK_APP_MENUS = (
    'sale.sale_menu_root',
    'purchase.menu_purchase_root',
    'hr.menu_hr_root',
    'utm.menu_link_tracker_root',
    'base.menu_tests',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _sb_hide_legacy_apps(self):
        group = self.env.ref('sonador_books.group_sb_legacy_apps')
        for xmlid in LEGACY_APP_MENUS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                menu.sudo().group_ids = [Command.set(group.ids)]
        # Apps and Tests are for administrators only (base leaves them open to every user)
        admin = self.env.ref('base.group_system')
        for xmlid in ('base.menu_management', 'base.menu_tests'):
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                menu.sudo().group_ids = [Command.set(admin.ids)]

    @api.model
    def _visible_menu_ids(self, debug=False):
        """Odoo's own apps stay out of the app list whatever order modules were installed in (an app installed later
        brings its menu back with its own groups): only members of group_sb_legacy_apps see them."""
        visible = super()._visible_menu_ids(debug)
        ref = self.env['ir.model.data']._xmlid_to_res_id
        groups = self.env.user._get_group_ids()
        if ref('base.group_system', raise_if_not_found=False) not in groups:
            # Odoo's own invoices, bills, credit / debit notes and payments are entered as vouchers (with their controls):
            # their menus are for administrators only
            visible = visible - self._sb_native_document_menus()
        legacy = ref('sonador_books.group_sb_legacy_apps', raise_if_not_found=False)
        if legacy and legacy in groups:
            return visible
        hidden = {ref(x, raise_if_not_found=False) for x in LEGACY_APP_MENUS + STOCK_APP_MENUS}
        # Odoo's module list, where the company's own Apps screen exists
        if ref('sonador_saas_client.menu_apps', raise_if_not_found=False):
            hidden.add(ref('base.menu_management', raise_if_not_found=False))
        # and everything under a hidden app (reachable otherwise by its address)
        roots = self.sudo().browse([h for h in hidden if h]).exists()
        if roots:
            hidden |= set(self.sudo().search([('id', 'child_of', roots.ids)]).ids)
        return visible - hidden

    @api.model
    def _sb_native_document_menus(self):
        """Menus opening Odoo's own invoice / bill / refund lists or payments."""
        menus = self.sudo().search([('action', '!=', False)])
        found = set()
        for m in menus:
            action = m.action
            if action._name != 'ir.actions.act_window':
                continue
            ctx = action.context or ''
            if action.res_model == 'account.payment' or (action.res_model == 'account.move' and any(
                    t in ctx for t in ("'out_invoice'", "'out_refund'", "'in_invoice'", "'in_refund'", "'out_receipt'", "'in_receipt'"))):
                found.add(m.id)
        return found
