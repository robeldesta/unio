"""Country packs. The apps are built on one chart of accounts (the IFRS-mapped Ethiopian one, which is generic) and are
made right for a country by its pack: calendar and fiscal year, VAT and withholding, employment income tax bands and
the social-security pension. A database gets its pack once, at sign-up, before the apps are installed
(tools/new_company.sh); Ethiopia is the default."""
from odoo import Command, api, models

from .ethiopian_calendar import set_mode

LOCALES = {
    'ET': {
        'calendar': 'ethiopian', 'currency': 'ETB', 'vat': 15.0, 'wht': 3.0, 'wht_goods': 20000.0, 'wht_services': 10000.0,
        'pension_ee': 7.0, 'pension_er': 11.0, 'pension_name': 'Pension (POESSA)', 'tz': 'Africa/Addis_Ababa',
        'tax_authority': 'Ministry of Revenue', 'pension_fund': 'POESSA (Private Organization Employees Social Security Agency)',
        'hr': {'national_id': 'Fayda ID', 'pension_no': 'Pension (POESSA) no.', 'addr': ['Sub-city / Zone', 'Woreda', 'Kebele', 'House no.'],
               'labour_law': 'Labour Proclamation 1156/2019', 'probation_months': 0,
               'probation_note': 'Probation: 60 working days (Labour Proclamation 1156/2019, art. 11) on the 48-hour week, set automatically.'},
    },
    'UG': {
        # Uganda: companies keep calendar-year books (1 January - 31 December); VAT Act 18%; withholding 6% (Income Tax Act s.119, payments over 1m);
        # PAYE, resident individuals, monthly (Income Tax Act, 3rd Schedule): quick-calculation form
        'calendar': 'gregorian:1', 'currency': 'UGX', 'vat': 18.0, 'wht': 6.0, 'wht_goods': 1000000.0, 'wht_services': 1000000.0,
        'brackets': [(0, 235000, 0, 0), (235000, 335000, 10, 23500), (335000, 410000, 20, 57000),
                     (410000, 10000000, 30, 98000), (10000000, 0, 40, 1098000)],
        'pension_ee': 5.0, 'pension_er': 10.0, 'pension_name': 'NSSF', 'tz': 'Africa/Kampala',
        'tax_authority': 'Uganda Revenue Authority (URA)', 'pension_fund': 'National Social Security Fund (NSSF)', 'pension_base': 'gross',
        'fy_last': ('12', 31),
        'hr': {'national_id': 'National ID (NIN)', 'pension_no': 'NSSF no.', 'addr': ['District', 'Sub-county', 'Parish / Village', 'Plot no.'],
               'labour_law': 'Employment Act 2006', 'probation_months': 6,
               'probation_note': 'Probation: up to six months (Employment Act 2006, s. 67), set automatically.'},
        'regions': [('ug_kampala', 'Kampala'), ('ug_central', 'Central'), ('ug_eastern', 'Eastern'), ('ug_northern', 'Northern'),
                    ('ug_western', 'Western')],
        'po_bands': (5000000, 50000000, 200000000),  # purchase approval: store, finance, management, general manager
        # Local Service Tax (Local Governments Act, 2nd Schedule as amended): yearly amount by monthly income, collected by
        # the employer in four instalments in the first four months of the government year (July - October)
        'lst': [(100000, 0), (200000, 5000), (300000, 10000), (400000, 20000), (500000, 30000), (600000, 40000), (700000, 60000),
                (800000, 70000), (900000, 80000), (1000000, 90000), (0, 100000)],
        'lst_months': (7, 8, 9, 10),
        'banks_hint': 'One account per bank (Stanbic, Centenary, dfcu ...), USD accounts, MTN MoMo and Airtel Money, petty cash.',
        'tax_hint': 'Check the withholding rate (6%) and thresholds; VAT 18% and EFRIS are set by the Uganda pack.',
    },
    'KE': {
        'calendar': 'gregorian:7', 'currency': 'KES', 'vat': 16.0, 'wht': 0.0, 'wht_goods': 0.0, 'wht_services': 0.0,
        'pension_ee': 6.0, 'pension_er': 6.0, 'pension_name': 'NSSF', 'tz': 'Africa/Nairobi', 'fy_last': ('6', 30),
        'tax_authority': 'Kenya Revenue Authority (KRA)', 'pension_fund': 'National Social Security Fund (NSSF)', 'pension_base': 'gross',
        'hr': {'national_id': 'National ID no.', 'pension_no': 'NSSF no.', 'addr': ['County', 'Sub-county', 'Ward', 'Plot no.'],
               'labour_law': 'Employment Act 2007', 'probation_months': 6,
               'probation_note': 'Probation: up to six months (Employment Act 2007, s. 42), set automatically.'},
        'po_bands': (200000, 2000000, 10000000),
        'banks_hint': 'One account per bank (KCB, Equity, Co-op ...), USD accounts, M-Pesa till, petty cash.',
        'tax_hint': 'Check the withholding rate and thresholds; VAT 16% is set by the Kenya pack.',
    },
}


