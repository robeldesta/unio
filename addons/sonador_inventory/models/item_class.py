"""Every item has an item class: the class decides how it is costed (AVCO / FIFO), which stock account holds it and
where an issue charges it. An item left without one was valued and issued with the company's catch-all accounts.
Bought-only stock items default to Raw Materials, others to Goods, services to Services; the person can change it."""
from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def _sb_default_class(self, vals):
        Cat = self.env['product.category']
        ptype = vals.get('type', 'consu')
        if ptype == 'service':
            return Cat.search([('name', '=', 'Services')], limit=1)
        sale = vals.get('sale_ok', True)
        buy = vals.get('purchase_ok', True)
        if buy and not sale:
            found = Cat.search([('name', 'ilike', 'Raw Materials')], limit=1)
            if found:
                return found
        return self.env.ref('product.product_category_goods', raise_if_not_found=False) or Cat.search([], limit=1)

    @api.onchange('type', 'sale_ok', 'purchase_ok')
    def _onchange_sb_item_class(self):
        """While the class is empty or still the one chosen automatically, it follows what the item is."""
        guess = self._sb_default_class({'type': self.type, 'sale_ok': self.sale_ok, 'purchase_ok': self.purchase_ok})
        if guess and (not self.categ_id or self.categ_id in self._sb_auto_classes()):
            self.categ_id = guess

    def _sb_auto_classes(self):
        Cat = self.env['product.category']
        return (Cat.search([('name', 'ilike', 'Raw Materials')], limit=1)
                | (self.env.ref('product.product_category_goods', raise_if_not_found=False) or Cat)
                | Cat.search([('name', '=', 'Services')], limit=1))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('categ_id'):
                found = self._sb_default_class(vals)
                if found:
                    vals['categ_id'] = found.id
        return super().create(vals_list)
