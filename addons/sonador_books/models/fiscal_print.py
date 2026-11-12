"""Ethiopia: a sales invoice, cash sale receipt or credit note (refund) is issued only by a fiscal cash register or as the Ministry of Revenue's
QR-coded e-invoice. A company in Ethiopia records its sales here but never prints, downloads or emails them as invoices:
every report that would render one is refused, whichever button, menu, email or portal link asked for it."""
from odoo import api, models
from odoo.exceptions import UserError

SALE_VOUCHERS = ('sale', 'cash_sale', 'sale_return')
SALE_MOVES = ('out_invoice', 'out_receipt', 'out_refund')
INTERNAL_COPY = 'sonador_books.report_voucher_internal'


def fiscal_only(company):
    """True for companies whose sales invoices come only from fiscal devices / the Ministry (Ethiopia)."""
    company = company.sudo()
    country = company.account_fiscal_country_id or company.country_id
    return (country.code or '') == 'ET'


def is_sales_invoice(record):
    if record._name == 'sb.voucher':
        return record.voucher_type in SALE_VOUCHERS
    if record._name == 'account.move':
        return record.move_type in SALE_MOVES
    return False


def check_printable(records):
    for r in records:
        if r._name in ('sb.voucher', 'account.move') and is_sales_invoice(r) and fiscal_only(r.company_id):
            raise UserError(r.env._(
                'Sales invoices and credit notes are not printed in Ethiopia: they are issued only by your fiscal cash register or as the '
                'Ministry of Revenue\'s QR-coded e-invoice. %s is recorded in the books; issue the customer\'s invoice from '
                'the cash register or the Ministry\'s system.', r.display_name))


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _sb_records(self, report_ref, res_ids, data):
        report = self._get_report(report_ref)
        out = []
        if report.report_name == INTERNAL_COPY:
            # the internal copy is marked "not a tax invoice" on every page and is never emailed: not an invoice
            return out
        if res_ids and report.model in ('sb.voucher', 'account.move'):
            out.append(self.env[report.model].browse(res_ids).exists())
        data = data or {}
        if data.get('model') in ('sb.voucher', 'account.move') and data.get('ids'):
            out.append(self.env[data['model']].browse(data['ids']).exists())
        return out

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        for recs in self._sb_records(report_ref, res_ids, data):
            check_printable(recs.sudo())
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def _render_qweb_html(self, report_ref, docids, data=None):
        for recs in self._sb_records(report_ref, docids, data):
            check_printable(recs.sudo())
        return super()._render_qweb_html(report_ref, docids, data=data)


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def sb_fiscal_only(self):
        return fiscal_only(self.env.company)
