from odoo import api

# code: (name, type, reconcile)
ACCOUNTS = {
    '511600': ('Inventory count gains and losses', 'expense_direct_cost', False),
}


def post_init_hook(env):
    for company in env['res.company'].search([]):
        setup_company(env, company)


def _account(env, company, code):
    return env['account.account'].with_company(company)._sb_find(code, company)[:1]


def setup_company(env, company):
    Account = env['account.account'].with_company(company)
    for code, (name, atype, reconcile) in ACCOUNTS.items():
        if not _account(env, company, code):
            Account.create({'code': Account._sb_new_code(code, company), 'sb_legacy_code': code, 'name': name,
                            'account_type': atype, 'reconcile': reconcile,
                            'company_ids': [(6, 0, company.ids)]})
    grni = _account(env, company, '305100')
    if grni and not grni.reconcile:
        grni.reconcile = True  # GRN credits are matched with the bill debits: what is left is received, not billed
    journal = env['account.journal'].search([('code', '=', 'STK'), ('company_id', '=', company.id)], limit=1)
    if not journal:
        journal = env['account.journal'].create({'name': 'Inventory', 'code': 'STK', 'type': 'general',
                                                 'company_id': company.id, 'show_on_dashboard': False})
    vals = {'account_stock_journal_id': journal.id}
    defaults = {'sb_grni_account_id': '305100', 'sb_stock_adjust_account_id': '511600',
                'sb_giv_account_id': '621800', 'sb_cost_difference_account_id': '511200'}
    for field, code in defaults.items():
        current = company[field].sudo()
        if current and not current.active:
            current.active = True  # a setting never points to an archived account: posting with it would fail
        if not current:
            vals[field] = _account(env, company, code).id
    company.write(vals)
    env['sb.adjust.reason']._sb_defaults(company)
    # where issued goods go (consumed by departments, jobs, production later)
    Location = env['stock.location']
    if not Location.search_count([('sb_giv_location', '=', True), ('company_id', '=', company.id)]):
        parent = env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
        Location.create({'name': 'Goods issued (GIV)', 'usage': 'inventory', 'sb_giv_location': True,
                         'location_id': parent.id if parent else False, 'company_id': company.id})
