"""Odoo shows each standard app's own icon wherever it names a module (activities menu, Settings sidebar, message
icons): those apps are shown with the icon of the app that replaces them here."""
from odoo.modules import module as odoo_module

BOOKS = '/sonador_books/static/description/icon.png'
PEOPLE = '/sonador_books/static/description/apps/employees.png'
SETTINGS = '/sonador_unio_brand/static/img/apps/settings.png'
DISCUSS = '/sonador_unio_brand/static/img/apps/discuss.png'
APPS = '/sonador_saas_client/static/description/apps.png'
ICONS = {
    'base': SETTINGS, 'base_setup': SETTINGS, 'general_settings': SETTINGS, 'web': SETTINGS,
    'mail': DISCUSS, 'mail_bot': DISCUSS, 'discuss': DISCUSS,
    'sale': '/sonador_sales/static/description/icon.png', 'sale_management': '/sonador_sales/static/description/icon.png',
    'sales_team': '/sonador_sales/static/description/icon.png',
    'crm': '/sonador_crm/static/description/icon.png',
    'purchase': '/sonador_purchase/static/description/icon.png',
    'stock': '/sonador_inventory/static/description/icon.png', 'product': '/sonador_inventory/static/description/icon.png',
    'mrp': '/sonador_factory/static/description/icon.png',
    'project': '/sonador_project/static/description/icon.png', 'hr_timesheet': '/sonador_project/static/description/icon.png',
    'fleet': '/sonador_fleet/static/description/icon.png',
    'website': '/sonador_site/static/description/icon.png',
    'calendar': '/sonador_planning/static/description/icon.png', 'resource': '/sonador_planning/static/description/icon.png',
    'spreadsheet_dashboard': '/sonador_saas/static/description/icon.png',
}
OURS = ('sonador_', 'muk_')


def unio_icon(module):
    module = module or ''
    if module.startswith(OURS):
        return None
    if module in ICONS:
        return ICONS[module]
    if module.startswith(('account', 'om_account', 'analytic', 'l10n_')):
        return BOOKS
    if module.startswith('hr'):
        return PEOPLE
    if module.startswith(('sale', 'crm')):
        return ICONS['sale']
    if module.startswith(('stock', 'product')):
        return ICONS['stock']
    if module.startswith(('purchase',)):
        return ICONS['purchase']
    if module.startswith(('mrp', 'quality', 'maintenance')):
        return ICONS['mrp']
    if module.startswith(('mail', 'im_', 'discuss')):
        return DISCUSS
    return APPS


_get_module_icon = odoo_module.get_module_icon


def get_module_icon(module):
    return unio_icon(module) or _get_module_icon(module)


odoo_module.get_module_icon = get_module_icon
