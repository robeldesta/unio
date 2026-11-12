"""Which register (Peachtree's special journals) a journal belongs to. The Sales, Purchase, Cash Receipts, Cash
Disbursements and General journals list the journals of their register, so a company whose books came with other journal
codes (SJ, PJ, imported bank journals ...) still sees everything in the right register. Set from the journal's type and
code; the accountant can change it on the journal."""
from odoo import api, fields, models

REGISTERS = [('sales', 'Sales Journal'), ('purchases', 'Purchase Journal'), ('receipts', 'Cash Receipts Journal'),
             ('disbursements', 'Cash Disbursements Journal'), ('general', 'General Journal'), ('other', 'None of these')]
BY_CODE = {'INV': 'sales', 'SJ': 'sales', 'BILL': 'purchases', 'PJ': 'purchases', 'CRV': 'receipts', 'CS': 'receipts',
           'CPV': 'disbursements', 'CP': 'disbursements', 'JV': 'general', 'OB': 'general'}


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    sb_register = fields.Selection(REGISTERS, string='Register', compute='_compute_sb_register', store=True,
                                   readonly=False, help='The register (journal report) its entries are listed in.')

    @api.depends('code', 'type')
    def _compute_sb_register(self):
        for j in self:
            if j.sb_register:
                continue
            j.sb_register = BY_CODE.get((j.code or '').upper()) or {'sale': 'sales', 'purchase': 'purchases'}.get(j.type, 'other')
