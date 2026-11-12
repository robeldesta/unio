"""The print engine: every printable document becomes one plain description (title, number, dates, the other party,
details, lines, totals, notes, signatures, fiscal QR), and one set of templates prints that description in the
company's design for the kind of document. Each module only describes its own documents (adapters.py)."""
import re

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian, gregorian

from .labels import amharic, translator
from .words import amharic_amount

_FACES = {}
_PLACEHOLDER = []


def _is_placeholder_logo(data):
    """Odoo's "Your logo" picture, which a company has until it uploads its own: never printed."""
    if not _PLACEHOLDER:
        import base64
        import hashlib
        from odoo.tools.misc import file_open
        with file_open('base/static/img/res_company_logo.png', 'rb') as f:
            _PLACEHOLDER.append(hashlib.md5(base64.b64encode(f.read())).hexdigest())
    import hashlib
    raw = data if isinstance(data, bytes) else data.encode()
    return hashlib.md5(raw).hexdigest() == _PLACEHOLDER[0]


def ethiopic_font_face():
    """Noto Sans Ethiopic (SIL Open Font Licence) carried inside the PDF, so Amharic prints on any server."""
    if not _FACES:
        import base64
        from odoo.tools.misc import file_path
        css = []
        for weight, name in (('normal', 'NotoSansEthiopic.ttf'), ('bold', 'NotoSansEthiopic-Bold.ttf')):
            with open(file_path('sonador_print/static/fonts/%s' % name), 'rb') as f:
                data = base64.b64encode(f.read()).decode()
            css.append("@font-face { font-family: 'Noto Sans Ethiopic'; font-weight: %s; src: url(data:font/truetype;base64,%s) format('truetype'); }" % (weight, data))
        _FACES['css'] = Markup('\n'.join(css))
    return _FACES['css']

# kind: (label, Amharic title, model)
KINDS = {
    'invoice': ('Tax invoice', 'የግብር ደረሰኝ', 'sb.voucher'),
    'cash_sale': ('Cash sale receipt', 'የጥሬ ገንዘብ ሽያጭ ደረሰኝ', 'sb.voucher'),
    'credit_note': ('Credit note', 'የተመላሽ ማስታወሻ', 'sb.voucher'),
    'receipt': ('Receipt (money received)', 'የገንዘብ መቀበያ ደረሰኝ', 'sb.voucher'),
    'payment': ('Payment voucher', 'የክፍያ ሰነድ', 'sb.voucher'),
    'bill': ('Supplier bill', 'የአቅራቢ ደረሰኝ', 'sb.voucher'),
    'journal': ('Journal voucher', 'የሂሳብ መዝገብ ሰነድ', 'sb.voucher'),
    'transfer': ('Bank transfer', 'የባንክ ዝውውር', 'sb.voucher'),
    'quotation': ('Quotation', 'የዋጋ ማቅረቢያ', 'sale.order'),
    'sales_order': ('Sales order', 'የሽያጭ ትዕዛዝ', 'sale.order'),
    'purchase_order': ('Purchase order', 'የግዢ ትዕዛዝ', 'purchase.order'),
    'delivery_note': ('Delivery note', 'የእቃ ማስረከቢያ', 'sb.delivery.batch'),
    'grn': ('Goods received note', 'የእቃ መረከቢያ', 'sb.stock.voucher'),
    'giv': ('Goods issue voucher', 'የእቃ ወጪ ሰነድ', 'sb.stock.voucher'),
    'stock_transfer': ('Stock transfer', 'የእቃ ዝውውር', 'sb.stock.voucher'),
    'stock_count': ('Stock count', 'የእቃ ቆጠራ', 'sb.stock.voucher'),
    'payslip': ('Payslip', 'የደመወዝ መግለጫ', 'sb.payroll.line'),
    'statement': ('Statement of account', 'የሂሳብ መግለጫ', 'res.partner'),
}
# what a customer receives that could be mistaken for an invoice
NOT_TAX_INVOICE = {'quotation', 'sales_order', 'delivery_note', 'receipt', 'statement'}
PAPER_REPORT = {'a4': 'sonador_print.action_print_a4', 'a5': 'sonador_print.action_print_a5', 'receipt': 'sonador_print.action_print_receipt'}


