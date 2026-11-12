import io
import re

from odoo import api, models

MONEY = '#,##0.00;(#,##0.00);"-"'
ETHIOPIC = re.compile('[\u1200-\u139f\u2d80-\u2ddf\uab00-\uab2f]+')


def basis_lines(doc):
    """What every rendering states under the title: drafts included, and the filters in force."""
    out = []
    if doc.get('unposted') or not (doc.get('options') or {}).get('posted_only', True):
        out.append('Basis: includes unposted (draft) entries - not a final statement')
    if doc.get('filters'):
        out.append('Filters: ' + '; '.join(doc['filters']))
    elif (doc.get('options') or {}).get('search'):
        out.append('Filters: Search "%s"' % doc['options']['search'])
    return out


_FONTS = {}


def ethiopic_fonts():
    """Noto Sans Ethiopic (SIL Open Font Licence), bundled with the module, registered once for ReportLab: (regular,
    bold) font names, or None when it cannot be loaded (Helvetica then, as before)."""
    if 'names' not in _FONTS:
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from odoo.tools.misc import file_path
            pdfmetrics.registerFont(TTFont('SbEthiopic', file_path('sonador_books/static/fonts/NotoSansEthiopic.ttf')))
            pdfmetrics.registerFont(TTFont('SbEthiopic-Bold', file_path('sonador_books/static/fonts/NotoSansEthiopic-Bold.ttf')))
            _FONTS['names'] = ('SbEthiopic', 'SbEthiopic-Bold')
        except Exception:  # no font file, or ReportLab without TrueType: Latin only
            _FONTS['names'] = None
    return _FONTS['names']


def ethiopic_css():
    """@font-face rules carrying the Ethiopic font inside the HTML-to-PDF route (the server has no Ethiopic font)."""
    if 'css' not in _FONTS:
        import base64
        from odoo.tools.misc import file_path
        css = []
        for weight, name in (('normal', 'NotoSansEthiopic.ttf'), ('bold', 'NotoSansEthiopic-Bold.ttf')):
            with open(file_path('sonador_books/static/fonts/%s' % name), 'rb') as f:
                data = base64.b64encode(f.read()).decode()
            css.append("@font-face { font-family: 'Noto Sans Ethiopic'; font-weight: %s; src: url(data:font/truetype;base64,%s) "
                       "format('truetype'); }" % (weight, data))
        _FONTS['css'] = '\n'.join(css)
    return _FONTS['css']


def has_ethiopic(doc):
    import json
    return bool(ETHIOPIC.search(json.dumps([doc.get('title'), doc.get('subtitle'), doc.get('company'), doc.get('filters'),
                                            doc.get('columns'), doc.get('rows')], ensure_ascii=False, default=str)))


def sum_formula(rows, col):
    """=SUM over the cells of these sheet rows in one column: runs of adjacent rows as ranges (A5:A90), and never more
    than Excel's 255 arguments to one function (longer lists are summed in chunks: SUM(SUM(...),SUM(...)))."""
    from xlsxwriter.utility import xl_rowcol_to_cell
    rows = sorted(set(rows))
    args, i = [], 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1] == rows[j] + 1:
            j += 1
        a = xl_rowcol_to_cell(rows[i], col)
        args.append(a if i == j else '%s:%s' % (a, xl_rowcol_to_cell(rows[j], col)))
        i = j + 1
    while len(args) > 255:
        args = ['SUM(%s)' % ','.join(args[k:k + 255]) for k in range(0, len(args), 255)]
    return '=SUM(%s)' % ','.join(args)


MAX_FORMULA = 8000  # Excel refuses a formula over 8,192 characters: past that the typed figure stays


