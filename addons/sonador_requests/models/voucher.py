"""The payment voucher remembers the request it pays; posting, voiding or deleting it updates the request (paid, partly
paid). Accounting's own logic is unchanged: these only call it and then look at the result."""
from odoo import fields, models


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_request_id = fields.Many2one('sb.payment.request', 'Payment request', index=True, copy=False, ondelete='set null',
                                    readonly=True)

    def action_post(self, *args, **kwargs):
        res = super().action_post(*args, **kwargs)
        self.sb_request_id._sb_sync_paid()
        return res

    def action_void(self, *args, **kwargs):
        res = super().action_void(*args, **kwargs)
        self.sb_request_id._sb_sync_paid()
        return res

    def unlink(self):
        reqs = self.sb_request_id
        res = super().unlink()
        reqs.exists()._sb_sync_paid()
        return res

    def action_sb_open_request(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.payment.request', 'res_id': self.sb_request_id.id,
                'views': [[False, 'form']], 'target': 'current'}
