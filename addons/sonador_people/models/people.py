"""People app service: the OWL screens (home, directory, org chart, profile) read and write through here.
HR officers see and change everything; every employee sees the directory and their own record (self-service);
managers see their team's job details."""
import base64
from collections import Counter, defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .employee import CHANGE_REASONS, DOC_TYPES, REGIONS
from odoo.addons.sonador_books.models.locale import hr_labels, locale_of

SELF_FIELDS = {'private_phone', 'private_email', 'sb_region', 'sb_subcity', 'sb_woreda', 'sb_kebele', 'sb_house_no',
               'marital', 'children', 'mobile_phone'}
PAY_FIELDS = [('wage', 'wage'), ('house', 'sb_house_allowance'), ('position', 'sb_position_allowance'), ('cash', 'sb_cash_indemnity'),
              ('taxable', 'sb_taxable_allowance'), ('nontaxable', 'sb_nontaxable_allowance')]
HR_FIELDS = SELF_FIELDS | {'name', 'work_email', 'work_phone', 'birthday', 'sex', 'place_of_birth', 'country_id',
                           'sb_tin', 'sb_pension_no', 'sb_fayda', 'sb_pension', 'passport_id', 'permit_no', 'visa_expire',
                           'work_permit_expiration_date', 'certificate', 'study_field', 'study_school', 'employee_type',
                           'parent_id', 'user_id', 'sb_code', 'job_title'}


def _s(d):
    return fields.Date.to_string(d) if d else False


def _years(start, end):
    if not start:
        return None
    r = relativedelta(end, start)
    return {'years': r.years, 'months': r.months, 'label': ('%dy %dm' % (r.years, r.months)) if r.years else '%dm' % r.months}


