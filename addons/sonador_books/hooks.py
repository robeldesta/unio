"""One-time (re-runnable) company setup for Accounting."""
import logging

from odoo import Command

from .models.ethiopian_calendar import fiscal_periods

_logger = logging.getLogger(__name__)

# Accounts added to the l10n_et chart: code -> (name, account_type, reconcile, ifrs line code)
NEW_ACCOUNTS = {
    '110100': ('Software licence revenue', 'income', False, 'PL-01'),
    '110200': ('Implementation and professional services revenue', 'income', False, 'PL-01'),
    '110300': ('Hosting and subscription revenue', 'income', False, 'PL-01'),
    '120100': ('Interest income', 'income_other', False, 'PL-09'),
    '120200': ('Other income', 'income_other', False, 'PL-03'),
    '120300': ('Gain on disposal of fixed assets', 'income_other', False, 'PL-03'),
    '120400': ('Finance charges on overdue accounts', 'income_other', False, 'PL-09'),
    '225500': ('Fixed asset disposal clearing', 'asset_current', False, 'SFP-CA-02'),
    '305900': ('Customer advances and deposits', 'liability_current', True, 'SFP-CL-02'),
    '306300': ('Shareholder / director current account', 'liability_current', True, 'SFP-CL-05'),
    '306400': ('Dividends payable', 'liability_current', False, 'SFP-CL-01'),
    '306600': ('Dividend withholding tax payable', 'liability_current', False, 'SFP-CL-01'),
    '401100': ('Share premium / capital contribution', 'equity', False, 'SFP-EQ-02'),
    '110900': ('Sales discounts allowed', 'income', False, 'PL-01'),
    '211100': ('Cheques on hand / in transit', 'asset_current', False, 'SFP-CA-06'),
    '211200': ('Petty cash', 'asset_cash', False, 'SFP-CA-06'),
    '211300': ('Undeposited funds (cash and cheques for the bank)', 'asset_current', True, 'SFP-CA-06'),
    '306500': ('Outstanding cheques issued', 'liability_current', False, 'SFP-CA-06'),
    '300900': ('Turnover tax (TOT) payable', 'liability_current', False, 'SFP-CL-04'),
    '409000': ('Opening balance equity (clearing)', 'equity', False, 'SFP-EQ-03'),
    '211000': ('Cash on hand', 'asset_cash', False, 'SFP-CA-06'),
    '221600': ('Prepayments', 'asset_prepayments', False, 'SFP-CA-04'),
    '221700': ('Contract assets (unbilled revenue)', 'asset_current', False, 'SFP-CA-03'),
    '221800': ('Advance business profit tax paid', 'asset_current', False, 'SFP-CA-05'),
    '221900': ('Allowance for expected credit losses', 'asset_current', False, 'SFP-CA-02'),
    '235200': ('Raw materials and supplies', 'asset_current', False, 'SFP-CA-01'),
    '235900': ('Inventory write-down allowance', 'asset_current', False, 'SFP-CA-01'),
    '252400': ('Computer and IT equipment', 'asset_fixed', False, 'SFP-NCA-01'),
    '254100': ('Intangible assets - software', 'asset_non_current', False, 'SFP-NCA-03'),
    '255100': ('Accumulated depreciation - PPE', 'asset_fixed', False, 'SFP-NCA-01'),
    '255200': ('Accumulated amortisation - intangibles', 'asset_non_current', False, 'SFP-NCA-03'),
    '256100': ('Right-of-use assets', 'asset_non_current', False, 'SFP-NCA-02'),
    '256200': ('Accumulated depreciation - right-of-use assets', 'asset_non_current', False, 'SFP-NCA-02'),
    '257100': ('Deferred tax assets', 'asset_non_current', False, 'SFP-NCA-04'),
    '305100': ('Goods received not invoiced (GRNI)', 'liability_current', False, 'SFP-CL-01'),
    '305500': ('Contract liabilities - deferred revenue', 'liability_current', False, 'SFP-CL-02'),
    '305600': ('Accrued expenses', 'liability_current', False, 'SFP-CL-01'),
    '305700': ('Business profit tax payable', 'liability_current', False, 'SFP-CL-04'),
    '305800': ('Provisions', 'liability_current', False, 'SFP-CL-07'),
    '306200': ('Lease liabilities - current', 'liability_current', False, 'SFP-CL-06'),
    '316100': ('Lease liabilities - non-current', 'liability_non_current', False, 'SFP-NCL-02'),
    '316200': ('Deferred tax liabilities', 'liability_non_current', False, 'SFP-NCL-03'),
    '403000': ('Retained earnings', 'equity', False, 'SFP-EQ-03'),
    '405000': ('Other comprehensive income reserve', 'equity', False, 'SFP-EQ-04'),
    '511200': ('Purchase price variance', 'expense_direct_cost', False, 'PL-02'),
    '511300': ('Material usage variance', 'expense_direct_cost', False, 'PL-02'),
    '511400': ('Inventory write-down expense', 'expense_direct_cost', False, 'PL-02'),
    '511500': ('Manufacturing overhead absorbed', 'expense_direct_cost', False, 'PL-02'),
    '590200': ('Impairment loss on receivables (ECL)', 'expense', False, 'PL-07'),
    '631600': ('Amortisation of intangible assets', 'expense_depreciation', False, 'PL-06'),
    '631700': ('Depreciation of right-of-use assets', 'expense_depreciation', False, 'PL-06'),
    '641100': ('Business profit tax expense', 'expense', False, 'PL-11'),
    '641200': ('Deferred income tax expense', 'expense', False, 'PL-11'),
    '643500': ('Interest on lease liabilities', 'expense', False, 'PL-10'),
    '643600': ('Interest on late tax payments', 'expense', False, 'PL-10'),
    '644100': ('Tax penalties (not deductible)', 'expense', False, 'PL-08'),
}