def locale_of(company):
    return LOCALES.get(company.country_id.code or 'ET', LOCALES['ET'])


OVERTIME = {
    # rates per category (working day, night, weekly rest day, public holiday) and limits on overtime minutes
    'ET': {'rates': {'ot_day': 1.5, 'ot_night': 1.75, 'ot_rest': 2.0, 'ot_holiday': 2.5}, 'limit_day': 240, 'limit_week': 720,
           'law': 'Labour Proclamation 1156/2019, art. 66-68',
           'note': '6 am-10 pm 1.5x, 10 pm-6 am 1.75x, weekly rest day 2x, public holiday 2.5x; at most 4 hours a day and 12 a week.'},
    # Uganda, Employment Regulations 2011: overtime 1.5x, public holidays and rest days 2x; at most 10 hours a day and
    # 56 a week including overtime (2 hours a day, 8 a week over the normal 48)
    'UG': {'rates': {'ot_day': 1.5, 'ot_night': 1.5, 'ot_rest': 2.0, 'ot_holiday': 2.0}, 'limit_day': 120, 'limit_week': 480,
           'law': 'Employment Act 2006 and Employment Regulations 2011',
           'note': 'Overtime 1.5x (night too), rest day and public holiday 2x; at most 10 hours a day and 56 a week including overtime.'},
}


def overtime_rules(company):
    return OVERTIME.get(company.country_id.code or 'ET', OVERTIME['ET'])


def hr_labels(company):
    return dict(LOCALES['ET']['hr'], **locale_of(company).get('hr', {}))


