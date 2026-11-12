from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_grni_account_id = fields.Many2one('account.account', 'Goods received not invoiced',
                                         help='Credited by a GRN, cleared by the vendor bill entered from it.')
    sb_stock_adjust_account_id = fields.Many2one('account.account', 'Stock count differences',
                                                 help='Gains and losses found when stock is counted.')
    sb_giv_account_id = fields.Many2one('account.account', 'Default account for goods issued',
                                        help='Expense charged by a GIV unless the voucher or line says otherwise.')
    sb_cost_difference_account_id = fields.Many2one('account.account', 'Cost differences',
                                                    help='Bill price different from the GRN cost, and cost differences on voids and returns.')
    sb_allow_negative_stock = fields.Boolean('Allow negative stock',
                                             help='Off: a sale or issue cannot take more than is on hand in the warehouse.')
    sb_recost_entries = fields.Selection([('day', 'One per date'), ('month', 'One per accounting month')], 'Cost recalculation entries',
                                         default='day', required=True,
                                         help='When a receipt dated earlier, a correction or a void changes the cost of later issues.\n'
                                              'One per date: each difference is posted on the date of its issue, so the stock records '
                                              'equal the ledger on every date (an entry per date touched).\n'
                                              'One per month: the differences of a month are posted together on its last day: far '
                                              'fewer entries and faster for busy items; the stock records equal the ledger at each '
                                              'month end, and within a month the ledger catches up on its last day.')


class StockLocation(models.Model):
    _inherit = 'stock.location'

    sb_giv_location = fields.Boolean('Goods issued location')