# Corrections to the l10n_et chart so classification is right for IFRS (IAS 1 current / non-current)
FIX_ACCOUNTS = {
    '221200': {'account_type': 'asset_current'},
    '221300': {'account_type': 'asset_current'},
    '221400': {'account_type': 'asset_current'},
    '300600': {'account_type': 'liability_current'},
    '300700': {'account_type': 'liability_current'},
    '300800': {'account_type': 'liability_current', 'name': 'Employee income tax payable (PAYE)'},
    '315300': {'account_type': 'liability_non_current', 'name': 'Long-term loan'},
    '310300': {'name': 'Short-term loan'},
    '305700': {'name': 'Business profit tax payable'},
    '641100': {'name': 'Business profit tax expense'},
    '225300': {'reconcile': True},
    # in the chart as receivables, but nobody's customer account: kept out of the aged receivables
    '220100': {'account_type': 'asset_current'},
    '220200': {'account_type': 'asset_current'},
    '220300': {'account_type': 'asset_current', 'reconcile': True},
    '220400': {'account_type': 'asset_current'},
    '511100': {'account_type': 'expense_direct_cost'},
    '631100': {'account_type': 'expense_depreciation'},
    '631300': {'account_type': 'expense_depreciation'},
    '631400': {'account_type': 'expense_depreciation'},
    '631500': {'account_type': 'expense_depreciation'},
}

