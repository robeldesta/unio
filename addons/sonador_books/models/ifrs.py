import re

from odoo import api, fields, models

STATEMENTS = [
    ('sfp', 'Statement of Financial Position'),
    ('pl', 'Profit or Loss'),
    ('oci', 'Other Comprehensive Income'),
]

SECTIONS = [
    ('non_current_asset', 'Non-current assets'),
    ('current_asset', 'Current assets'),
    ('equity', 'Equity'),
    ('non_current_liability', 'Non-current liabilities'),
    ('current_liability', 'Current liabilities'),
    ('revenue', 'Revenue'),
    ('cost_of_sales', 'Cost of sales'),
    ('other_income', 'Other income'),
    ('operating_expense', 'Operating expenses'),
    ('finance', 'Finance income and costs'),
    ('income_tax', 'Income tax'),
    ('oci_no_recycle', 'OCI - not reclassified to P&L'),
    ('oci_recycle', 'OCI - may be reclassified to P&L'),
]

CASH_FLOW = [
    ('cash', 'Cash and cash equivalents'),
    ('working_capital', 'Operating - working capital'),
    ('non_cash', 'Operating - non-cash adjustment'),
    ('investing', 'Investing'),
    ('financing', 'Financing'),
    ('profit', 'Profit for the period'),
    ('none', 'Not in cash flow'),
]


class SbIfrsLine(models.Model):
    _name = 'sb.ifrs.line'
    _description = 'IFRS Line Item'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    statement = fields.Selection(STATEMENTS, required=True)
    section = fields.Selection(SECTIONS, required=True)
    cash_flow = fields.Selection(CASH_FLOW, required=True, default='none')
    standard = fields.Char(help='Main IFRS / IAS reference, e.g. IAS 16')
    account_ids = fields.One2many('account.account', 'sb_ifrs_line_id', string='Accounts')
    account_count = fields.Integer(compute='_compute_account_count')

    _code_uniq = models.Constraint('unique(code)', 'IFRS line code must be unique.')

    @api.depends('account_ids')
    def _compute_account_count(self):
        for line in self:
            line.account_count = len(line.account_ids)

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for line in self:
            line.display_name = '%s %s' % (line.code, line.name)


class AccountAccount(models.Model):
    _inherit = 'account.account'

    sb_ifrs_line_id = fields.Many2one(
        'sb.ifrs.line', string='IFRS Line', index=True,
        help='IFRS financial statement line item this account rolls up to.')
    sb_ifrs_section = fields.Selection(related='sb_ifrs_line_id.section', string='IFRS Section', store=True)
    sb_cash_flow = fields.Selection(related='sb_ifrs_line_id.cash_flow', string='Cash Flow Class', store=True)


# fallback IFRS line by account type, for accounts created later (e.g. a new bank journal)
IFRS_BY_TYPE = {
    'asset_cash': 'SFP-CA-06', 'asset_receivable': 'SFP-CA-02', 'asset_current': 'SFP-CA-02',
    'asset_prepayments': 'SFP-CA-04', 'asset_fixed': 'SFP-NCA-01', 'asset_non_current': 'SFP-NCA-05',
    'liability_payable': 'SFP-CL-01', 'liability_credit_card': 'SFP-CL-01', 'liability_current': 'SFP-CL-01',
    'liability_non_current': 'SFP-NCL-01', 'equity': 'SFP-EQ-02', 'equity_unaffected': 'SFP-EQ-03',
    'income': 'PL-01', 'income_other': 'PL-03', 'expense': 'PL-05', 'expense_direct_cost': 'PL-02',
    'expense_depreciation': 'PL-06',
}