class SbPrint(models.AbstractModel):
    _name = 'sb.print'
    _description = 'Print engine'

    # ------------------------------------------------------------------ kinds
    @api.model
    def kind_of(self, record):
        m = record._name
        if m == 'sb.voucher':
            return {'sale': 'invoice', 'cash_sale': 'cash_sale', 'sale_return': 'credit_note', 'receipt': 'receipt', 'payment': 'payment',
                    'bill': 'bill', 'purchase_return': 'bill', 'cash_purchase': 'payment', 'journal': 'journal', 'opening': 'journal',
                    'transfer': 'transfer'}.get(record.voucher_type, 'journal')
        if m == 'sale.order':
            return 'sales_order' if record.state == 'sale' else 'quotation'
        if m == 'purchase.order':
            return 'purchase_order'
        if m == 'sb.delivery.batch':
            return 'delivery_note'
        if m == 'sb.stock.voucher':
            return {'grn': 'grn', 'giv': 'giv', 'transfer': 'stock_transfer', 'adjustment': 'stock_count', 'opening': 'stock_count'}.get(record.voucher_type, 'giv')
        if m == 'sb.payroll.line':
            return 'payslip'
        if m == 'res.partner':
            return 'statement'
        raise UserError(self.env._('This document cannot be printed with the designer yet.'))

    # ------------------------------------------------------------------ the description of one document
    @api.model
    def doc(self, record, style=None):
        kind = self.kind_of(record)
        st = style or self.env['sb.print.style'].for_kind(kind, record.company_id if 'company_id' in record._fields else None).as_dict()
        d = getattr(self, '_doc_%s' % record._name.replace('.', '_'))(record, kind)
        label, title_am, _m = KINDS[kind]
        d.setdefault('title', label)
        d['kind'] = kind
        d['title_am'] = title_am
        if st.get('title'):
            d['title'] = st['title']
        cur = d.get('currency') or self.env.company.currency_id
        d['currency_name'] = cur.name
        d['fmt'] = lambda v, cur=cur: self._money(v, cur)
        amount = d.get('amount')
        if amount is not None:
            d['words_en'] = cur.amount_to_text(abs(amount)) if st.get('show_words') else ''
            d['words_am'] = amharic_amount(amount, *self._am_units(cur)) if st.get('show_words_am') or (st.get('lang') == 'am' and st.get('show_words')) else ''
            if st.get('lang') == 'am' and d['words_am']:
                d['words_en'] = ''
        d['date_ec'] = format_ethiopian(d['date']) if st.get('show_ec_date') and d.get('date') and not gregorian() else ''
        d['style'] = st
        # Ethiopia: only the fiscal register or the Ministry's e-invoice is a tax invoice; say so on what the customer gets
        from odoo.addons.sonador_books.models.fiscal_print import fiscal_only
        company = record.company_id if 'company_id' in record._fields and record.company_id else self.env.company
        d['not_tax_invoice'] = kind in NOT_TAX_INVOICE and fiscal_only(company)
        d['font_css'] = {'serif': "Georgia, 'DejaVu Serif', serif", 'mono': "'DejaVu Sans Mono', monospace"}.get(
            st.get('font'), "'Noto Sans', 'DejaVu Sans', Arial, sans-serif")
        d['colors'] = [c if re.fullmatch(r'#[0-9A-Fa-f]{3,8}', c or '') else dflt
                       for c, dflt in ((st.get('color'), '#1B2240'), (st.get('accent'), '#2D6BFF'))]
        amharic = st.get('lang') in ('am', 'both') or bool(d.get('words_am')) or d['not_tax_invoice']
        if amharic and self.env.context.get('sb_print_screen'):
            # on screen the browser fetches the font file itself; only the PDF needs it carried inside
            d['font_face'] = Markup("@font-face { font-family: 'Noto Sans Ethiopic'; font-weight: normal; src: url(/sonador_print/static/fonts/NotoSansEthiopic.ttf); }"
                                    "@font-face { font-family: 'Noto Sans Ethiopic'; font-weight: bold; src: url(/sonador_print/static/fonts/NotoSansEthiopic-Bold.ttf); }")
        else:
            d['font_face'] = ethiopic_font_face() if amharic else ''
        if amharic:
            # the PDF printer only falls back to an embedded font named first; Latin letters fall through to the next
            d['font_css'] = "'Noto Sans Ethiopic', " + d['font_css']
        d['company'] = self._company(record)
        d['signatures'] = [s.strip() for s in (st.get('signature_labels') or '').split(',') if s.strip()] if st.get('show_signatures') else []
        self._translate(d, st.get('lang') or 'en')
        return d

    def _translate(self, d, lang):
        t = d['t'] = translator(lang)
        if d.get('party'):
            d['party'] = dict(d['party'], label=t(d['party']['label']))
            if d['party']['name'] == 'Cash customer':
                d['party']['name'] = t('Cash customer')
        for key in ('meta', 'fiscal'):
            d[key] = [(t(a), b) for a, b in d.get(key) or []]
        d['totals'] = [(t(a), b, c) for a, b, c in d.get('totals') or []]
        d['status_mark'] = t(d.get('status_mark') or '')
        d['signatures'] = [t(s) for s in d['signatures']]
        # column heads: in both languages the Amharic goes on a second line, to keep the columns narrow
        d['columns'] = [dict(c, label=c['label'] if lang == 'both' else t(c['label']), label_am=amharic(c['label']) if lang == 'both' else '')
                        for c in d.get('columns') or []]
        for l in d.get('lines') or []:
            if d['kind'] == 'payslip' and lang != 'en':
                l['desc'] = t(l['desc'])
            if l.get('tax') == 'Exempt':
                l['tax'] = t('Exempt') if lang != 'both' else 'Exempt'

    def _am_units(self, cur):
        return {'ETB': ('ብር', 'ሳንቲም'), 'USD': ('የአሜሪካ ዶላር', 'ሳንቲም'), 'EUR': ('ዩሮ', 'ሳንቲም')}.get(cur.name, (cur.name, ''))

    def _money(self, v, cur):
        if v is None or v == '':
            return ''
        d = cur.decimal_places if cur else 2
        return '{:,.{d}f}'.format(v, d=d)

    def _company(self, record):
        c = record.company_id if 'company_id' in record._fields and record.company_id else self.env.company
        p = c.partner_id
        return {'name': c.name, 'logo': c.logo if c.logo and not _is_placeholder_logo(c.logo) else False, 'tin': c.vat or '', 'vat_no': c.company_registry or '',
                'address': ', '.join(x for x in (p.street, p.street2, p.city, p.country_id.name) if x),
                'phone': c.phone or p.phone or '', 'email': c.email or p.email or '', 'website': c.website or ''}

    def _party(self, partner, label):
        p = partner.commercial_partner_id if partner else partner
        if not p:
            return {'label': label, 'name': '', 'tin': '', 'address': '', 'phone': ''}
        return {'label': label, 'name': p.name or '', 'tin': p.vat or '', 'address': ', '.join(x for x in (p.street, p.city, p.country_id.name) if x),
                'phone': p.phone or ''}

    def _qty(self, q):
        return '{:,.4f}'.format(q).rstrip('0').rstrip('.')

    # ------------------------------------------------------------------ printing
    @api.model
    def print_action(self, records, style=None):
        if not records:
            raise UserError(self.env._('Nothing to print.'))
        kind = self.kind_of(records[:1])
        st = style or self.env['sb.print.style'].for_kind(kind, records[:1].company_id if 'company_id' in records._fields else None).as_dict()
        report = self.env.ref(PAPER_REPORT.get(st.get('paper'), PAPER_REPORT['a4']))
        data = {'model': records._name, 'ids': records.ids}
        if style:
            data['style'] = style
        # config=False: our designs replace Odoo's document layout dialog, which it would otherwise open first
        action = report.report_action(None, data=data, config=False)
        action['name'] = '%s %s' % (KINDS[kind][0], records[:1].display_name)
        return action

    @api.model
    def pdf(self, records, style=None):
        """The PDF of records in their design, as bytes (for downloads and attachments)."""
        kind = self.kind_of(records[:1])
        st = style or self.env['sb.print.style'].for_kind(kind, records[:1].company_id if 'company_id' in records._fields else None).as_dict()
        report = self.env.ref(PAPER_REPORT.get(st.get('paper'), PAPER_REPORT['a4']))
        pdf, _t = report._render_qweb_pdf(report.report_name, [], data={'model': records._name, 'ids': records.ids, 'style': st})
        return pdf

    @api.model
    def kinds(self):
        from odoo.addons.sonador_books.models.fiscal_print import fiscal_only
        # Ethiopia: sales invoices come only from the fiscal cash register or the Ministry's e-invoice, never from here
        hidden = {'invoice', 'cash_sale', 'credit_note'} if fiscal_only(self.env.company) else set()
        return [{'kind': k, 'label': v[0], 'model': v[2]} for k, v in KINDS.items() if k not in hidden]

    @api.model
    def sample(self, kind):
        """The latest document of a kind, to preview a design on."""
        model = KINDS[kind][2]
        M = self.env[model]
        if kind == 'statement':
            v = self.env['sb.voucher'].search([('company_id', '=', self.env.company.id), ('voucher_type', '=', 'sale'), ('state', '=', 'posted')],
                                              order='date desc, id desc', limit=1)
            return v.partner_id.commercial_partner_id
        dom = [('company_id', '=', self.env.company.id)] if 'company_id' in M._fields else []
        for r in M.search(dom, order='id desc', limit=200):
            try:
                if self.kind_of(r) == kind:
                    return r
            except UserError:
                continue
        return M

    @api.model
    def preview(self, kind, style):
        """HTML of the latest document of the kind in a design not yet saved."""
        r = self.sample(kind)
        if not r:
            return '<p style="font:14px sans-serif;color:#666;padding:30px">There is no %s yet to preview. Save the design; it applies as soon as one is printed.</p>' % KINDS[kind][0].lower()
        base = self.env['sb.print.style'].for_kind(kind).as_dict()
        base.update(style or {})
        report = self.env.ref(PAPER_REPORT.get(base.get('paper'), PAPER_REPORT['a4']))
        html, _t = report.with_context(sb_print_screen=True)._render_qweb_html(report.report_name, [], data={'model': r._name, 'ids': r.ids, 'style': base})
        html = html.decode() if isinstance(html, bytes) else html
        # shown inside a frame: let its stylesheets and images resolve against this server
        root = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        return html.replace('<head>', '<head><base href="%s/"/>' % root, 1)