# IFRS mapping for the l10n_et accounts, by code prefix (longest prefix wins)
IFRS_BY_PREFIX = {
    '11': 'PL-01', '120': 'PL-03', '1200': 'PL-03',
    '2102': 'SFP-CA-06', '2104': 'SFP-CA-07', '211': 'SFP-CA-06', '212': 'SFP-CA-06',
    '220': 'SFP-CA-02', '2211': 'SFP-CA-02', '2212': 'SFP-CA-05', '2213': 'SFP-CA-05', '2214': 'SFP-CA-05',
    '2215': 'SFP-CA-02', '225': 'SFP-CA-04', '2254': 'SFP-CA-02',
    '23': 'SFP-CA-01', '24': 'SFP-CA-01', '25': 'SFP-NCA-01',
    '3001': 'SFP-CL-01', '3002': 'SFP-CL-01', '3003': 'SFP-CL-03', '3004': 'SFP-CL-03',
    '3006': 'SFP-CL-04', '3007': 'SFP-CL-04', '3008': 'SFP-CL-04',
    '305': 'SFP-CL-01', '306': 'SFP-CL-01', '310': 'SFP-CL-05', '315': 'SFP-NCL-01',
    '401': 'SFP-EQ-01', '402': 'SFP-EQ-02', '9999': 'SFP-EQ-03',
    '5': 'PL-02', '6': 'PL-05', '626': 'PL-08', '631': 'PL-06', '632': 'PL-08',
    '6432': 'PL-10', '6434': 'PL-10', '999001': 'PL-03', '999002': 'PL-08',
}
# Not expenses under IFRS (repayment of loan principal) - left unmapped on purpose so the
# IFRS check lists them for the accountant.
IFRS_UNMAPPED = {'643100', '643300'}

JOURNALS = [
    # code, name, type, default account code
    ('INV', 'Sales Invoices (SJ)', 'sale', None),
    ('BILL', 'Purchase Bills (PJ)', 'purchase', None),
    ('CS', 'Cash Sales', 'general', None),
    ('CP', 'Cash Purchases', 'general', None),
    ('CRV', 'Cash Receipts (CRJ)', 'general', None),
    ('BT', 'Bank Transfers', 'general', None),
    ('OB', 'Opening Balances', 'general', None),
    ('PC', 'Petty Cash', 'cash', '211200'),
    ('CPV', 'Cash Disbursements (CDJ)', 'general', None),
    ('JV', 'Journal Vouchers (GENJ)', 'general', None),
    ('CSH1', 'Cash on Hand', 'cash', '211000'),
]


def post_init_hook(env):
    from .models.locale import LOCALES
    for company in env['res.company'].search([]).filtered(
            lambda c: c.account_fiscal_country_id.code == 'ET' or c.country_id.code in LOCALES):
        env['res.company'].browse(company.id)._sb_setup()


def _account(env, company, code):
    """By number, old or renumbered (Ethiopian layout, see models/coa_et.py)."""
    return env['account.account'].with_company(company)._sb_find(code, company)[:1]


