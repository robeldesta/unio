from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_labour_absorbed_account_id = fields.Many2one('account.account', 'Labour absorbed account',
                                                    help='Credited with the labour cost a build adds to finished goods.')
    sb_overhead_absorbed_account_id = fields.Many2one('account.account', 'Overhead absorbed account',
                                                      help='Credited with the manufacturing overhead a build adds to finished goods.')


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    sb_labour_cost = fields.Float('Labour per unit', digits=(16, 4),
                                  help='Direct labour absorbed per unit produced (e.g. per litre).')
    sb_overhead_cost = fields.Float('Overhead per unit', digits=(16, 4),
                                    help='Manufacturing overhead absorbed per unit produced.')
    sb_date_from = fields.Date('Valid from', help='Recipe versions: a build uses the version valid on its date.')
    sb_date_to = fields.Date('Valid to')
    consumption = fields.Selection(default='flexible')

    @api.model
    def _sb_for(self, product, day):
        """The recipe for a product on a date: the version whose validity covers the date, else the undated one."""
        boms = self.search([('product_tmpl_id', '=', product.product_tmpl_id.id), ('active', '=', True),
                            '|', ('product_id', '=', False), ('product_id', '=', product.id)], order='sequence, id')
        dated = boms.filtered(lambda b: (not b.sb_date_from or b.sb_date_from <= day) and (not b.sb_date_to or b.sb_date_to >= day)
                              and (b.sb_date_from or b.sb_date_to))
        return dated[:1] or boms.filtered(lambda b: not b.sb_date_from and not b.sb_date_to)[:1] or boms[:1]
