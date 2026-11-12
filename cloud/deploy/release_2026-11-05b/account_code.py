"""Account number style: 12345678, 1234.5678 or 1234-5678 (the Ethiopian / Peachtree habit), switchable any time.
Applies to 8-digit account numbers; other codes (e.g. the 6-digit standard chart) are left alone."""
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

CODE_RE = re.compile(r'^[A-Za-z0-9.\-]+$')
STYLES = [('dash', '1234-5678'), ('dot', '1234.5678'), ('plain', '12345678')]


def styled(code, style, split=4, digits=8):
    if not code:
        return code
    bare = re.sub(r'[\s.\-]', '', code)
    if not (bare.isdigit() and len(bare) == digits):
        return code
    sep = {'dash': '-', 'dot': '.', 'plain': ''}[style or 'plain']
    return bare[:split] + sep + bare[split:]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_account_code_style = fields.Selection(STYLES, 'Account number style', default='plain', required=True,
                                             help='How 8-digit account numbers are written. Apply to existing accounts with the button.')

    def action_sb_apply_code_style(self):
        self.check_access('write')
        Account = self.env['account.account']
        for company in self:
            accounts = Account.with_company(company).search([('company_ids', 'in', company.id)])
            changes = {a: styled(a.code, company.sb_account_code_style) for a in accounts}
            changes = {a: c for a, c in changes.items() if c != a.code}
            taken = set(accounts.mapped('code')) - {a.code for a in changes}
            clash = [c for c in changes.values() if c in taken]
            if clash:
                raise ValidationError(self.env._('These numbers already exist: %s', ', '.join(clash)))
            for a, c in changes.items():
                a.with_company(company).code = c
        return True


class AccountAccount(models.Model):
    _inherit = 'account.account'

    @api.constrains('code')
    def _check_account_code(self):
        # Odoo allows letters, digits and dots; Ethiopian charts also use hyphens (1101-0002)
        for account in self:
            if account.code and not CODE_RE.match(account.code):
                raise ValidationError(self.env._('The account number can only contain letters, digits, dots and hyphens (%s).', account.code))

    def action_sb_view(self):
        """The account's transactions: its ledger for this fiscal year (balance brought forward, every entry with its
        voucher, running balance), each line opening its voucher; the dates can be changed there."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        fy = self.env.company.compute_fiscalyear_dates(today)
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.report', 'name': '%s %s' % (self.code, self.name),
                'context': {'report': 'account_transactions',
                            'options': {'account_ids': [self.id], 'date_from': fields.Date.to_string(fy['date_from']),
                                        'date_to': fields.Date.to_string(today), '_title': '%s %s' % (self.code, self.name)}}}

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = styled(vals['code'], self.env.company.sb_account_code_style)
        return super().create(vals_list)