def setup_company(env, company):
    # a fresh database can install Accounting in the same run as l10n_et: load the Ethiopian chart first
    if 'chart_template' in company._fields and not company.chart_template:
        env['account.chart.template'].try_loading('et', company, install_demo=False)
    # Ethiopian fiscal year: Hamle 1 - Sene 30, i.e. it ends on 7 July (voucher numbers per fiscal year: CRV/25-26/0001);
    # a Gregorian database ends its year on the last day of the month before its start month
    from .models.ethiopian_calendar import fiscal_periods as _fp, fiscal_year_of as _fyo, gregorian
    if company.fiscalyear_last_month == '12' and company.fiscalyear_last_day == 31 or gregorian():
        from datetime import date as _date
        end = _fp(_fyo(_date.today()))[-1][3]
        company.write({'fiscalyear_last_month': str(end.month), 'fiscalyear_last_day': end.day})
    Account = env['account.account'].with_company(company)
    ifrs = {l.code: l for l in env['sb.ifrs.line'].search([])}

    # 1. accounts
    for code, (name, atype, reconcile, ifrs_code) in NEW_ACCOUNTS.items():
        # an account archived because the company never used it stays archived: it is not made again
        if not env['account.account'].with_context(active_test=False).with_company(company)._sb_find(code, company)[:1]:
            Account.create({'code': Account._sb_new_code(code, company), 'sb_legacy_code': code if Account._sb_renumbered(company) else False,
                            'name': name, 'account_type': atype, 'reconcile': reconcile,
                            'company_ids': [Command.link(company.id)],
                            'sb_ifrs_line_id': ifrs[ifrs_code].id if ifrs_code in ifrs else False})
    for code, vals in FIX_ACCOUNTS.items():
        acc = _account(env, company, code)
        if acc:
            acc.write(vals)

    # 2. IFRS mapping
    explicit = {code: v[3] for code, v in NEW_ACCOUNTS.items()}
    from .models.ifrs import IFRS_BY_TYPE
    for acc in Account.search([('company_ids', 'in', company.id)]):
        if acc.code in IFRS_UNMAPPED:
            continue
        # a line chosen by someone stays; the one given automatically when the account was created (its type's default,
        # often from the chart's original type, e.g. a tax payable that started as an asset) is replaced
        current = acc.sb_ifrs_line_id.code
        if current and current != IFRS_BY_TYPE.get(acc.account_type) and not acc._sb_ifrs_suggest(strict=True):
            continue
        line_code = explicit.get(acc.code)
        if not line_code:
            prefixes = [p for p in IFRS_BY_PREFIX if acc.code.startswith(p)]
            if prefixes:
                line_code = IFRS_BY_PREFIX[max(prefixes, key=len)]
        if line_code and line_code in ifrs:
            acc.sb_ifrs_line_id = ifrs[line_code]
    # direct costs are cost of sales, whatever line they picked up when they were still plain expenses
    if 'PL-02' in ifrs:
        Account.search([('company_ids', 'in', company.id), ('account_type', '=', 'expense_direct_cost'),
                        ('sb_ifrs_line_id.code', '=', 'PL-05')]).write({'sb_ifrs_line_id': ifrs['PL-02'].id})

    # 3. journals (Peachtree journal names)
    Journal = env['account.journal'].with_company(company)
    for code, name, jtype, account_code in JOURNALS:
        journal = Journal.search([('code', '=', code), ('company_id', '=', company.id)], limit=1)
        if not journal and code == 'JV':
            journal = Journal.search([('code', '=', 'MISC'), ('company_id', '=', company.id)], limit=1)
            if journal and not env['account.move'].search_count([('journal_id', '=', journal.id)], limit=1):
                journal.code = 'JV'
            else:
                journal = Journal.browse()
        vals = {'name': name}
        if not journal:
            vals.update({'code': code, 'type': jtype, 'company_id': company.id})
            if account_code:
                vals['default_account_id'] = _account(env, company, account_code).id
            journal = Journal.create(vals)
        else:
            journal.name = name

    # 4. withholding defaults
    if not company.sb_wht_payable_account_id:
        company.sb_wht_payable_account_id = _account(env, company, '300600')
    if not company.sb_wht_receivable_account_id:
        company.sb_wht_receivable_account_id = _account(env, company, '221300')

    if not company.sb_customer_advance_account_id:
        company.sb_customer_advance_account_id = _account(env, company, '305900')
    if not company.sb_vendor_advance_account_id:
        company.sb_vendor_advance_account_id = _account(env, company, '225300')

    defaults = {
        'sb_cheque_on_hand_account_id': '211100', 'sb_cheque_outstanding_account_id': '306500',
        'sb_bank_charges_account_id': '625600', 'sb_discount_allowed_account_id': '110900',
        'sb_discount_received_account_id': '120001', 'sb_opening_equity_account_id': '409000',
    }
    for field, code in defaults.items():
        if not company[field]:
            company[field] = _account(env, company, code)
    _setup_tot(env, company)

    undep = _account(env, company, '211300')
    if undep and not undep.sb_undeposited:
        undep.sb_undeposited = True

    # 4b. receipt / payment purposes
    _setup_purposes(env, company)

    # 5. retained earnings for year-end closing (om_fiscal_year)
    if 'retained_earnings_account_id' in company._fields and not company.retained_earnings_account_id:
        company.retained_earnings_account_id = _account(env, company, '403000')

    # 5a0. payroll tax table
    if 'sb.payroll.bracket' in env and not env['sb.payroll.bracket'].search_count([('company_id', '=', company.id)]):
        from .models.payroll import DEFAULT_BRACKETS
        env['sb.payroll.bracket'].create([{'company_id': company.id, 'lower': lo, 'upper': up, 'rate': r, 'deduction': d}
                                          for lo, up, r, d in DEFAULT_BRACKETS])

    # 5a. ready-made report packs
    _setup_packs(env, company)

    # 5b. hide the Odoo Mates report wizards: the report engine replace them
    hide_wizard_menus(env)

    # 5c. TINs are ten digits (leading zeros)
    if hasattr(env['res.partner'], '_sb_normalize_all_tins'):
        env['res.partner']._sb_normalize_all_tins()

    # 6. costing: perpetual valuation, FIFO / AVCO categories, ready for mrp_account
    _setup_costing(env, company)

    # 7. fiscal years (E.C.) for om_fiscal_year
    _setup_fiscal_years(env, company)

    # 8. asset and deferred revenue (IFRS 15) types
    _setup_asset_types(env, company)
    _logger.info('Accounting setup done for %s', company.name)


