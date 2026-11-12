"""One bank account, one holder (Robel, 11 Nov 2026): the same account number cannot be saved for two people, in the
database (a unique index on the account number of active bank accounts) and with a clear message in the app."""
import logging

from odoo import api, models
from odoo.addons.base.models.res_bank import sanitize_account_number
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
INDEX = 'res_partner_bank_sb_unique_acc'


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    def _auto_init(self):
        res = super()._auto_init()
        cr = self.env.cr
        cr.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (INDEX,))
        if not cr.fetchone():
            cr.execute("""SELECT sanitized_acc_number FROM res_partner_bank WHERE active AND sanitized_acc_number IS NOT NULL
                          GROUP BY 1 HAVING count(*) > 1 LIMIT 5""")
            dup = [r[0] for r in cr.fetchall()]
            if dup:
                _logger.warning('Bank accounts used twice, unique index not created yet: %s', ', '.join(dup))
            else:
                cr.execute('CREATE UNIQUE INDEX %s ON res_partner_bank (sanitized_acc_number) '
                           'WHERE active AND sanitized_acc_number IS NOT NULL' % INDEX)
        return res

    def _sb_check_free(self, acc, exclude=()):
        """Before saving: the database would refuse it anyway, but with a message nobody understands."""
        num = sanitize_account_number(acc or '')
        if not num:
            return
        other = self.sudo().search([('sanitized_acc_number', '=', num), ('id', 'not in', list(exclude))], limit=1)
        if other:
            raise ValidationError(self.env._('Account %(acc)s already belongs to %(who)s. Each bank account belongs to one '
                                             'person: check the number.', acc=acc, who=other.partner_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('active', True):
                self._sb_check_free(v.get('acc_number'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('acc_number') or vals.get('active'):
            for b in self:
                if vals.get('active', b.active):
                    self._sb_check_free(vals.get('acc_number') or b.acc_number, exclude=self.ids)
        return super().write(vals)

    @api.constrains('acc_number', 'active', 'partner_id')
    def _sb_check_unique(self):
        for b in self.filtered(lambda x: x.active and x.sanitized_acc_number):
            other = self.sudo().search([('sanitized_acc_number', '=', b.sanitized_acc_number), ('id', '!=', b.id)], limit=1)
            if other:
                raise ValidationError(self.env._('Account %(acc)s already belongs to %(who)s. Each bank account belongs to one '
                                                 'person: check the number.', acc=b.acc_number, who=other.partner_id.name))
