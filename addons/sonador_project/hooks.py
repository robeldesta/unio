CODES = [('LAB', 'Labour', 'labour'), ('MAT', 'Materials', 'material'), ('SUB', 'Subcontractors', 'subcontract'),
         ('EQP', 'Equipment and vehicles', 'equipment'), ('TRV', 'Travel and per diem', 'other'), ('OTH', 'Other costs', 'other')]


def _account(env, company, code):
    return env['account.account'].with_company(company)._sb_find(code, company)[:1]


def _retention(env, company):
    """The retention receivable account: an existing one by name, else a new one on the first free code (charts differ)."""
    A = env['account.account'].with_company(company)
    ret = A.search([('name', 'ilike', 'retention receivable'), ('company_ids', 'in', company.id)], limit=1)
    if ret:
        return ret
    for code in ('221950', env['account.account']._sb_new_code('221950', company), '1300-9900', 'RET-0001'):
        if not _account(env, company, code):
            return A.create({'code': code, 'name': 'Retention receivable on contracts', 'account_type': 'asset_current',
                             'company_ids': [(6, 0, company.ids)]})
    return A


def post_init_hook(env):
    """Default cost codes; job revenue, retention receivable and customer advance accounts from the Ethiopian chart."""
    C = env['sb.cost.code']
    for code, name, kind in CODES:
        if not C.search_count([('code', '=', code)]):
            C.create({'code': code, 'name': name, 'kind': kind, 'company_id': False})
    for company in env['res.company'].search([]):
        ret = _retention(env, company)
        company.write({'sb_job_income_account_id': company.sb_job_income_account_id.id or _account(env, company, '110000').id,
                       'sb_job_retention_account_id': company.sb_job_retention_account_id.id or ret.id,
                       'sb_job_advance_account_id': company.sb_job_advance_account_id.id or _account(env, company, '305900').id})
