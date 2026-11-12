def _expense(env, company, words):
    A = env['account.account'].with_company(company)
    for w in words:
        accs = A.search([('name', 'ilike', w), ('company_ids', 'in', company.id),
                         ('account_type', 'in', ('expense', 'expense_direct_cost', 'expense_other'))])
        if accs:
            # the one the books use most
            used = dict(env['account.move.line'].sudo()._read_group([('account_id', 'in', accs.ids)], ['account_id'], ['__count']))
            return max(accs, key=lambda a: used.get(a, 0))
    return A


def post_init_hook(env):
    """Per diem and fuel expense accounts from the chart, when it has them."""
    for company in env['res.company'].search([]):
        company.write({
            'sb_fleet_perdiem_account_id': company.sb_fleet_perdiem_account_id.id or _expense(env, company, ['per diem', 'perdiem', 'travel']).id,
            'sb_fleet_fuel_account_id': company.sb_fleet_fuel_account_id.id or _expense(env, company, ['fuel', 'vehicle running', 'vehicle']).id,
        })
