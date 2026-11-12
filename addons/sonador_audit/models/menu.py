"""An internal auditor sees two apps: Accounting (read only) and the Audit Room. Every other app is hidden from them,
whatever the apps' own menus allow everyone."""
from odoo import api, models, tools

KEEP = ('sonador_books.menu_sb_root', 'sonador_audit.menu_root')


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    @tools.ormcache('frozenset(self.env.user._get_group_ids())', 'debug')
    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug)
        user = self.env.user
        if not user.has_group('sonador_audit.group_auditor') or user.has_group('sonador_books.group_sb_clerk') \
                or user.has_group('base.group_system'):
            return visible
        roots = [self.env.ref(x, raise_if_not_found=False) for x in KEEP]
        allowed = set(self.sudo().search([('id', 'child_of', [r.id for r in roots if r])]).ids)
        return frozenset(i for i in visible if i in allowed)
