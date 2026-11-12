"""Fixed assets (IAS 16): categories with their accounts and depreciation, the register, and one depreciation line per
asset and month. Assets come from bills (lines posted to a fixed-asset account), from a capital project (all its lines
on that account), or are entered at go-live with what was already depreciated. Items under the company's threshold are
expensed when bought and kept on the register all the same (who holds them, where they are).

Depreciation goes by the company's accounting periods: Ethiopian months (Hamle - Sene, Pagume with Nehase) or calendar
months, so each line falls in one period of the books."""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of

METHODS = [('straight', 'Straight line'), ('declining', 'Reducing balance')]
FREQUENCIES = [('monthly', 'Every month'), ('yearly', 'Once a year, at year end'), ('manual', 'Only when run by hand')]


def period_of(d):
    """(name, first day, last day) of the accounting period containing d."""
    for _n, name, start, end in fiscal_periods(fiscal_year_of(d)):
        if start <= d <= end:
            return name, start, end
    raise ValueError(d)


def period_end(d):
    return period_of(d)[2]


def next_period(d):
    """First day of the period after the one containing d."""
    return period_end(d) + timedelta(days=1)


def year_end(d):
    return fiscal_periods(fiscal_year_of(d))[-1][3]


class SbAssetCategory(models.Model):
    _name = 'sb.asset.category'
    _description = 'Asset category'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    asset_account_id = fields.Many2one('account.account', 'Cost account', required=True, check_company=True,
                                       domain="[('account_type', '=', 'asset_fixed')]")
    depreciation_account_id = fields.Many2one('account.account', 'Accumulated depreciation account', required=True, check_company=True)
    expense_account_id = fields.Many2one('account.account', 'Depreciation expense account', required=True, check_company=True)
    method = fields.Selection(METHODS, default='straight', required=True)
    life_months = fields.Integer('Useful life (months)', default=60)
    rate = fields.Float('Rate % a year', help='Reducing balance: the yearly rate on the carrying amount.')
    salvage_pct = fields.Float('Residual value %')
    expensed_account_id = fields.Many2one('account.account', 'Expensed items account', check_company=True,
                                          help='Expense account for items of this kind bought under the threshold: written off when bought, '
                                               'kept on the register.')
    law = fields.Char('Rule', help='Where the tax rate comes from (tax law of the country).')
    # tax depreciation (the law's rate, for the tax return) - the fields above are the books' depreciation
    tax_method = fields.Selection(METHODS, 'Tax method', default='declining')
    tax_rate = fields.Float('Tax rate % a year')
    tax_life_months = fields.Integer('Tax life (months)')


