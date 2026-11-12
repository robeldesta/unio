"""The Ethiopian chart of accounts layout (as Peachtree-kept Ethiopian companies, e.g. Eco Green, number it):
one digit per family, four-digit groups, codes like 1104-0001.

  1101 cash · 1102 cash clearing · 1104 banks · 1200 staff debtors · 1300 prepayments and advances · 1400 trade debtors
  1401 tax receivables · 1500-1505 inventory · 1600 goods in transit · 1700 fixed assets · 1701 accumulated depreciation
  1800 construction in progress and intangibles · 1900 other receivables
  2000 payables · 2100 taxes and statutory payables · 2200 long-term liabilities · 3000 equity
  4000 sales · 4100 other income · 5000 cost of sales · 6000 production overhead
  8100 selling expenses · 8200 administration expenses · 9000 finance costs and tax

Companies set up on the generic six-digit chart are renumbered once (renumber()); every account keeps its balances and
entries, its old number stays in sb_legacy_code, and code that looks an account up by its old number finds it
(account.account._sb_find)."""
import re

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

# (old six-digit code prefixes, new group); the first match wins
RULES = [
    (('1109',), '4000'), (('11',), '4000'), (('12',), '4100'),
    (('211000', '211200'), '1101'),
    (('211002', '211003', '211004', '211100', '211300', '212001'), '1102'),
    (('2102', '2104', '2110'), '1104'),
    (('220300',), '1200'),
    (('2216', '2217', '2218', '2251', '2252', '2253'), '1300'),
    (('2211', '2215', '2219'), '1400'),
    (('2212', '2213', '2214'), '1401'),
    (('2201', '2202', '2204', '2254', '2255', '2571'), '1900'),
    (('2301',), '1600'),
    (('235100', '235200'), '1501'), (('2359',), '1500'), (('2411',), '1505'), (('2412',), '1504'),
    (('2501', '2503', '2541'), '1800'),
    (('2551', '2552', '2562'), '1701'),
    (('25',), '1700'),
    (('3003', '3006', '3007', '3008', '3009', '3057', '3066'), '2100'),
    (('3153', '3161', '3162'), '2200'),
    (('30', '31'), '2000'),
    (('4', '999999'), '3000'),
    (('5902', '5921'), '8200'), (('5903',), '9000'),
    (('5',), '5000'),
    (('6253', '6255'), '8100'),
    (('626', '641', '643', '644', '999002'), '9000'),
    (('999001',), '4100'),
    (('6',), '8200'),
]


def group_of(code):
    code = re.sub(r'\D', '', code or '')
    for prefixes, group in RULES:
        if any(code.startswith(p) for p in prefixes):
            return group
    return None


