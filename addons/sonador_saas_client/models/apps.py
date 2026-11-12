"""Which apps a customer company sees. Every app is in the price; a company starts with the ones its kind of business
uses, and switches the others on or off itself on the Apps screen. An app not yet added to the company is added (its
module installed) the first time it is switched on."""
from odoo import api, models
from odoo.exceptions import AccessError

# key: (name, root menu, what it is for)
CATALOG = [
    ('accounting', 'Accounting', 'sonador_books.menu_sb_root', 'Invoices, receipts and payments, banks, VAT and withholding, financial reports'),
    ('sales', 'Sales', 'sonador_sales.menu_sales_root', 'Quotations, sales orders, deliveries and customer credit'),
    ('crm', 'CRM', 'sonador_crm.menu_crm_root', 'Leads, opportunities and the sales pipeline'),
    ('purchase', 'Purchase', 'sonador_purchase.menu_purchase_root', 'Purchase requests and orders, suppliers, imports and landed cost'),
    ('inventory', 'Inventory', 'sonador_inventory.menu_store_root', 'Stores and stock, receipts and issues, transfers, counts and costing'),
    ('production', 'Production', 'sonador_factory.menu_root', 'Recipes, production orders, materials and product costing'),
    ('projects', 'Projects', 'sonador_project.menu_root', 'Projects and tasks, timesheets and job costing'),
    ('planning', 'Planning', 'sonador_planning.menu_planning_root', 'Shifts, rosters and who works when'),
    ('people', 'People', 'sonador_books.menu_hr_root', 'Employees, leave, attendance, payroll and performance'),
    ('fleet', 'Fleet', 'sonador_fleet.menu_root', 'Vehicles, drivers, fuel, trips and maintenance'),
    ('website', 'Website', 'sonador_site.menu_site_root', 'Your website and online shop: pages, products, web orders'),
    ('school', 'School', 'sonador_school.menu_school_root', 'Students and admissions, classes, attendance, marks and report cards, fees'),
    ('farm', 'Farm', 'sonador_farm.menu_root', 'Poultry flocks: daily feed, eggs and deaths, health programme, cost per egg and per bird'),
    ('agenda', 'Agenda', 'sonador_agenda.menu_root', 'What is due and when: tax deadlines, bills to pay, customers to collect, payroll'),
    ('controller', 'Controller', 'sonador_controller.menu_root', 'Checks the books every night and lists what looks wrong'),
    ('audit', 'Audit Room', 'sonador_audit.menu_root', 'For internal auditors: visits, cash counts, findings and answers, visit reports'),
    ('studio', 'Studio', 'sonador_studio.menu_studio_root', 'Build your own apps and change screens, without code'),
]
INDUSTRY = {
    'services': ['accounting', 'sales', 'crm', 'purchase', 'projects', 'planning', 'people', 'website', 'studio'],
    'trading': ['accounting', 'sales', 'crm', 'purchase', 'inventory', 'people', 'fleet', 'website', 'studio'],
    'retail': ['accounting', 'sales', 'crm', 'purchase', 'inventory', 'people', 'website', 'studio'],
    'manufacturing': ['accounting', 'sales', 'crm', 'purchase', 'inventory', 'production', 'planning', 'people', 'fleet', 'website', 'studio'],
    'school': ['accounting', 'purchase', 'inventory', 'people', 'fleet', 'website', 'studio', 'school'],
    'farm': ['accounting', 'sales', 'purchase', 'inventory', 'farm', 'people', 'fleet', 'website', 'studio'],
    'construction': ['accounting', 'sales', 'crm', 'purchase', 'inventory', 'projects', 'planning', 'people', 'fleet', 'website', 'studio'],
}


class SaasApps(models.AbstractModel):
    _name = 'saas.apps'
    _description = 'Apps shown in this company'

    def _menu(self, xmlid):
        return self.env.ref(xmlid, raise_if_not_found=False)

    @api.model
    def apply_industry(self, industry):
        """At sign-up: show the apps of this kind of business, hide the rest (all stay installed)."""
        keep = set(INDUSTRY.get(industry) or [k for k, *_r in CATALOG if k not in ('school', 'farm')])
        for key, _name, xmlid, _what in CATALOG:
            menu = self._menu(xmlid)
            if menu:
                menu.sudo().active = key in keep
        # the Odoo module list is not for customers: this company's apps are switched on and off on the Apps screen
        odoo_apps = self._menu('base.menu_management')
        if odoo_apps:
            odoo_apps.sudo().active = False
        self.env['ir.config_parameter'].sudo().set_param('saas.industry', industry)
        return True

    def _menu_of(self, xmlid):
        res = self.env['ir.model.data']._xmlid_to_res_model_res_id(xmlid, raise_if_not_found=False) or (None, False)
        return self.env['ir.ui.menu'].sudo().with_context(active_test=False).browse(res[1]).exists()

    @api.model
    def list_apps(self):
        industry = INDUSTRY.get(self.env['ir.config_parameter'].sudo().get_param('saas.industry')) or []
        mods = {m.name: m.state for m in self.env['ir.module.module'].sudo().search(
            [('name', 'in', [x.split('.')[0] for _k, _n, x, _w in CATALOG])])}
        out = []
        for key, name, xmlid, what in CATALOG:
            module = xmlid.split('.')[0]
            menu = self._menu_of(xmlid)
            if menu:
                icon = menu.web_icon_data.decode() if isinstance(menu.web_icon_data, bytes) else menu.web_icon_data or ''
                out.append({'key': key, 'name': name, 'what': what, 'on': menu.active, 'added': True, 'icon': icon,
                            'icon_url': '' if icon else '/%s/static/description/icon.png' % module, 'industry': key in industry})
            elif module in mods:  # available, not added to this company yet: switching it on adds it
                out.append({'key': key, 'name': name, 'what': what, 'on': False, 'added': False, 'icon': '',
                            'icon_url': '/%s/static/description/icon.png' % module, 'industry': key in industry})
        return out

    @api.model
    def set_app(self, key, on):
        if not self.env.user.has_group('base.group_erp_manager'):
            raise AccessError(self.env._('Only administrators choose the apps of the company.'))
        xmlid = next((x for k, _n, x, _w in CATALOG if k == key), None)
        if not xmlid:
            return self.list_apps()
        menu = self._menu_of(xmlid)
        if menu:
            menu.active = bool(on)
            return self.list_apps()
        if on:
            module = self.env['ir.module.module'].sudo().search([('name', '=', xmlid.split('.')[0])], limit=1)
            if module and module.state != 'installed':
                module.button_immediate_install()
                return {'reload': True}
        return self.list_apps()


class SaasReferral(models.AbstractModel):
    _name = 'saas.referral'
    _description = 'Refer a friend'

    @api.model
    def info(self):
        """The company's referral link and results (kept up to date by the cloud service)."""
        import json
        raw = self.env['ir.config_parameter'].sudo().get_param('saas.referral')
        return json.loads(raw) if raw else {}
