from odoo import api, fields, models


def normalize_tin(vat):
    """Ethiopian TINs are ten digits: '5096318' is stored as '0005096318'."""
    if not vat:
        return vat
    s = ''.join(vat.split())
    return s.zfill(10) if s.isdigit() and len(s) < 10 else s


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sb_trade_license = fields.Char('Trade licence no.', help='Ethiopia: a company with a TIN and a trade licence is withheld '
                                                              '3%; individuals and suppliers without them 30%.')
    sb_wht_exempt_until = fields.Date('Withholding exemption valid until',
                                      help='Uganda: the supplier is on the URA withholding tax exemption list; paid gross until '
                                           'this date.')
    sb_books_country = fields.Char(compute='_compute_sb_books_country')

    @api.depends_context('company')
    def _compute_sb_books_country(self):
        co = self.env.company
        for p in self:
            p.sb_books_country = (co.account_fiscal_country_id or co.country_id).code

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('vat'):
                vals['vat'] = normalize_tin(vals['vat'])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('vat'):
            vals = dict(vals, vat=normalize_tin(vals['vat']))
        return super().write(vals)

    @api.model
    def _sb_normalize_all_tins(self):
        for p in self.with_context(active_test=False).search([('vat', '!=', False)]):
            new = normalize_tin(p.vat)
            if new != p.vat:
                p.vat = new
