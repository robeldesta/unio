from odoo import api, fields, models

ITEM_KINDS = [
    ('stock', 'Stock item'),
    ('non_stock', 'Non-stock item'),
    ('service', 'Service'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    sb_item_kind = fields.Selection(ITEM_KINDS, string='Item type', compute='_compute_sb_item_kind',
                                    inverse='_inverse_sb_item_kind', store=True,
                                    help='Stock item: quantity and cost tracked (FIFO / average).\n'
                                         'Non-stock item: bought or sold, not counted.\nService: labour, fees, subscriptions.')
    sb_reorder_point = fields.Float('Reorder point', digits='Product Unit',
                                    help='Order more when the quantity on hand falls to this level.')
    sb_reorder_qty = fields.Float('Reorder quantity', digits='Product Unit')
    sb_vendor_id = fields.Many2one('res.partner', 'Preferred vendor')
    sb_bin = fields.Char('Bin / shelf')
    sb_legacy_code = fields.Char('Previous item ID', index=True, help='The item number in the previous system (e.g. Peachtree), kept for reference and imports.')
    sb_pending_corrections = fields.Char('Corrections waiting', compute='_compute_sb_pending_corrections',
                                         help='Inventory vouchers of this item reset to draft for a correction and not posted again.')

    def _compute_sb_pending_corrections(self):
        lines = self.env['sb.stock.voucher.line'].sudo().search([('product_id.product_tmpl_id', 'in', self.ids),
                                                                  ('voucher_id.state', '=', 'draft'),
                                                                  ('voucher_id.sb_correction_before', '!=', False)])
        for t in self:
            docs = lines.filtered(lambda l: l.product_id.product_tmpl_id == t).voucher_id
            t.sb_pending_corrections = ', '.join(docs.mapped('name')) or False

    @api.depends('type', 'is_storable')
    def _compute_sb_item_kind(self):
        for t in self:
            t.sb_item_kind = 'service' if t.type == 'service' else ('stock' if t.is_storable else 'non_stock')

    # new items are stock items unless said otherwise (the item type follows type / tracked quantity)
    is_storable = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('type') in ('service', 'combo'):
                vals['is_storable'] = False
        return super().create(vals_list)

    def _inverse_sb_item_kind(self):
        for t in self:
            if t.sb_item_kind == 'service':
                t.write({'type': 'service', 'is_storable': False})
            else:
                t.write({'type': 'consu', 'is_storable': t.sb_item_kind == 'stock'})