class SbLocale(models.AbstractModel):
    _name = 'sb.locale'
    _description = 'Country pack'

    @api.model
    def set_calendar(self, value):
        """Before the apps are installed: the calendar every date, fiscal year and period follows."""
        self.env['ir.config_parameter'].sudo().set_param('sonador.calendar', value)
        set_mode(self.env.cr.dbname, value)

    @api.model
    def apply(self, company=None):
        """After the apps are installed: taxes, withholding, payroll and pension for the company's country."""
        company = company or self.env.company
        code = company.country_id.code or 'ET'
        loc = locale_of(company)
        self._sb_day_first_dates()
        if code == 'ET':
            return False
        country = company.country_id
        Tax = self.env['account.tax'].sudo().with_context(active_test=False)
        taxes = Tax.search([('company_id', '=', company.id)])
        for t in taxes:
            if t.amount_type == 'percent' and t.amount == 15.0:
                t.write({'amount': loc['vat'], 'name': 'VAT %g%%' % loc['vat'] + (' (purchases)' if t.type_tax_use == 'purchase' else ''),
                         'description': 'VAT %g%%' % loc['vat']})
            elif t.amount != 0.0:
                t.active = False  # Ethiopian turnover tax, excise and the like
        taxes.write({'country_id': country.id})
        self.env['account.tax.group'].sudo().search([('company_id', '=', company.id)]).write({'country_id': country.id})
        vals = {'account_fiscal_country_id': country.id}
        cur = self.env['res.currency'].sudo().with_context(active_test=False).search([('name', '=', loc['currency'])], limit=1)
        if cur and company.currency_id != cur:
            cur.active = True
            vals['currency_id'] = cur.id  # the chart load puts birr back
        if loc.get('fy_last'):
            vals.update(fiscalyear_last_month=loc['fy_last'][0], fiscalyear_last_day=loc['fy_last'][1])
        for f, k in (('sb_wht_rate', 'wht'), ('sb_wht_goods_threshold', 'wht_goods'), ('sb_wht_service_threshold', 'wht_services'),
                     ('sb_pension_employee', 'pension_ee'), ('sb_pension_employer', 'pension_er')):
            if f in company._fields:
                vals[f] = loc[k]
        company.sudo().write(vals)
        if loc.get('brackets') and 'sb.payroll.bracket' in self.env:
            B = self.env['sb.payroll.bracket'].sudo()
            B.search([('company_id', '=', company.id)]).unlink()
            for lo, up, rate, ded in loc['brackets']:
                B.create({'company_id': company.id, 'lower': lo, 'upper': up, 'rate': rate, 'deduction': ded})
        # controls on by default: no overdrawn cash or bank accounts, credit hold after 30 days overdue
        self.env['account.account'].sudo().search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash')]).write(
            {'sb_overdraft_control': True})
        if 'sb_overdue_hold_days' in company._fields and not company.sb_overdue_hold_days:
            company.sudo().sb_overdue_hold_days = 30
        if code == 'UG' and 'sb.leave.type' in self.env:
            # Employment Act 2006: annual s. 54, sick s. 55, maternity 60 working days s. 56, paternity 4 working days s. 57
            T = self.env['sb.leave.type'].sudo().with_context(active_test=False)
            for kind, vals in (('annual', {'legal_ref': 'Employment Act s. 54'}),
                               ('sick', {'legal_ref': 'Employment Act s. 55'}),
                               ('maternity_post', {'name': 'Maternity leave', 'unit': 'working', 'max_days': 60, 'legal_ref': 'Employment Act s. 56'}),
                               ('maternity_pre', {'active': False}),
                               ('paternity', {'unit': 'working', 'max_days': 4, 'legal_ref': 'Employment Act s. 57'})):
                T.search([('kind', '=', kind)]).write({k: v for k, v in vals.items() if k in T._fields})
            T.search([('kind', 'not in', ('annual', 'sick', 'maternity_post', 'paternity'))]).write({'legal_ref': False})
        if loc.get('tz'):
            self.env['res.users'].sudo().search([('share', '=', False)]).write({'tz': loc['tz']})
        if loc.get('po_bands') and 'sb.po.approval.rule' in self.env:
            self._apply_po_bands(company, loc['po_bands'])
        self._apply_foreign_position(company)
        return True

    @api.model
    def _sb_day_first_dates(self):
        """Every country with a pack (Ethiopia, Uganda, Kenya) writes dates day first: 10/05/2026 is 10 May, never
        5 October. The languages users work in (English (US) included) show and read dates as dd/mm/yyyy.
        Run at every update and when a pack is applied, so existing and new companies get it."""
        langs = self.env['res.lang'].sudo().search([('active', '=', True)])
        for lang in langs.filtered(lambda l: (l.date_format or '').startswith('%m')):
            lang.date_format = '%d/%m/%Y'
        return True

    def _apply_foreign_position(self, company):
        """Foreign customers and suppliers: no local VAT on their invoices (imports pay VAT at customs, exports are zero-rated),
        applied by itself to every contact outside the company's country."""
        FP = self.env['account.fiscal.position'].sudo()
        if FP.search_count([('company_id', '=', company.id), ('name', '=', 'Foreign (imports and exports)')]):
            return
        Tax = self.env['account.tax'].sudo()
        others = self.env['res.country'].sudo().search([('id', '!=', company.country_id.id)])
        group = self.env['res.country.group'].sudo().create({'name': 'Outside %s' % company.country_id.name, 'country_ids': [Command.set(others.ids)]})
        fp = FP.create({'name': 'Foreign (imports and exports)', 'company_id': company.id, 'auto_apply': True, 'country_group_id': group.id, 'sequence': 90})
        for use in ('purchase', 'sale'):
            src = Tax.search([('company_id', '=', company.id), ('type_tax_use', '=', use), ('amount', '>', 0), ('amount_type', '=', 'percent')])
            dest = Tax.search([('company_id', '=', company.id), ('type_tax_use', '=', use), ('amount', '=', 0), ('name', 'ilike', 'out')], limit=1)
            if dest and src:  # Odoo 19: the replacing tax belongs to the position and names the taxes it replaces
                dest.write({'fiscal_position_ids': [Command.link(fp.id)], 'original_tax_ids': [Command.link(t.id) for t in src]})
        return fp

    def _apply_po_bands(self, company, bands):
        """Purchase approval bands in the country's money (the shipped ones are in birr), with the approver groups named to match."""
        a, b, c = bands
        fmt = lambda x: '{:,.0f}'.format(x)  # noqa: E731
        for xid, lo, up, label in (('po_rule_1', 0, a, 'Purchase Approver: up to %s' % fmt(a)),
                                   ('po_rule_2', a, b, 'Purchase Approver: up to %s' % fmt(b)),
                                   ('po_rule_3', b, c, 'Purchase Approver: up to %s' % fmt(c)),
                                   ('po_rule_4', c, 0, False)):
            rule = self.env.ref('sonador_purchase.%s' % xid, raise_if_not_found=False)
            if not rule:
                continue
            rule.sudo().write({'amount_from': lo, 'amount_to': up, 'company_id': company.id})
            if label:
                rule.group_id.sudo().name = label
        top = self.env.ref('sonador_purchase.group_sb_po_gm', raise_if_not_found=False)
        if top:
            top.sudo().comment = 'Approves purchase orders in the highest band (over %s).' % fmt(c)
