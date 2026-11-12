"""Print from the voucher screen, Peachtree / Sage style: every voucher prints as what it is.

Payment and receipt vouchers show the payee or payer, the amount in words, the bank and cash rows with cheque numbers,
the invoices settled, the withholding deducted, the net paid or received, the GL distribution and the signatures.
Journal vouchers, transfers, bills and cash purchases print their lines and GL distribution. Sales invoices, cash sales
and credit notes print as the customer's invoice, except where only a fiscal device may issue them (Ethiopia): there the
Print button is not offered at all."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from .ethiopian_calendar import format_ethiopian
from .fiscal_print import SALE_VOUCHERS, fiscal_only

TITLES = {
    'payment': 'Payment Voucher',
    'receipt': 'Receipt Voucher',
    'cash_purchase': 'Cash Purchase Voucher',
    'journal': 'Journal Voucher',
    'opening': 'Opening Balances Voucher',
    'transfer': 'Bank Transfer Voucher',
    'bill': 'Purchase Bill',
    'purchase_return': 'Debit Note (Purchase Return)',
    'sale': 'Sales Invoice',
    'cash_sale': 'Cash Sale',
    'sale_return': 'Credit Note',
}
# sonador_print's designed documents, when it is installed, for what goes to customers and suppliers as a document
DESIGNED = ('sale', 'cash_sale', 'sale_return', 'bill', 'purchase_return')
DEFAULT_LAYOUT = 'web.external_layout_standard'


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_printable = fields.Boolean(compute='_compute_sb_printable')

    @api.depends('voucher_type', 'company_id')
    def _compute_sb_printable(self):
        for v in self:
            v.sb_printable = not (v.voucher_type in SALE_VOUCHERS and fiscal_only(v.company_id or self.env.company))

    def action_sb_print(self):
        if not self:
            return False
        blocked = self.filtered(lambda v: not v.sb_printable)
        if blocked:
            raise UserError(self.env._(
                '%s: sales invoices, cash sales and credit notes are issued by the fiscal cash register or the Ministry '
                'e-invoice, not printed here.', ', '.join(blocked.mapped('display_name'))))
        if len(set(self.mapped('voucher_type'))) > 1:
            raise UserError(self.env._('Print one kind of voucher at a time.'))
        vtype = self[:1].voucher_type
        if vtype in DESIGNED and 'sb.print' in self.env:
            return self.env['sb.print'].print_action(self)
        if vtype in SALE_VOUCHERS:
            return self.env.ref('sonador_books.action_report_sale_document').report_action(self)
        return self.env.ref('sonador_books.action_report_voucher').report_action(self)

    def action_sb_print_internal(self):
        """A sales invoice, cash sale or credit note printed for the file, marked "INTERNAL COPY - NOT A TAX INVOICE" on
        every page. Allowed where the customer's invoice comes only from the fiscal register: it is not an invoice."""
        if self.filtered(lambda v: v.voucher_type not in SALE_VOUCHERS):
            raise UserError(self.env._('Internal copies are for sales invoices, cash sales and credit notes.'))
        for v in self:
            v.message_post(body=self.env._('Internal copy printed (not a tax invoice).'))
        return self.env.ref('sonador_books.action_report_voucher_internal').report_action(self)

    @api.model
    def _sb_print_date(self, day):
        """(30 Oct 2026, its Ethiopian date): how every printout writes a date, whatever the user's language."""
        return (day.strftime('%d %b %Y'), format_ethiopian(day)) if day else ('', '')

    # ------------------------------------------------------------------ what the voucher printout shows
    def _sb_print_data(self, internal=False):
        self.ensure_one()
        cur = self.currency_id
        doc_cur = self.amount_currency_id or cur
        vtype = self.voucher_type
        money = lambda x, c=cur: '{:,.{d}f}'.format(x or 0.0, d=c.decimal_places if c else 2)  # noqa: E731
        d = {
            'title': TITLES.get(vtype, 'Voucher'),
            'number': self.name if self.name not in (False, 'New', '/') else self.env._('Draft'),
            'date': self.date, 'date_ec': format_ethiopian(self.date),
            'mark': {'draft': 'NOT POSTED', 'void': 'VOID'}.get(self.state, ''),
            'not_tax_invoice': vtype == 'receipt' and fiscal_only(self.company_id),
            'internal': internal,
            'currency': cur.name, 'memo': self.memo or '', 'reference': self.reference or '',
            'party_label': {'payment': 'Paid to', 'cash_purchase': 'Paid to', 'receipt': 'Received from',
                            'bill': 'Supplier', 'purchase_return': 'Supplier', 'sale': 'Customer', 'cash_sale': 'Customer',
                            'sale_return': 'Customer'}.get(vtype, 'Partner'),
            'party': False, 'party_tin': '', 'amount': None, 'words': '',
            'bank_rows': [], 'applied': [], 'lines': [], 'line_kind': False, 'summary': [], 'gl': [], 'gl_note': '',
        }
        p = self.partner_id.commercial_partner_id
        if p:
            d['party'], d['party_tin'] = p.name, p.vat or ''
        elif vtype == 'transfer':
            d['party_label'], d['party'] = 'From', self.cash_account_id.display_name
        money_voucher = vtype in ('payment', 'receipt', 'cash_purchase', 'transfer')
        if money_voucher:
            d['amount'] = money(self.amount_cash)
            d['words'] = cur.amount_to_text(abs(self.amount_cash)) if cur else ''
            methods = dict(self.env['sb.voucher.cash']._fields['method']._description_selection(self.env))
            d['bank_rows'] = [{
                'account': r.account_id.display_name, 'method': methods.get(r.method, ''),
                'ref': r.reference or '', 'cheque_date': self._sb_print_date(r.cheque_date)[0],
                'bank': r.cheque_bank or '',
                'amount': ('%s %s' % (r.row_currency_id.name, money(r.amount_currency, r.row_currency_id)))
                if r.row_currency_id and r.row_currency_id != cur else money(r.amount_currency),
                'charges': money(r.charges, r.row_currency_id) if r.charges else '',
            } for r in self.cash_line_ids]
        if vtype in ('payment', 'receipt'):
            primary = 'debit' if vtype == 'receipt' else 'credit'
            for a in self.apply_ids.filtered(lambda x: x.amount_applied or x.discount):
                d['applied'].append({
                    'doc': a.document or '', 'date': self._sb_print_date(a.invoice_date)[0],
                    'ref': a.invoice_ref or '', 'kind': a.item_type or '',
                    'original': money(a.amount_total, a.item_currency_id),
                    'applied': ('' if a.direction == primary else '-') + money(a.amount_applied_company),
                    'discount': money(a.discount) if a.discount else ''})
            d['line_kind'] = 'gl'
            d['lines'] = [{'account': l.account_id.display_name, 'desc': l.name or '', 'partner': l.partner_id.name or '',
                           'tax': ', '.join(l.tax_ids.mapped('name')), 'amount': money(l.amount)} for l in self.line_ids if l.amount]
            untaxed, tax = self.line_ids._sb_split_tax()
            s = d['summary']
            if self.amount_applied:
                s.append(('Invoices and items settled', money(self.amount_applied), False))
            if self.amount_credits:
                s.append(('Less advances / credits used', '-' + money(self.amount_credits), False))
            if untaxed:
                s.append(('Other items (GL lines)', money(untaxed), False))
            if tax:
                s.append(('VAT', money(tax), False))
            s.append(('Total', money(self.amount_total), False))
            if self.withholding and self.wht_amount:
                s.append(('Less withholding tax%s' % (' (receipt %s)' % self.wht_certificate_no if self.wht_certificate_no else ''),
                          '-' + money(self.wht_amount), False))
            if self.amount_unapplied:
                s.append(('Advance (not applied to an invoice)', money(self.amount_unapplied), False))
            s.append(('Net %s %s' % ('received' if vtype == 'receipt' else 'paid', cur.name), money(self.amount_cash), True))
            if self.amount_discount:
                # written off on top of the money: the invoices settled are cleared by it too
                s.append(('Discount / write-off given, reason: %s' % (self.sb_discount_reason or '-'), money(self.amount_discount), False))
        elif vtype in ('cash_purchase', 'bill', 'purchase_return') + SALE_VOUCHERS:
            d['line_kind'] = 'item'
            d['lines'] = [{'desc': l.name or l.product_id.display_name or l.account_id.name or '',
                           'account': l.account_id.display_name or '',
                           'qty': '{:,.4f}'.format(l.quantity).rstrip('0').rstrip('.'), 'price': money(l.price_unit, doc_cur),
                           'tax': ', '.join(l.tax_ids.mapped('name')), 'amount': money(l.price_subtotal, doc_cur)} for l in self.line_ids]
            s = d['summary']
            s += [('Amount before VAT', money(self.amount_untaxed, doc_cur), False), ('VAT', money(self.amount_tax, doc_cur), False),
                  ('Total %s' % doc_cur.name, money(self.amount_total, doc_cur), vtype not in ('cash_purchase', 'cash_sale'))]
            if vtype == 'cash_sale':
                s.append(('Received %s' % cur.name, money(self.amount_cash), True))
            elif vtype == 'cash_purchase':
                if self.withholding and self.wht_amount:
                    s.append(('Less withholding tax%s' % (' (receipt %s)' % self.wht_certificate_no if self.wht_certificate_no else ''),
                              '-' + money(self.wht_amount), False))
                s.append(('Net paid %s' % cur.name, money(self.amount_cash), True))
            elif self.due_date:
                d['reference'] = ' '.join(filter(None, [d['reference'], self.env._('due %s', self._sb_print_date(self.due_date)[0])]))
        elif vtype == 'transfer':
            d['line_kind'] = 'gl'
            d['lines'] = [{'account': l.account_id.display_name, 'desc': l.name or '', 'partner': l.partner_id.name or '',
                           'tax': '', 'amount': money(l.amount)} for l in self.line_ids if l.amount]
            d['summary'] = [('Transferred %s' % cur.name, money(self.amount_cash), True)]
        # the GL distribution: the posted entry; a journal not yet posted shows its own lines
        if self.move_id and self.state in ('posted', 'void'):
            src = self.move_id.line_ids.sorted(lambda l: (-l.debit, l.id))
        elif vtype in ('journal', 'opening'):
            src = self.line_ids
        else:
            src = False
            d['gl_note'] = self.env._('Not posted yet: the GL distribution shows once the voucher is posted.')
        if src:
            # an entry line without a label of its own ("/") says what it is part of
            d['gl'] = [{'account': '%s %s' % (l.account_id.code or '', l.account_id.name or ''),
                        'desc': l.name if l.name not in (False, '', '/') else (self.memo or d['number']),
                        'partner': l.partner_id.name or '', 'debit': money(l.debit) if l.debit else '',
                        'credit': money(l.credit) if l.credit else ''} for l in src]
            d['gl_total'] = (money(sum(src.mapped('debit'))), money(sum(src.mapped('credit'))))
        if vtype in ('journal', 'opening'):
            d['summary'] = []
        signatures = ['Prepared by', 'Checked by', 'Approved by']
        if vtype in ('payment', 'cash_purchase'):
            signatures.append('Received by (payee)')
        elif vtype == 'receipt':
            signatures.append('Received by (cashier)')
        d['signatures'] = signatures
        d['prepared_by'] = self.create_uid.name or ''
        return d


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies._sb_print_defaults()
        return companies

    @api.model
    def _sb_print_defaults(self):
        """Printing works on the first click: a company with no document layout gets the standard one (else Odoo first
        opens its "Configure your document layout" dialog, which does not print), and documents are not held a second
        before printing (Odoo waits 1 s per page for scripts our printouts do not use). Run on every upgrade."""
        layout = self.env.ref(DEFAULT_LAYOUT, raise_if_not_found=False)
        for company in (self or self.search([])).sudo().filtered(lambda c: not c.external_report_layout_id):
            company.external_report_layout_id = layout
        params = self.env['ir.config_parameter'].sudo()
        if not params.get_param('report.print_delay'):
            params.set_param('report.print_delay', '200')
