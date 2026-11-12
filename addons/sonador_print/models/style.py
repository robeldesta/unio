"""Document designs: one per company and document kind (and a company default the others start from)."""
from odoo import api, fields, models

TEMPLATES = [('classic', 'Classic'), ('modern', 'Modern'), ('compact', 'Compact'), ('bold', 'Bold')]
PAPERS = [('a4', 'A4'), ('a5', 'A5'), ('receipt', 'Receipt (80 mm)')]
FONTS = [('sans', 'Clean sans'), ('serif', 'Classic serif'), ('mono', 'Typewriter')]
LANGS = [('en', 'English'), ('am', 'Amharic'), ('both', 'English and Amharic')]

# what a design can show or hide, with the default
SHOW = [
    ('show_logo', 'Logo', True), ('show_company_address', 'Our address and phone', True), ('show_tin', 'TIN and VAT numbers', True),
    ('show_ec_date', 'Ethiopian date', True), ('show_codes', 'Item codes', True), ('show_tax_column', 'Tax per line', True),
    ('show_words', 'Amount in words', True), ('show_words_am', 'Amount in words in Amharic', False), ('show_bank', 'Our bank accounts', True),
    ('show_signatures', 'Signature lines', True), ('show_stamp', 'Stamp box', False), ('show_qr', 'Fiscal QR code (e-invoicing)', True),
    ('show_copy_label', '"Original" / "Copy" mark', False), ('show_notes', 'Notes and memo', True), ('show_terms', 'Terms', True),
]


class SbPrintStyle(models.Model):
    _name = 'sb.print.style'
    _description = 'Document design'
    _order = 'kind'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    kind = fields.Char(required=True, help="'default' or a document kind (invoice, receipt, quotation ...).")
    template = fields.Selection(TEMPLATES, default='modern', required=True)
    paper = fields.Selection(PAPERS, default='a4', required=True)
    font = fields.Selection(FONTS, default='sans', required=True)
    lang = fields.Selection(LANGS, default='en', required=True)
    color = fields.Char('Main colour', default='#1B2240')
    accent = fields.Char('Accent colour', default='#2D6BFF')
    logo_position = fields.Selection([('left', 'Left'), ('center', 'Centre'), ('right', 'Right')], default='left')
    title = fields.Char('Title', help='Leave empty for the standard title (e.g. "Tax Invoice").')
    header_note = fields.Text('Note under the title')
    footer = fields.Text('Footer', help='Printed at the bottom of every page, e.g. registration numbers, website.')
    terms = fields.Text('Terms and conditions')
    bank_details = fields.Text('Bank accounts to print', help='e.g. "CBE 1000123456789 · Awash 01320..."')
    signature_labels = fields.Char('Signatures', default='Prepared by, Approved by, Received by',
                                   help='Comma-separated: one signature line each.')
    for _f, _label, _default in SHOW:
        locals()[_f] = fields.Boolean(_label, default=_default)
    del _f, _label, _default

    _uniq = models.Constraint('unique(company_id, kind)', 'One design per document kind.')

    @api.model
    def for_kind(self, kind, company=None):
        """The design of a kind, falling back on the company default, then on built-in defaults (not saved)."""
        company = company or self.env.company
        s = self.sudo().search([('company_id', '=', company.id), ('kind', '=', kind)], limit=1)
        if s:
            return s
        return self.sudo().search([('company_id', '=', company.id), ('kind', '=', 'default')], limit=1) or self.new({'kind': kind, 'company_id': company.id})

    def as_dict(self):
        self.ensure_one()
        names = ['template', 'paper', 'font', 'lang', 'color', 'accent', 'logo_position', 'title', 'header_note', 'footer', 'terms',
                 'bank_details', 'signature_labels'] + [f for f, _l, _d in SHOW]
        return {n: self[n] for n in names}
