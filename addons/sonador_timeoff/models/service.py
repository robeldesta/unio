"""Leave requests: the leave engine (working days, annual leave by service year, sick leave pay tiers, the checks of the
Labour Proclamation) and the calls of the Leave Requests screens."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .leave import annual_entitlement

SICK_TIERS = [(30, 'full'), (60, 'half'), (90, 'unpaid')]  # art. 86: 1 month 100%, 2 months 50%, 3 months unpaid
SICK_MAX = 180


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbLeaveService(models.AbstractModel):
    _name = 'sb.leave.service'
    _description = 'Leave requests'

    # ------------------------------------------------------------------ access
    def _P(self):
        return self.env['sb.people']

    def _is_hr(self):
        return self._P()._is_hr()

    def _me(self):
        return self._P()._me()

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    def _can_manage(self, e):
        me = self._me()
        return self._is_hr() or (me and e in self._E().search([('id', 'child_of', me.id), ('id', '!=', me.id)]))

    def _can_see(self, e):
        return self._can_manage(e) or e == self._me()

    # ------------------------------------------------------------------ calendar
    def _holiday_dates(self, df, dt, company):
        for y in range(df.year, dt.year + 1):
            self.env['sb.holidays'].sudo().ensure_year(y, company)
        L = self.env['resource.calendar.leaves'].sudo().search([
            ('resource_id', '=', False), ('company_id', 'in', (company.id, False)),
            ('date_from', '<=', datetime.combine(dt, time(23, 59))), ('date_to', '>=', datetime.combine(df, time(0, 0)))])
        out = {}
        for l in L:
            d = l.date_from.date()
            while d <= l.date_to.date():
                out[d] = l.name
                d += timedelta(days=1)
        return out

    @api.model
    def working_days(self, employee, df, dt):
        cal = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        workdays = {int(a.dayofweek) for a in cal.attendance_ids} if cal else {0, 1, 2, 3, 4, 5}
        hol = self._holiday_dates(df, dt, employee.company_id)
        n, d = 0, df
        while d <= dt:
            if d.weekday() in workdays and d not in hol:
                n += 1
            d += timedelta(days=1)
        return float(n)

    # ------------------------------------------------------------------ annual leave
    @api.model
    def annual(self, employee, on=None):
        on = on or fields.Date.context_today(self)
        start = employee.current_version_id.contract_date_start or employee.version_id.contract_date_start
        lots = []
        if start:
            k = 1
            while True:
                ys = start + relativedelta(years=k - 1)
                ye = start + relativedelta(years=k) - timedelta(days=1)
                if ys > on:
                    break
                full = annual_entitlement(k, employee.company_id.country_id.code)
                if ye <= on:
                    earned = float(full)
                else:  # art. 77(5): a part year earns in proportion
                    earned = round(full * ((on - ys).days + 1) / float((ye - ys).days + 1), 2)
                lots.append({'label': 'Service year %d' % k, 'from': ys, 'to': ye, 'entitlement': full, 'earned': earned,
                             'expires': ye + relativedelta(years=2), 'used': 0.0, 'lapsed': 0.0, 'current': ye > on})
                k += 1
        A = self.env['sb.leave.adjust'].sudo().search([('employee_id', '=', employee.id), ('date', '<=', on)], order='date, id')
        for a in A.filtered(lambda a: a.days > 0):
            lots.append({'label': a.note, 'from': a.date, 'to': a.date, 'entitlement': a.days, 'earned': a.days,
                         'expires': a.date + relativedelta(years=2), 'used': 0.0, 'lapsed': 0.0, 'current': False})
        lots.sort(key=lambda l: l['to'])
        takes = [(l.date_from, l.days, l.id) for l in self.env['sb.leave'].sudo().search([
            ('employee_id', '=', employee.id), ('kind', '=', 'annual'), ('state', '=', 'approved'), ('date_from', '<=', on)],
            order='date_from')]
        takes += [(a.date, -a.days, 'adj%d' % a.id) for a in A.filtered(lambda a: a.days < 0)]
        overdrawn = 0.0
        for day, amount, _key in sorted(takes, key=lambda t: t[0]):
            rest = amount
            for lot in lots:  # oldest first, among lots still valid on that day
                if rest <= 0:
                    break
                if lot['expires'] < day:
                    continue
                free = lot['earned'] - lot['used']
                take = min(free, rest)
                if take > 0:
                    lot['used'] += take
                    rest -= take
            overdrawn += max(rest, 0.0)
        for lot in lots:
            if lot['expires'] < on:
                lot['lapsed'] = round(lot['earned'] - lot['used'], 2)
        available = round(sum(l['earned'] - l['used'] - l['lapsed'] for l in lots) - overdrawn, 2)
        soon = [l for l in lots if not l['lapsed'] and on <= l['expires'] <= on + relativedelta(months=3) and l['earned'] - l['used'] > 0]
        service_years = relativedelta(on, start).years if start else None
        return {
            'start': _s(start), 'start_ec': format_ethiopian(start) if start else '', 'service_years': service_years,
            'this_year': annual_entitlement((service_years or 0) + 1, employee.company_id.country_id.code) if start else None,
            'earned': round(sum(l['earned'] for l in lots), 2), 'taken': round(sum(l['used'] for l in lots) + overdrawn, 2),
            'lapsed': round(sum(l['lapsed'] for l in lots), 2), 'available': available,
            'expiring': [{'days': round(l['earned'] - l['used'], 2), 'date': _s(l['expires']), 'ec': format_ethiopian(l['expires'])}
                         for l in soon],
            'lots': [dict(l, **{'from': _s(l['from']), 'to': _s(l['to']), 'expires': _s(l['expires']),
                                'expires_ec': format_ethiopian(l['expires']), 'remaining': round(l['earned'] - l['used'] - l['lapsed'], 2)})
                     for l in lots],
            'no_start': not start,
        }

    # ------------------------------------------------------------------ sick leave
    def _sick_used(self, employee, before, exclude=None):
        """Sick days (calendar) approved in the 12 months before the date."""
        L = self.env['sb.leave'].sudo().search([('employee_id', '=', employee.id), ('kind', '=', 'sick'), ('state', '=', 'approved'),
                                               ('date_to', '>=', before - relativedelta(months=12)), ('date_from', '<', before)])
        if exclude:
            L -= exclude
        return sum(l.days for l in L)

    def _sick_split(self, used_before, days):
        split = {'full': 0.0, 'half': 0.0, 'unpaid': 0.0, 'over': 0.0}
        pos = used_before
        remaining = days
        edge = 0
        for size, kind in SICK_TIERS:
            lo, hi = edge, edge + size
            edge = hi
            if remaining <= 0:
                break
            if pos < hi:
                take = min(hi - pos, remaining)
                split[kind] += take
                pos += take
                remaining -= take
        split['over'] = max(remaining, 0.0)
        return split

    # ------------------------------------------------------------------ checks
    def _check(self, leave, hr_override=False):
        """Warnings (list) and blocking errors (raise) for a request."""
        e, t = leave.employee_id, leave.type_id
        warnings = []
        sex = e.current_version_id.sex
        if t.gender and sex and sex != t.gender:
            raise ValidationError(self.env._('%(t)s is for %(g)s only.', t=t.name, g='women' if t.gender == 'female' else 'men'))
        if t.max_days and leave.days > t.max_days:
            raise ValidationError(self.env._('%(t)s: at most %(n)s %(u)s per request (%(ref)s).',
                                             t=t.name, n=int(t.max_days), u='working days' if t.unit == 'working' else 'days', ref=t.legal_ref or ''))
        if not leave.days:
            raise ValidationError(self.env._('These dates have no working days (weekly rest days or public holidays).'))
        overlap = self.env['sb.leave'].sudo().search_count([('employee_id', '=', e.id), ('id', '!=', leave.id or 0),
                                                            ('state', 'in', ('submitted', 'approved')), ('date_from', '<=', leave.date_to),
                                                            ('date_to', '>=', leave.date_from)])
        if overlap:
            raise ValidationError(self.env._('%s already has leave on some of these days.', e.name))
        if t.max_per_year:
            fy = e.company_id.compute_fiscalyear_dates(leave.date_from)
            n = self.env['sb.leave'].sudo().search_count([('employee_id', '=', e.id), ('type_id', '=', t.id), ('id', '!=', leave.id or 0),
                                                          ('state', 'in', ('submitted', 'approved')),
                                                          ('date_from', '>=', fy['date_from']), ('date_from', '<=', fy['date_to'])])
            if n >= t.max_per_year:
                raise ValidationError(self.env._('%(t)s: at most %(n)s times in a year.', t=t.name, n=t.max_per_year))
        if t.kind == 'annual':
            bal = self.annual(e, leave.date_from)
            if bal['no_start']:
                warnings.append(self.env._('No start date on the employee record: the balance cannot be worked out.'))
            elif leave.days > bal['available'] + 0.001:
                msg = self.env._('Annual leave available on %(d)s: %(a)s days; this request is %(r)s days.',
                                 d=format_ethiopian(leave.date_from), a=bal['available'], r=leave.days)
                if not hr_override:
                    raise ValidationError(msg)
                warnings.append(msg)
        if t.kind == 'sick':
            used = self._sick_used(e, leave.date_from, exclude=leave)
            split = self._sick_split(used, leave.days)
            if split['over']:
                raise ValidationError(self.env._('Sick leave cannot pass six months in twelve: %s days already used.', int(used)))
            if split['half'] or split['unpaid']:
                warnings.append(self.env._('Pay: %(f)s days full pay, %(h)s days half pay, %(u)s days unpaid.',
                                           f=split['full'], h=split['half'], u=split['unpaid']))
            if t.needs_document and not leave.attachment_id:
                warnings.append(self.env._('Attach the medical certificate.'))
        return warnings

    # ------------------------------------------------------------------ screens
    def _leave_dict(self, l):
        return {'id': l.id, 'employee': self._P()._card(l.employee_id), 'type': l.type_id.name, 'type_id': l.type_id.id,
                'kind': l.kind, 'color': l.type_id.color, 'date_from': _s(l.date_from), 'date_to': _s(l.date_to),
                'from_ec': format_ethiopian(l.date_from), 'to_ec': format_ethiopian(l.date_to), 'days': l.days,
                'unit': l.type_id.unit, 'state': 'manager' if l.state == 'submitted' and l.manager_ok_id else l.state,
                'reason': l.reason or '', 'approver': l.approver_id.name or '', 'manager_ok': l.manager_ok_id.name or '',
                'refuse_reason': l.refuse_reason or '', 'file': {'id': l.attachment_id.id, 'name': l.attachment_id.name} if l.attachment_id else False,
                'pay': {'full': l.days_full_pay, 'half': l.days_half_pay, 'unpaid': l.days_unpaid} if l.kind == 'sick' else False,
                'can_decide': l.state == 'submitted' and self._can_manage(l.employee_id) and l.employee_id != self._me()
                and (self._is_hr() or not l.manager_ok_id),
                'can_cancel': l.state in ('draft', 'submitted') and (l.employee_id == self._me() or self._is_hr())
                or (l.state == 'approved' and self._is_hr())}

    @api.model
    def ctx(self):
        me = self._me()
        team = self._E().search([('id', 'child_of', me.id), ('id', '!=', me.id), ('active', '=', True)]) if me else self._E().browse()
        sex = me.current_version_id.sex if me else False
        types = self.env['sb.leave.type'].sudo().search([])
        return {'is_hr': self._is_hr(), 'me': me.id or False, 'is_manager': bool(team), 'today': _s(fields.Date.context_today(self)),
                'types': [{'id': t.id, 'name': t.name, 'kind': t.kind, 'unit': t.unit, 'max_days': t.max_days, 'paid': t.paid,
                           'needs_document': t.needs_document, 'gender': t.gender or '', 'ref': t.legal_ref or '', 'color': t.color,
                           'mine': not t.gender or not sex or t.gender == sex} for t in types],
                'people': [{'id': e.id, 'name': e.name} for e in (self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)], order='name')
                                                                if self._is_hr() else (team | me))]}

    @api.model
    def overview(self, employee_id=None):
        e = self._E().browse(int(employee_id)) if employee_id else self._me()
        if not e:
            return {'employee': False}
        if not self._can_see(e):
            raise AccessError(self.env._('Not allowed.'))
        today = fields.Date.context_today(self)
        L = self.env['sb.leave'].sudo()
        leaves = L.search([('employee_id', '=', e.id)], limit=40)
        sick_used = self._sick_used(e, today + timedelta(days=1))
        fy = e.company_id.compute_fiscalyear_dates(today)
        by_kind = defaultdict(float)
        for l in leaves.filtered(lambda l: l.state == 'approved' and l.date_from >= fy['date_from']):
            by_kind[l.kind] += l.days
        return {'employee': self._P()._card(e), 'annual': self.annual(e), 'sick': {'used': sick_used, 'max': SICK_MAX,
                'next': self._sick_split(sick_used, 1)}, 'this_year': dict(by_kind),
                'leaves': [self._leave_dict(l) for l in leaves], 'can_manage': self._can_manage(e),
                'adjustments': [{'id': a.id, 'date': _s(a.date), 'days': a.days, 'note': a.note} for a in
                                self.env['sb.leave.adjust'].sudo().search([('employee_id', '=', e.id)])] if self._can_manage(e) else []}

    def _new(self, vals):
        e = self._E().browse(int(vals.get('employee_id') or self._me().id))
        if not e:
            raise UserError(self.env._('Your login is not linked to an employee.'))
        if e != self._me() and not self._can_manage(e):
            raise AccessError(self.env._('You can request leave for yourself and your team.'))
        return e

    @api.model
    def preview(self, vals):
        """Days and warnings for a request being filled in (nothing saved)."""
        e = self._new(vals)
        if not (vals.get('type_id') and vals.get('date_from') and vals.get('date_to')):
            return {'days': 0, 'warnings': [], 'error': ''}
        L = self.env['sb.leave'].sudo()
        leave = L.new({'employee_id': e.id, 'type_id': int(vals['type_id']), 'date_from': vals['date_from'], 'date_to': vals['date_to']})
        leave._compute_days()
        try:
            warnings = self._check(leave, hr_override=self._is_hr())
            error = ''
        except ValidationError as ex:
            warnings, error = [], str(ex.args[0] if ex.args else ex)
        hol = self._holiday_dates(fields.Date.to_date(vals['date_from']), fields.Date.to_date(vals['date_to']), e.company_id)
        return {'days': leave.days, 'warnings': warnings, 'error': error,
                'holidays': ['%s (%s)' % (n, format_ethiopian(d)) for d, n in sorted(hol.items())]}

    @api.model
    def request(self, vals, submit=True):
        e = self._new(vals)
        L = self.env['sb.leave'].sudo()
        with self.env.cr.savepoint():  # a refused request leaves nothing behind
            leave = L.create({'employee_id': e.id, 'type_id': int(vals['type_id']), 'date_from': vals['date_from'],
                              'date_to': vals['date_to'], 'reason': vals.get('reason') or False})
            if vals.get('file_data'):
                leave.attachment_id = self.env['ir.attachment'].sudo().create({
                    'name': vals.get('file_name') or 'document', 'datas': vals['file_data'], 'res_model': 'sb.leave', 'res_id': leave.id})
            self._check(leave, hr_override=self._is_hr())
        if submit:
            leave.state = 'submitted'
            if self._is_hr() and vals.get('approve'):
                self.decide(leave.id, True)
        return self._leave_dict(leave)

    @api.model
    def decide(self, leave_id, approve, reason=''):
        l = self.env['sb.leave'].sudo().browse(int(leave_id))
        me = self._me()
        own = l.employee_id == me and (not self._is_hr() or l.employee_id.company_id._sb_no_self_approval())
        if not self._can_manage(l.employee_id) or own:
            raise AccessError(self.env._('Leave is approved by the manager or HR.'))
        if l.state != 'submitted':
            raise UserError(self.env._('Only requests waiting approval can be decided.'))
        vals = {'approver_id': me.id or False, 'decided_on': fields.Date.context_today(self)}
        if approve and not self._is_hr():
            # the department manager approves first; HR then (department manager, then HR)
            if l.manager_ok_id:
                raise UserError(self.env._('Already approved by %s: waiting for HR.', l.manager_ok_id.name))
            l.manager_ok_id = me
            l.employee_id.message_post(body=self.env._('%(t)s %(f)s to %(to)s: approved by %(m)s, waiting for HR', t=l.type_id.name,
                                                       f=format_ethiopian(l.date_from), to=format_ethiopian(l.date_to), m=me.name))
            return self._leave_dict(l)
        if approve:
            self._check(l, hr_override=self._is_hr())
            if l.kind == 'sick':
                split = self._sick_split(self._sick_used(l.employee_id, l.date_from, exclude=l), l.days)
                vals.update(days_full_pay=split['full'], days_half_pay=split['half'], days_unpaid=split['unpaid'])
            vals['state'] = 'approved'
        else:
            vals.update(state='refused', refuse_reason=reason or False)
        l.write(vals)
        l.employee_id.message_post(body=self.env._('%(t)s %(f)s to %(to)s (%(d)s days): %(s)s', t=l.type_id.name,
                                                   f=format_ethiopian(l.date_from), to=format_ethiopian(l.date_to), d=l.days,
                                                   s='approved' if approve else 'refused'))
        return self._leave_dict(l)

    @api.model
    def cancel(self, leave_id):
        l = self.env['sb.leave'].sudo().browse(int(leave_id))
        d = self._leave_dict(l)
        if not d['can_cancel']:
            raise AccessError(self.env._('This request cannot be cancelled.'))
        l.state = 'cancelled'
        return self._leave_dict(l)

    @api.model
    def approvals(self):
        me = self._me()
        L = self.env['sb.leave'].sudo()
        dom = [('state', '=', 'submitted'), ('company_id', '=', self.env.company.id)]
        if not self._is_hr():
            team = self._E().search([('id', 'child_of', me.id or -1), ('id', '!=', me.id or -1)])
            dom += [('employee_id', 'in', team.ids or [-1]), ('manager_ok_id', '=', False)]
        rows = []
        for l in L.search(dom, order='date_from'):
            d = self._leave_dict(l)
            if l.kind == 'annual':
                d['balance'] = self.annual(l.employee_id, l.date_from)['available']
            rows.append(d)
        return rows

    @api.model
    def calendar(self, month_start, scope='team'):
        ms = fields.Date.to_date(month_start)
        me_ = ms + relativedelta(months=1) - timedelta(days=1)
        me = self._me()
        if scope == 'all' and (self._is_hr() or True):
            emps = self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)], order='name')
        else:
            base = me.parent_id if me and me.parent_id else me
            emps = self._E().search([('id', 'child_of', base.id or -1), ('active', '=', True)], order='name') if base else self._E().browse()
        L = self.env['sb.leave'].sudo().search([('employee_id', 'in', emps.ids), ('state', 'in', ('submitted', 'approved')),
                                               ('date_from', '<=', me_), ('date_to', '>=', ms)])
        by = defaultdict(list)
        for l in L:
            by[l.employee_id.id].append(l)
        days = []
        d = ms
        hol = self._holiday_dates(ms, me_, self.env.company)
        while d <= me_:
            days.append({'date': _s(d), 'day': d.day, 'dow': d.strftime('%a')[0:2], 'weekend': d.weekday() == 6,
                         'holiday': hol.get(d, ''), 'ec': format_ethiopian(d)})
            d += timedelta(days=1)
        rows = []
        for e in emps:
            cells = {}
            for l in by.get(e.id, []):
                x = max(l.date_from, ms)
                while x <= min(l.date_to, me_):
                    cells[_s(x)] = {'type': l.type_id.name, 'color': l.type_id.color, 'pending': l.state == 'submitted'}
                    x += timedelta(days=1)
            if cells or scope != 'all':
                rows.append({'employee': self._P()._card(e), 'cells': cells})
        return {'days': days, 'rows': rows, 'label': '%s %s' % (ms.strftime('%B %Y'), ''), 'today': _s(fields.Date.context_today(self))}

    @api.model
    def balances(self):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        today = fields.Date.context_today(self)
        rows = []
        for e in self._E().search([('company_id', '=', self.env.company.id), ('active', '=', True)], order='name'):
            a = self.annual(e, today)
            rows.append({'employee': self._P()._card(e), 'start_ec': a['start_ec'], 'years': a['service_years'],
                         'this_year': a['this_year'], 'earned': a['earned'], 'taken': a['taken'], 'lapsed': a['lapsed'],
                         'available': a['available'], 'expiring': sum(x['days'] for x in a['expiring']),
                         'daily_wage': round((e.current_version_id.wage or 0.0) / 30.0, 2),
                         'liability': round(max(a['available'], 0) * (e.current_version_id.wage or 0.0) / 30.0, 2)})
        return {'rows': rows, 'total_days': round(sum(r['available'] for r in rows), 2),
                'total_liability': round(sum(r['liability'] for r in rows), 2)}

    @api.model
    def adjust(self, employee_id, days, day, note):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        if not note:
            raise ValidationError(self.env._('Say why (e.g. opening balance, correction).'))
        self.env['sb.leave.adjust'].sudo().create({'employee_id': int(employee_id), 'days': float(days), 'date': day, 'note': note})
        return self.overview(employee_id)

    @api.model
    def holidays(self, year):
        return self.env['sb.holidays'].list_year(int(year))