class SbAsset(models.Model):
    _name = 'sb.asset'
    _description = 'Fixed asset'
    _inherit = ['mail.thread']
    _order = 'state, code'

    code = fields.Char(default='/', copy=False, index=True)
    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    category_id = fields.Many2one('sb.asset.category', required=True, check_company=True, tracking=True)
    state = fields.Selection([('draft', 'To confirm'), ('running', 'Depreciating'), ('done', 'Fully depreciated'), ('disposed', 'Disposed'),
                              ('expensed', 'Expensed when bought')],
                             default='draft', required=True, tracking=True, index=True)
    date_acquired = fields.Date('Acquired', required=True, default=fields.Date.context_today)
    date_start = fields.Date('In use from', required=True, default=fields.Date.context_today,
                             help='Depreciation starts the month after (a whole first month when on the 1st).')
    cost = fields.Monetary(required=True, tracking=True)
    salvage = fields.Monetary('Residual value')
    method = fields.Selection(METHODS, required=True, default='straight')
    life_months = fields.Integer('Useful life (months)')
    rate = fields.Float('Rate % a year')
    opening_depreciation = fields.Monetary('Depreciated before go-live',
                                           help='For assets brought onto the system: accumulated depreciation already in the opening balance.')
    serial = fields.Char('Serial / plate / tag')
    location = fields.Char()
    custodian_id = fields.Many2one('hr.employee', 'Held by')
    voucher_line_ids = fields.Many2many('sb.voucher.line', 'sb_asset_voucher_line_rel', 'asset_id', 'line_id', string='Bought on', copy=False)
    job_ref = fields.Integer('Capital project', help='The job (Projects) whose costs make up this asset.')
    line_ids = fields.One2many('sb.asset.dep', 'asset_id', string='Depreciation')
    depreciated = fields.Monetary('Accumulated depreciation', compute='_compute_values', store=True)
    book_value = fields.Monetary('Carrying amount', compute='_compute_values', store=True)
    date_disposed = fields.Date('Disposed on')
    disposal_amount = fields.Monetary('Sold for')
    disposal_voucher_id = fields.Many2one('sb.voucher', 'Disposal entry', readonly=True)
    expense_voucher_id = fields.Many2one('sb.voucher', 'Expensed by', readonly=True)
    capitalise_voucher_id = fields.Many2one('sb.voucher', 'Capitalised by', readonly=True)
    dep_from = fields.Date('Depreciate here from', help='Periods before are in the opening or catch-up depreciation.')
    tax_method = fields.Selection(METHODS, 'Tax method')
    tax_rate = fields.Float('Tax rate % a year')
    tax_life_months = fields.Integer('Tax life (months)')
    tax_opening = fields.Monetary('Tax depreciation before go-live', help='Tax allowances already claimed before the books started here.')
    below_threshold = fields.Boolean(compute='_compute_below_threshold')
    note = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('code', '/') == '/':
                v['code'] = self.env['ir.sequence'].next_by_code('sb.asset') or '/'
            cat = self.env['sb.asset.category'].browse(v.get('category_id'))
            for f in ('method', 'life_months', 'rate', 'tax_method', 'tax_rate', 'tax_life_months'):
                v.setdefault(f, cat[f] if cat else False)
            v.setdefault('tax_opening', v.get('opening_depreciation') or 0.0)
            if cat and 'salvage' not in v and v.get('cost'):
                v['salvage'] = round(v['cost'] * cat.salvage_pct / 100.0, 2)
        return super().create(vals_list)

    @api.depends('cost', 'opening_depreciation', 'line_ids.amount', 'line_ids.voucher_id.state', 'state')
    def _compute_values(self):
        for a in self:
            if a.state == 'expensed':
                a.depreciated, a.book_value = a.cost, 0.0
                continue
            done = sum(a.line_ids.filtered(lambda l: l.voucher_id and l.voucher_id.state == 'posted').mapped('amount'))
            a.depreciated = round(a.opening_depreciation + done, 2)
            a.book_value = 0.0 if a.state == 'disposed' else round(a.cost - a.depreciated, 2)

    @api.depends('cost', 'state', 'company_id.sb_asset_threshold')
    def _compute_below_threshold(self):
        for a in self:
            t = a.company_id.sb_asset_threshold
            a.below_threshold = bool(a.state == 'draft' and t and a.cost < t)

    @api.constrains('cost', 'salvage', 'opening_depreciation')
    def _check_values(self):
        for a in self:
            if a.cost < 0 or a.salvage < 0 or a.salvage > a.cost:
                raise ValidationError(self.env._('%s: the residual value must be between nil and the cost.', a.name))
            if a.opening_depreciation > a.cost - a.salvage + 0.01:
                raise ValidationError(self.env._('%s: more depreciation than cost less residual value.', a.name))

    def _first_month(self):
        """First day of the first period depreciated here: the period after it went into use (that period when it went into
        use on its first day), and never before the books started on the system (earlier depreciation is in the opening
        balance)."""
        self.ensure_one()
        d = self.date_start
        _n, start, _e = period_of(d)
        first = start if d == start else next_period(d)
        books = self.company_id.sb_books_start if 'sb_books_start' in self.company_id._fields else False
        if books and first < books:
            first = period_of(books)[1]
        if self.dep_from and first < self.dep_from:
            first = period_of(self.dep_from)[1]
        return first

    def _book_through(self, upto):
        """Book depreciation from going into use to upto (inclusive), as the schedule would charge it: for capitalising
        an item that was expensed in the past."""
        self.ensure_one()
        d = self.date_start
        _n, start, _e = period_of(d)
        d = start if d == start else next_period(d)
        acc, bv = 0.0, self.cost
        while period_end(d) <= upto and bv - self.salvage > 0.5:
            if self.method == 'declining' and self.rate:
                amt = bv * self.rate / 100.0 / 12.0
            else:
                amt = (self.cost - self.salvage) / max(self.life_months or 1, 1)
            amt = min(amt, bv - self.salvage)
            acc += amt
            bv -= amt
            d = next_period(d)
        return round(acc, 2)

    def _tax_wdv(self, upto):
        """Tax written-down value at upto: the law's rate a year on the full year of purchase (the pool rules of
        Ethiopia and Uganda), years ended by upto in full, the current year for the periods elapsed."""
        self.ensure_one()
        if self.state in ('expensed', 'draft') or (self.state == 'disposed' and self.date_disposed and self.date_disposed <= upto):
            return 0.0
        method = self.tax_method or self.method
        rate = self.tax_rate if method == 'declining' else (1200.0 / max(self.tax_life_months or self.life_months or 1, 1))
        books = self.company_id.sb_books_start if 'sb_books_start' in self.company_id._fields else False
        start = max(self.date_acquired, books) if books else self.date_acquired
        wdv = self.cost - (self.tax_opening if (books and books > self.date_acquired) or self.capitalise_voucher_id else 0.0)
        fy, last = fiscal_year_of(start), fiscal_year_of(upto)
        while fy <= last and wdv > 0.5:
            periods = fiscal_periods(fy)
            share = 1.0 if periods[-1][3] <= upto else sum(1 for p in periods if p[3] <= upto) / 12.0
            amt = (self.cost * rate / 100.0 if method == 'straight' else wdv * rate / 100.0) * share
            wdv -= min(amt, wdv)
            fy += 1
        return round(wdv, 2)

    def _amount_for(self, month_end, done=None):
        """Depreciation of one period (month_end = its last day). Straight line: (cost - residual) / life; reducing balance:
        carrying amount x rate / 12. Never below the residual value."""
        self.ensure_one()
        if self.state != 'running' or month_end < self._first_month():
            return 0.0
        depreciated = self.depreciated if done is None else done
        left = self.cost - self.salvage - depreciated
        if left <= 0.005:
            return 0.0
        if self.method == 'declining' and self.rate:
            amt = (self.cost - depreciated) * self.rate / 100.0 / 12.0
        else:
            amt = (self.cost - self.salvage) / max(self.life_months or 1, 1)
        return round(min(amt, left), 0 if self.currency_id.decimal_places == 0 else 2)


class SbAssetDep(models.Model):
    _name = 'sb.asset.dep'
    _description = 'Depreciation line'
    _order = 'date, asset_id'

    asset_id = fields.Many2one('sb.asset', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='asset_id.company_id', store=True)
    date = fields.Date(required=True, index=True)
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related='asset_id.currency_id')
    voucher_id = fields.Many2one('sb.voucher', 'Journal voucher', ondelete='set null')

    _uniq = models.Constraint('unique(asset_id, date)', 'One depreciation line per asset and month.')


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    sb_asset_ids = fields.Many2many('sb.asset', 'sb_asset_voucher_line_rel', 'line_id', 'asset_id', string='Fixed assets', copy=False)


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def action_post(self):
        res = super().action_post()
        self.env['sb.assets']._from_vouchers(self.filtered(lambda v: v.state == 'posted'))
        return res


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_asset_frequency = fields.Selection(FREQUENCIES, 'Post depreciation', default='monthly', required=True)
    sb_asset_threshold = fields.Monetary('Capitalise from', currency_field='currency_id',
                                         help='Items costing less are expensed when bought and kept on the register.')
