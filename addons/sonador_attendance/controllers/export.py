"""Attendance > Days worked / OT sheet: the month as an Excel workbook, a sheet per department and a summary sheet."""
import io
import re

from odoo import http
from odoo.http import content_disposition, request

COLS = {
    'days': [('days_worked', 'Days worked'), ('paid_days', 'Paid days'), ('annual_leave', 'Annual leave'),
             ('sick_leave', 'Sick leave'), ('other_leave', 'Other leave'), ('absences', 'Absent'), ('late_days', 'Late (days)'),
             ('late_min', 'Late (min)')],
    'ot': [('ot_day', 'Normal OT (h)'), ('ot_night', 'Night OT (h)'), ('ot_rest', 'Weekend OT (h)'), ('ot_holiday', 'Holiday OT (h)')],
}


def _sheet_name(name, used):
    base = re.sub(r'[\[\]:*?/\\]', '-', name or 'No department')[:31] or 'Sheet'
    n, out = 2, base
    while out.lower() in used:
        suffix = ' (%d)' % n
        out, n = base[:31 - len(suffix)] + suffix, n + 1
    used.add(out.lower())
    return out


class AttendanceExport(http.Controller):

    @http.route('/sonador_attendance/month_xlsx', type='http', auth='user')
    def month_xlsx(self, period=None, view='days', **kw):
        if not request.env['sb.att.service']._is_ot_clerk():
            return request.not_found()
        view = view if view in COLS else 'days'
        P = request.env['sb.att.service'].month(period or False)
        cols = COLS[view]
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        f = {'title': wb.add_format({'bold': True, 'font_size': 13}), 'sub': wb.add_format({'font_color': '#6B7A85'}),
             'head': wb.add_format({'bold': True, 'bg_color': '#243742', 'font_color': '#FFFFFF', 'border': 1, 'text_wrap': True}),
             'cell': wb.add_format({'border': 1}), 'num': wb.add_format({'border': 1, 'num_format': '0.##'}),
             'tot': wb.add_format({'bold': True, 'top': 2, 'num_format': '0.##'}), 'totl': wb.add_format({'bold': True, 'top': 2})}
        title = '%s: %s' % ('Days worked' if view == 'days' else 'OT sheet', P.get('name') or P.get('period'))
        sub = '%s – %s  (%s to %s)' % (P.get('from_ec', ''), P.get('to_ec', ''), P.get('from', ''), P.get('to', ''))
        groups = {}
        for l in P['lines']:
            groups.setdefault(l.get('department') or 'No department', []).append(l)

        def write(ws, lines, heading):
            ws.write(0, 0, heading, f['title'])
            ws.write(1, 0, sub, f['sub'])
            head = ['Code', 'Employee'] + [c[1] for c in cols] + ['Attendance exempt']
            ex = len(head) - 1
            for c, h in enumerate(head):
                ws.write(3, c, h, f['head'])
            r = 4
            for l in sorted(lines, key=lambda x: x.get('name') or ''):
                ws.write(r, 0, l.get('code') or '', f['cell'])
                ws.write(r, 1, l.get('name') or '', f['cell'])
                for c, (k, _h) in enumerate(cols, start=2):
                    ws.write_number(r, c, float(l.get(k) or 0), f['num'])
                ws.write(r, ex, 'Exempt' if l.get('exempt') else '', f['cell'])
                r += 1
            ws.write(r, 1, '%d people' % len(lines), f['totl'])
            for c, (k, _h) in enumerate(cols, start=2):
                ws.write_number(r, c, sum(float(l.get(k) or 0) for l in lines), f['tot'])
            ws.write_number(r, ex, sum(1 for l in lines if l.get('exempt')), f['tot'])
            ws.set_column(0, 0, 11)
            ws.set_column(1, 1, 30)
            ws.set_column(2, len(head) - 1, 11)
            ws.freeze_panes(4, 2)

        used = set()
        # the summary: one row per department
        ws = wb.add_worksheet(_sheet_name('Summary', used))
        ws.write(0, 0, title, f['title'])
        ws.write(1, 0, sub, f['sub'])
        head = ['Department', 'People'] + [c[1] for c in cols] + ['Attendance exempt']
        for c, h in enumerate(head):
            ws.write(3, c, h, f['head'])
        r = 4
        for dept in sorted(groups):
            ls = groups[dept]
            ws.write(r, 0, dept, f['cell'])
            ws.write_number(r, 1, len(ls), f['num'])
            for c, (k, _h) in enumerate(cols, start=2):
                ws.write_number(r, c, sum(float(l.get(k) or 0) for l in ls), f['num'])
            ws.write_number(r, len(head) - 1, sum(1 for l in ls if l.get('exempt')), f['num'])
            r += 1
        ws.write(r, 0, 'All', f['totl'])
        ws.write_number(r, 1, len(P['lines']), f['tot'])
        for c, (k, _h) in enumerate(cols, start=2):
            ws.write_number(r, c, sum(float(l.get(k) or 0) for l in P['lines']), f['tot'])
        ws.write_number(r, len(head) - 1, sum(1 for l in P['lines'] if l.get('exempt')), f['tot'])
        ws.set_column(0, 0, 30)
        ws.set_column(1, len(head) - 1, 11)
        for dept in sorted(groups):
            write(wb.add_worksheet(_sheet_name(dept, used)), groups[dept], '%s · %s' % (title, dept))
        wb.close()
        name = '%s %s by department.xlsx' % ('Days worked' if view == 'days' else 'OT sheet', P.get('name') or P.get('period'))
        return request.make_response(buf.getvalue(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(name))])

    @http.route('/sonador_attendance/ot_template', type='http', auth='user')
    def ot_template(self, period=None, **kw):
        """The month's OT sheet to fill: everyone paid this month, with the hours from the records. Upload it back on
        Attendance > Days worked > OT sheet."""
        if not request.env['sb.att.service']._is_ot_clerk():
            return request.not_found()
        P = request.env['sb.att.service'].month(period or False)
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        ws = wb.add_worksheet('OT sheet')
        head = wb.add_format({'bold': True, 'bg_color': '#243742', 'font_color': '#FFFFFF', 'border': 1, 'text_wrap': True})
        cell = wb.add_format({'border': 1})
        num = wb.add_format({'border': 1, 'num_format': '0.##', 'bg_color': '#FFF8E1'})
        ws.write(0, 0, 'Overtime sheet: %s (%s to %s)' % (P.get('name'), P.get('from'), P.get('to')), wb.add_format({'bold': True, 'font_size': 13}))
        ws.write(1, 0, 'Fill the real hours in the yellow columns (0 for none; do not delete rows), then upload it on '
                       'Attendance > Days worked > OT sheet. HR approves it before payroll.', wb.add_format({'font_color': '#6B7A85'}))
        cols = ['Code', 'Employee', 'Department', 'Normal (h)', 'Night (h)', 'Weekend (h)', 'Holiday (h)']
        for c, h in enumerate(cols):
            ws.write(3, c, h, head)
        r = 4
        for l in sorted(P['lines'], key=lambda x: ((x.get('department') or ''), x.get('name') or '')):
            if l.get('exempt'):
                continue
            ws.write(r, 0, l.get('code') or '', cell)
            ws.write(r, 1, l.get('name') or '', cell)
            ws.write(r, 2, l.get('department') or '', cell)
            for c, k in enumerate(('ot_day', 'ot_night', 'ot_rest', 'ot_holiday'), start=3):
                ws.write_number(r, c, float(l.get(k) or 0), num)
            r += 1
        ws.set_column(0, 0, 11)
        ws.set_column(1, 1, 30)
        ws.set_column(2, 2, 24)
        ws.set_column(3, 6, 11)
        ws.freeze_panes(4, 2)
        wb.close()
        return request.make_response(buf.getvalue(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('OT sheet %s (to fill).xlsx' % (P.get('name') or P.get('period'))))])


