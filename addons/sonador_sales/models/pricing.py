"""Price levels (Peachtree) with Eco Green-style rules: delivery channel, road distance bands, quantity tiers
(equivalent items count together), validity dates. The most specific matching rule sets the price."""
from odoo import api, fields, models

CHANNELS = [('company', 'Company delivery'), ('pickup', 'Customer pick-up'), ('rented', 'Rented vehicle'), ('van', 'Van sales')]


class SbPriceLevel(models.Model):
    _name = 'sb.price.level'
    _description = 'Price Level'
    _order = 'sequence, id'

    name = fields.Char(required=True, help='e.g. Standard, Agent, Loyal customer, Wholesale')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    rule_ids = fields.One2many('sb.price.rule', 'level_id', string='Prices')


class SbPriceGroup(models.Model):
    _name = 'sb.price.group'
    _description = 'Equivalent items for pricing'

    name = fields.Char(required=True, help='e.g. 5 L finished products: Aquapure and AVON are priced together')
    product_ids = fields.Many2many('product.product', string='Items', help='Quantities of these items on one order count together for tiers.')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)


class SbPriceRule(models.Model):
    _name = 'sb.price.rule'
    _description = 'Price Rule'
    _order = 'sequence, id'

    level_id = fields.Many2one('sb.price.level', 'Price level', ondelete='cascade',
                               help='Empty: applies to every level unless a level-specific rule matches.')
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    product_id = fields.Many2one('product.product', 'Item')
    group_id = fields.Many2one('sb.price.group', 'Equivalent items')
    categ_id = fields.Many2one('product.category', 'Item class')
    channel = fields.Selection(CHANNELS, string='Delivery', help='Empty: any delivery channel.')
    distance_min = fields.Float('From km')
    distance_max = fields.Float('To km', help='0: no upper limit.')
    qty_min = fields.Float('From qty', digits='Product Unit')
    qty_max = fields.Float('To qty', digits='Product Unit', help='0: no upper limit.')
    date_from = fields.Date('Valid from')
    date_to = fields.Date('Valid to')
    price = fields.Float('Unit price', digits='Product Price', required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    def _score(self, product):
        self.ensure_one()
        s = 300 if self.product_id == product else (200 if self.group_id and product in self.group_id.product_ids else 100)
        s += 20 if self.level_id else 0
        s += 10 if self.channel else 0
        s += 5 if (self.distance_min or self.distance_max) else 0
        s += 2 if (self.qty_min or self.qty_max) else 0
        s += 1 if (self.date_from or self.date_to) else 0
        return s

    @api.model
    def _find(self, product, qty, level=None, channel=None, distance=0.0, day=None, company=None):
        company = company or self.env.company
        day = day or fields.Date.context_today(self)
        groups = self.env['sb.price.group'].search([('product_ids', 'in', product.id)])
        dom = [('company_id', 'in', (company.id, False)),
               '|', '|', ('product_id', '=', product.id), ('group_id', 'in', groups.ids),
               '&', ('product_id', '=', False), '&', ('group_id', '=', False), ('categ_id', 'parent_of', product.categ_id.id)]
        best, best_score = self.browse(), -1
        for r in self.search(dom):
            if r.level_id and r.level_id != level:
                continue
            if r.channel and r.channel != channel:
                continue
            if (r.distance_min and distance < r.distance_min) or (r.distance_max and distance > r.distance_max):
                continue
            if (r.qty_min and qty < r.qty_min) or (r.qty_max and qty > r.qty_max):
                continue
            if (r.date_from and day < r.date_from) or (r.date_to and day > r.date_to):
                continue
            sc = r._score(product)
            if sc > best_score or (sc == best_score and r.sequence < best.sequence):
                best, best_score = r, sc
        return best


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sb_price_level_id = fields.Many2one('sb.price.level', 'Price level', company_dependent=True)
    sb_distance_km = fields.Float('Road distance (km)', help='From the factory / main warehouse, for distance-based prices.')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sb_price_level_id = fields.Many2one('sb.price.level', 'Price level', compute='_compute_sb_pricing_defaults', store=True, readonly=False)
    sb_channel = fields.Selection(CHANNELS, 'Delivery', default='company')
    sb_distance_km = fields.Float('Road distance (km)', compute='_compute_sb_pricing_defaults', store=True, readonly=False)

    @api.depends('partner_id')
    def _compute_sb_pricing_defaults(self):
        for so in self:
            p = so.partner_id.commercial_partner_id
            so.sb_price_level_id = p.sb_price_level_id or so.sb_price_level_id
            so.sb_distance_km = p.sb_distance_km or so.sb_distance_km

    def action_sb_update_prices(self):
        self.order_line.with_context(force_price_recomputation=True)._compute_price_unit()
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sb_price_rule_id = fields.Many2one('sb.price.rule', 'Price rule', copy=False, readonly=True)

    @api.depends('product_id', 'product_uom_id', 'product_uom_qty', 'order_id.sb_price_level_id', 'order_id.sb_channel',
                 'order_id.sb_distance_km')
    def _compute_price_unit(self):
        return super()._compute_price_unit()

    def _sb_tier_qty(self):
        groups = self.env['sb.price.group'].search([('product_ids', 'in', self.product_id.id)])
        members = groups.product_ids | self.product_id
        return sum(l.product_uom_qty for l in self.order_id.order_line if l.product_id in members)

    def _reset_price_unit(self):
        res = super()._reset_price_unit()
        for line in self:
            if not line.product_id:
                continue
            so = line.order_id
            rule = self.env['sb.price.rule']._find(line.product_id, line._sb_tier_qty(), so.sb_price_level_id, so.sb_channel,
                                                   so.sb_distance_km, (so.date_order or fields.Datetime.now()).date(), so.company_id)
            line.sb_price_rule_id = rule
            if rule:
                line.price_unit = line.technical_price_unit = rule.price
        return res
