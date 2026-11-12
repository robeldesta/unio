"""Install: Ethiopian working week and reasons for leaving, then every payroll employee (sb.employee) becomes an
Odoo employee (hr.employee) with its first job / pay version, linked to its payroll profile."""
from datetime import date

from odoo import fields

ETHIOPIAN_REASONS = [
    'Resignation (art. 31-32)', 'Termination with notice (art. 28)', 'Termination without notice (art. 27)',
    'Reduction of workforce (art. 29)', 'End of fixed-term contract', 'Not confirmed after probation',
    'Retirement', 'Death', 'Mutual agreement']


def _calendar(env):
    """Labour Proclamation 1156/2019: 8 hours a day, 48 hours a week (Monday to Saturday)."""
    Cal = env['resource.calendar']
    cal = Cal.search([('name', '=', 'Ethiopia: 48 hours (Mon-Sat)')], limit=1)
    if not cal:
        att = []
        for day in range(6):
            att.append((0, 0, {'name': 'Morning', 'dayofweek': str(day), 'hour_from': 8.0, 'hour_to': 12.0, 'day_period': 'morning'}))
            att.append((0, 0, {'name': 'Afternoon', 'dayofweek': str(day), 'hour_from': 13.0, 'hour_to': 17.0, 'day_period': 'afternoon'}))
        cal = Cal.create({'name': 'Ethiopia: 48 hours (Mon-Sat)', 'hours_per_day': 8.0, 'attendance_ids': att,
                          'tz': 'Africa/Addis_Ababa'})
    for company in env['res.company'].search([]):
        if not company.resource_calendar_id or company.resource_calendar_id.name.startswith('Standard 40'):
            company.resource_calendar_id = cal
    return cal


def _reasons(env):
    R = env['hr.departure.reason']
    for i, name in enumerate(ETHIOPIAN_REASONS):
        if not R.search([('name', '=', name)], limit=1):
            R.create({'name': name, 'sequence': 20 + i})


def migrate_payroll_employees(env):
    E = env['hr.employee'].with_context(active_test=False, sb_no_payroll_sync=True, tracking_disable=True,
                                         mail_create_nolog=True)
    P = env['sb.employee'].with_context(active_test=False)
    People = env['sb.people']
    linked = set(E.search([('sb_payroll_id', '!=', False)]).mapped('sb_payroll_id').ids)
    made = {}
    for company in env['res.company'].search([]):
        default_day = company.sb_books_start or fields.Date.context_today(env.user)
        cal = company.resource_calendar_id
        for p in P.search([('company_id', '=', company.id)], order='id'):
            if p.id in linked:
                continue
            P2 = People.with_company(company)
            job = P2._job(p.job) if p.job else env['hr.job']
            dept = P2._department(p.department) if p.department else env['hr.department']
            day = p.start_date or (p.end_date if p.end_date and p.end_date < default_day else default_day)
            user = p.user_id if p.user_id and not E.search([('user_id', '=', p.user_id.id), ('company_id', '=', company.id)]) else False
            e = E.with_company(company).create({
                'name': p.name, 'company_id': company.id, 'job_id': job.id or False, 'job_title': p.job or False,
                'department_id': dept.id or False, 'work_email': p.work_email or False, 'work_phone': p.work_phone or False,
                'mobile_phone': p.mobile or False, 'sb_tin': p.tin or False, 'sb_pension_no': p.pension_no or False,
                'sb_pension': p.pension, 'user_id': user.id if user else False,
                'employee_type': p.employee_type or 'employee', 'sb_payroll_id': p.id,
                'date_version': day, 'contract_date_start': p.start_date or False, 'wage': p.basic_salary or 0.0,
                'resource_calendar_id': cal.id if cal else False})
            e.version_id.write({'sb_taxable_allowance': p.taxable_allowance, 'sb_nontaxable_allowance': p.nontaxable_allowance,
                                'sb_change_reason': 'hire'})
            if p.bank_account:
                partner = e.work_contact_id or env['res.partner'].create({'name': p.name})
                if not e.work_contact_id:
                    e.work_contact_id = partner
                acc = env['res.partner.bank'].search([('acc_number', '=', p.bank_account), ('partner_id', '=', partner.id)], limit=1) \
                    or env['res.partner.bank'].create({'acc_number': p.bank_account, 'partner_id': partner.id})
                e.bank_account_ids = [(4, acc.id)]
            if p.end_date or not p.active:
                e.write({'departure_date': p.end_date or False})
                e.active = False
            made[p.id] = e
    for pid, e in made.items():
        mgr = P.browse(pid).manager_id
        if mgr and mgr.id in made and made[mgr.id] != e:
            e.parent_id = made[mgr.id]
    return len(made)


def _archive_default_admin(env):
    """Installing Odoo HR creates an employee called after the administrator login: not a person on the payroll."""
    admin = env.ref('hr.employee_admin', raise_if_not_found=False)
    if admin and admin.active and not admin.sb_payroll_id and admin.name in ('Administrator', 'Mitchell Admin'):
        admin.with_context(sb_no_payroll_sync=True).active = False


def post_init_hook(env):
    _archive_default_admin(env)
    _calendar(env)
    _reasons(env)
    n = migrate_payroll_employees(env)
    env['sb.people']._hide_odoo_hr()
    return n
