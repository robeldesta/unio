"""Ethiopian employee record on hr.employee, and what hangs off it: emergency contacts, documents, issued assets.
Job and pay history are hr.version records (effective-dated), with the Ethiopian allowances on them.
The payroll profile (sb.employee, Accounting) is kept in step with the employee and its current version."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

PROBATION_WORKING_DAYS = 60  # Labour Proclamation 1156/2019, art. 11

REGIONS = [('addis_ababa', 'Addis Ababa'), ('dire_dawa', 'Dire Dawa'), ('afar', 'Afar'), ('amhara', 'Amhara'),
           ('benishangul', 'Benishangul-Gumuz'), ('central', 'Central Ethiopia'), ('gambela', 'Gambela'),
           ('harari', 'Harari'), ('oromia', 'Oromia'), ('sidama', 'Sidama'), ('somali', 'Somali'),
           ('south', 'South Ethiopia'), ('south_west', 'South West Ethiopia Peoples'), ('tigray', 'Tigray'),
           ('ug_kampala', 'Kampala'), ('ug_central', 'Central'), ('ug_eastern', 'Eastern'), ('ug_northern', 'Northern'),
           ('ug_western', 'Western')]

CHANGE_REASONS = [('hire', 'Hired'), ('promotion', 'Promotion'), ('transfer', 'Transfer'),
                  ('salary', 'Salary adjustment'), ('demotion', 'Demotion'), ('contract', 'Contract renewal'),
                  ('correction', 'Correction'), ('other', 'Other')]


class HrVersion(models.Model):
    _inherit = 'hr.version'

    sb_house_allowance = fields.Monetary('House allowance', tracking=True, groups='hr.group_hr_user')
    sb_position_allowance = fields.Monetary('Position allowance', tracking=True, groups='hr.group_hr_user')
    sb_cash_indemnity = fields.Monetary('Cash indemnity', tracking=True, groups='hr.group_hr_user',
                                        help='For staff who handle cash (cashiers, collectors): covers shortages they answer for.')
    sb_taxable_allowance = fields.Monetary('Other taxable allowances', tracking=True, groups='hr.group_hr_user',
                                           help='Monthly taxed allowances other than house, position and cash indemnity.')
    sb_nontaxable_allowance = fields.Monetary('Non-taxable allowances', tracking=True, groups='hr.group_hr_user',
                                              help='Monthly allowances within the tax-free limits (transport, ...).')
    sb_change_reason = fields.Selection(CHANGE_REASONS, string='Reason for change', groups='hr.group_hr_user')
    sb_grade = fields.Char('Grade', groups='hr.group_hr_user')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_code = fields.Char('Employee ID', copy=False, index=True, groups='hr.group_hr_user')
    sb_tin = fields.Char('TIN', groups='hr.group_hr_user', tracking=True)
    sb_pension_no = fields.Char('Pension no.', groups='hr.group_hr_user', tracking=True)
    sb_fayda = fields.Char('National ID no.', groups='hr.group_hr_user', tracking=True)
    sb_region = fields.Selection(REGIONS, string='Region', groups='hr.group_hr_user')
    sb_subcity = fields.Char('Sub-city / Zone', groups='hr.group_hr_user')
    sb_woreda = fields.Char('Woreda', groups='hr.group_hr_user')
    sb_kebele = fields.Char('Kebele', groups='hr.group_hr_user')
    sb_house_no = fields.Char('House no.', groups='hr.group_hr_user')
    sb_pension = fields.Boolean('In the pension scheme', default=True, groups='hr.group_hr_user',
                                help='Private Organization Employees Pension (Proclamation 715/2011): 7% + 11%.')
    sb_payroll_id = fields.Many2one('sb.employee', 'Payroll profile', copy=False, groups='hr.group_hr_user')
    sb_contact_ids = fields.One2many('sb.people.contact', 'employee_id', 'Emergency contacts', groups='hr.group_hr_user')
    sb_document_ids = fields.One2many('sb.people.document', 'employee_id', 'Documents', groups='hr.group_hr_user')
    sb_asset_ids = fields.One2many('sb.people.asset', 'employee_id', 'Assets issued', groups='hr.group_hr_user')

    @api.model
    def _sb_probation_end(self, start, calendar=None):
        """Last day of probation: 60 working days from the start (the company's working calendar)."""
        if not start:
            return False
        from odoo.addons.sonador_books.models.locale import hr_labels
        months = hr_labels(self.env.company).get('probation_months')
        if months:
            from dateutil.relativedelta import relativedelta
            return start + relativedelta(months=months, days=-1)
        calendar = calendar or self.env.company.resource_calendar_id
        if calendar:
            from datetime import datetime, time
            planned = calendar.plan_days(PROBATION_WORKING_DAYS, datetime.combine(start, time(8)), compute_leaves=True)
            if planned:
                return planned.date()
        return start + timedelta(days=PROBATION_WORKING_DAYS * 7 // 6)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('sb_code'):
                vals['sb_code'] = self.env['ir.sequence'].sudo().next_by_code('sb.people.employee')
        employees = super().create(vals_list)
        if not self.env.context.get('sb_no_payroll_sync'):
            employees.sudo()._sb_sync_payroll()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('sb_no_payroll_sync'):
            self.sudo()._sb_sync_payroll()
        return res

    def _sb_sync_payroll(self):
        """Keep the payroll profile in step: name, job, department, ids, bank, pay of the current version."""
        Payroll = self.env['sb.employee'].sudo().with_context(active_test=False)
        for e in self:
            v = e.current_version_id or e.version_id
            bank = e.primary_bank_account_id
            vals = {
                'name': e.name, 'company_id': e.company_id.id, 'job': e.job_title or e.job_id.name or False,
                'department': e.department_id.name or False, 'tin': e.sb_tin or False, 'pension_no': e.sb_pension_no or False,
                'pension': e.sb_pension, 'start_date': v.contract_date_start or False, 'end_date': e.departure_date or False,
                'bank_account': bank.acc_number if bank else False, 'user_id': e.user_id.id or False,
                'work_email': e.work_email or False, 'work_phone': e.work_phone or False, 'mobile': e.mobile_phone or False,
                'employee_type': e.employee_type if e.employee_type in ('employee', 'contractor', 'freelance', 'student', 'worker')
                else 'employee',
                'basic_salary': v.wage or 0.0, 'taxable_allowance': (v.sb_taxable_allowance or 0.0) + (v.sb_house_allowance or 0.0)
                + (v.sb_position_allowance or 0.0) + (v.sb_cash_indemnity or 0.0),
                'nontaxable_allowance': v.sb_nontaxable_allowance or 0.0, 'active': e.active,
            }
            payroll = e.sb_payroll_id
            if not payroll and not e.active:
                continue  # archived records without payroll (e.g. Odoo's default "Administrator") stay out of payroll
            if payroll:
                changed = {k: val for k, val in vals.items() if (payroll[k].id if hasattr(payroll[k], 'id') else payroll[k]) != val}
                if changed:
                    payroll.write(changed)
            else:
                payroll = Payroll.create(vals)
                e.with_context(sb_no_payroll_sync=True).sb_payroll_id = payroll
        managers = self.filtered(lambda x: x.sb_payroll_id)
        for e in managers:
            mgr = e.parent_id.sb_payroll_id
            if e.sb_payroll_id.manager_id != mgr:
                e.sb_payroll_id.manager_id = mgr


class HrVersionSync(models.Model):
    _inherit = 'hr.version'

    @api.model_create_multi
    def create(self, vals_list):
        versions = super().create(vals_list)
        if not self.env.context.get('sb_no_payroll_sync'):
            versions.employee_id.sudo()._sb_sync_payroll()
        return versions

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('sb_no_payroll_sync') and {'wage', 'sb_taxable_allowance', 'sb_nontaxable_allowance', 'sb_house_allowance',
                'sb_position_allowance', 'sb_cash_indemnity', 'job_id', 'job_title', 'department_id',
                'contract_date_start'} & set(vals):
            self.employee_id.sudo()._sb_sync_payroll()
        return res


class SbEmployee(models.Model):
    _inherit = 'sb.employee'

    hr_employee_id = fields.Many2one('hr.employee', compute='_compute_hr_employee', string='Employee (People)')

    def _compute_hr_employee(self):
        found = {e.sb_payroll_id.id: e for e in self.env['hr.employee'].sudo().with_context(active_test=False).search(
            [('sb_payroll_id', 'in', self.ids)])}
        for p in self:
            p.hr_employee_id = found.get(p.id, False)


class SbPeopleContact(models.Model):
    _name = 'sb.people.contact'
    _description = 'Emergency contact'
    _order = 'sequence, id'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    relationship = fields.Selection([('spouse', 'Spouse'), ('parent', 'Parent'), ('child', 'Child'), ('sibling', 'Sibling'),
                                     ('relative', 'Relative'), ('friend', 'Friend'), ('guarantor', 'Guarantor'),
                                     ('other', 'Other')], default='relative')
    phone = fields.Char()
    phone2 = fields.Char('Other phone')
    address = fields.Char()
    primary = fields.Boolean()


DOC_TYPES = [('fayda', 'National ID card'), ('kebele_id', 'Local ID / residence letter'), ('passport', 'Passport'),
             ('contract', 'Employment contract'), ('cv', 'CV'), ('degree', 'Degree / diploma'),
             ('certificate', 'Certificate'), ('experience', 'Experience letter'), ('guarantee', 'Guarantee (wass)'),
             ('work_permit', 'Work permit'), ('driving', 'Driving licence'), ('medical', 'Medical certificate'),
             ('tin', 'TIN certificate'), ('photo', 'Photo'), ('warning', 'Warning letter'), ('other', 'Other')]


class SbPeopleDocument(models.Model):
    _name = 'sb.people.document'
    _description = 'Employee document'
    _order = 'expiry_date, id'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    doc_type = fields.Selection(DOC_TYPES, string='Type', required=True, default='other')
    name = fields.Char('Number / title')
    issue_date = fields.Date('Issued')
    expiry_date = fields.Date('Expires')
    attachment_id = fields.Many2one('ir.attachment', 'File', ondelete='set null')
    note = fields.Char()
    state = fields.Selection([('valid', 'Valid'), ('expiring', 'Expiring'), ('expired', 'Expired'), ('none', 'No expiry')],
                             compute='_compute_state')

    @api.depends('expiry_date')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for d in self:
            if not d.expiry_date:
                d.state = 'none'
            elif d.expiry_date < today:
                d.state = 'expired'
            elif d.expiry_date <= today + relativedelta(days=60):
                d.state = 'expiring'
            else:
                d.state = 'valid'


class SbPeopleAsset(models.Model):
    _name = 'sb.people.asset'
    _description = 'Asset issued to an employee'
    _order = 'returned_on desc, issued_on desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    name = fields.Char('Item', required=True)
    category = fields.Selection([('laptop', 'Laptop / computer'), ('phone', 'Phone / SIM'), ('vehicle', 'Vehicle'),
                                 ('keys', 'Keys / access card'), ('uniform', 'Uniform / PPE'), ('tools', 'Tools'),
                                 ('other', 'Other')], default='other')
    serial = fields.Char('Serial / plate no.')
    issued_on = fields.Date(default=fields.Date.context_today)
    returned_on = fields.Date()
    value = fields.Float('Value')
    note = fields.Char()
