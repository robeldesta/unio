"""Finance's decision reaches the person who prepared the document, in Me and the chat bot, wherever finance decided
(in Me, or on the document itself): what the store sent to finance, posted or sent back with the reason
("GRV/03188 was posted by Zufan Demissie."), and a voucher posted by someone other than the person who prepared it."""
from odoo import SUPERUSER_ID, models


def _person(user):
    return user if user and user.active and not user.share and user.id != SUPERUSER_ID else user.browse()


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    def _sb_finance_item(self, who):
        return {'key': 'store:%d' % self.id, 'kind': 'store', 'app': 'Inventory', 'title': self.display_name,
                'model': self._name, 'res_id': self.id, 'submitter_uid': who.id}

    def _sb_finance_log(self, sent, approve, note=''):
        if self.env.context.get('sb_me_logged') or self.env.uid == SUPERUSER_ID:
            return  # decided in Me (Me logs it), or by a job
        for v, who in sent.items():
            if who and who != self.env.user and (v.state == 'posted') == approve:
                self.env['sb.me']._log_decision(v._sb_finance_item(who), approve, note, 'web')

    def _sb_sent_by(self):
        return {v: _person(v.sb_sent_finance_by or v.create_uid) for v in self.filtered(lambda x: x.state == 'draft' and x.sb_sent_finance_at)}

    def action_sb_finance_post(self):
        sent = self._sb_sent_by()
        res = super().action_sb_finance_post()
        self._sb_finance_log(sent, True)
        return res

    def action_sb_send_back(self, reason):
        sent = self._sb_sent_by()
        res = super().action_sb_send_back(reason)
        self._sb_finance_log(sent, False, (reason or '').strip())
        return res


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def action_post(self):
        made = {v: _person(v.create_uid) for v in self.filtered(lambda x: x.state == 'draft')}
        res = super().action_post()
        if not self.env.context.get('sb_me_logged') and self.env.uid != SUPERUSER_ID:  # syncs and jobs tell no one
            for v, who in made.items():
                if who and who != self.env.user and v.state == 'posted' and not getattr(v, 'sb_migrated', False):
                    self.env['sb.me']._log_decision({'key': 'voucher:%d' % v.id, 'kind': 'voucher', 'app': 'Accounting',
                                                     'title': v.display_name, 'model': v._name, 'res_id': v.id,
                                                     'submitter_uid': who.id}, True, '', 'web')
        return res
