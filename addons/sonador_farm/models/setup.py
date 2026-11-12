"""Farm setup: the items the farm works with (egg grades, manure, feed, birds) and its accounts, made in one click with
the company's own account numbers when they are missing."""
from odoo import Command, api, fields, models

EGG_GRADES = [('Eggs - large', 'L'), ('Eggs - medium', 'M'), ('Eggs - small', 'S')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_farm_egg_product_ids = fields.Many2many('product.product', 'sb_farm_egg_product_rel', 'company_id', 'product_id',
                                               string='Egg grades', help='Each grade an item in the store (eggs counted one by one).')
    sb_farm_manure_product_id = fields.Many2one('product.product', 'Manure', help='Received from the flocks into the store.')
    sb_farm_broiler_product_id = fields.Many2one('product.product', 'Broilers (live, to sell)')


class SbFarmSetup(models.AbstractModel):
    _name = 'sb.farm.setup'
    _description = 'Farm setup'

    @api.model
    def _products(self, company):
        return {'eggs': company.sb_farm_egg_product_ids, 'manure': company.sb_farm_manure_product_id,
                'broiler': company.sb_farm_broiler_product_id}

    def _next_code(self, company, prefix, account_type):
        """The next free account number in a group (prefix 1800, 8200 ...), written as the chart writes them."""
        Account = self.env['account.account'].sudo().with_company(company)
        existing = Account.search([('company_ids', 'in', company.id)]).mapped('code')
        sample = next((c for c in existing if c.replace('-', '').replace('.', '').startswith(prefix)), '')
        sep = '-' if '-' in sample else ('.' if '.' in sample else '')
        width = len(sample.replace('-', '').replace('.', '')) or 8
        bare = {c.replace('-', '').replace('.', '') for c in existing}
        for n in range(1, 10 ** (width - len(prefix))):
            code = prefix + str(n).zfill(width - len(prefix))
            if code not in bare:
                return code[:4] + sep + code[4:] if width == 8 and sep else code
        return False

    def _account(self, company, field, name, account_type, prefix):
        acc = company[field]
        if acc:
            return acc
        Account = self.env['account.account'].sudo().with_company(company)
        acc = Account.search([('name', '=ilike', name), ('company_ids', 'in', company.id)], limit=1)
        if not acc:
            acc = Account.create({'code': self._next_code(company, prefix, account_type), 'name': name, 'account_type': account_type,
                                  'company_ids': [Command.set(company.ids)]})
        company.sudo()[field] = acc
        return acc

    def _product(self, company, name, uom_xmlid, category, ref=None, sale=True):
        P = self.env['product.product'].sudo()
        p = P.search([('name', '=ilike', name), ('company_id', 'in', (False, company.id))], limit=1)
        if p:
            return p
        uom = self.env.ref(uom_xmlid, raise_if_not_found=False) or self.env.ref('uom.product_uom_unit')
        return P.create({'name': name, 'type': 'consu', 'is_storable': True, 'uom_id': uom.id,
                         'categ_id': category.id, 'default_code': ref or False, 'sale_ok': sale, 'purchase_ok': not sale,
                         'company_id': False})

    def _category(self, name, company, expense=None):
        """An item category of the farm: stock valued at average cost, in real time (what a store issue to a flock is worth)."""
        C = self.env['product.category'].sudo()
        cat = C.search([('name', '=', name)], limit=1) or C.create({'name': name})
        cat = cat.with_company(company)
        if expense:
            cat.property_account_expense_categ_id = expense
        if cat.property_cost_method != 'average' and not self.env['product.product'].sudo().search_count([('categ_id', '=', cat.id)]):
            cat.property_cost_method = 'average'
        if cat.property_valuation != 'real_time':
            cat.property_valuation = 'real_time'
        if not cat.property_stock_valuation_account_id and company.account_stock_valuation_id:
            cat.property_stock_valuation_account_id = company.account_stock_valuation_id
        return cat

    @api.model
    def make_defaults(self):
        """What the farm needs and the company does not have yet: the accounts (birds kept as biological assets and their
        wearing off, feed and medicine used, chicks bought), the items (egg grades, manure, broilers) and their categories."""
        c = self.env.company
        bio = self._account(c, 'sb_farm_bio_account_id', 'Biological assets - layer flocks', 'asset_non_current', '1800')
        self._account(c, 'sb_farm_bio_dep_account_id', 'Layer flocks - worn off to date', 'asset_non_current', '1800')
        self._account(c, 'sb_farm_dep_account_id', 'Layer flock depreciation', 'expense_direct_cost', '5000')
        cost = self._account(c, 'sb_farm_cost_account_id', 'Feed, medicine and farm inputs used', 'expense_direct_cost', '5000')
        chicks = self._account(c, 'sb_farm_birds_account_id', 'Broiler chicks bought', 'expense_direct_cost', '5000')
        produce = self._category('Farm produce', c)
        self._category('Pullets (layer flocks)', c, expense=bio)
        self._category('Broiler chicks', c, expense=chicks)
        self._category('Feed', c, expense=cost)
        self._category('Veterinary medicine and vaccines', c, expense=cost)
        if not c.sb_farm_egg_product_ids:
            c.sudo().sb_farm_egg_product_ids = [Command.set([self._product(c, n, 'uom.product_uom_unit', produce, ref='EGG-%s' % g).id
                                                             for n, g in EGG_GRADES])]
        if not c.sb_farm_manure_product_id:
            c.sudo().sb_farm_manure_product_id = self._product(c, 'Chicken manure', 'uom.product_uom_kgm', produce, ref='MANURE')
        if not c.sb_farm_broiler_product_id:
            c.sudo().sb_farm_broiler_product_id = self._product(c, 'Broilers (live)', 'uom.product_uom_unit', produce, ref='BROILER')
        return True
