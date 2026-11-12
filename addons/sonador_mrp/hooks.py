ACCOUNTS = {
    # code: (name, type)  - credited when production absorbs labour and overhead into stock
    '511510': ('Direct labour absorbed into production', 'expense_direct_cost'),
    '511520': ('Manufacturing overhead absorbed into production', 'expense_direct_cost'),
}


def post_init_hook(env):
    setup_precision(env)
    for company in env['res.company'].search([]):
        setup_company(env, company)


def setup_precision(env):
    # recipes need small quantities (grams of cover per litre, rolls of tape per batch): at least 4 decimals
    prec = env.ref('uom.decimal_product_uom', raise_if_not_found=False) or env['decimal.precision'].search([('name', '=', 'Product Unit')], limit=1)
    if prec and prec.digits < 4:
        prec.digits = 4


def setup_company(env, company):
    Account = env['account.account'].with_company(company)
    found = {}
    for code, (name, atype) in ACCOUNTS.items():
        acc = Account._sb_find(code, company)[:1] \
            or Account.create({'code': Account._sb_new_code(code, company), 'sb_legacy_code': code, 'name': name, 'account_type': atype, 'company_ids': [(6, 0, company.ids)]})
        found[code] = acc
    vals = {}
    if not company.sb_labour_absorbed_account_id:
        vals['sb_labour_absorbed_account_id'] = found['511510'].id
    if not company.sb_overhead_absorbed_account_id:
        vals['sb_overhead_absorbed_account_id'] = found['511520'].id
    company.write(vals)
    # production must not post Odoo's own entries: the voucher posts the build on the voucher date
    env['stock.location'].search([('usage', '=', 'production'), ('company_id', 'in', (company.id, False))]).write({'valuation_account_id': False})