def _write_sheet(wb, f, doc, used_names):
    from xlsxwriter.utility import xl_rowcol_to_cell
    name = (doc['title'] or 'Report')[:28]
    base, n = name, 2
    while name.lower() in used_names:
        name = '%s %d' % (base[:25], n)
        n += 1
    used_names.add(name.lower())
    ws = wb.add_worksheet(name.replace('/', '-').replace(':', '-'))

    def fmt(kind, row_kind, level):
        key = (kind, row_kind, level)
        if key not in f:
            props = {}
            if kind in ('monetary', 'number'):
                props['num_format'] = MONEY
            elif kind == 'percent':
                props['num_format'] = '0.0"%"'
            elif kind == 'number_int':
                props['num_format'] = '#,##0'
            if row_kind in ('section', 'total', 'group'):
                props['bold'] = True
            if row_kind == 'total':
                props['top'] = 1
                props['bottom'] = 6 if level == 0 else 1
            if kind == 'text':
                props['indent'] = level
            f[key] = wb.add_format(props)
        return f[key]

    cols = doc['columns']
    ws.write(0, 0, doc['company'], f['title'])
    ws.write(1, 0, doc['title'], f['title'])
    ws.write(2, 0, doc['subtitle'], f['sub'])
    opts = doc['options']
    hidden = set(opts.get('hidden_cols') or [])
    cols = [c for c in cols if c['key'] not in hidden] or cols
    basis = basis_lines(doc)
    ws.write(3, 0, 'Printed %s (%s)' % (doc['printed'][:16], doc['printed_ec']), f['sub'])
    for i, text in enumerate(basis):
        ws.write(4 + i, 0, text, f['sub'])
    hr = 5 + len(basis)
    for c, col in enumerate(cols):
        label = col['label'] + (('\n' + col['sub']) if col.get('sub') else '')
        ws.write(hr, c, label, f['head_num'] if col['type'] in ('monetary', 'number', 'percent', 'number_int') else f['head'])
        ws.set_column(c, c, 16 if col['type'] in ('monetary', 'number', 'percent') else (12 if col['type'] == 'date' else 34))
    ws.set_row(hr, 32)
    ws.freeze_panes(hr + 1, 1)
    row_index = {}
    r = hr + 1
    for row in doc['rows']:
        row_index[row['id']] = r
        level = row.get('level', 0)
        vals = row.get('values', {})
        for c, col in enumerate(cols):
            v = vals.get(col['key'])
            if c == 0 and col['key'] in ('label', 'code', 'partner', 'date', 'name', 'number') and not v:
                v = row.get('label')
            if c == 0 and col['key'] == 'label':
                v = row.get('label')
            if v is None or v == '':
                continue
            if isinstance(v, float) and col['type'] in ('monetary', 'number'):
                v = round(v, 2) + 0.0  # no float noise (2e-08, -45076.17999999993) in the cells; + 0.0 drops -0.0
            ws.write(r, c, v, fmt(col['type'], row.get('kind'), level))
        ws.set_row(r, None, None, {'level': min(level, 7), 'hidden': False})
        r += 1
    # live subtotals: a group / section equal to the sum of its direct children becomes =SUM(children)
    children = {}
    for row in doc['rows']:
        if row.get('parent') and row.get('kind') != 'detail':
            children.setdefault(row['parent'], []).append(row)
    for row in doc['rows']:
        kids = children.get(row['id'])
        if not kids or row['id'] not in row_index:
            continue
        for c, col in enumerate(cols):
            if col['type'] not in ('monetary', 'number') or col['key'] in ('balance', 'var_pct'):
                continue
            v = row.get('values', {}).get(col['key'])
            if v is None:
                continue
            # the cells hold rounded figures: a formula only where their sum shows the same figure
            total = sum(round(k.get('values', {}).get(col['key']) or 0.0, 2) for k in kids)
            if abs(total - round(v, 2)) > 0.005:
                continue
            cells = [row_index[k['id']] for k in kids if k['id'] in row_index]
            formula = sum_formula(cells, c) if cells else ''
            if cells and len(formula) <= MAX_FORMULA:
                ws.write_formula(row_index[row['id']], c, formula,
                                 fmt(col['type'], row.get('kind'), row.get('level', 0)), round(v, 2))
    # totals with no children of their own: a formula only over the rows the report says they sum ('sum_of': [(row id,
    # sign)], e.g. gross profit = revenue + cost of sales, total equity and liabilities = equity + liabilities); a
    # report's grand total sums its top-level rows. Anything else stays the typed figure.
    tops = [r for r in doc['rows'] if r.get('level', 0) == 0 and r['id'] in row_index]
    for n, row in enumerate(tops):
        if row.get('kind') != 'total' or children.get(row['id']):
            continue
        parts = row.get('sum_of')
        if parts is None and row['id'] == 'total':
            parts = [(r['id'], 1) for r in tops[:n] if r.get('kind') in ('section', 'group', 'line')]
        parts = [(rid, sg) for rid, sg in (parts or []) if rid in row_index]
        if not parts:
            continue
        by_id = {r['id']: r for r in doc['rows']}
        for c, col in enumerate(cols):
            if col['type'] not in ('monetary', 'number') or col['key'] in ('balance', 'var_pct'):
                continue
            v = row.get('values', {}).get(col['key'])
            if not isinstance(v, (int, float)):
                continue
            if abs(sum(sg * round(by_id[rid].get('values', {}).get(col['key']) or 0.0, 2) for rid, sg in parts)
                   - round(v, 2)) > 0.005:
                continue
            if all(sg > 0 for _rid, sg in parts):
                formula = sum_formula([row_index[rid] for rid, _sg in parts], c)
            else:
                formula = '=' + ''.join('%s%s' % ('+' if sg > 0 else '-', xl_rowcol_to_cell(row_index[rid], c))
                                        for rid, sg in parts).lstrip('+')
            if len(formula) > MAX_FORMULA:
                continue
            ws.write_formula(row_index[row['id']], c, formula, fmt(col['type'], row.get('kind'), 0), round(v, 2))
    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(hr)
    ws.set_footer('&L%s&RPage &P of &N' % doc['title'])
    return ws