def _setup_costing(env, company):
    stock_acc = _account(env, company, '235100')
    if 'inventory_valuation' in company._fields:
        company.inventory_valuation = 'real_time'
    if 'account_stock_valuation_id' in company._fields and not company.account_stock_valuation_id:
        company.account_stock_valuation_id = stock_acc
    Categ = env['product.category'].with_company(company)
    goods = env.ref('product.product_category_goods', raise_if_not_found=False)
    income = _account(env, company, '110000')
    cogs = _account(env, company, '511100')
    ppv = _account(env, company, '511200')
    specs = [
        (goods, None, 'average', stock_acc),
        (None, 'Merchandise (FIFO)', 'fifo', stock_acc),
        (None, 'Raw Materials & Supplies (AVCO)', 'average', _account(env, company, '235200')),
        (None, 'Work in Progress (AVCO)', 'average', _account(env, company, '241100')),
        (None, 'Finished Goods (AVCO)', 'average', _account(env, company, '241200')),
    ]
    for categ, name, method, valuation_account in specs:
        if not categ and not name:
            continue
        if not categ:
            categ = Categ.search([('name', '=', name)], limit=1) or Categ.create({'name': name})
        vals = {
            'property_cost_method': method,
            'property_valuation': 'real_time',
            'property_stock_valuation_account_id': valuation_account.id,
            'property_price_difference_account_id': ppv.id,
            'property_account_income_categ_id': income.id,
            'property_account_expense_categ_id': cogs.id,
        }
        categ.with_company(company).write({k: v for k, v in vals.items() if k in categ._fields})
    services = env.ref('product.product_category_services', raise_if_not_found=False) \
        or Categ.search([('name', '=', 'Services')], limit=1) or Categ.create({'name': 'Services'})
    services.with_company(company).write({
        'property_account_income_categ_id': _account(env, company, '110200').id,
        'property_account_expense_categ_id': _account(env, company, '625100').id,
    })


def _setup_fiscal_years(env, company):
    from datetime import date
    from .models.ethiopian_calendar import fiscal_year_of, fy_label
    if 'account.fiscal.year' not in env:
        return
    FY = env['account.fiscal.year']
    current = fiscal_year_of(date.today())
    for fy in (current - 1, current):
        periods = fiscal_periods(fy)
        start, end = periods[0][2], periods[-1][3]
        if not FY.search_count([('company_id', '=', company.id), ('date_from', '<=', end), ('date_to', '>=', start)]):
            FY.create({'name': fy_label(fy), 'date_from': start, 'date_to': end, 'company_id': company.id})


