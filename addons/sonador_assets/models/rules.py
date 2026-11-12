"""Asset classes and depreciation rates by country (the tax law's rates, used for the books as well). Each class has its
own cost account and its own accumulated-depreciation account in the chart, so the ledger holds one control account per
class and the register is its subsidiary ledger.

Ethiopia: Federal Income Tax Proclamation 979/2016, art. 25, and Regulation 410/2017: buildings and structures 5% and
intangible assets 10% a year straight line; computers, information systems, software and data-storage equipment 25%, and
every other business asset 20%, on the reducing balance (the pool method of art. 25, worked per asset).
Uganda: Income Tax Act (Cap. 338), s. 27 and the Sixth Schedule, wear and tear on the reducing balance by class: 1
computers and data-handling equipment 40%; 2 automobiles, buses and goods vehicles under 7 tonnes, construction and
earth-moving equipment 35%; 3 larger buses and goods vehicles, tractors, trailers, plant and machinery used in
manufacturing, farming or mining 30%; 4 everything else (furniture, fixtures, office equipment) 20%. Industrial buildings
5% straight line (s. 28); intangible assets over their useful life (s. 40)."""
from odoo import Command

# (key, name, cost account, its name if missing, accumulated account, its name, expense account, method, life months, rate, rule)
RULES = {
    'ET': [
        ('buildings', 'Buildings and structures', '252500', 'Buildings', '255110', 'Accumulated depreciation - buildings',
         '631400', 'straight', 240, 0, 'Proclamation 979/2016 art. 25: 5% straight line'),
        ('intangible', 'Intangible assets (software, licences)', '254100', 'Intangible assets - software', '255200',
         'Accumulated amortisation - intangibles', '631600', 'straight', 120, 0, 'Proclamation 979/2016 art. 25: 10% straight line'),
        ('computers', 'Computers and IT equipment', '252400', 'Computer and IT equipment', '255140',
         'Accumulated depreciation - computers and IT', '631300', 'declining', 0, 25, 'Proclamation 979/2016 art. 25: 25% reducing balance'),
        ('vehicles', 'Vehicles', '252100', 'Vehicles and other vehicular transport', '255120', 'Accumulated depreciation - vehicles',
         '631100', 'declining', 0, 20, 'Proclamation 979/2016 art. 25: other business assets, 20% reducing balance'),
        ('machinery', 'Plant and machinery', '252300', 'Plant machinery and equipment', '255130',
         'Accumulated depreciation - plant and machinery', '631300', 'declining', 0, 20,
         'Proclamation 979/2016 art. 25: other business assets, 20% reducing balance'),
        ('furniture', 'Furniture, fittings and office equipment', '252900', 'Furnishings and fixtures', '255150',
         'Accumulated depreciation - furniture and fittings', '631400', 'declining', 0, 20,
         'Proclamation 979/2016 art. 25: other business assets, 20% reducing balance'),
    ],
    'UG': [
        ('computers', 'Class 1: computers and data-handling equipment', '252400', 'Computer and IT equipment', '255140',
         'Accumulated depreciation - computers and IT', '631300', 'declining', 0, 40, 'Income Tax Act s. 27, class 1: 40% reducing balance'),
        ('vehicles', 'Class 2: cars, light goods vehicles, construction equipment', '252100', 'Vehicles and other vehicular transport',
         '255120', 'Accumulated depreciation - vehicles', '631100', 'declining', 0, 35, 'Income Tax Act s. 27, class 2: 35% reducing balance'),
        ('machinery', 'Class 3: heavy vehicles, plant and machinery', '252300', 'Plant machinery and equipment', '255130',
         'Accumulated depreciation - plant and machinery', '631300', 'declining', 0, 30, 'Income Tax Act s. 27, class 3: 30% reducing balance'),
        ('furniture', 'Class 4: furniture, fixtures and other assets', '252900', 'Furnishings and fixtures', '255150',
         'Accumulated depreciation - furniture and fittings', '631400', 'declining', 0, 20, 'Income Tax Act s. 27, class 4: 20% reducing balance'),
        ('buildings', 'Industrial buildings', '252500', 'Buildings', '255110', 'Accumulated depreciation - buildings',
         '631400', 'straight', 240, 0, 'Income Tax Act s. 28: 5% straight line'),
        ('intangible', 'Intangible assets (software, licences)', '254100', 'Intangible assets - software', '255200',
         'Accumulated amortisation - intangibles', '631600', 'straight', 120, 0, 'Income Tax Act s. 40: over the useful life'),
    ],
}
RULES['KE'] = RULES['ET']
# company income tax, for deferred tax on the difference between the books and the tax written-down values (IAS 12)
INCOME_TAX = {'ET': 30.0, 'UG': 30.0, 'KE': 30.0}  # until a Kenya set is written: same classes, rates to be set by the company
EXPENSED = ('631900', 'Low-value assets expensed')


def apply_rules(env, company):
    """Asset classes of the company's country, each on its own accounts (made where the chart lacks them). A class already
    there is brought to the country's rule while none of its assets has depreciation posted."""
    Acc = env['account.account'].sudo().with_company(company)
    Cat = env['sb.asset.category'].sudo()

    def account(code, name, kind):
        acc = Acc._sb_find(code, company)[:1]
        if not acc:
            acc = Acc.create({'code': Acc._sb_new_code(code, company), 'sb_legacy_code': code, 'name': name, 'account_type': kind,
                              'company_ids': [Command.set(company.ids)]})
            env.flush_all()  # the next new account is numbered after this one
        return acc

    expensed = account(EXPENSED[0], EXPENSED[1], 'expense')
    country = company.country_id.code or 'ET'
    for key, name, cost, cost_name, dep, dep_name, exp, method, life, rate, law in RULES.get(country, RULES['ET']):
        tax = {'law': law, 'tax_method': method, 'tax_rate': rate, 'tax_life_months': life}
        book = {'method': method, 'life_months': life, 'rate': rate}
        vals = {'name': name, 'company_id': company.id, 'asset_account_id': account(cost, cost_name, 'asset_fixed').id,
                'depreciation_account_id': account(dep, dep_name, 'asset_fixed').id,
                'expense_account_id': account(exp, 'Depreciation', 'expense_depreciation').id, 'expensed_account_id': expensed.id}
        cat = Cat.search([('company_id', '=', company.id), ('asset_account_id', '=', vals['asset_account_id'])], limit=1)
        if not cat:
            Cat.create(dict(vals, **tax, **book))  # the books start on the law's rate; the company may change them
        elif not env['sb.asset'].sudo().search_count([('category_id', '=', cat.id)]):
            cat.write(dict(vals, **tax, **book))
        else:
            cat.write(tax)  # the law follows the country; the books' terms stay as the company set them
