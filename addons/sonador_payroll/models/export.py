"""Payroll files as Eco Green sent them from Odoo 18: the finance workbook by department, the bank workbook, the salary
transfer letter to the bank (printed, signed and stamped), and the overtime sheet HR fills with the real hours and
imports back."""
import base64
import csv
import io
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian
from odoo.addons.sonador_books.models.locale import overtime_rules

OT_COLS = [('ot_day', 'Normal (hours)'), ('ot_night', 'Night (hours)'), ('ot_rest', 'Weekend (hours)'), ('ot_holiday', 'Holiday (hours)')]
# finance columns: (title, value of a line); the optional ones only when some line has an amount
FIN_COLS = [
    ('Basic Salary', lambda l: l.full_basic, True),
    ('Days Worked', lambda l: l.pay_days, True),
    ('Actual Salary', lambda l: l.basic, True),
    ('Taxable Allowance', lambda l: l.taxable_allowance, True),
    ('Non-taxable Allowance', lambda l: l.nontaxable_allowance, True),
    ('House Allowance', lambda l: l.house, True),
    ('Position Allowance', lambda l: l.position, True),
    ('Cash Indemnity', lambda l: l.cash_indemnity, False),
    ('Overtime Pay', lambda l: l.overtime, True),
    ('Commission Amount', lambda l: l.commission, True),
    ('Bonus', lambda l: l.bonus, False),
    ('Final Settlement', lambda l: l.leave_payout + l.severance + l.compensation + l.notice_pay, False),
    ('Gross Salary', lambda l: l.gross, True),
    ('Taxable Salary', lambda l: l.taxable, True),
    ('Income Tax on Salary', lambda l: l._sb_tax_split()[0], False),
    ('Income Tax on Settlement', lambda l: l._sb_tax_split()[1], False),
    ('Income Tax', lambda l: l.income_tax, True),
    ('Pension Employee', lambda l: l.pension_ee, True),
    ('Pension Employer', lambda l: l.pension_er, True),
    ('Other Deductions', lambda l: l.other_deduction + l.benefit_deduction + l.loan_deduction, True),
    ('Net Salary', lambda l: l.net, True),
    ('Notes', lambda l: _note(l), False),
]


def _note(l):
    """What finance needs to read on the line: the note, and how a payment on leaving was taxed."""
    parts = [l.note] if l.note else []
    on_pay, on_settlement, months = l._sb_tax_split()
    if on_settlement:
        parts.append('Payment on leaving = %s months of salary, taxed month by month (Income Tax Reg. 410): %s on the '
                     'salary + %s on the payment (%s a month)' % ('%g' % months, '{:,.2f}'.format(on_pay),
                                                               '{:,.2f}'.format(on_settlement), '{:,.2f}'.format(l._tax(l.full_basic, l.company_id))))
    return '. '.join(parts)


