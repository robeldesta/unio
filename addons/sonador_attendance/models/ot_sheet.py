"""The month's overtime sheet, before the payroll (Robel, 9 Nov 2026: "payroll is sacred"):
the person who sends the payroll attendance (e.g. Sintayehu) downloads the OT sheet of the month (everyone with the hours
from the records), fills the real hours and uploads it on Attendance > Days worked; HR (not the person who uploaded it)
approves or refuses it. Once approved, the sheet's hours are the month's overtime for the people on it: Days worked, the
OT sheet and the payroll take them from there. Holiday hours stay capped at 8 hours a holiday of the month.
A new upload replaces one still waiting; a newly approved sheet replaces the one approved before.
The sheet may also carry each person's paid days (Robel, 11 Nov 2026: the days on the payroll attendance, Sundays
included, are what is paid): once approved, those days are the month's paid days instead of the records'."""
import re
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

OT = ('ot_day', 'ot_night', 'ot_rest', 'ot_holiday')


def _key(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _num(v):
    try:
        return round(float(str(v).replace(',', '').strip() or 0), 2) if v not in (None, '') else 0.0
    except ValueError:
        return 0.0


class SbAttOtUpload(models.Model):
    _name = 'sb.att.ot.upload'
    _description = 'Overtime sheet of a month (uploaded, approved by HR)'
    _inherit = ['mail.thread']
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    period = fields.Char(required=True, index=True)
    name = fields.Char(required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([('submitted', 'Waiting for HR'), ('approved', 'Approved'), ('refused', 'Refused'),
                              ('replaced', 'Replaced')], default='submitted', required=True, tracking=True)
    uploaded_by = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    decided_by = fields.Many2one('res.users', readonly=True)
    decided_on = fields.Datetime(readonly=True)
    refuse_reason = fields.Char()
    filename = fields.Char()
    file = fields.Binary(attachment=True)
    line_ids = fields.One2many('sb.att.ot.upload.line', 'upload_id')


class SbAttOtUploadLine(models.Model):
    _name = 'sb.att.ot.upload.line'
    _description = 'Overtime sheet line'

    upload_id = fields.Many2one('sb.att.ot.upload', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', required=True)
    ot_day = fields.Float()
    ot_night = fields.Float()
    ot_rest = fields.Float()
    ot_holiday = fields.Float()
    rec_day = fields.Float()  # what the records said when it was uploaded
    rec_night = fields.Float()
    rec_rest = fields.Float()
    rec_holiday = fields.Float()
    has_ot = fields.Boolean('Overtime given', default=True,
                            help='The sheet gives the overtime of this person. Off: their overtime comes from Attendance (the '
                                 'records of the machines), unless their manager refuses it.')
    has_days = fields.Boolean('Paid days given', help='The sheet gives the paid days of this person for the month.')
    paid_days = fields.Float('Paid days')
    rec_paid_days = fields.Float()


class SbAttMonth(models.AbstractModel):
    _inherit = 'sb.att.month'

    @api.model
    def ot_sheet(self, company, df, dt):
        return self.env['sb.att.ot.upload'].sudo().search([('company_id', '=', company.id), ('date_from', '=', df),
                                                            ('date_to', '=', dt), ('state', '=', 'approved')], limit=1)

    @api.model
    def rows(self, company, df, dt, people=None):
        out = super().rows(company, df, dt, people)
        sheet = self.ot_sheet(company, df, dt)
        if sheet:
            G = self.env['sb.att.engine']
            cap = 8.0 * len(G._holidays(company, df, dt, {}))
            month = company.sb_pay_days or 30.0
            span = [df + timedelta(days=i) for i in range((dt - df).days + 1)]
            for l in sheet.line_ids:
                r = out.get(l.employee_id.id)
                if r is None:
                    continue
                if l.has_ot:
                    for f in OT:
                        r[f] = round(l[f], 2)
                    r['ot_sheet'] = True
                # not on the overtime sheet: the overtime of the records (the machines), approved or still waiting for their
                # manager; what the manager refuses drops out when the payroll is filled again
                r['ot_holiday'] = min(r['ot_holiday'], cap)
                if l.has_days:  # the payroll attendance's days are the paid days (at most the days of a month)
                    r['paid_days'] = r['days_worked'] = min(round(l.paid_days, 2), month)
                    r['days_sheet'] = True
                    # absent = the days employed this month that are not paid (the machine's count no longer applies)
                    employed = min(len([d for d in span if G._employed_paid(l.employee_id, d)]), month)
                    r['absences'] = max(round(employed - r['paid_days'], 2), 0.0)
        return out


class SbAttService(models.AbstractModel):
    _inherit = 'sb.att.service'

    def _is_ot_clerk(self):
        return self._is_hr() or self.env.user.has_group('sonador_attendance.group_ot_clerk')

    def _require_ot_clerk(self):
        if not self._is_ot_clerk():
            raise AccessError(self.env._('Only HR or the person who sends the payroll attendance can do this.'))

    @api.model
    def ctx(self):
        res = super().ctx()
        res['is_ot_clerk'] = self._is_ot_clerk()
        return res

    @api.model
    def month(self, period=False):
        if not self._is_hr():
            self._require_ot_clerk()
            res = super(SbAttService, self.sudo()).month(period)
        else:
            res = super().month(period)
        c = self.env.company
        U = self.env['sb.att.ot.upload'].sudo()
        df, dt = fields.Date.to_date(res['from']), fields.Date.to_date(res['to'])
        dom = [('company_id', '=', c.id), ('date_from', '=', df), ('date_to', '=', dt)]
        approved = U.search(dom + [('state', '=', 'approved')], limit=1)
        waiting = U.search(dom + [('state', '=', 'submitted')], limit=1)
        # where each person's overtime comes from: the month's sheet (manual), else the device most of their days were on
        sheet_people = set(approved.line_ids.filtered('has_ot').mapped('employee_id').ids) if approved else set()
        P = self.env['sb.att.punch'].sudo()
        lo, hi = fields.Datetime.to_datetime(df) - timedelta(hours=3), fields.Datetime.to_datetime(dt) + timedelta(hours=21)
        counts = {}
        if res['lines']:
            for emp, dev, n in P._read_group([('employee_id', 'in', [l['id'] for l in res['lines']]), ('time', '>=', lo), ('time', '<', hi),
                                               ('device_id', '!=', False)], ['employee_id', 'device_id'], ['__count']):
                counts.setdefault(emp.id, []).append((n, dev.id, dev.name))
        for l in res['lines']:
            if l['id'] in sheet_people:
                l['ot_source'], l['ot_source_label'] = 'manual', 'Manual (OT sheet)'
            elif counts.get(l['id']):
                _n, dev_id, dev_name = max(counts[l['id']])
                l['ot_source'], l['ot_source_label'] = 'device:%d' % dev_id, dev_name
            else:
                l['ot_source'], l['ot_source_label'] = 'none', 'No device'
        res['ot_sources'] = sorted({(l['ot_source'], l['ot_source_label']) for l in res['lines']}, key=lambda x: x[1])
        res['ot_sheets'] = {'approved': self._ot_dict(approved) if approved else False,
                            'waiting': self._ot_dict(waiting, compare=True) if waiting else False,
                            'can_upload': self._is_ot_clerk(), 'can_decide': self._is_hr(),
                            'history': [self._ot_dict(u) for u in U.search(dom, limit=8)]}
        return res

    def _ot_dict(self, u, compare=False):
        tz = self.env.user.tz or 'Africa/Addis_Ababa'
        when = lambda d: fields.Datetime.context_timestamp(u.with_context(tz=tz), d).strftime('%d %b %H:%M') if d else ''  # noqa: E731
        d = {'id': u.id, 'state': u.state, 'state_label': dict(u._fields['state'].selection)[u.state],
             'by': u.uploaded_by.name, 'on': when(u.create_date), 'decided_by': u.decided_by.name or '',
             'decided_on': when(u.decided_on), 'reason': u.refuse_reason or '', 'filename': u.filename or '',
             'people': len(u.line_ids), 'hours': round(sum(sum(l[f] for f in OT) for l in u.line_ids), 2),
             'mine': u.uploaded_by == self.env.user}
        if compare:
            rows = []
            for l in u.line_ids.sorted(lambda x: x.employee_id.name or ''):
                new = [l[f] for f in OT]
                old = [l.rec_day, l.rec_night, l.rec_rest, l.rec_holiday]
                rows.append({'name': l.employee_id.name, 'code': l.employee_id.sb_code or '', 'new': new, 'old': old,
                             'changed': any(abs(a - b) > 0.005 for a, b in zip(new, old))})
            d['rows'] = rows
            d['changed'] = len([r for r in rows if r['changed']])
        return d

    @api.model
    def ot_upload(self, period, data, filename):
        """The month's OT sheet (Excel or CSV): Code, Employee, Normal, Night, Weekend, Holiday. Waits for HR."""
        self._require_ot_clerk()
        P = super(SbAttService, self.sudo()).month(period)
        c = self.env.company
        df, dt = fields.Date.to_date(P['from']), fields.Date.to_date(P['to'])
        table = self.env['sb.payroll.export'].sudo()._read_sheet(data, filename)
        hdr = None
        for i, r in enumerate(table):
            cells = [_key(str(x or '')) for x in r]
            if any(c_.startswith('employee') or c_ == 'name' for c_ in cells) and any('night' in c_ for c_ in cells):
                hdr = i
                break
        if hdr is None:
            raise UserError(self.env._('No heading row found: the sheet needs Employee, Normal, Night, Weekend and Holiday '
                                       'columns. Download the template to start from.'))
        cells = [_key(str(x or '')) for x in table[hdr]]

        def col(*names):
            return next((j for j, c_ in enumerate(cells) if any(n in c_ for n in names)), None)
        ci = {'code': col('code'), 'name': col('employee', 'name'), 'ot_day': col('normal', 'regular', 'day'),
              'ot_night': col('night'), 'ot_rest': col('weekend', 'rest'), 'ot_holiday': col('holiday'),
              'paid_days': col('paiddays', 'paydays', 'daysworked')}
        if ci['name'] is None and ci['code'] is None:
            raise UserError(self.env._('The sheet needs an Employee or Code column.'))
        people = {l['id']: l for l in P['lines']}
        E = self.env['hr.employee'].sudo().with_context(active_test=False).browse(list(people))
        by_code = {_key(e.sb_code): e for e in E if e.sb_code}
        by_name = {}
        for e in E:
            by_name.setdefault(_key(e.name), []).append(e)
        lines, unknown, seen = [], [], set()
        for r in table[hdr + 1:]:
            def val(k):
                j = ci.get(k)
                return r[j] if j is not None and j < len(r) else None
            name, code = str(val('name') or '').strip(), _key(str(val('code') or ''))
            if not name and not code:
                continue
            e = by_code.get(code) if code else None
            if not e:
                hits = by_name.get(_key(name), [])
                e = hits[0] if len(hits) == 1 else None
            if not e:
                unknown.append(name or code)
                continue
            if e.id in seen:
                raise UserError(self.env._('%s is on the sheet twice.', e.name))
            seen.add(e.id)
            rec = people[e.id]
            vals = {f: _num(val(f)) for f in OT}
            if any(v < 0 for v in vals.values()):
                raise UserError(self.env._('%s: hours cannot be negative.', e.name))
            days = val('paid_days')
            if days not in (None, ''):
                d = _num(days)
                if d < 0 or d > 31:
                    raise UserError(self.env._('%s: paid days must be between 0 and the days of the month.', e.name))
                vals.update(has_days=True, paid_days=d, rec_paid_days=rec.get('paid_days', 0))
            lines.append((0, 0, dict(vals, employee_id=e.id, rec_day=rec.get('ot_day', 0), rec_night=rec.get('ot_night', 0),
                                     rec_rest=rec.get('ot_rest', 0), rec_holiday=rec.get('ot_holiday', 0))))
        if not lines:
            raise UserError(self.env._('Nobody on the sheet was found among this month\'s staff.'))
        U = self.env['sb.att.ot.upload'].sudo()
        U.search([('company_id', '=', c.id), ('date_from', '=', df), ('date_to', '=', dt), ('state', '=', 'submitted')]).write(
            {'state': 'replaced'})
        u = U.create({'company_id': c.id, 'period': P['period'], 'name': 'OT sheet %s' % P['name'], 'date_from': df, 'date_to': dt,
                      'uploaded_by': self.env.uid, 'filename': filename, 'file': data, 'line_ids': lines})
        hr = self.env.ref('sonador_books.group_sb_hr').sudo().all_user_ids.filtered(
            lambda x: c in x.company_ids and x.active and not x.share and x != self.env.user)
        u.message_post(body=Markup('<p>%s uploaded the overtime sheet of %s: %d people, %s hours. It waits for HR approval '
                                   'in Attendance › Days worked › OT sheet.</p>') % (
            self.env.user.name, P['name'], len(lines), '{:,.2f}'.format(u and sum(sum(l[f] for f in OT) for l in u.line_ids))),
                       partner_ids=hr.partner_id.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        res = self.month(P['period'])
        res['ot_result'] = {'people': len(lines), 'unknown': unknown}
        return res

    @api.model
    def ot_decide(self, upload_id, approve, reason=''):
        self._require_hr()
        u = self.env['sb.att.ot.upload'].sudo().browse(int(upload_id)).exists()
        if not u or u.state != 'submitted':
            raise UserError(self.env._('This sheet is no longer waiting for a decision.'))
        if u.uploaded_by == self.env.user:
            raise UserError(self.env._('Another HR person approves the sheet you uploaded.'))
        if not approve and not (reason or '').strip():
            raise UserError(self.env._('Say why the sheet is refused.'))
        if approve:
            u.search([('company_id', '=', u.company_id.id), ('date_from', '=', u.date_from), ('date_to', '=', u.date_to),
                      ('state', '=', 'approved')]).write({'state': 'replaced'})
        u.write({'state': 'approved' if approve else 'refused', 'decided_by': self.env.uid,
                 'decided_on': fields.Datetime.now(), 'refuse_reason': False if approve else reason.strip()})
        u.message_post(body=Markup('<p>%s %s the overtime sheet.%s</p>') % (
            self.env.user.name, 'approved' if approve else 'refused', Markup(' <i>%s</i>') % reason if reason else ''),
                       partner_ids=u.uploaded_by.partner_id.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        return self.month(u.period)
