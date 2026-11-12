"""Excel both ways: a personal template for the month (dropdowns, date limits, a guide sheet, a check column that turns red
while you type) and reading it back into the plan screen, where the plan checker reviews every row before anything is saved.
The hidden Ref column lets an exported plan go out to Excel and come back without making duplicates."""
import base64
import io
from datetime import date, datetime

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .service import month_bounds

HEAD = ['No.', 'Category', 'What will you do?', 'Done when… (the result you can check)', 'Target', 'Unit', 'From', 'To',
        'Evidence / output', 'Goal', 'Check', 'Ref']
GOOD = [
    ('Collect overdue receivables, Oromia zone', 'At least 1,200,000 ETB collected from the 15 oldest accounts', 1200000, 'ETB',
     'Receipts list + bank deposit slips'),
    ('Reconcile CBE and Dashen bank accounts', 'Both reconciled to the bank statement, differences explained', 2, 'accounts',
     'Signed reconciliation sheets'),
    ('ክፍት የስራ መደቦችን መሙላት', 'ለሽያጭ ክፍል 2 ሰራተኞች ተቀጥረው ውል ፈርመዋል', 2, 'ሰራተኛ', 'የተፈረሙ ውሎች'),
]
BAD = [
    ('Follow up', 'Follow up', 'Vague, and the same text in two columns'),
    ('Report', 'Measure 5', 'Placeholders: nobody can tell what you will deliver'),
    ('Support the sales team as needed', 'Ongoing', '"As needed" and "ongoing" have no end and no result'),
    ('asdf sdfg', 'done', 'Random typing'),
]