class AccountAccountIfrsDefault(models.Model):
    _inherit = 'account.account'

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        Line = self.env['sb.ifrs.line'].sudo()
        for acc in accounts.filtered(lambda a: not a.sb_ifrs_line_id):
            code = acc._sb_ifrs_suggest()
            line = code and Line.search([('code', '=', code)], limit=1)
            if line:
                acc.sb_ifrs_line_id = line
        return accounts

    sb_ifrs_suggested_id = fields.Many2one('sb.ifrs.line', 'Suggested IFRS line', compute='_compute_sb_ifrs_suggested',
                                           search='_search_sb_ifrs_suggested')

    def _compute_sb_ifrs_suggested(self):
        Line = self.env['sb.ifrs.line'].sudo()
        lines = {l.code: l for l in Line.search([])}
        for a in self:
            sug = lines.get(a._sb_ifrs_suggest(strict=True)) if a.id else False
            a.sb_ifrs_suggested_id = sug if sug and sug != a.sb_ifrs_line_id else False

    def _search_sb_ifrs_suggested(self, operator, value):
        if operator not in ('!=', '=') or value is not False:
            raise NotImplementedError()
        accounts = self.search([('company_ids', 'in', self.env.company.id)])
        ids = accounts.filtered(lambda a: a.sb_ifrs_suggested_id).ids
        return [('id', 'in' if operator == '!=' else 'not in', ids)]

    @api.model
    def _sb_ifrs_autofix(self):
        """On upgrade: put back on the right statement line the accounts the old defaults left on a clearly wrong one
        (inventory on receivables, share capital on reserves, a tax payable on an asset line). Lines people chose stay."""
        from ..hooks import IFRS_BY_PREFIX
        Line = self.env['sb.ifrs.line'].sudo()
        lines = {l.code: l for l in Line.search([])}
        for a in self.sudo().with_context(active_test=False).search([]):
            sug = a._sb_ifrs_suggest(strict=True)
            if not sug:
                continue
            prefixes = [p for p in IFRS_BY_PREFIX if (a.code or '').startswith(p)]
            code = IFRS_BY_PREFIX[max(prefixes, key=len)] if prefixes else sug
            if code[:6] != sug[:6]:  # the chart's prefix points elsewhere than the clear case: trust the clear case
                code = sug
            if code in lines and lines[code] != a.sb_ifrs_line_id:
                a.sb_ifrs_line_id = lines[code]

    def action_sb_ifrs_apply_suggested(self):
        for a in self.filtered('sb_ifrs_suggested_id'):
            a.sb_ifrs_line_id = a.sb_ifrs_suggested_id

    def _sb_ifrs_suggest(self, strict=False):
        """The statement line an account most likely belongs on, from its type, its name and whether stock is valued
        on it. Used for new accounts; with strict, by the mapping check: then only a clear case is suggested (inventory,
        share capital, retained earnings, a liability on an asset line ...), never a change of a deliberate choice."""
        self.ensure_one()
        code = IFRS_BY_TYPE.get(self.account_type)
        name = (self.name or '').lower()
        if strict:
            current = self.sb_ifrs_line_id.code or ''
            family = {'asset': ('SFP-CA', 'SFP-NCA'), 'liability': ('SFP-CL', 'SFP-NCL'), 'equity': ('SFP-EQ',),
                      'income': ('PL',), 'expense': ('PL',)}.get((self.account_type or '').split('_')[0], ())
            specific = self._sb_ifrs_suggest()
            if not current or (family and not current.startswith(family)):
                return specific  # unmapped, or on the wrong side (a tax payable on a receivables line)
            if specific in ('SFP-CA-01', 'SFP-EQ-01', 'SFP-EQ-03', 'SFP-NCA-01', 'SFP-CL-04', 'SFP-CA-05', 'SFP-CL-02') \
                    and current in (code, 'SFP-EQ-02', 'SFP-NCA-05', 'SFP-CA-04') and current != specific:
                return specific  # left on its type's default line although it is inventory, capital, retained
                # earnings, construction in progress, a tax payable or prepaid, or a customer deposit
            if self.account_type == 'expense_depreciation' and current == 'PL-05':
                return 'PL-06'
            return False
        if self.account_type == 'expense' and ('profit tax' in name or 'income tax' in name) and 'payable' not in name:
            return 'PL-11'  # a tax on profit is income tax expense, not an operating expense
        if self.account_type == 'expense' and ('interest' in name or 'loan processing' in name):
            return 'PL-10'  # finance costs
        if self.account_type == 'equity':
            if 'retained' in name or 'accumulated profit' in name:
                return 'SFP-EQ-03'
            if 'capital' in name and not any(w in name for w in ('premium', 'reserve', 'contribution')):
                return 'SFP-EQ-01'
        if self.account_type in ('asset_non_current', 'asset_fixed') and re.search(r'construction|capital work', name):
            return 'SFP-NCA-01'  # construction in progress is property, plant and equipment (IAS 16)
        if self.account_type.startswith('liability') and re.search(r'\btax|vat\b|paye|with+olding|\btot\b', name) \
                and 'deferred' not in name:
            return 'SFP-CL-04'
        if self.account_type in ('asset_current', 'asset_receivable', 'asset_prepayments') and re.search(
                r'profit tax|income tax|with+olding (tax )?receivable|advance tax|tax paid|vat receivable|input vat', name):
            return 'SFP-CA-05'
        if self.account_type.startswith('liability') and re.search(r'customer advance|advance from customer|customer deposit|deposits? from customer|unearned|deferred revenue', name):
            return 'SFP-CL-02'
        if self.account_type == 'asset_current':
            stock = 'product.category' in self.env and self.env['product.category'].sudo().search_count(
                [('property_stock_valuation_account_id', '=', self.id)]) if self.id else 0
            if stock or any(w in name for w in ('inventory', 'raw material', 'finished good', 'finished product',
                                                 'work in process', 'work in progress', 'packing material', 'spare part')):
                return 'SFP-CA-01'
        return code