class SbReportExport(models.AbstractModel):
    _inherit = 'sb.report'

    @api.model
    def render_xlsx(self, docs, cover=None):
        """One workbook, one sheet per report document (plus an optional contents sheet)."""
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        f = {
            'title': wb.add_format({'bold': True, 'font_size': 14}),
            'sub': wb.add_format({'italic': True, 'font_color': '#555555'}),
            'head': wb.add_format({'bold': True, 'bottom': 1, 'bg_color': '#EEF3F1', 'text_wrap': True, 'valign': 'bottom'}),
            'head_num': wb.add_format({'bold': True, 'bottom': 1, 'bg_color': '#EEF3F1', 'align': 'right', 'text_wrap': True,
                                       'valign': 'bottom'}),
        }
        used = set()
        if cover:
            ws = wb.add_worksheet('Contents')
            used.add('contents')
            ws.write(0, 0, cover['company'], f['title'])
            ws.write(1, 0, cover['title'], f['title'])
            ws.write(2, 0, cover['subtitle'], f['sub'])
            for i, d in enumerate(docs):
                ws.write(4 + i, 0, d['title'])
                ws.write(4 + i, 1, d['subtitle'])
            ws.set_column(0, 0, 40)
            ws.set_column(1, 1, 60)
        for doc in docs:
            _write_sheet(wb, f, doc, used)
        wb.close()
        return buf.getvalue()

    BIG_PDF = 2500  # rows: above this the PDF is drawn directly; the HTML-to-PDF route takes minutes on a year's ledger

    @api.model
    def _render_pdf_direct(self, doc):
        """A long report (a year's general ledger: 20,000 lines) straight to PDF with ReportLab, in seconds: same
        columns, same number format, the heading repeated on every page, page numbers."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from .report_engine import SbReportPdf

        fmt = SbReportPdf._fmt
        numeric = SbReportPdf.NUMERIC
        cols = doc['columns']
        page_w, page_h = landscape(A4)
        avail = page_w - 20 * mm
        font, size, pad = 'Helvetica', 7, 6
        widths_of = {}
        # Amharic (Ge'ez script) is drawn in the bundled Noto Sans Ethiopic, the rest in Helvetica: run by run
        eth = ethiopic_fonts()

        def runs(text, f):
            if text.isascii() or not eth:
                return [(text, f)]
            ef = eth[1] if f.endswith('Bold') else eth[0]
            out, pos = [], 0
            for m in ETHIOPIC.finditer(text):
                if m.start() > pos:
                    out.append((text[pos:m.start()], f))
                out.append((m.group(), ef))
                pos = m.end()
            if pos < len(text):
                out.append((text[pos:], f))
            return out

        def text_width(text, f, s):
            return sum(stringWidth(t, ff, s) for t, ff in runs(text, f))

        def draw(x, y, text, f, s, right=False):
            parts = runs(text, f)
            if right:
                x -= sum(stringWidth(t, ff, s) for t, ff in parts)
            for t, ff in parts:
                cv.setFont(ff, s)
                cv.drawString(x, y, t)
                x += stringWidth(t, ff, s)
            cv.setFont(f, s)

        def sw(text):
            # one measurement per distinct string: a ledger repeats its dates, journals and amounts thousands of times
            w = widths_of.get(text)
            if w is None:
                w = widths_of[text] = text_width(text, font, size)
            return w

        def first_label(row):
            # the row's label in the first column, unless the label is already in the row (a ledger's account heading
            # has its code and name in their own columns; "Opening balance" is in the description)
            label = row.get('label') or ''
            shown = [str(x) for x in row['values'].values() if isinstance(x, str) and len(x) >= 4]
            return '' if any(x in label or label in x for x in shown) else label

        # every cell's text once: the widths are measured on the full text of every row, and the drawing reuses it
        texts, labelled = [], set()  # labelled: rows whose first cell is their label, free to run on to the right
        for n, row in enumerate(doc['rows']):
            line = []
            for i, c in enumerate(cols):
                v = row['values'].get(c['key'])
                if i == 0 and (c['key'] == 'label' or not v):
                    v = first_label(row) if c['key'] != 'label' else row.get('label')
                    labelled.add(n)
                line.append(str(fmt(v, c['type'])) if v not in (None, '') else '')
            texts.append(line)
        indent = 8  # points per level, in a text first column
        natural = []
        for i, c in enumerate(cols):
            w = max([sw(t[i]) + (indent * (r.get('level') or 0) if i == 0 and c['type'] == 'text' else 0)
                     for n, (t, r) in enumerate(zip(texts, doc['rows']))
                     if t[i] and not (i == 0 and n in labelled and c['type'] != 'text')] or [0.0])
            if c['type'] in numeric:
                w *= 1.08  # totals are bold, a little wider
            natural.append(max(w, text_width(c['label'], 'Helvetica-Bold', size)) + pad)
        # numbers and dates always fit; text columns share the rest, the short ones first (water-filling), and any
        # width left over goes to the text columns so the table spans the page
        fixed = [i for i, c in enumerate(cols) if c['type'] in numeric or c['type'] == 'date']
        flex = sorted((i for i in range(len(cols)) if i not in fixed), key=lambda i: natural[i])
        widths = list(natural)
        room = avail - sum(natural[i] for i in fixed)
        for n, i in enumerate(flex):
            share = room / (len(flex) - n)
            widths[i] = min(natural[i], max(share, 10 * mm))
            room -= widths[i]
        spare = avail - sum(widths)
        if spare > 0 and flex:
            grow = sum(widths[i] for i in flex) or 1.0
            for i in flex:
                widths[i] += spare * widths[i] / grow

        def clip(text, room_w):
            room = room_w - pad
            if sw(text) <= room:
                return text
            lo, hi = 0, len(text)  # longest prefix that fits with the ellipsis: a binary search, not a character loop
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if text_width(text[:mid] + '…', state['font'], size) <= room:
                    lo = mid
                else:
                    hi = mid - 1
            return text[:lo] + '…'

        # drawn line by line on the canvas: a table engine takes minutes on 20,000 rows, this takes seconds
        from reportlab.pdfgen import canvas as rl_canvas
        buf = io.BytesIO()
        cv = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
        cv.setTitle(doc['title'])
        left, top, bottom, lead = 10 * mm, page_h - 12 * mm, 14 * mm, 9.2
        xs = [left]
        for w in widths[:-1]:
            xs.append(xs[-1] + w)
        state = {'page': 0, 'y': 0, 'font': font}
        basis = basis_lines(doc)

        def cell(text, i, x_extra=0.0):
            if text.isascii():
                if cols[i]['type'] in numeric:
                    cv.drawRightString(xs[i] + widths[i] - 3, state['y'], text)
                else:
                    cv.drawString(xs[i] + 3 + x_extra, state['y'], text)
            elif cols[i]['type'] in numeric:
                draw(xs[i] + widths[i] - 3, state['y'], text, state['font'], size, right=True)
            else:
                draw(xs[i] + 3 + x_extra, state['y'], text, state['font'], size)

        def new_page():
            if state['page']:
                cv.showPage()
            state['page'] += 1
            cv.setFont(font, 7)
            cv.setFillColor(colors.HexColor('#555555'))
            draw(left, page_h - 8 * mm, '%s · %s' % (doc['company'], doc['title']), font, 7)
            draw(page_w - left, page_h - 8 * mm, doc['subtitle'][:120], font, 7, right=True)
            # the basis on every page's foot: a page torn from a draft or filtered ledger still says so
            draw(left, 7 * mm, 'Printed %s (%s)%s' % (doc['printed'][:16], doc['printed_ec'],
                                                     ('  |  ' + '  |  '.join(basis))[:200] if basis else ''), font, 7)
            cv.drawRightString(page_w - left, 7 * mm, 'Page %d' % state['page'])
            cv.setFillColor(colors.black)
            y = top
            if state['page'] == 1:
                draw(left, y, doc['company'] + (' · TIN %s' % doc['company_vat'] if doc.get('company_vat') else ''), 'Helvetica', 8.5)
                draw(left, y - 16, doc['title'], 'Helvetica-Bold', 14)
                draw(left, y - 28, doc['subtitle'], 'Helvetica', 8.5)
                y -= 42
                for text in basis:
                    cv.setFillColor(colors.HexColor('#BB0000') if text.startswith('Basis') else colors.HexColor('#333333'))
                    draw(left, y + 2, text[:250], 'Helvetica', 8)
                    y -= 11
                cv.setFillColor(colors.black)
            cv.setFillColor(colors.HexColor('#F5F8FA'))
            cv.rect(left, y - 3, sum(widths), lead + 1, stroke=0, fill=1)
            cv.setFillColor(colors.black)
            state['y'] = y
            cv.setFont('Helvetica-Bold', size)
            state['font'] = 'Helvetica-Bold'
            for i, c in enumerate(cols):
                cell(c['label'] if c['type'] in numeric else clip(c['label'], widths[i]), i)
            cv.setLineWidth(0.6)
            cv.line(left, y - 3.5, left + sum(widths), y - 3.5)
            state['y'] = y - lead - 1

        new_page()
        for row, line in zip(doc['rows'], texts):
            if state['y'] < bottom:
                new_page()
            kind = row.get('kind')
            state['font'] = 'Helvetica-Bold' if kind in ('section', 'total', 'group') else font
            cv.setFont(state['font'], size)
            if row.get('warning'):
                cv.setFillColor(colors.HexColor('#BB0000'))
            if kind == 'total':
                cv.setLineWidth(0.5)
                cv.line(left, state['y'] + lead - 2, left + sum(widths), state['y'] + lead - 2)
            for i, text in enumerate(line):
                if not text:
                    continue
                if cols[i]['type'] in numeric:
                    cell(text, i)
                    continue
                x_extra = indent * (row.get('level') or 0) if i == 0 and cols[i]['type'] == 'text' else 0.0
                # a text runs on over the empty cells to its right (a heading in a date column), as in a spreadsheet
                span = widths[i]
                j = i + 1
                while j < len(line) and not line[j] and cols[j]['type'] not in numeric:
                    span += widths[j]
                    j += 1
                cell(clip(text, span - x_extra), i, x_extra)
            cv.setFillColor(colors.black)
            state['y'] -= lead
        cv.save()
        return buf.getvalue()

    @api.model
    def render_pdf(self, doc):
        if len(doc.get('rows') or []) > self.BIG_PDF:
            doc['company_vat'] = self.env.company.vat or ''
            return self._render_pdf_direct(doc)
        company = self.env.company
        doc['logo'] = company.logo.decode() if company.logo else ''
        doc['company_vat'] = company.vat or ''
        # a draft (unposted entries in it) is not signed off: no signature block
        doc['signatures'] = doc.get('signatures', doc['key'] not in ('partner_list', 'chart', 'transactions', 'find_vouchers')) \
            and not doc.get('unposted')
        doc['basis'] = basis_lines(doc)
        from markupsafe import Markup
        doc['font_face'] = Markup(ethiopic_css()) if has_ethiopic(doc) else ''
        pdf, _ = self.env['ir.actions.report']._render_qweb_pdf('sonador_books.action_report_document', res_ids=[], data={'doc': doc})
        return pdf
