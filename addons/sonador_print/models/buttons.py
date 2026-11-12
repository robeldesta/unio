"""The apps' own Print buttons print in the company's designs."""
from odoo import models


class SbDeliveryBatch(models.Model):
    _inherit = 'sb.delivery.batch'

    def action_print(self):
        self._sb_before_print()
        return self.env['sb.print'].print_action(self)


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    def action_print_voucher(self):
        return self.env['sb.print'].print_action(self)