class AccountAccount(models.Model):
    _inherit = 'account.account'

    sb_legacy_code = fields.Char('Former number', index=True, company_dependent=False, copy=False,
                                 help='The number before the chart was renumbered to the Ethiopian layout.')

    sb_hidden_template = fields.Boolean(
        'Left out of pickers', copy=False, readonly=True,
        help='An account of the standard chart this company never used (no entries, not a setting): it is not offered '
             'when choosing an account. It stays in the chart of accounts.')

    def _compute_display_name(self):
        """Accounts always show their number ("6000-0011 Printing and Stationery"), for a storekeeper or a clerk too
        (Odoo shows it to accountants only): two accounts of the same name are told apart by it."""
        super()._compute_display_name()
        if self.env.user.has_group('account.group_account_readonly'):
            return
        for account in self:
            name = (account.display_name or '').lstrip()
            if account.code and not name.startswith(account.code):
                account.display_name = '%s %s' % (account.code, name)

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        # an empty picker (no text typed yet) leaves out the standard chart's idle accounts too
        if not name and not self.env.context.get('sb_show_all_accounts'):
            domain = Domain.AND([domain or [], [('sb_hidden_template', '=', False)]])
        return super().name_search(name, domain, operator, limit)

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if self.env.context.get('sb_show_all_accounts'):
            return domain
        return Domain.AND([[('sb_hidden_template', '=', False)], domain])

    def write(self, vals):
        if 'active' in vals and not vals['active']:
            for account in self.filtered('active'):
                used = account._sb_used_as_setting()
                if used:
                    raise UserError(self.env._(
                        '%(acc)s cannot be archived: it is used as "%(setting)s". Choose another account there first '
                        '(Setup > Settings), then archive it.', acc=account.display_name, setting=used))
        return super().write(vals)

    def _sb_used_as_setting(self):
        """The label of the first company setting or journal that uses this account, or ''."""
        self.ensure_one()
        Company = self.env['res.company'].sudo()
        for name, field in Company._fields.items():
            if field.type == 'many2one' and field.comodel_name == 'account.account' and field.store \
                    and not field.company_dependent and Company.search_count([(name, '=', self.id)], limit=1):
                return field.string
        journal = self.env['account.journal'].sudo().search([('default_account_id', '=', self.id)], limit=1)
        return self.env._('Account of the %s journal', journal.name) if journal else ''

    @api.model
    def _sb_hide_unused_template_accounts(self):
        """Run on every update. A company keeping its own chart (Eco Green: 1104-0002 ...) does not see the accounts the
        standard chart and the setup added for everyone (110100 Software licence revenue ...) that it never used: one with
        no entry that nothing points to (a journal, a tax, a setting, an item, a voucher ...) is archived. A company
        working on the standard chart keeps all of them."""
        from ..hooks import NEW_ACCOUNTS
        cr = self.env.cr
        Account = self.sudo().with_context(active_test=False)
        self._sb_restore_setting_accounts()
        for company in self.env['res.company'].sudo().search([]):
            accounts = Account.search([('company_ids', 'in', company.id), ('active', '=', True)])
            templated = set(self.env['ir.model.data'].sudo().search(
                [('model', '=', 'account.account'), ('module', '=', 'account'), ('res_id', 'in', accounts.ids)]).mapped('res_id'))
            template = accounts.filtered(lambda a: a.id in templated or a.code in NEW_ACCOUNTS or a.sb_legacy_code in NEW_ACCOUNTS)
            cr.execute('SELECT DISTINCT account_id FROM account_move_line WHERE company_id = %s', (company.id,))
            used = {r[0] for r in cr.fetchall()}
            own_used = len([a for a in (accounts - template).ids if a in used])
            if own_used < 20:
                continue  # the company works on the standard chart
            looked_up = self._sb_codes_in_code()
            # the standard chart's unused accounts the apps still look up by number, or that a category points to, stay
            # active but are left out of the account pickers: the company sees its own chart only
            # a setting or a journal pointing to one does not bring it back into the pickers (the setting keeps working;
            # Setup shows it): the company should point those settings at its own accounts
            hide = template.filtered(lambda a: a.id not in used)
            (accounts - hide).filtered('sb_hidden_template').write({'sb_hidden_template': False})
            hide.filtered(lambda a: not a.sb_hidden_template).write({'sb_hidden_template': True})
            candidates = template.filtered(lambda a: a.id not in used and not ({a.code, a.sb_legacy_code} & looked_up))
            if candidates:
                candidates -= candidates.browse(self._sb_referenced_accounts(candidates.ids))
            if candidates:
                super(AccountAccount, candidates).write({'active': False})

    @api.model
    def _sb_codes_in_code(self):
        """Account numbers the apps look up by number (_sb_find('120400') for finance charges ...): kept even when unused.
        The list of accounts the setup adds for every company (NEW_ACCOUNTS) does not count as a look-up."""
        import os
        from odoo.modules.module import get_module_path
        codes = set()
        # during an update the modules being updated are 'to upgrade', not 'installed': they count too
        mods = self.env['ir.module.module'].sudo().search([('state', 'in', ('installed', 'to upgrade', 'to install')),
                                                           ('name', '=like', 'sonador%')])
        for mod in mods:
            path = get_module_path(mod.name)
            if not path:
                continue
            for sub in ('models', 'wizard', 'hooks.py', 'controllers'):
                target = os.path.join(path, sub)
                files = [target] if target.endswith('.py') else [os.path.join(d, f) for d, _x, fs in os.walk(target)
                                                                for f in fs if f.endswith('.py') and not f.startswith('._')]
                for fname in files:
                    if not os.path.isfile(fname):
                        continue
                    with open(fname, encoding='utf-8', errors='replace') as fh:  # stray non-UTF-8 files (macOS ._ copies)
                        text = fh.read()
                    if fname.endswith('hooks.py'):
                        text = re.sub(r'NEW_ACCOUNTS = \{.*?\n\}', '', text, flags=re.S)
                    codes |= set(re.findall(r"['\"](\d{6})['\"]", text))
        return codes

    @api.model
    def _sb_referenced_accounts(self, ids):
        """Of these accounts, the ones any record points to (many2one, many2many, company-dependent values, defaults)."""
        cr = self.env.cr
        found = set(self._sb_fk_referenced(ids))
        fields_ = self.env['ir.model.fields'].sudo().search([('relation', '=', 'account.account'), ('store', '=', True),
                                                             ('ttype', 'in', ('many2one', 'many2many'))])
        for f in fields_:
            Model = self.env.get(f.model)
            if Model is None or Model._abstract or not Model._auto or f.name not in Model._fields:
                continue
            field = Model._fields[f.name]
            if field.type == 'many2one' and not field.company_dependent and field.store:
                cr.execute('SELECT DISTINCT "%s" FROM "%s" WHERE "%s" = ANY(%%s)' % (f.name, Model._table, f.name), (ids,))
            elif field.type == 'many2one' and field.company_dependent:
                cr.execute('SELECT DISTINCT (v.value)::int FROM "%s" t, jsonb_each_text(t."%s") v '
                           'WHERE v.value ~ \'^[0-9]+$\' AND (v.value)::int = ANY(%%s)' % (Model._table, f.name), (ids,))
            elif field.type == 'many2many' and field.store:
                cr.execute('SELECT DISTINCT "%s" FROM "%s" WHERE "%s" = ANY(%%s)' % (field.column2, field.relation, field.column2), (ids,))
            else:
                continue
            found |= {r[0] for r in cr.fetchall()}
        cr.execute("SELECT json_value FROM ir_default d JOIN ir_model_fields f ON f.id = d.field_id WHERE f.relation = 'account.account'")
        for (value,) in cr.fetchall():
            if str(value).strip('"').isdigit():
                found.add(int(str(value).strip('"')))
        return [i for i in ids if i in found]

    @api.model
    def _sb_fk_columns(self):
        """Every database column pointing to an account (from the database's own foreign keys): it does not depend on
        which apps are loaded yet, so an update of one app never misses a setting another app keeps (the GRNI account)."""
        self.env.cr.execute("""
            SELECT cl.relname, a.attname FROM pg_constraint c
              JOIN pg_class cl ON cl.oid = c.conrelid
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
             WHERE c.contype = 'f' AND c.confrelid = 'account_account'::regclass AND array_length(c.conkey, 1) = 1""")
        return self.env.cr.fetchall()

    @api.model
    def _sb_fk_referenced(self, ids, tables=None):
        found = set()
        for table, column in self._sb_fk_columns():
            if table == 'account_move_line' or (tables and table not in tables):
                continue
            self.env.cr.execute('SELECT DISTINCT "%s" FROM "%s" WHERE "%s" = ANY(%%s)' % (column, table, column), (list(ids),))
            found |= {r[0] for r in self.env.cr.fetchall()}
        return found

    @api.model
    def _sb_restore_setting_accounts(self):
        """An account a company setting or a journal points to (goods received not invoiced, the issue account ...) is
        never left archived: posting with it would fail."""
        archived = self.sudo().with_context(active_test=False).search([('active', '=', False)])
        if archived:
            keep = self._sb_fk_referenced(archived.ids, tables=('res_company', 'account_journal'))
            if keep:
                archived.browse(list(keep)).write({'active': True})

    @api.model
    def _sb_find(self, codes, company=None, prefix=False):
        """Accounts by number, old or new: _sb_find('611100') finds 8200-0003 after renumbering."""
        company = company or self.env.company
        codes = [codes] if isinstance(codes, str) else list(codes)
        op = '=like' if prefix else 'in'
        vals = [c + '%' for c in codes] if prefix else codes
        if prefix:
            dom = ['|'] * (2 * len(vals) - 1)
            for v in vals:
                dom += [('code', '=like', v), ('sb_legacy_code', '=like', v)]
        else:
            dom = ['|', ('code', 'in', vals), ('sb_legacy_code', 'in', vals)]
        return self.search([('company_ids', 'in', company.id)] + dom)

    @api.model
    def _sb_new_code(self, legacy, company=None):
        """The number a new account gets: the old-style number on the generic chart, the next in its group on the
        Ethiopian layout."""
        company = company or self.env.company
        if not self._sb_renumbered(company):
            return legacy
        group = group_of(legacy) or '8200'
        return self._sb_next(group, company)

    def _sb_renumbered(self, company):
        """True once the company's chart was renumbered (an account carries a former number different from its own)."""
        return any(a.code != a.sb_legacy_code for a in self.with_company(company).search(
            [('company_ids', 'in', company.id), ('sb_legacy_code', '!=', False)], limit=20))

    def _sb_next(self, group, company):
        # codes may be kept with or without the hyphen (the company's code style): 1701-0004 and 17010004 are the same
        used = self.with_company(company).search([('company_ids', 'in', company.id), ('code', '=like', group + '%')]).mapped('code')
        nums = [int(m.group(1)) for m in (re.fullmatch(group + r'-?(\d{4})', c or '') for c in used) if m]
        return '%s-%04d' % (group, max(nums or [0]) + 1)

    @api.model
    def sb_renumber_et(self, company=None):
        """Renumber the generic six-digit chart of an Ethiopian company to the Ethiopian layout. Idempotent."""
        company = company or self.env.company
        accounts = self.with_company(company).search([('company_ids', 'in', company.id)], order='code')
        todo = [a for a in accounts if re.fullmatch(r'\d{6}', a.code or '') and group_of(a.code)]
        done = 0
        for a in todo:
            legacy = a.code
            a.with_company(company).write({'code': self._sb_next(group_of(legacy), company), 'sb_legacy_code': legacy})
            done += 1
        return done