class SbPlanExcel(models.AbstractModel):
    _name = 'sb.plan.excel'
    _description = 'Planning Excel'

    @api.model
    def template(self, month, with_plan=True):
        """Base64 .xlsx of my plan for the month (empty rows to fill, or the current tasks to edit)."""
        try:
            import openpyxl
            from openpyxl.formatting.rule import FormulaRule
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.worksheet.datavalidation import DataValidation
        except ImportError:
            raise UserError(self.env._('Excel support (openpyxl) is not installed on the server.'))
        S = self.env['sb.planning']
        e = S._me()
        if not e:
            raise UserError(self.env._('Your login is not linked to a person in People.'))
        m0, m1, label = month_bounds(month)
        info = S.ctx()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Plan'
        navy = PatternFill('solid', fgColor='243742')
        band = PatternFill('solid', fgColor='F5F8FA')
        thin = Side(style='thin', color='E4E9ED')
        ws['A1'] = '%s – plan for %s' % (e.name, label)
        ws['A1'].font = Font(size=16, bold=True, color='243742')
        ws['A2'] = '%s to %s  (%s to %s).  Fill one row per task; read the Guide sheet first. The Check column must say OK.' % (
            format_ethiopian(m0), format_ethiopian(m1), m0.strftime('%d %b %Y'), m1.strftime('%d %b %Y'))
        ws['A2'].font = Font(italic=True, color='6B7A85')
        for i, h in enumerate(HEAD, 1):
            c = ws.cell(row=4, column=i, value=h)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = navy
            c.alignment = Alignment(wrap_text=True, vertical='center')
        widths = [5, 20, 42, 46, 10, 10, 12, 12, 30, 28, 30, 6]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        ws.row_dimensions[4].height = 34
        ws.column_dimensions['L'].hidden = True
        # lists
        lst = wb.create_sheet('Lists')
        cats = [c['name'] for c in info['categories']] or ['General']
        goals = [g['name'][:250] for g in info['goals']] or ['(no goals yet)']
        for i, v in enumerate(cats, 1):
            lst.cell(row=i, column=1, value=v)
        for i, v in enumerate(goals, 1):
            lst.cell(row=i, column=2, value=v)
        lst.sheet_state = 'hidden'
        rows = []
        if with_plan:
            plan = S._plan(e, m0)
            if plan:
                rows = [(t.category_id.name or '', t.name, t.done_when or '', t.target_qty or None, t.unit or '', t.date_from, t.date_to,
                         t.output or '', t.goal_id.name or '', t.id) for t in plan.task_ids]
        n = max(len(rows) + 10, 25)
        first, last = 5, 4 + n
        dv_cat = DataValidation(type='list', formula1='=Lists!$A$1:$A$%d' % len(cats), allow_blank=True,
                                errorTitle='Category', error='Pick a category from the list.')
        dv_goal = DataValidation(type='list', formula1='=Lists!$B$1:$B$%d' % len(goals), allow_blank=True)
        dv_date = DataValidation(type='date', operator='between', formula1='DATE(%d,%d,%d)' % (m0.year, m0.month, m0.day),
                                 formula2='DATE(%d,%d,%d)' % (m1.year, m1.month, m1.day), allow_blank=True,
                                 errorTitle='Date', error='Dates must be inside %s (%s to %s).' % (label, m0.strftime('%d %b'), m1.strftime('%d %b %Y')))
        dv_num = DataValidation(type='decimal', operator='greaterThanOrEqual', formula1='0', allow_blank=True)
        for dv in (dv_cat, dv_goal, dv_date, dv_num):
            ws.add_data_validation(dv)
        dv_cat.add('B%d:B%d' % (first, last))
        dv_goal.add('J%d:J%d' % (first, last))
        dv_date.add('G%d:H%d' % (first, last))
        dv_num.add('E%d:E%d' % (first, last))
        for r in range(first, last + 1):
            ws.cell(row=r, column=1, value=r - first + 1).font = Font(color='6B7A85')
            for col in (7, 8):
                ws.cell(row=r, column=col).number_format = 'DD-MMM-YYYY'
            ws.cell(row=r, column=11, value=(
                '=IF(AND(C{r}="",D{r}=""),"",IF(LEN(TRIM(C{r}))<8,"What is too short",IF(LEN(TRIM(D{r}))<6,"Say when it is done",'
                'IF(EXACT(TRIM(LOWER(C{r})),TRIM(LOWER(D{r}))),"Same text twice",IF(OR(G{r}="",H{r}=""),"Give the dates",'
                'IF(H{r}<G{r},"Ends before it starts",IF(SUMPRODUCT(--(TRIM(LOWER($C${f}:$C${l}))=TRIM(LOWER(C{r}))))>1,"Duplicate task",'
                'IF(OR(ISNUMBER(SEARCH("follow up",C{r}&D{r})),ISNUMBER(SEARCH("ongoing",C{r}&D{r})),ISNUMBER(SEARCH("as needed",C{r}&D{r}))),'
                '"Vague wording","OK"))))))))').format(r=r, f=first, l=last))
            for col in range(1, 12):
                cell = ws.cell(row=r, column=col)
                cell.border = Border(bottom=thin)
                cell.alignment = Alignment(wrap_text=True, vertical='top')
                if r % 2 == 0:
                    cell.fill = band
        for i, row in enumerate(rows):
            r = first + i
            for col, v in zip((2, 3, 4, 5, 6, 7, 8, 9, 10, 12), row):
                ws.cell(row=r, column=col, value=v)
        red = PatternFill('solid', fgColor='F8D7D2')
        green = PatternFill('solid', fgColor='D6EFE3')
        ws.conditional_formatting.add('K%d:K%d' % (first, last), FormulaRule(formula=['AND(K%d<>"",K%d<>"OK")' % (first, first)], fill=red))
        ws.conditional_formatting.add('K%d:K%d' % (first, last), FormulaRule(formula=['K%d="OK"' % first], fill=green))
        ws.freeze_panes = 'C5'
        # guide
        g = wb.create_sheet('Guide', 0)
        g.column_dimensions['A'].width = 34
        g.column_dimensions['B'].width = 52
        g.column_dimensions['C'].width = 44
        g['A1'] = 'How to fill your plan'
        g['A1'].font = Font(size=16, bold=True, color='243742')
        tips = [
            ('One row = one task', 'Something you will finish this month, with a result someone can check.'),
            ('What will you do?', 'Start with a verb: collect, reconcile, hire, install, deliver, train… Name the thing.'),
            ('Done when…', 'The result, with a number, a date or a finished document: "1,200,000 ETB collected", "Signed by the GM".'),
            ('Target / Unit', 'Optional number and unit (ETB, litres, customers, km, %). Your check-ins compare against it.'),
            ('From / To', 'The days you will work on it, inside the month. Spread tasks over the weeks; avoid putting everything on the last day.'),
            ('Evidence / output', 'What proves it: a report, a signed form, a photo, a system record.'),
            ('Goal', 'Which of your yearly goals (Performance) it moves forward.'),
            ('Check column', 'Turns green (OK) or red with the reason. Fix every red row before importing.'),
            ('Bring it back', 'Planning › My month › Import Excel. Every row is checked again and shown to you before anything is saved.'),
            ('Amharic', 'Write in Amharic or English, whichever you use with your manager.'),
        ]
        for i, (a, b) in enumerate(tips, 3):
            g.cell(row=i, column=1, value=a).font = Font(bold=True)
            g.cell(row=i, column=2, value=b).alignment = Alignment(wrap_text=True)
        r = len(tips) + 5
        g.cell(row=r, column=1, value='Good tasks').font = Font(bold=True, color='1E8E5A', size=13)
        for i, (w, d, q, u, o) in enumerate(GOOD, r + 1):
            g.cell(row=i, column=1, value=w).alignment = Alignment(wrap_text=True)
            g.cell(row=i, column=2, value='%s  (target %s %s)' % (d, '{:,}'.format(q), u)).alignment = Alignment(wrap_text=True)
            g.cell(row=i, column=3, value='Evidence: ' + o).alignment = Alignment(wrap_text=True)
        r = r + len(GOOD) + 2
        g.cell(row=r, column=1, value='Plans that come back').font = Font(bold=True, color='C0392B', size=13)
        for i, (w, d, why) in enumerate(BAD, r + 1):
            g.cell(row=i, column=1, value=w)
            g.cell(row=i, column=2, value=d)
            g.cell(row=i, column=3, value=why).alignment = Alignment(wrap_text=True)
        wb.active = 1
        buf = io.BytesIO()
        wb.save(buf)
        return {'name': 'Plan %s - %s.xlsx' % (label, e.name), 'data': base64.b64encode(buf.getvalue()).decode()}

    @api.model
    def read(self, month, data):
        """Rows from an uploaded template (or any sheet with the same headings) for the plan screen; nothing is saved."""
        try:
            import openpyxl
        except ImportError:
            raise UserError(self.env._('Excel support (openpyxl) is not installed on the server.'))
        try:
            wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(data)), data_only=True)
        except Exception:
            raise UserError(self.env._('This file is not an Excel workbook (.xlsx).'))
        ws = wb['Plan'] if 'Plan' in wb.sheetnames else wb.worksheets[0]
        head_row, cols = None, {}
        keys = {'category': ('category',), 'name': ('what', 'task', 'activity'), 'done_when': ('done when', 'target (smart)', 'measurable', 'result'),
                'target_qty': ('target',), 'unit': ('unit', 'uom'), 'date_from': ('from', 'start'), 'date_to': ('to', 'end'),
                'output': ('evidence', 'output', 'deliverable'), 'goal': ('goal',), 'ref': ('ref',)}
        for r in range(1, 15):
            vals = [str(c.value or '').strip().lower() for c in ws[r]]
            hits = {}
            for i, v in enumerate(vals):
                for k, words in keys.items():
                    if k not in hits and v and any(v == w or v.startswith(w) for w in words):
                        if k == 'target_qty' and 'done' in v:
                            continue
                        hits[k] = i
            if 'name' in hits and len(hits) >= 3:
                head_row, cols = r, hits
                break
        if not head_row:
            raise UserError(self.env._('Could not find the heading row ("What will you do?", "Done when", "From", "To"...). Use the template.'))
        S = self.env['sb.planning']
        info = S.ctx()
        cat_by = {c['name'].lower(): c['id'] for c in info['categories']}
        goal_by = {g['name'][:250].lower(): g['id'] for g in info['goals']}
        m0, m1, _l = month_bounds(month)

        def d(v):
            if isinstance(v, datetime):
                return fields.Date.to_string(v.date())
            if isinstance(v, date):
                return fields.Date.to_string(v)
            if isinstance(v, (int, float)) and 30000 < v < 80000:
                from datetime import timedelta
                return fields.Date.to_string(date(1899, 12, 30) + timedelta(days=int(v)))
            if isinstance(v, str) and v.strip():
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%b-%Y', '%d %b %Y'):
                    try:
                        return fields.Date.to_string(datetime.strptime(v.strip(), fmt).date())
                    except ValueError:
                        continue
            return ''
        rows = []
        for row in ws.iter_rows(min_row=head_row + 1, values_only=True):
            get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
            name = str(get('name') or '').strip()
            done = str(get('done_when') or '').strip()
            if not name and not done:
                continue
            cat = str(get('category') or '').strip()
            goal = str(get('goal') or '').strip()
            ref = get('ref')
            rows.append({'id': int(ref) if isinstance(ref, (int, float)) and ref else False, 'name': name, 'done_when': done,
                         'target_qty': get('target_qty') if isinstance(get('target_qty'), (int, float)) else '',
                         'unit': str(get('unit') or '').strip(), 'date_from': d(get('date_from')), 'date_to': d(get('date_to')) or d(get('date_from')),
                         'output': str(get('output') or '').strip(), 'category_id': cat_by.get(cat.lower(), ''), 'category': cat,
                         'goal_id': goal_by.get(goal[:250].lower(), ''), 'from_excel': True})
        if not rows:
            raise UserError(self.env._('No tasks found in the file.'))
        return rows
