"""What the report designer screen calls: my reports, open one (blocks filled for its period), save words and blocks,
send (figures frozen), the manager's accept / return, and the exports."""
import base64
import io
import re
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

from . import periods as P
from .blocks import DEPARTMENTS

# figures that add up over time, compared period by period in the quarterly, half-yearly and yearly reports
TREND = {'sales_summary': ('Revenue', 'Litres', 'Invoices and cash sales', 'Returns'), 'collections': ('Collected', 'Credit sales billed'),
         'production': ('Litres produced',), 'deliveries': ('Delivery orders',), 'vat_wht': ('VAT payable',)}
GRAIN_TITLE = {'week': 'Week by week', 'month': 'Month by month', 'quarter': 'Quarter by quarter', 'half': 'First half against second half'}


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbRepService(models.AbstractModel):
    _name = 'sb.rep.service'
    _description = 'Report designer'

    def _me(self):
        return self.env.user.employee_ids.filtered(lambda e: e.company_id == self.env.company)[:1] or self.env.user.employee_ids[:1]

    def _now(self):
        """Now on the company's clock (the report windows are local times)."""
        tz = pytz.timezone(self.env.user.tz or self.env.company.partner_id.tz or 'Africa/Addis_Ababa')
        return datetime.now(pytz.utc).astimezone(tz).replace(tzinfo=None)

    def _label(self, r):
        return P.label(r.period, r.date_from, r.date_to)

    def _is_open(self, r):
        return r.late or P.is_open(r.period, r.date_from, r.date_to, self._now())

    def _closes(self, r):
        _o, c = P.window(r.period, r.date_from, r.date_to)
        if not c:
            return ''
        # a window closing at midnight is open the whole day before
        return '%s %s' % (format_ethiopian(c.date()), c.strftime('%H:%M')) if c.hour else format_ethiopian(c.date() - timedelta(days=1))

    def _end(self, r):
        """Blocks run to today while the period is still running (the yearly report: the year to date)."""
        return min(r.date_to, self._now().date())

    def _reads_all(self):
        return self.env.user in self.env.company.sudo().sb_rep_reader_ids

    def _templates_for(self, me):
        """The reports this person writes, by role: head of the template's department, named on it, or holding its role;
        the general manager any of them."""
        T = self.env['sb.rep.template'].sudo().search([('company_id', 'in', (False, self.env.company.id))])
        return T if self._reads_all() else T.filtered(lambda t: t._sb_writers_include(me))

    def _can_read(self, r):
        user = self.env.user
        # the author, their manager and the managers above; nobody else, finance included
        above = self.env['res.users']
        boss = r.employee_id.sudo().parent_id
        while boss and boss.user_id not in above:
            above |= boss.user_id
            boss = boss.parent_id
        if self.env.su or user in (r.user_id | r.manager_id | above) or self._reads_all() or user.has_group('base.group_system'):
            return True
        raise AccessError(self.env._('This report is not yours to read.'))

    # ------------------------------------------------------------------ lists
    @api.model
    def home(self):
        me = self._me()
        R = self.env['sb.rep.report'].sudo()
        mine = R.search([('user_id', '=', self.env.uid)], limit=40)
        to_read = R.search([('manager_id', '=', self.env.uid), ('state', '=', 'sent')])
        read = R.search([('manager_id', '=', self.env.uid), ('state', 'in', ('accepted', 'returned'))], limit=20)
        # the general manager: every report sent in the company, besides those sent to them
        now = self._now()
        opens, earlier = [], []  # the newest open period of each kind as a button, the rest under 'Earlier periods'
        for k, name in P.KINDS:
            for i, (a, _b, lab) in enumerate(P.open_periods(k, now, self.env.company)):
                (opens if i == 0 or k == 'week' else earlier).append({'kind': k, 'kind_name': name, 'from': _s(a), 'label': lab})
        everyone = R.search([('company_id', '=', self.env.company.id), ('state', '!=', 'draft'), ('user_id', '!=', self.env.uid),
                             ('manager_id', '!=', self.env.uid)], limit=60) if self._reads_all() else R
        row = lambda r: {'id': r.id, 'name': r.template_id.name, 'who': r.employee_id.name, 'period': self._label(r),  # noqa: E731
                         'state': r.state, 'kind': dict(P.KINDS).get(r.period), 'late': r.late}
        return {'me': me.name if me else self.env.user.name,
                'templates': [{'id': t.id, 'name': t.name, 'open': opens, 'earlier': earlier} for t in self._templates_for(me)],
                'next': [{'kind': dict(P.KINDS)[k], 'when': P.next_opening(k, now, self.env.company)} for k, _n in P.KINDS
                         if k != 'year' and not any(o['kind'] == k for o in opens)],
                'mine': [row(r) for r in mine], 'to_read': [row(r) for r in to_read], 'read': [row(r) for r in read],
                'everyone': [row(r) for r in everyone],
                'can_design': self.env.user.has_group('sonador_books.group_sb_manager'),
                'reopen': self._reopen_list(now) if self._reads_all() else []}

    @api.model
    def start(self, template_id, kind='week', date_from=False):
        """My report of this template for an open period: the existing one, else a new one from the template."""
        me = self._me()
        if not me:
            raise UserError(self.env._('Your login is not linked to an employee: ask HR.'))
        t = self.env['sb.rep.template'].sudo().browse(int(template_id)).exists()
        if t not in self._templates_for(me):
            raise AccessError(self.env._('This report is written by another role.'))
        span = [x for x in P.open_periods(kind, self._now(), self.env.company) if not date_from or _s(x[0]) == date_from]
        if not span:
            raise UserError(self.env._('This report is not open now. It opens on %s.', P.next_opening(kind, self._now(), self.env.company)))
        a, b, _n = span[0]
        return self._report_for(t, me, kind, a, b).id

    def _report_for(self, t, employee, kind, a, b, late=False):
        """The person's report of this template and period: the existing one, else a new one from the template."""
        R = self.env['sb.rep.report'].sudo()
        r = R.search([('template_id', '=', t.id), ('employee_id', '=', employee.id), ('period', '=', kind), ('date_from', '=', a)], limit=1)
        if r:
            return r
        noun = P.NOUN[kind]
        word = lambda txt: re.sub(r'\b(week|month)\b', noun, txt or '') or False  # noqa: E731  'Next week's focus' -> 'Next quarter's focus'
        sections = [(0, 0, {'sequence': s.sequence, 'title': s.title, 'prompt': word(s.prompt), 'required': s.required,
                            'block_codes': s.block_codes}) for s in t.section_ids]
        grains = self._grains(kind, ','.join(t.section_ids.mapped('block_codes')))
        if grains:
            sections.append((0, 0, {'sequence': 1000, 'title': self.env._('Compared'), 'required': False,
                                    'prompt': self.env._('What changed from one period to the next, and why'),
                                    'block_codes': ','.join('trend_' + g for g in grains)}))
        return R.create({'template_id': t.id, 'employee_id': employee.id, 'user_id': employee.user_id.id or self.env.uid, 'period': kind,
                         'date_from': a, 'date_to': b, 'late': late, 'manager_id': employee.parent_id.user_id.id or False,
                         'section_ids': sections})

    # ------------------------------------------------------------------ the general manager reopens a closed week or month
    def _reopen_list(self, now):
        """For the general manager: the week and the month just before the current ones, per report and writer, with
        where each stands; one not sent can be reopened for its writer."""
        R = self.env['sb.rep.report'].sudo()
        out = []
        for kind in ('week', 'month'):
            prev = P.previous(kind, now, self.env.company)
            if not prev:
                continue
            a, _b, lab = prev
            for t in self.env['sb.rep.template'].sudo().search([('company_id', 'in', (False, self.env.company.id))]):
                for e in t._sb_writers():
                    if not e.user_id:
                        continue
                    r = R.search([('template_id', '=', t.id), ('employee_id', '=', e.id), ('period', '=', kind), ('date_from', '=', a)], limit=1)
                    out.append({'template_id': t.id, 'employee_id': e.id, 'kind': kind, 'kind_name': dict(P.KINDS)[kind], 'from': _s(a),
                                'label': lab, 'report': t.name, 'who': e.name, 'state': r.state if r else 'none',
                                'reopened': bool(r and r.late)})
        return out

    @api.model
    def reopen(self, template_id, employee_id, kind, date_from):
        if not self._reads_all():
            raise AccessError(self.env._('Only the general manager reopens a closed report.'))
        prev = P.previous(kind, self._now(), self.env.company)
        if not prev or _s(prev[0]) != date_from:
            raise UserError(self.env._('Only the week or month just before the current one can be reopened.'))
        t = self.env['sb.rep.template'].sudo().browse(int(template_id)).exists()
        e = self.env['hr.employee'].sudo().browse(int(employee_id)).exists()
        r = self._report_for(t, e, kind, prev[0], prev[1], late=True)
        if r.state != 'draft':
            raise UserError(self.env._('%s already sent this report.', e.name))
        if not r.late:
            r.late = True
        r.message_post(body=self.env._('Reopened by %s: write it and send it now.', self.env.user.name), message_type='comment',
                       subtype_xmlid='mail.mt_note', partner_ids=r.user_id.partner_id.ids)
        return self._reopen_list(self._now())

    def _grains(self, kind, codes):
        return P.GRAINS.get(kind, []) if any(c in TREND for c in (codes or '').split(',')) else []

    def _trend(self, r, grain):
        """A comparison table: the report's adding-up figures (sales, collections, production ...) per week, month, quarter or
        half. In the half-year and yearly reports quarters and halves run from the start of the fiscal year (Q1 and Q2 against
        Q3 and Q4)."""
        end = self._end(r)
        codes = [c for s in r.section_ids for c in (s.block_codes or '').split(',') if c in TREND]
        codes = list(dict.fromkeys(codes))
        start = r.date_from
        if r.period in ('half', 'year') and grain in ('quarter', 'half'):
            start = fiscal_periods(fiscal_year_of(r.date_from))[0][2]
        rows_span = P.sub_spans(grain, start, r.date_to, end, self.env.company)
        title = self.env._(GRAIN_TITLE[grain])
        if not codes or (grain == 'half' and len(rows_span) < 2):
            return {'title': title, 'note': self.env._('Shows once there is more than one period to compare.') if codes else ''}
        B = self.env['sb.rep.blocks'].with_user(r.user_id)
        cols, money_cols, rows = [], [], []
        for x, y, name in rows_span:
            vals = {}
            for c in codes:
                out = B.run(c, x, y, r.employee_id, r.template_id.config)
                for k in out.get('kpis') or []:
                    if k['label'] in TREND[c] and isinstance(k['value'], (int, float)):
                        vals[k['label']] = k['value']
                        if k['label'] not in cols:
                            cols.append(k['label'])
                            if k.get('money'):
                                money_cols.append(len(cols))
            rows.append([name] + [vals.get(k, 0) for k in cols])
        rows = [row + [0] * (len(cols) + 1 - len(row)) for row in rows]
        total = [self.env._('Total')] + [round(sum(row[i] for row in rows), 2) for i in range(1, len(cols) + 1)]
        return {'title': title, 'table': {'columns': [self.env._('Period')] + cols, 'money': money_cols, 'rows': rows, 'total': total}}

    def _run_block(self, r, code):
        if code.startswith('trend_'):
            return dict(self._trend(r, code[6:]), code=code)
        return self.env['sb.rep.blocks'].with_user(r.user_id).run(code, r.date_from, self._end(r), r.employee_id, r.template_id.config,
                                                                   kind=r.period)

    # ------------------------------------------------------------------ one report
    @api.model
    def load(self, report_id):
        r = self.env['sb.rep.report'].sudo().browse(int(report_id)).exists()
        self._can_read(r)
        frozen = r.state != 'draft' and r.snapshot
        blocks = {}
        if frozen:
            blocks = r.snapshot.get('blocks', {})
        else:
            for s in r.section_ids:
                for code in (s.block_codes or '').split(','):
                    if code and code not in blocks:
                        blocks[code] = self._run_block(r, code)
        return {'id': r.id, 'name': r.template_id.name, 'who': r.employee_id.name, 'period': self._label(r),
                'from': _s(r.date_from), 'to': _s(r.date_to), 'state': r.state, 'frozen': bool(frozen), 'kind': dict(P.KINDS).get(r.period),
                'open': r.state == 'returned' or self._is_open(r), 'closes': self._closes(r), 'late': r.late,
                'mine': r.user_id == self.env.user, 'manager': r.manager_id.name or '', 'is_manager': r.manager_id == self.env.user,
                'manager_note': r.manager_note or '', 'sent_at': fields.Datetime.to_string(r.sent_at) if r.sent_at else '',
                'sections': [{'id': s.id, 'title': s.title, 'prompt': s.prompt or '', 'required': s.required, 'narrative': s.narrative or '',
                              'blocks': [c for c in (s.block_codes or '').split(',') if c]} for s in r.section_ids],
                'blocks': blocks,
                'library': self.env['sb.rep.blocks'].catalog(), 'departments': [{'code': c, 'name': n} for c, n in DEPARTMENTS],
                'currency': r.company_id.currency_id.symbol or r.company_id.currency_id.name}

    @api.model
    def block(self, report_id, code):
        r = self.env['sb.rep.report'].sudo().browse(int(report_id)).exists()
        self._can_read(r)
        return self._run_block(r, code)

    @api.model
    def save(self, report_id, sections):
        r = self.env['sb.rep.report'].sudo().browse(int(report_id)).exists()
        if r.user_id != self.env.user:
            raise AccessError(self.env._('Only the author changes a report.'))
        if r.state not in ('draft', 'returned'):
            raise UserError(self.env._('The report was sent: it is read only.'))
        if r.state == 'draft' and not self._is_open(r):
            raise UserError(self.env._('This report was open until %s. The general manager can reopen it for you.', self._closes(r)))
        for s in sections:
            rec = r.section_ids.filtered(lambda x: x.id == s['id'])
            rec.write({'narrative': s.get('narrative') or False, 'block_codes': ','.join(s.get('blocks') or [])})
        if r.state == 'returned':
            r.state = 'draft'
        return True

    @api.model
    def send(self, report_id):
        r = self.env['sb.rep.report'].sudo().browse(int(report_id)).exists()
        if r.user_id != self.env.user:
            raise AccessError(self.env._('Only the author sends a report.'))
        if r.state == 'draft' and not self._is_open(r):
            raise UserError(self.env._('This report was open until %s. The general manager can reopen it for you.', self._closes(r)))
        empty = r.section_ids.filtered(lambda s: s.required and not (s.narrative or '').strip())
        if empty:
            raise UserError(self.env._('Write something under: %s', ', '.join(empty.mapped('title'))))
        if not r.manager_id:
            raise UserError(self.env._('Nobody is set as your manager: ask HR.'))
        data = self.load(r.id)
        r.write({'state': 'sent', 'snapshot': {'blocks': data['blocks']}, 'sent_at': fields.Datetime.now(), 'manager_note': False})
        body = self.env._('Sent late to %s.', r.manager_id.name) if r.late else self.env._('Sent to %s.', r.manager_id.name)
        r.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return True

    @api.model
    def decide(self, report_id, accept, note=''):
        r = self.env['sb.rep.report'].sudo().browse(int(report_id)).exists()
        if r.manager_id != self.env.user and not self.env.su:
            raise AccessError(self.env._('Only %s decides this report.', r.manager_id.name))
        if not accept and not (note or '').strip():
            raise UserError(self.env._('Say what to change: the author sees your note.'))
        r.write({'state': 'accepted' if accept else 'returned', 'manager_note': note or False, 'decided_at': fields.Datetime.now()})
        return True

    # ------------------------------------------------------------------ exports
    @api.model
    def export(self, report_id, kind):
        data = self.load(report_id)
        name = '%s %s' % (data['name'], data['period'])
        if kind == 'xlsx':
            content, mime, ext = self._xlsx(data), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'
        elif kind == 'pptx':
            content = self.env['sb.rep.pptx'].build(data)
            mime, ext = 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'pptx'
        else:
            r = self.env['sb.rep.report'].sudo().browse(int(report_id))
            content, _t = self.env['ir.actions.report'].sudo()._render_qweb_pdf('sonador_report_designer.action_report_pdf', r.ids,
                                                                                data={'report': data})
            mime, ext = 'application/pdf', 'pdf'
        att = self.env['ir.attachment'].sudo().create({'name': '%s.%s' % (name.replace('/', '-'), ext), 'datas': base64.b64encode(content),
                                                       'mimetype': mime, 'res_model': 'sb.rep.report', 'res_id': int(report_id)})
        return '/web/content/%d?download=true' % att.id

    def _xlsx(self, data):
        import xlsxwriter
        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {'in_memory': True})
        bold = wb.add_format({'bold': True})
        num = wb.add_format({'num_format': '#,##0.00'})
        head = wb.add_format({'bold': True, 'bg_color': '#E8EEF6', 'border': 1})
        ws = wb.add_worksheet('Report')
        ws.write(0, 0, data['name'], bold)
        ws.write(1, 0, '%s · %s' % (data['who'], data['period']))
        row = 3
        for s in data['sections']:
            ws.write(row, 0, s['title'], bold)
            row += 1
            if s['narrative']:
                ws.write(row, 0, s['narrative'])
                row += 1
            row += 1
        ws.set_column(0, 0, 90)
        used = set()
        for s in data['sections']:
            for code in s['blocks']:
                b = data['blocks'].get(code) or {}
                if code in used or not b:
                    continue
                used.add(code)
                sh = wb.add_worksheet(((b.get('title') or code)[:28]).replace('/', '-'))
                sh.write(0, 0, b.get('title') or code, bold)
                r = 2
                for k in b.get('kpis') or []:
                    sh.write(r, 0, k['label'])
                    sh.write(r, 1, k['value'], num if k.get('money') else None)
                    r += 1
                t = b.get('table')
                if t:
                    r += 1
                    for c, col in enumerate(t['columns']):
                        sh.write(r, c, col, head)
                    for line in t['rows'] + ([t['total']] if t.get('total') else []):
                        r += 1
                        for c, v in enumerate(line):
                            sh.write(r, c, v, num if c in (t.get('money') or []) else None)
                    sh.set_column(0, 0, 36)
                    sh.set_column(1, len(t['columns']), 16)
        wb.close()
        return out.getvalue()