def _setup_asset_types(env, company):
    if 'account.asset.category' not in env:
        return
    Cat = env['account.asset.category'].with_company(company)
    journal = env['account.journal'].search([('code', '=', 'JV'), ('company_id', '=', company.id)], limit=1)
    a = lambda code: _account(env, company, code).id  # noqa: E731
    types = [
        # name, type, asset acc, depreciation acc, expense/revenue acc, months, period
        ('Computer and IT equipment (4 yrs)', 'purchase', '252400', '255100', '631300', 48, 1),
        ('Furniture and fixtures (10 yrs)', 'purchase', '252900', '255100', '631400', 120, 1),
        ('Vehicles (5 yrs)', 'purchase', '252100', '255100', '631100', 60, 1),
        ('Software - intangible (3 yrs)', 'purchase', '254100', '255200', '631600', 36, 1),
        ('Deferred revenue - software subscription (12 m)', 'sale', '305500', '305500', '110300', 12, 1),
        ('Deferred revenue - hosting (12 m)', 'sale', '305500', '305500', '110300', 12, 1),
        ('Deferred revenue - support and maintenance (12 m)', 'sale', '305500', '305500', '110200', 12, 1),
    ]
    for name, ctype, asset, dep, exp, number, period in types:
        if Cat.search_count([('name', '=', name), ('company_id', '=', company.id)]):
            continue
        if not (a(asset) and a(dep) and a(exp)):
            continue  # an account is missing from this chart: the type can be added from settings later
        Cat.create({
            'name': name, 'type': ctype, 'company_id': company.id, 'journal_id': journal.id,
            'account_asset_id': a(asset), 'account_depreciation_id': a(dep),
            'account_depreciation_expense_id': a(exp),
            'method': 'linear', 'method_number': number, 'method_period': period,
            'prorata': True, 'open_asset': True,
        })


PURPOSES = [
    # voucher type, name, payer type, account code
    ('receipt', 'Capital injection (share capital)', 'shareholder', '401000'),
    ('receipt', 'Capital contribution / share premium', 'shareholder', '401100'),
    ('receipt', 'Shareholder loan / director current account', 'shareholder', '306300'),
    ('receipt', 'Bank loan received (long-term)', 'lender', '315300'),
    ('receipt', 'Short-term loan received', 'lender', '310300'),
    ('receipt', 'Customer advance / deposit', 'customer', '305900'),
    ('receipt', 'Other income', 'other', '120200'),
    ('receipt', 'Interest income', 'lender', '120100'),
    ('receipt', 'Sale of fixed asset', 'other', '225500'),
    ('receipt', 'VAT / tax refund', 'government', '221200'),
    ('receipt', 'Staff advance repaid', 'employee', '220300'),
    ('receipt', 'Vendor refund', 'vendor', '225300'),
    ('receipt', 'Insurance claim received', 'other', '120200'),
    ('payment', 'Dividend paid', 'shareholder', '306400'),
    ('payment', 'Shareholder loan repaid / drawings', 'shareholder', '306300'),
    ('payment', 'Loan principal repayment (long-term)', 'lender', '315300'),
    ('payment', 'Loan principal repayment (short-term)', 'lender', '310300'),
    ('payment', 'Interest paid', 'lender', '643400'),
    ('payment', 'Salaries paid (net pay)', 'employee', '300400'),
    ('payment', 'Pension contribution paid (POESSA)', 'government', '300300'),
    ('payment', 'Employee income tax paid (PAYE)', 'government', '300800'),
    ('payment', 'VAT paid', 'government', '300700'),
    ('payment', 'Withholding tax remitted', 'government', '300600'),
    ('payment', 'Business profit tax paid (annual)', 'government', '305700'),
    ('payment', 'Advance business profit tax paid', 'government', '221800'),
    ('payment', 'Dividend tax paid', 'government', '306600'),
    ('payment', 'Tax penalty paid', 'government', '644100'),
    ('payment', 'Interest on late tax paid', 'government', '643600'),
    ('payment', 'Fixed asset purchase', 'vendor', '252400'),
    ('payment', 'Prepayment (rent, insurance ...)', 'vendor', '221600'),
    ('payment', 'Staff advance', 'employee', '220300'),
    ('payment', 'Vendor advance', 'vendor', '225300'),
    ('payment', 'Bank charges', 'lender', '625600'),
]