class SbPeople(models.AbstractModel):
    _name = 'sb.people'
    _description = 'People app'

    @api.model
    def _hide_odoo_hr(self):
        group = self.env.ref('sonador_books.group_sb_legacy_apps')
        for xmlid in ('hr.menu_hr_root',):
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                menu.sudo().group_ids = [(6, 0, group.ids)]

    # ------------------------------------------------------------------ access
    def _is_hr(self):
        return self.env.su or self.env.user.has_group('sonador_books.group_sb_hr')

    def _me(self):
        return self.env.user.employee_ids.filtered(lambda e: e.company_id == self.env.company)[:1] or self.env.user.employee_ids[:1]

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    def _level(self, employee):
        """'hr' | 'self' | 'manager' | 'public'"""
        if self._is_hr():
            return 'hr'
        me = self._me()
        if me and employee == me:
            return 'self'
        if me and employee in self._E().search([('id', 'child_of', me.id)]):
            return 'manager'
        return 'public'

    def _require_hr(self):
        if not self._is_hr():
            raise AccessError(self.env._('Only HR officers can do this.'))

    # ------------------------------------------------------------------ cards
    def _card(self, e):
        return {'id': e.id, 'name': e.name, 'job': e.job_title or e.job_id.name or '', 'department': e.department_id.name or '',
                'department_id': e.department_id.id, 'manager': e.parent_id.name or '', 'manager_id': e.parent_id.id,
                'email': e.work_email or '', 'phone': e.mobile_phone or e.work_phone or '', 'active': e.active,
                'type': e.employee_type, 'code': e.sb_code or '',
                'avatar': '/web/image/hr.employee.public/%d/avatar_128' % e.id}

    @api.model
    def ctx(self):
        me = self._me()
        return {'is_hr': self._is_hr(), 'me': me.id or False, 'company': self.env.company.name,
                'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search(
                    [('company_id', 'in', (self.env.company.id, False))], order='name')],
                'jobs': [{'id': j.id, 'name': j.name} for j in self.env['hr.job'].sudo().search(
                    [('company_id', 'in', (self.env.company.id, False))], order='name')],
                'regions': [{'id': k, 'name': v} for k, v in (locale_of(self.env.company).get('regions') or REGIONS[:14])],
                'loc': hr_labels(self.env.company),
                'reasons': [{'id': k, 'name': v} for k, v in CHANGE_REASONS],
                'doc_types': [{'id': k, 'name': v} for k, v in DOC_TYPES],
                'types': [{'id': k, 'name': v} for k, v in self.env['hr.employee']._fields['employee_type']._description_selection(self.env)],
                'today': _s(fields.Date.context_today(self)), 'today_ec': format_ethiopian(fields.Date.context_today(self))}

    # ------------------------------------------------------------------ home
    @api.model
    def home(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        E = self._E()
        active = E.search([('company_id', '=', company.id), ('active', '=', True)])
        fy = company.compute_fiscalyear_dates(today)
        left = E.search([('company_id', '=', company.id), ('active', '=', False), ('departure_date', '>=', fy['date_from'])])
        joined = active.filtered(lambda e: e.current_version_id.contract_date_start
                                 and e.current_version_id.contract_date_start >= fy['date_from'])
        by_dept = Counter(e.department_id.name or 'No department' for e in active)
        by_type = Counter(dict(E._fields['employee_type']._description_selection(self.env)).get(e.employee_type, 'Employee') for e in active)
        by_sex = Counter({'male': 'Men', 'female': 'Women'}.get(e.current_version_id.sex, 'Not set') for e in active)
        tenures = [relativedelta(today, e.current_version_id.contract_date_start).years +
                   relativedelta(today, e.current_version_id.contract_date_start).months / 12.0
                   for e in active if e.current_version_id.contract_date_start]
        avg_head = (len(active) + len(active) + len(left)) / 2.0 or 1.0
        out = {
            'kpis': {'headcount': len(active), 'joined': len(joined), 'left': len(left),
                     'turnover': round(len(left) / avg_head * 100.0, 1),
                     'avg_tenure': round(sum(tenures) / len(tenures), 1) if tenures else None,
                     'fy': '%s - %s' % (format_ethiopian(fy['date_from']), format_ethiopian(fy['date_to']))},
            'by_dept': sorted(by_dept.items(), key=lambda kv: -kv[1]),
            'by_type': sorted(by_type.items(), key=lambda kv: -kv[1]),
            'by_sex': sorted(by_sex.items(), key=lambda kv: -kv[1]),
            'is_hr': self._is_hr(),
        }
        horizon = today + timedelta(days=30)
        events = []
        for e in active:
            v = e.current_version_id
            if e.birthday and (self._is_hr() or e.birthday_public_display):
                nxt = self._next_anniversary(e.birthday, today)
                if nxt and nxt <= horizon:
                    events.append({'kind': 'birthday', 'date': _s(nxt), 'ec': format_ethiopian(nxt), 'employee': self._card(e),
                                   'text': 'Birthday'})
            if v.contract_date_start and v.contract_date_start < today:
                nxt = self._next_anniversary(v.contract_date_start, today)
                if nxt and nxt <= horizon:
                    yrs = nxt.year - v.contract_date_start.year
                    if yrs > 0:
                        events.append({'kind': 'anniversary', 'date': _s(nxt), 'ec': format_ethiopian(nxt),
                                       'employee': self._card(e), 'text': '%d year%s at the company' % (yrs, 's' if yrs > 1 else '')})
        out['events'] = sorted(events, key=lambda x: x['date'])[:20]
        if self._is_hr():
            alerts = []
            for e in active:
                v = e.current_version_id
                if v.trial_date_end and today - timedelta(days=7) <= v.trial_date_end <= horizon:
                    alerts.append({'kind': 'probation', 'date': _s(v.trial_date_end), 'ec': format_ethiopian(v.trial_date_end),
                                   'employee': self._card(e), 'text': 'Probation ends: confirm or end employment'})
                if v.contract_date_end and today <= v.contract_date_end <= today + timedelta(days=60):
                    alerts.append({'kind': 'contract', 'date': _s(v.contract_date_end), 'ec': format_ethiopian(v.contract_date_end),
                                   'employee': self._card(e), 'text': 'Fixed-term contract ends'})
                for f, label in (('work_permit_expiration_date', 'Work permit expires'), ('visa_expire', 'Visa expires')):
                    d = e[f]
                    if d and d <= today + timedelta(days=60):
                        alerts.append({'kind': 'permit', 'date': _s(d), 'ec': format_ethiopian(d), 'employee': self._card(e),
                                       'text': label})
            docs = self.env['sb.people.document'].sudo().search([('employee_id', 'in', active.ids), ('expiry_date', '!=', False),
                                                                 ('expiry_date', '<=', today + timedelta(days=60))])
            for d in docs:
                alerts.append({'kind': 'document', 'date': _s(d.expiry_date), 'ec': format_ethiopian(d.expiry_date),
                               'employee': self._card(d.employee_id),
                               'text': '%s %s %s' % (dict(DOC_TYPES).get(d.doc_type), d.name or '',
                                                     'expired' if d.expiry_date < today else 'expires')})
            out['alerts'] = sorted(alerts, key=lambda x: x['date'])[:30]
            missing = defaultdict(list)
            loc = hr_labels(self.env.company)
            checks = [('sb_tin', 'TIN'), ('sb_pension_no', loc['pension_no']), ('sb_fayda', loc['national_id']), ('birthday', 'Date of birth'),
                      ('department_id', 'Department'), ('parent_id', 'Manager')]
            for e in active:
                for f, label in checks:
                    if not e[f]:
                        missing[label].append(e.id)
                if not e.primary_bank_account_id:
                    missing['Salary bank account'].append(e.id)
                if not e.sb_contact_ids:
                    missing['Emergency contact'].append(e.id)
                if not e.current_version_id.wage:
                    missing['Salary'].append(e.id)
                if not e.current_version_id.contract_date_start:
                    missing['Start date (tenure, leave, severance)'].append(e.id)
            out['missing'] = [{'label': k, 'count': len(v), 'ids': v} for k, v in sorted(missing.items(), key=lambda kv: -len(kv[1]))]
            out['recent'] = [self._card(e) | {'date': _s(e.current_version_id.contract_date_start),
                                              'ec': format_ethiopian(e.current_version_id.contract_date_start)}
                             for e in joined.sorted(lambda e: e.current_version_id.contract_date_start, reverse=True)[:8]]
        return out

    @api.model
    def _next_anniversary(self, d, today):
        for y in (today.year, today.year + 1):
            try:
                n = d.replace(year=y)
            except ValueError:  # 29 Feb
                n = d.replace(year=y, day=28)
            if n >= today:
                return n
        return None

    # ------------------------------------------------------------------ directory and org chart
    @api.model
    def directory(self, search='', department_id=None, emp_type=None, status='active', ids=None):
        dom = [('company_id', '=', self.env.company.id)]
        if status == 'active':
            dom.append(('active', '=', True))
        elif status == 'left':
            dom.append(('active', '=', False))
        if not self._is_hr():
            dom.append(('active', '=', True))
        if search:
            dom += ['|', '|', '|', ('name', 'ilike', search), ('job_title', 'ilike', search),
                    ('department_id.name', 'ilike', search), ('work_email', 'ilike', search)]
        if department_id:
            dom.append(('department_id', '=', int(department_id)))
        if emp_type:
            dom.append(('employee_type', '=', emp_type))
        if ids:
            dom.append(('id', 'in', ids))
        rows = [self._card(e) for e in self._E().search(dom, order='name')]
        return {'rows': rows, 'count': len(rows)}

    @api.model
    def org_chart(self):
        emps = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)], order='name')
        nodes = []
        for e in emps:
            c = self._card(e)
            c['parent_id'] = e.parent_id.id if e.parent_id in emps else False
            nodes.append(c)
        return {'nodes': nodes}

    # ------------------------------------------------------------------ profile
    @api.model
    def profile(self, employee_id):
        e = self._E().browse(int(employee_id)).exists()
        if not e:
            raise UserError(self.env._('Employee not found.'))
        level = self._level(e)
        if level == 'public' and not e.active:
            raise AccessError(self.env._('Not available.'))
        today = fields.Date.context_today(self)
        v = e.current_version_id or e.version_id
        start = v.contract_date_start
        out = {'level': level, 'card': self._card(e), 'is_hr': level == 'hr',
               'header': {'start': _s(start), 'start_ec': format_ethiopian(start) if start else '',
                          'tenure': _years(start, e.departure_date or today), 'type_label': dict(
                              e._fields['employee_type']._description_selection(self.env)).get(e.employee_type, ''),
                          'left': _s(e.departure_date), 'left_ec': format_ethiopian(e.departure_date) if e.departure_date else '',
                          'departure_reason': e.departure_reason_id.name or ''},
               'reports': [self._card(x) for x in e.child_ids.filtered('active')]}
        if level in ('hr', 'self', 'manager'):
            trial = v.trial_date_end
            out['job'] = {'job': e.job_title or e.job_id.name or '', 'job_id': e.job_id.id, 'department_id': e.department_id.id,
                          'parent_id': e.parent_id.id, 'type': e.employee_type,
                          'contract_type': v.contract_type_id.name or '', 'contract_start': _s(start),
                          'contract_end': _s(v.contract_date_end),
                          'trial_end': _s(trial), 'trial_end_ec': format_ethiopian(trial) if trial else '',
                          'on_probation': bool(trial and trial >= today), 'calendar': v.resource_calendar_id.name or '',
                          'grade': v.sb_grade or ''}
        if level in ('hr', 'self'):
            bank = e.primary_bank_account_id
            out['personal'] = {f: (e[f].id if hasattr(e[f], 'id') else e[f]) for f in HR_FIELDS if f in e._fields}
            out['personal'].update({'birthday': _s(e.birthday), 'visa_expire': _s(e.visa_expire),
                                    'work_permit_expiration_date': _s(e.work_permit_expiration_date),
                                    'sex': v.sex or False, 'marital': v.marital or False, 'children': v.children or 0,
                                    'country_id': v.country_id.id or False, 'passport_id': v.passport_id or '',
                                    'birthday_ec': format_ethiopian(e.birthday) if e.birthday else '',
                                    'age': _years(e.birthday, today)['years'] if e.birthday else None,
                                    'bank': {'acc': bank.acc_number or '', 'bank': bank.bank_id.name or ''} if bank else False})
            out['contacts'] = [{'id': c.id, 'name': c.name, 'relationship': c.relationship, 'phone': c.phone or '',
                                'phone2': c.phone2 or '', 'address': c.address or '', 'primary': c.primary}
                               for c in e.sb_contact_ids]
            out['documents'] = [{'id': d.id, 'doc_type': d.doc_type, 'type_label': dict(DOC_TYPES).get(d.doc_type),
                                 'name': d.name or '', 'issue_date': _s(d.issue_date), 'expiry_date': _s(d.expiry_date),
                                 'state': d.state, 'note': d.note or '',
                                 'file': {'id': d.attachment_id.id, 'name': d.attachment_id.name} if d.attachment_id else False}
                                for d in e.sb_document_ids]
            out['assets'] = [{'id': a.id, 'name': a.name, 'category': a.category, 'serial': a.serial or '',
                              'issued_on': _s(a.issued_on), 'returned_on': _s(a.returned_on), 'value': a.value, 'note': a.note or ''}
                             for a in e.sb_asset_ids]
        if level == 'hr' or level == 'self':
            out['history'] = [{'id': x.id, 'date': _s(x.date_version), 'ec': format_ethiopian(x.date_version),
                               'job': x.job_title or x.job_id.name or '', 'department': x.department_id.name or '',
                               'wage': x.wage, 'taxable': x.sb_taxable_allowance, 'nontaxable': x.sb_nontaxable_allowance,
                               'house': x.sb_house_allowance, 'position': x.sb_position_allowance, 'cash': x.sb_cash_indemnity,
                               'reason': dict(CHANGE_REASONS).get(x.sb_change_reason, '') if x.sb_change_reason else '',
                               'grade': x.sb_grade or '', 'contract_type': x.contract_type_id.name or '',
                               'current': x == e.current_version_id}
                              for x in e.version_ids.sorted('date_version', reverse=True)]
            out['timeline'] = self._timeline(e, out)
        return out

    def _timeline(self, e, data):
        items = []
        hist = sorted(data.get('history', []), key=lambda h: h['date'])
        prev = None
        for h in hist:
            if prev is None:
                text = 'Joined as %s' % (h['job'] or 'employee') + (' in %s' % h['department'] if h['department'] else '')
                icon = 'fa-sign-in'
            else:
                bits = []
                if h['job'] != prev['job']:
                    bits.append('%s → %s' % (prev['job'] or '—', h['job'] or '—'))
                if h['department'] != prev['department']:
                    bits.append('moved to %s' % (h['department'] or '—'))
                pay = lambda x: sum(x.get(k) or 0 for k in ('wage', 'taxable', 'house', 'position', 'cash'))  # noqa: E731
                if abs(pay(h) - pay(prev)) > 0.005:
                    bits.append('pay %s → %s' % ('{:,.2f}'.format(pay(prev)), '{:,.2f}'.format(pay(h))))
                text = (h['reason'] + ': ' if h['reason'] else '') + ('; '.join(bits) or 'Record updated')
                icon = 'fa-level-up' if h['reason'] == 'Promotion' else 'fa-exchange'
            items.append({'date': h['date'], 'ec': h['ec'], 'icon': icon, 'text': text})
            prev = h
        for d in data.get('documents', []):
            if d['issue_date']:
                items.append({'date': d['issue_date'], 'ec': format_ethiopian(fields.Date.to_date(d['issue_date'])),
                              'icon': 'fa-file-text-o', 'text': '%s %s' % (d['type_label'], d['name'])})
        for a in data.get('assets', []):
            if a['issued_on']:
                items.append({'date': a['issued_on'], 'ec': format_ethiopian(fields.Date.to_date(a['issued_on'])),
                              'icon': 'fa-laptop', 'text': 'Issued %s %s' % (a['name'], a['serial'])})
            if a['returned_on']:
                items.append({'date': a['returned_on'], 'ec': format_ethiopian(fields.Date.to_date(a['returned_on'])),
                              'icon': 'fa-undo', 'text': 'Returned %s' % a['name']})
        if e.departure_date:
            items.append({'date': _s(e.departure_date), 'ec': format_ethiopian(e.departure_date), 'icon': 'fa-sign-out',
                          'text': 'Left' + (': %s' % e.departure_reason_id.name if e.departure_reason_id else '')})
        return sorted(items, key=lambda x: x['date'], reverse=True)

    # ------------------------------------------------------------------ edits
    @api.model
    def save_personal(self, employee_id, vals):
        e = self._E().browse(int(employee_id))
        level = self._level(e)
        allowed = HR_FIELDS if level == 'hr' else SELF_FIELDS if level == 'self' else set()
        bad = set(vals) - allowed - {'bank_acc', 'bank_name'}
        if bad or (('bank_acc' in vals or 'bank_name' in vals) and level != 'hr'):
            raise AccessError(self.env._('You cannot change: %s', ', '.join(sorted(bad or {'bank'}))))
        version_fields = {'sex', 'marital', 'children', 'country_id', 'passport_id'}
        emp_vals = {k: (v or False) for k, v in vals.items() if k not in version_fields and k not in ('bank_acc', 'bank_name')}
        for k in ('parent_id', 'user_id'):
            if k in emp_vals and emp_vals[k]:
                emp_vals[k] = int(emp_vals[k])
        if emp_vals:
            e.write(emp_vals)
        ver = {k: (v or False) for k, v in vals.items() if k in version_fields}
        if ver:
            (e.current_version_id or e.version_id).write(ver)
        if 'bank_acc' in vals:
            acc = (vals.get('bank_acc') or '').strip()
            if acc:
                partner = e.work_contact_id or e.user_id.partner_id
                if not partner:
                    partner = self.env['res.partner'].sudo().create({'name': e.name})
                    e.work_contact_id = partner
                bank = False
                if vals.get('bank_name'):
                    bank = self.env['res.bank'].sudo().search([('name', '=ilike', vals['bank_name'])], limit=1) or \
                        self.env['res.bank'].sudo().create({'name': vals['bank_name']})
                rpb = self.env['res.partner.bank'].sudo().search([('acc_number', '=', acc), ('partner_id', '=', partner.id)], limit=1) \
                    or self.env['res.partner.bank'].sudo().create({'acc_number': acc, 'partner_id': partner.id,
                                                                    'bank_id': bank.id if bank else False})
                if bank and rpb.bank_id != bank:
                    rpb.bank_id = bank
                e.bank_account_ids = [(6, 0, [rpb.id])]  # the salary account is replaced (the old one stays on the contact)
                e._sb_sync_payroll()
        return self.profile(e.id)

    def _job(self, name):
        if not name:
            return self.env['hr.job']
        J = self.env['hr.job'].sudo()
        if isinstance(name, int) or (isinstance(name, str) and name.isdigit()):
            return J.browse(int(name))
        return J.search([('name', '=ilike', name.strip()), ('company_id', 'in', (self.env.company.id, False))], limit=1) or \
            J.create({'name': name.strip(), 'company_id': self.env.company.id})

    def _department(self, value):
        if not value:
            return self.env['hr.department']
        D = self.env['hr.department'].sudo()
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return D.browse(int(value))
        return D.search([('name', '=ilike', value.strip()), ('company_id', 'in', (self.env.company.id, False))], limit=1) or \
            D.create({'name': value.strip(), 'company_id': self.env.company.id})

    @api.model
    def job_change(self, employee_id, vals):
        """A dated change of job, department, manager or pay: a new version from that date (history is kept)."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        day = fields.Date.to_date(vals.get('date')) if vals.get('date') else fields.Date.context_today(self)
        values = {'date_version': day}
        if 'job' in vals:
            job = self._job(vals.get('job'))
            values.update(job_id=job.id or False, job_title=job.name or False)
        if vals.get('department_id') is not None:
            values['department_id'] = self._department(vals.get('department_id')).id or False
        for k, f in PAY_FIELDS:
            if vals.get(k) is not None and vals.get(k) != '':
                values[f] = float(vals[k])
        if vals.get('reason'):
            values['sb_change_reason'] = vals['reason']
        if vals.get('grade') is not None:
            values['sb_grade'] = vals['grade'] or False
        if vals.get('contract_end') is not None:
            values['contract_date_end'] = vals['contract_end'] or False
        version = e.create_version(dict(values))
        version.write({k: v for k, v in values.items() if k != 'date_version'})
        if vals.get('parent_id') is not None:
            e.parent_id = int(vals['parent_id']) or False
        e._sb_sync_payroll()
        return self.profile(e.id)

    @api.model
    def hire(self, vals):
        """New employee: record, first version (job, pay, contract start), probation end per the proclamation."""
        self._require_hr()
        if not (vals.get('name') or '').strip():
            raise ValidationError(self.env._('Enter the name.'))
        start = fields.Date.to_date(vals.get('start')) if vals.get('start') else fields.Date.context_today(self)
        job = self._job(vals.get('job'))
        dept = self._department(vals.get('department_id'))
        e = self.env['hr.employee'].sudo().create({
            'name': vals['name'].strip(), 'company_id': self.env.company.id, 'job_id': job.id or False,
            'job_title': job.name or False, 'department_id': dept.id or False,
            'parent_id': int(vals['parent_id']) if vals.get('parent_id') else False,
            'work_email': vals.get('email') or False, 'mobile_phone': vals.get('phone') or False,
            'employee_type': vals.get('type') or 'employee', 'date_version': start, 'contract_date_start': start,
            'wage': float(vals.get('wage') or 0.0)})
        v = e.version_id
        v.write({f: float(vals.get(k) or 0.0) for k, f in PAY_FIELDS if k != 'wage'})
        v.write({'sb_change_reason': 'hire',
                 'trial_date_end': e._sb_probation_end(start, v.resource_calendar_id),
                 'contract_date_end': vals.get('contract_end') or False})
        e._sb_sync_payroll()
        return e.id

    @api.model
    def depart(self, employee_id, vals):
        self._require_hr()
        e = self._E().browse(int(employee_id))
        day = fields.Date.to_date(vals.get('date')) if vals.get('date') else fields.Date.context_today(self)
        reason = self.env['hr.departure.reason'].sudo().browse(int(vals['reason_id'])) if vals.get('reason_id') else False
        e.write({'departure_date': day, 'departure_reason_id': reason.id if reason else False,
                 'departure_description': vals.get('note') or False})
        e.action_archive()
        e._sb_sync_payroll()
        return self.profile(e.id)

    @api.model
    def set_start(self, employee_id, start):
        """The date the employee started: tenure, annual leave, probation and severance count from it."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        day = fields.Date.to_date(start)
        if not day:
            raise ValidationError(self.env._('Enter the start date.'))
        versions = e.version_ids.sorted('date_version')
        first = versions[:1]
        if first and first.date_version > day:
            first.with_context(sb_no_payroll_sync=True).date_version = day
        versions.with_context(sb_no_payroll_sync=True).write({'contract_date_start': day})
        trial = (first.trial_date_end if first else False) or e._sb_probation_end(day, first.resource_calendar_id if first else None)
        versions.filtered(lambda v: not v.trial_date_end).with_context(sb_no_payroll_sync=True).write({'trial_date_end': trial})
        e._sb_sync_payroll()
        e.message_post(body=self.env._('Start date set to %s.', format_ethiopian(day)))
        return self.profile(e.id)

    @api.model
    def departure_reasons(self):
        return [{'id': r.id, 'name': r.name} for r in self.env['hr.departure.reason'].sudo().search([])]

    @api.model
    def confirm_probation(self, employee_id):
        self._require_hr()
        e = self._E().browse(int(employee_id))
        v = e.current_version_id or e.version_id
        today = fields.Date.context_today(self)
        if v.trial_date_end and v.trial_date_end > today:
            v.trial_date_end = today
        e.message_post(body=self.env._('Probation completed: employment confirmed.'))
        return self.profile(e.id)

    @api.model
    def login_info(self, employee_id):
        """The person's login and the logins HR can link (not yet linked to anyone in this company)."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        Users = self.env['res.users'].sudo()
        taken = set(self._E().search([('company_id', '=', e.company_id.id), ('user_id', '!=', False), ('id', '!=', e.id),
                                      ('active', '=', True)]).mapped('user_id').ids)
        free = Users.search([('share', '=', False), ('id', 'not in', list(taken))], order='name')
        return {'user': e.user_id and {'id': e.user_id.id, 'name': e.user_id.name, 'login': e.user_id.login} or False,
                'choices': [{'id': u.id, 'label': '%s (%s)' % (u.name, u.login)} for u in free]}

    @api.model
    def set_login(self, employee_id, user_id):
        """Link a login to the person (False: unlink). Their clock, leave, payslips and tasks follow the login."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        user = self.env['res.users'].sudo().browse(int(user_id)) if user_id else self.env['res.users']
        if user:
            others = self._E().search([('company_id', '=', e.company_id.id), ('user_id', '=', user.id), ('id', '!=', e.id)])
            if others.filtered('active'):
                raise ValidationError(self.env._('%(l)s is already %(n)s\'s login.', l=user.login, n=others.filtered('active')[0].name))
            others.write({'user_id': False})  # archived records give the login up
            if e.company_id not in user.company_ids:
                user.company_ids = [(4, e.company_id.id)]
        old = e.user_id
        e.user_id = user.id or False
        e.message_post(body=self.env._('Login %s.', user and self.env._('set to %s', user.login) or self.env._('removed (was %s)', old.login or '-')))
        return self.login_info(e.id)

    @api.model
    def new_login(self, employee_id):
        """Create an employee login for this person (no password: set it in Settings > Users, or send an invite)."""
        self._require_hr()
        e = self._E().browse(int(employee_id))
        if e.user_id:
            raise UserError(self.env._('%(n)s already has the login %(l)s.', n=e.name, l=e.user_id.login))
        self.with_context(sb_only_employee=e.id).create_logins()
        return self.login_info(e.id)

    @api.model
    def create_logins(self):
        """Self-service for every current employee: link the existing login of the same name, else create one
        (login = work email, else first.last). Employees only (no app rights); no password is set here."""
        self._require_hr()
        import re
        import unicodedata
        Users = self.env['res.users'].sudo().with_context(no_reset_password=True)
        employee_group = self.env.ref('base.group_user')
        stats = {'linked': 0, 'created': 0, 'logins': []}
        emps = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True), ('user_id', '=', False)])
        if self.env.context.get('sb_only_employee'):
            emps = emps.filtered(lambda x: x.id == self.env.context['sb_only_employee'])
        for e in emps:
            name = (e.name or '').strip()
            user = Users.search([('share', '=', False), ('partner_id.name', '=ilike', name)], limit=1)
            if user and not user.employee_ids.filtered(lambda x: x.company_id == e.company_id):
                e.user_id = user
                stats['linked'] += 1
                continue
            login = (e.work_email or '').strip().lower()
            if not login or Users.with_context(active_test=False).search_count([('login', '=', login)]):
                base = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
                parts = [p for p in re.split(r'[^a-z0-9]+', base) if p]
                login = '.'.join(parts[:2]) or 'employee%d' % e.id
                n, cand = 1, login
                while Users.with_context(active_test=False).search_count([('login', '=', cand)]):
                    n += 1
                    cand = '%s%d' % (login, n)
                login = cand
            Users.create({'name': name, 'login': login, 'email': e.work_email or False, 'company_id': e.company_id.id,
                          'company_ids': [(6, 0, [e.company_id.id])], 'group_ids': [(6, 0, [employee_group.id])],
                          'create_employee_id': e.id})
            stats['created'] += 1
            stats['logins'].append({'name': name, 'login': login})
        return stats

    @api.model
    def save_rows(self, employee_id, kind, rows):
        """kind: contacts | documents | assets. rows: the full list (missing rows are removed)."""
        e = self._E().browse(int(employee_id))
        level = self._level(e)
        if kind == 'assets' and level != 'hr':
            raise AccessError(self.env._('Only HR officers record assets.'))
        if level not in ('hr', 'self'):
            raise AccessError(self.env._('Not allowed.'))
        model = {'contacts': 'sb.people.contact', 'documents': 'sb.people.document', 'assets': 'sb.people.asset'}[kind]
        fields_ok = {'contacts': ['name', 'relationship', 'phone', 'phone2', 'address', 'primary'],
                     'documents': ['doc_type', 'name', 'issue_date', 'expiry_date', 'note'],
                     'assets': ['name', 'category', 'serial', 'issued_on', 'returned_on', 'value', 'note']}[kind]
        M = self.env[model].sudo()
        existing = M.search([('employee_id', '=', e.id)])
        keep = M
        for r in rows:
            vals = {f: (r.get(f) if r.get(f) not in ('', None) else False) for f in fields_ok}
            if kind == 'contacts' and not vals.get('name'):
                continue
            if kind == 'assets' and not vals.get('name'):
                continue
            rec = M.browse(int(r['id'])) if r.get('id') else M
            if rec and rec in existing:
                rec.write(vals)
            else:
                rec = M.create(dict(vals, employee_id=e.id))
            if kind == 'documents' and r.get('file_data'):
                att = self.env['ir.attachment'].sudo().create({
                    'name': r.get('file_name') or 'document', 'datas': r['file_data'],
                    'res_model': 'hr.employee', 'res_id': e.id})
                rec.attachment_id = att
            keep |= rec
        (existing - keep).unlink()
        return self.profile(e.id)