def _key(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _sheet_name(name, used):
    name = (re.sub(r'[\[\]:*?/\\]', ' ', name or '').strip() or 'Sheet')[:31]
    out, n = name, 1
    while out in used:
        n += 1
        tail = ' (%d)' % n
        out = name[:31 - len(tail)] + tail
    used.add(out)
    return out


class SbPayrollExport(models.AbstractModel):
    _name = 'sb.payroll.export'
    _description = 'Payroll files'

    def _lines(self, run):
        return run.line_ids.sorted(lambda l: (self._dept(l), l.employee_id.name or ''))

    @api.model
    def _dept(self, l):
        return l.hr_employee_id.department_id.name or 'No department'

    def _account(self, l):
        acc = l.hr_employee_id.primary_bank_account_id
        return acc.acc_number if acc else ''

    def _period_text(self, run):
        return '%s - %s  (%s - %s)' % (format_ethiopian(run.date_from), format_ethiopian(run.date),
                                       run.date_from.strftime('%d %b %Y'), run.date.strftime('%d %b %Y'))

    def _book(self):
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        f = {
            'title': wb.add_format({'bold': True, 'font_size': 13, 'font_color': '#FFFFFF', 'bg_color': '#1F2D36', 'align': 'center', 'valign': 'vcenter'}),
            'sub': wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#3C5A6B', 'align': 'center'}),
            'head': wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#4F81BD', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True}),
            'dept': wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1}),
            'cell': wb.add_format({'border': 1}),
            'sn': wb.add_format({'border': 1, 'align': 'center'}),
            'num': wb.add_format({'border': 1, 'num_format': '#,##0.00'}),
            'days': wb.add_format({'border': 1, 'num_format': '0.##'}),
            'tot': wb.add_format({'bold': True, 'border': 1, 'bg_color': '#EEF2F7', 'num_format': '#,##0.00'}),
            'grand': wb.add_format({'bold': True, 'border': 2, 'bg_color': '#C9D6E8', 'num_format': '#,##0.00'}),
            'in': wb.add_format({'border': 1, 'bg_color': '#FFF8DC', 'num_format': '0.##'}),
            'note': wb.add_format({'italic': True, 'font_color': '#666666'}),
        }
        return wb, buf, f

    # ------------------------------------------------------------------ finance, by department
    def finance_xlsx(self, run):
        """Summary by department, every line by department with subtotals, and one sheet per department."""
        lines = self._lines(run)
        def shows(fn):
            return any((fn(l) if isinstance(fn(l), str) else abs(fn(l)) > 0.004) for l in lines)
        cols = [(t, fn) for t, fn, always in FIN_COLS if always or shows(fn)]
        if 'Income Tax on Settlement' not in [t for t, _fn in cols]:
            cols = [(t, fn) for t, fn in cols if t != 'Income Tax on Salary']
        elif 'Income Tax on Salary' not in [t for t, _fn in cols]:
            cols.insert([t for t, _fn in cols].index('Income Tax on Settlement'), next((t, fn) for t, fn, _a in FIN_COLS if t == 'Income Tax on Salary'))
        wb, buf, f = self._book()
        used = set()
        depts = []
        for l in lines:
            d = self._dept(l)
            if not depts or depts[-1][0] != d:
                depts.append((d, []))
            depts[-1][1].append(l)
        width = 3 + len(cols)

        def title(ws, sub):
            ws.merge_range(0, 0, 0, width - 1, run.company_id.name, f['title'])
            ws.merge_range(1, 0, 1, width - 1, '%s  |  %s  |  %s' % (run.name, run._reference(), sub), f['sub'])
            ws.merge_range(2, 0, 2, width - 1, self._period_text(run), f['sub'])
            ws.set_column(0, 0, 5)
            ws.set_column(1, 1, 28)
            ws.set_column(2, 2, 12)
            ws.set_column(3, width, 14)
            if cols and cols[-1][0] == 'Notes':
                ws.set_column(width - 1, width - 1, 80)
            ws.set_row(4, 30)
            return 4

        def head(ws, row):
            for c, t in enumerate(['S/N', 'Employee Name', 'TIN'] + [t for t, _fn in cols]):
                ws.write(row, c, t, f['head'])
            return row + 1

        def body(ws, row, group, start=1):
            for i, l in enumerate(group, start):
                ws.write(row, 0, i, f['sn'])
                ws.write(row, 1, l.employee_id.name or '', f['cell'])
                ws.write(row, 2, l.employee_id.tin or '', f['cell'])
                for c, (t, fn) in enumerate(cols, 3):
                    v = fn(l)
                    if isinstance(v, str):
                        ws.write(row, c, v, f['cell'])
                    else:
                        ws.write_number(row, c, round(v, 2), f['days'] if t == 'Days Worked' else f['num'])
                row += 1
            return row

        def total(ws, row, group, label, fmt):
            ws.write(row, 0, '', fmt)
            ws.write(row, 1, label, fmt)
            ws.write(row, 2, '%d people' % len(group), fmt)
            for c, (t, fn) in enumerate(cols, 3):
                if t == 'Notes':
                    ws.write(row, c, '', fmt)
                    continue
                ws.write_number(row, c, round(sum(fn(l) for l in group), 2), fmt)
            return row + 1

        # summary per department: what finance files and pays
        ws = wb.add_worksheet(_sheet_name('Summary', used))
        row = title(ws, 'Summary by department')
        keys = ['Gross Salary', 'Income Tax', 'Pension Employee', 'Pension Employer', 'Net Salary']
        sums = [(t, fn) for t, fn, _a in FIN_COLS if t in keys]
        for c, t in enumerate(['', 'Department', 'People'] + [t for t, _fn in sums]):
            ws.write(row, c, t, f['head'])
        row += 1
        for i, (d, group) in enumerate(depts, 1):
            ws.write(row, 0, i, f['sn'])
            ws.write(row, 1, d, f['cell'])
            ws.write(row, 2, len(group), f['sn'])
            for c, (t, fn) in enumerate(sums, 3):
                ws.write_number(row, c, round(sum(fn(l) for l in group), 2), f['num'])
            row += 1
        ws.write(row, 1, 'Total', f['grand'])
        ws.write(row, 2, len(lines), f['grand'])
        for c, (t, fn) in enumerate(sums, 3):
            ws.write_number(row, c, round(sum(fn(l) for l in lines), 2), f['grand'])

        ws = wb.add_worksheet(_sheet_name('All', used))
        row = title(ws, 'All departments')
        for d, group in depts:
            ws.merge_range(row, 0, row, width - 1, 'Department: %s' % d, f['dept'])
            row = body(ws, head(ws, row + 1), group)
            row = total(ws, row, group, 'Total %s' % d, f['tot']) + 1
        total(ws, row, lines, 'Grand total', f['grand'])
        ws.freeze_panes(5, 2)

        for d, group in depts:
            ws = wb.add_worksheet(_sheet_name(d, used))
            row = body(ws, head(ws, title(ws, d)), group)
            total(ws, row, group, 'Total', f['grand'])
            ws.freeze_panes(5, 2)
        wb.close()
        return buf.getvalue()

    # ------------------------------------------------------------------ bank
    def bank_rows(self, run):
        """(paid by transfer, without an account) - each [(name, account, net)], by name."""
        rows, missing = [], []
        for l in run.line_ids.filtered(lambda l: l.net > 0).sorted(lambda l: l.employee_id.name or ''):
            acc = self._account(l)
            (rows if acc else missing).append((l.employee_id.name or '', acc, round(l.net, 2), self._dept(l)))
        return rows, missing

    def bank_xlsx(self, run):
        rows, missing = self.bank_rows(run)
        wb, buf, f = self._book()
        ws = wb.add_worksheet('Payroll for Bank')
        ws.set_column(0, 0, 6)
        ws.set_column(1, 1, 34)
        ws.set_column(2, 2, 22)
        ws.set_column(3, 3, 16)
        ws.merge_range(0, 0, 0, 3, run.company_id.name, f['title'])
        ws.merge_range(1, 0, 1, 3, 'Bank Payment: %s  (%s)' % (run.name, run._reference()), f['sub'])
        ws.merge_range(2, 0, 2, 3, self._period_text(run), f['sub'])
        for c, t in enumerate(['S/N', 'Full Name', 'Bank Account', 'Amount']):
            ws.write(4, c, t, f['head'])
        row = 5
        for i, (name, acc, net, _d) in enumerate(rows, 1):
            ws.write(row, 0, i, f['sn'])
            ws.write(row, 1, name, f['cell'])
            ws.write_string(row, 2, acc, f['cell'])
            ws.write_number(row, 3, net, f['num'])
            row += 1
        ws.write(row, 1, 'Total', f['grand'])
        ws.write(row, 2, '%d transfers' % len(rows), f['grand'])
        ws.write_number(row, 3, round(sum(r[2] for r in rows), 2), f['grand'])
        if missing:
            ws = wb.add_worksheet('No bank account')
            ws.set_column(0, 0, 6)
            ws.set_column(1, 2, 30)
            ws.set_column(3, 3, 16)
            ws.merge_range(0, 0, 0, 3, 'Not in the bank file: no bank account in People. Pay them another way or add the account.', f['sub'])
            for c, t in enumerate(['S/N', 'Full Name', 'Department', 'Amount']):
                ws.write(2, c, t, f['head'])
            for i, (name, _acc, net, d) in enumerate(missing, 1):
                ws.write(2 + i, 0, i, f['sn'])
                ws.write(2 + i, 1, name, f['cell'])
                ws.write(2 + i, 2, d, f['cell'])
                ws.write_number(2 + i, 3, net, f['num'])
        wb.close()
        return buf.getvalue()

    def letter_data(self, run):
        """The salary transfer letter: to the bank, from our account, the list and the total in words."""
        c = run.company_id
        rows, missing = self.bank_rows(run)
        total = round(sum(r[2] for r in rows), 2)
        words = c.currency_id.amount_to_text(total).upper() if c.currency_id else ''
        return {'company': c.name, 'date': fields.Date.context_today(self).strftime('%d-%m-%Y'), 'reference': run._reference(),
                'run': run.name, 'period': self._period_text(run),
                'bank': c.sb_pay_bank_name or '', 'branch': c.sb_pay_bank_branch or '', 'city': c.sb_pay_bank_city or '',
                'account': c.sb_pay_bank_account or '', 'rows': [{'n': i, 'name': r[0], 'acc': r[1], 'amount': '{:,.2f}'.format(r[2])}
                                                                for i, r in enumerate(rows, 1)],
                'count': len(rows), 'total': '{:,.2f}'.format(total), 'words': words, 'missing': len(missing),
                'draft': run.state == 'draft'}

    def letter_pdf(self, run):
        c = run.company_id
        if not (c.sb_pay_bank_name and c.sb_pay_bank_account):
            raise UserError(self.env._('Enter the bank and our account number for the transfer letter (Payroll > Settings).'))
        pdf, _t = self.env['ir.actions.report'].sudo()._render_qweb_pdf('sonador_payroll.action_report_bank_letter', res_ids=run.ids,
                                                                         data={'d': self.letter_data(run)})
        return pdf

    # ------------------------------------------------------------------ overtime sheet
    def ot_xlsx(self, run):
        """Everyone on the payroll with this month's hours; HR puts the real hours in the yellow cells and imports it."""
        wb, buf, f = self._book()
        ws = wb.add_worksheet('Overtime')
        ws.set_column(0, 0, 10)
        ws.set_column(1, 1, 30)
        ws.set_column(2, 2, 22)
        ws.set_column(3, 6, 15)
        ws.merge_range(0, 0, 0, 6, '%s  |  overtime hours  |  %s' % (run.name, self._period_text(run)), f['title'])
        rates = overtime_rules(run.company_id)['rates']
        ws.merge_range(1, 0, 1, 6, 'Put the real hours in the yellow cells (0 or empty: no overtime), then Import overtime on the payroll. '
                       'Rates: normal %sx, night %sx, weekend %sx, holiday %sx.' % (rates['ot_day'], rates['ot_night'], rates['ot_rest'], rates['ot_holiday']), f['note'])
        for c, t in enumerate(['Code', 'Employee', 'Department'] + [t for _k, t in OT_COLS]):
            ws.write(3, c, t, f['head'])
        for i, l in enumerate(self._lines(run).sorted(lambda l: l.employee_id.name or ''), 4):
            ws.write(i, 0, l.hr_employee_id.sb_code or '', f['cell'])
            ws.write(i, 1, l.employee_id.name or '', f['cell'])
            ws.write(i, 2, self._dept(l), f['cell'])
            for c, (k, _t) in enumerate(OT_COLS, 3):
                ws.write_number(i, c, l[k] or 0, f['in'])
        ws.freeze_panes(4, 2)
        wb.close()
        return buf.getvalue()

    def _read_sheet(self, data, filename):
        raw = base64.b64decode(data)
        if (filename or '').lower().endswith('.csv'):
            text = raw.decode('utf-8-sig', errors='replace')
            return [r for r in csv.reader(io.StringIO(text))]
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        except Exception:
            raise UserError(self.env._('Could not read the file: send the Excel sheet (.xlsx) or a CSV.'))
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]

    def ot_import(self, run, data, filename):
        """Hours from the sheet onto the payroll: each row found by code or name; empty or 0 is no overtime. People not
        on the payroll yet but of its departments and not paid elsewhere are added. Holiday hours are capped at 8 hours a
        holiday of the month. Imported hours stay when the payroll is recalculated."""
        if run.state != 'draft':
            raise UserError(self.env._('Reset the payroll to draft to change it.'))
        table = self._read_sheet(data, filename)
        hdr = None
        for i, r in enumerate(table):
            cells = [_key(str(x or '')) for x in r]
            if any(c.startswith('employee') or c == 'name' for c in cells) and any(c.startswith('night') for c in cells):
                hdr = i
                break
        if hdr is None:
            raise UserError(self.env._('No heading row found: the sheet needs Employee, Normal, Night, Weekend, Holiday.'))
        cells = [_key(str(x or '')) for x in table[hdr]]

        def col(*names):
            return next((j for j, c in enumerate(cells) if any(c.startswith(n) for n in names)), None)
        ci = {'code': col('code'), 'name': col('employee', 'name'), 'ot_day': col('normal', 'day', 'regular'),
              'ot_night': col('night'), 'ot_rest': col('weekend', 'rest'), 'ot_holiday': col('holiday')}
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        staff = E.search([('company_id', '=', run.company_id.id)])
        by_code = {_key(e.sb_code): e for e in staff if e.sb_code}
        by_name = {}
        for e in staff:
            by_name.setdefault(_key(e.name), []).append(e)
        on_run = {l.hr_employee_id.id: l for l in run.line_ids if l.hr_employee_id}
        allowed = run._employees()
        cap = self.env['sb.payroll.line']._sb_holiday_cap(run)
        done, added, unknown, capped = 0, [], [], []
        for r in table[hdr + 1:]:
            def val(k):
                j = ci.get(k)
                return r[j] if j is not None and j < len(r) else None
            name = str(val('name') or '').strip()
            code = _key(str(val('code') or ''))
            if not name and not code:
                continue
            e = by_code.get(code) if code else None
            if not e:
                hits = by_name.get(_key(name), [])
                e = hits[0] if len(hits) == 1 else None
            if not e:
                unknown.append(name or code)
                continue
            hours = {}
            for k, _t in OT_COLS:
                try:
                    hours[k] = max(float(val(k) or 0), 0.0)
                except (TypeError, ValueError):
                    hours[k] = 0.0
            if cap is not None and hours['ot_holiday'] > cap:
                capped.append('%s %g h to %g h' % (e.name, hours['ot_holiday'], cap))
                hours['ot_holiday'] = cap
            line = on_run.get(e.id)
            new = not line
            if new:
                if e not in allowed or not any(hours.values()):
                    if any(hours.values()):
                        unknown.append('%s (not on this payroll)' % e.name)
                    continue
                e._sb_sync_payroll()
                line = self.env['sb.payroll.line'].sudo().create({'run_id': run.id, 'employee_id': e.sb_payroll_id.id})
                on_run[e.id] = line
                added.append(e.name)
            line.write(dict(hours, ot_hand=True))
            if new:
                line._sb_fill()
            line._sb_overtime()
            done += 1
        return {'done': done, 'added': added, 'unknown': unknown, 'capped': capped}

    def ot_reset(self, run):
        """Overtime back to the attendance records."""
        if run.state != 'draft':
            raise UserError(self.env._('Reset the payroll to draft to change it.'))
        run.line_ids.write({'ot_hand': False})
        run.line_ids._sb_fill()
        return True


class SbPayrollLine(models.Model):
    _inherit = 'sb.payroll.line'

    def _sb_holiday_cap(self, run):
        """Most holiday overtime hours a person can have in the run's month (Attendance: 8 a holiday); None: no cap."""
        return None

    def _sb_overtime(self):
        for l in self:
            hourly = (l.full_basic or 0.0) / (l.run_id.company_id.sb_pay_hours or 208.0)
            rates = overtime_rules(l.run_id.company_id)['rates']
            l.overtime = round(sum(l[k] * r for k, r in rates.items()) * hourly, 2)
