"""Products on the website: which are shown, their web address, description, pictures and category."""
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    site_published = fields.Boolean('On the website', copy=False)
    site_featured = fields.Boolean('Featured')
    site_slug = fields.Char('Web address', copy=False)
    site_description = fields.Html('Website description', sanitize=True, translate=True)
    site_category_ids = fields.Many2many('unio.site.category', string='Shop categories')
    site_image_ids = fields.One2many('unio.site.image', 'product_tmpl_id', string='More pictures')
    site_sequence = fields.Integer(default=10)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.filtered(lambda r: not r.site_slug)._set_slug()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals or vals.get('site_published'):
            self.filtered(lambda r: not r.site_slug)._set_slug()
        return res

    def _set_slug(self):
        Page = self.env['unio.site.page']
        for p in self:
            base = Page._clean_slug(p.name)
            p.site_slug = '%s-%d' % (base, p.id)

    def site_toggle(self):
        for p in self:
            p.site_published = not p.site_published
        return True

    @api.model
    def site_products(self, query='', limit=80, offset=0):
        """For the Website app: the products that can be sold, with their website status."""
        dom = [('sale_ok', '=', True), ('default_code', 'not in', ['DELIVERY'])]
        delivery = self.env.ref('sonador_site.product_delivery', raise_if_not_found=False)
        if delivery:
            dom.append(('id', '!=', delivery.product_tmpl_id.id))
        if query:
            dom += ['|', ('name', 'ilike', query), ('default_code', 'ilike', query)]
        recs = self.search(dom, limit=limit, offset=offset, order='site_published desc, name')
        return {'total': self.search_count(dom), 'products': [{
            'id': p.id, 'name': p.name, 'price': p.list_price, 'published': p.site_published, 'featured': p.site_featured,
            'has_image': bool(p.image_128), 'stock': p.qty_available if p.is_storable else None,
            'categories': p.site_category_ids.ids, 'code': p.default_code or ''} for p in recs]}

    @api.model
    def site_set(self, ids, vals):
        ok = {k: v for k, v in vals.items() if k in ('site_published', 'site_featured')}
        if 'categories' in vals:
            ok['site_category_ids'] = [(6, 0, vals['categories'])]
        self.browse(ids).write(ok)
        return True


class UnioSiteImage(models.Model):
    _name = 'unio.site.image'
    _description = 'Product picture'
    _order = 'sequence, id'

    product_tmpl_id = fields.Many2one('product.template', required=True, ondelete='cascade', index=True)
    image = fields.Image(max_width=1600, max_height=1600, required=True)
    sequence = fields.Integer(default=10)
