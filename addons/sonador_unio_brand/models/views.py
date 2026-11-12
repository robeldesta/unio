import re

from odoo import api, models

from .icons import unio_icon

ODOO = re.compile(r'\bOdoo\b(?![.\w/])')
APP_NAMES = {'account': 'Accounting', 'hr': 'People', 'mrp': 'Production', 'sale_management': 'Sales', 'stock': 'Inventory',
             'general_settings': 'General Settings'}
TEXT_ATTRS = ('string', 'help', 'title', 'placeholder', 'confirm', 'aria-label')


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """Screens say Unio where Odoo named itself (labels, help texts, placeholders); settings that only exist in
        Odoo's paid edition are not shown."""
        arch, view = super()._get_view(view_id, view_type, **options)
        for node in arch.iter():
            if not isinstance(node.tag, str):
                continue
            for attr in TEXT_ATTRS:
                value = node.get(attr)
                if value and 'Odoo' in value:
                    node.set(attr, ODOO.sub('Unio', value))
            if node.text and 'Odoo' in node.text:
                node.text = ODOO.sub('Unio', node.text)
            if node.tail and 'Odoo' in node.tail:
                node.tail = ODOO.sub('Unio', node.tail)
        if self._name == 'res.config.settings':
            # the sidebar: each standard app under the name and icon of the app that replaces it
            for app in arch.iter('app'):
                name = app.get('name') or ''
                icon = unio_icon(name)
                if icon:
                    app.set('logo', icon)
                if name in APP_NAMES:
                    app.set('string', APP_NAMES[name])
            for field in arch.xpath("//field[@widget='upgrade_boolean']"):
                box = next((a for a in field.iterancestors() if a.tag == 'setting'), field)
                box.set('invisible', '1')
        return arch, view