def _setup_purposes(env, company):
    Purpose = env['sb.voucher.purpose'].with_context(active_test=False)
    for seq, (vtype, name, payer, code) in enumerate(PURPOSES):
        if Purpose.search_count([('company_id', '=', company.id), ('voucher_type', '=', vtype), ('name', '=', name)]):
            continue
        account = _account(env, company, code)
        if account:
            Purpose.create({'sequence': seq, 'voucher_type': vtype, 'name': name, 'payer_type': payer,
                            'account_id': account.id, 'company_id': company.id})


def _setup_tot(env, company):
    """Turnover tax for non-VAT-registered sales (2% goods, 10% services)."""
    Tax = env['account.tax'].with_company(company)
    account = _account(env, company, '300900')
    Group = env['account.tax.group'].with_company(company)
    group = Group.search([('company_id', '=', company.id), ('name', '=', 'TOT')], limit=1) or Group.create(
        {'name': 'TOT', 'company_id': company.id, 'country_id': company.account_fiscal_country_id.id or False})
    for name, rate in (('TOT 2%', 2.0), ('TOT 10%', 10.0)):
        if Tax.search_count([('name', '=', name), ('type_tax_use', '=', 'sale'), ('company_id', '=', company.id)]):
            continue
        rep = [Command.create({'repartition_type': 'base'}),
               Command.create({'repartition_type': 'tax', 'account_id': account.id})]
        Tax.create({'name': name, 'amount': rate, 'amount_type': 'percent', 'type_tax_use': 'sale',
                    'company_id': company.id, 'description': name, 'tax_group_id': group.id,
                    'invoice_repartition_line_ids': rep, 'refund_repartition_line_ids': rep})


def hide_wizard_menus(env):
    data = env['ir.model.data'].search([('module', 'in', ('accounting_pdf_reports', 'om_account_daily_reports')),
                                        ('model', '=', 'ir.ui.menu')])
    env['ir.ui.menu'].browse(data.mapped('res_id')).exists().write({'active': False})


PACKS = [
    ('Monthly Management Pack', 'Trial balance, income statement, balance sheet, cash flow, ageing and cash book for last month.',
     'last_month', ['tb', 'is_ytd', 'sfp', 'cash_flow', 'aged_ar', 'aged_ap', 'cash_register']),
    ('Auditor Pack', 'Full statements, ledgers, VAT and withholding for last fiscal year.',
     'last_fy', ['sfp', 'pl_oci', 'cash_flow', 'tb', 'gl', 'aged_ar', 'aged_ap', 'vat_return', 'wht_payable', 'wht_received', 'bank_rec']),
    ('Monthly Tax Pack', 'VAT declaration, VAT registers and withholding for last month.',
     'last_month', ['vat_return', 'vat_sales', 'vat_purchases', 'wht_return', 'wht_received']),
]


def _setup_packs(env, company):
    if 'sb.report.pack' not in env:
        return
    Pack = env['sb.report.pack'].with_context(active_test=False)
    for seq, (name, desc, period, keys) in enumerate(PACKS):
        if Pack.search_count([('name', '=', name), ('company_id', '=', company.id)]):
            continue
        Pack.create({'name': name, 'description': desc, 'period': period, 'sequence': seq, 'company_id': company.id,
                     'line_ids': [Command.create({'report_key': k, 'sequence': i}) for i, k in enumerate(keys)]})