class ReportPrintDocument(models.AbstractModel):
    _name = 'report.sonador_print.document'
    _description = 'Designed document'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        records = self.env[data['model']].browse(data.get('ids') or docids)
        engine = self.env['sb.print']
        return {'doc_ids': records.ids, 'doc_model': data['model'], 'docs': records,
                'pages': [engine.doc(r, data.get('style')) for r in records]}


class SbPrintDesigner(models.AbstractModel):
    _inherit = 'sb.print'

    @api.model
    def designer(self):
        from .style import FONTS, LANGS, PAPERS, SHOW, TEMPLATES
        S = self.env['sb.print.style']
        saved = {s.kind for s in S.search([('company_id', '=', self.env.company.id)])}
        return {'kinds': [dict(k, custom=k['kind'] in saved) for k in self.kinds()], 'templates': TEMPLATES, 'papers': PAPERS, 'fonts': FONTS,
                'langs': LANGS, 'show': [{'key': f, 'label': l} for f, l, _d in SHOW], 'company': self.env.company.name,
                'has_default': 'default' in saved}

    @api.model
    def load_style(self, kind):
        return self.env['sb.print.style'].for_kind(kind).as_dict()

    @api.model
    def save_style(self, kind, vals, for_all=False):
        """Save the design of a kind; for_all: as the company default, and drop the per-kind designs so all follow it."""
        S = self.env['sb.print.style']
        if not self.env.user.has_group('sonador_books.group_sb_accountant'):
            from odoo.exceptions import AccessError
            raise AccessError(self.env._('Document designs are kept by the accountants.'))
        from .style import SHOW
        allowed = {'template', 'paper', 'font', 'lang', 'color', 'accent', 'logo_position', 'title', 'header_note', 'footer', 'terms',
                   'bank_details', 'signature_labels'} | {f for f, _l, _d in SHOW}
        clean = {k: v for k, v in (vals or {}).items() if k in allowed}
        target = 'default' if for_all else kind
        if for_all:
            clean.pop('title', None)
        row = S.search([('company_id', '=', self.env.company.id), ('kind', '=', target)], limit=1)
        if row:
            row.write(clean)
        else:
            S.create(dict(clean, kind=target, company_id=self.env.company.id))
        if for_all:
            S.search([('company_id', '=', self.env.company.id), ('kind', 'not in', ('default',))]).unlink()
        return self.designer()

    @api.model
    def print_sample(self, kind, style=None):
        r = self.sample(kind)
        if not r:
            raise UserError(self.env._('There is no document of this kind yet.'))
        return self.print_action(r, style)


class ReportPrintDocumentA4(models.AbstractModel):
    _name = 'report.sonador_print.document_a4'
    _inherit = 'report.sonador_print.document'
    _description = 'Designed document (A4)'


class ReportPrintDocumentA5(models.AbstractModel):
    _name = 'report.sonador_print.document_a5'
    _inherit = 'report.sonador_print.document'
    _description = 'Designed document (A5)'


class ReportPrintDocumentReceipt(models.AbstractModel):
    _name = 'report.sonador_print.document_receipt'
    _inherit = 'report.sonador_print.document'
    _description = 'Designed document (receipt)'