class AttendanceOtApproval(http.Controller):

    @http.route('/sonador_attendance/ot_approval_xlsx', type='http', auth='user')
    def ot_approval_xlsx(self, period=None, source='all', **kw):
        """Overtime for a signed approval: one list (Name, Normal, Night, Weekend, Holiday), of one source (a device or
        the manual sheet) or all, with the signature lines under it."""
        S = request.env['sb.att.service']
        if not S._is_ot_clerk():
            return request.not_found()
        P = S.month(period or False)
        lines = [l for l in P['lines'] if source in ('all', '', None) or l.get('ot_source') == source]
        lines = [l for l in lines if any(float(l.get(k) or 0) for k in ('ot_day', 'ot_night', 'ot_rest', 'ot_holiday'))]
        label = 'All sources' if source in ('all', '', None) else next((lb for k, lb in P.get('ot_sources', []) if k == source), source)
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        ws = wb.add_worksheet('Overtime')
        f = {'title': wb.add_format({'bold': True, 'font_size': 14}), 'sub': wb.add_format({'font_color': '#555555'}),
             'head': wb.add_format({'bold': True, 'bg_color': '#243742', 'font_color': '#FFFFFF', 'border': 1, 'align': 'center'}),
             'cell': wb.add_format({'border': 1}), 'num': wb.add_format({'border': 1, 'num_format': '0.00'}),
             'tot': wb.add_format({'bold': True, 'top': 2, 'bottom': 2, 'num_format': '0.00'}), 'totl': wb.add_format({'bold': True, 'top': 2, 'bottom': 2}),
             'sig': wb.add_format({'top': 1}), 'lab': wb.add_format({'bold': True})}
        company = request.env.company.name
        ws.write(0, 0, company, f['title'])
        ws.write(1, 0, 'Overtime for approval: %s  ·  %s' % (P.get('name') or P.get('period'), label), f['lab'])
        ws.write(2, 0, '%s – %s  (%s to %s)' % (P.get('from_ec', ''), P.get('to_ec', ''), P.get('from', ''), P.get('to', '')), f['sub'])
        head = ['No.', 'Name', 'Normal (h)', 'Night (h)', 'Weekend (h)', 'Holiday (h)', 'Total (h)']
        for c, h in enumerate(head):
            ws.write(4, c, h, f['head'])
        r = 5
        keys = ('ot_day', 'ot_night', 'ot_rest', 'ot_holiday')
        for i, l in enumerate(sorted(lines, key=lambda x: x.get('name') or ''), 1):
            ws.write_number(r, 0, i, f['cell'])
            ws.write(r, 1, l.get('name') or '', f['cell'])
            for c, k in enumerate(keys, 2):
                ws.write_number(r, c, float(l.get(k) or 0), f['num'])
            ws.write_number(r, 6, sum(float(l.get(k) or 0) for k in keys), f['num'])
            r += 1
        ws.write(r, 1, '%d people' % len(lines), f['totl'])
        ws.write(r, 0, '', f['totl'])
        for c, k in enumerate(keys, 2):
            ws.write_number(r, c, sum(float(l.get(k) or 0) for l in lines), f['tot'])
        ws.write_number(r, 6, sum(float(l.get(k) or 0) for l in lines for k in keys), f['tot'])
        r += 4
        for c, who in ((1, 'Prepared by'), (3, 'Checked by (HR)'), (5, 'Approved by')):
            ws.write(r, c, who, f['lab'])
            ws.write(r + 3, c, 'Name, signature, date', f['sig'])
        ws.set_column(0, 0, 5)
        ws.set_column(1, 1, 32)
        ws.set_column(2, 6, 12)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(4)
        wb.close()
        name = 'Overtime for approval %s %s.xlsx' % (P.get('name') or P.get('period'), label)
        return request.make_response(buf.getvalue(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(name))])
