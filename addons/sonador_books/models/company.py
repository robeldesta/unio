from odoo import api, fields, models

from .ethiopian_calendar import format_ethiopian


DEFAULT_FS_POLICIES = """Basis of preparation: these financial statements are prepared under International Financial Reporting Standards (IFRS for SMEs where applicable) on the historical cost basis, in the functional currency of the company, and on the accrual basis.
Revenue: revenue from the sale of goods is recognised when control passes to the customer, normally on delivery, at the amount invoiced net of VAT, returns and discounts.
Inventories: inventories are stated at the lower of cost and net realisable value; cost is determined on the method set for each item category (FIFO or weighted average).
Property, plant and equipment: items are carried at cost less accumulated depreciation and impairment; depreciation is charged on the straight-line basis over the useful lives of the assets.
Financial instruments: trade receivables are carried at the amount invoiced less an allowance for expected credit losses; trade payables at the amount payable.
Income tax: the income tax expense is the tax payable on the taxable profit for the year under the applicable tax law.
Foreign currencies: transactions in foreign currencies are recorded at the rate on the date of the transaction; monetary balances are translated at the closing rate and the differences are recognised in profit or loss."""


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_wht_rate = fields.Float(
        'Withholding Rate (%)', default=3.0,
        help='Withholding tax on payments to suppliers. Confirm the current rate and thresholds '
             'under the Income Tax Proclamation with your tax adviser.')
    sb_wht_individual_rate = fields.Float(
        'Withholding Rate - Individuals (%)', default=30.0,
        help='Ethiopia: payments to individuals, and to suppliers without a TIN or trade licence.')
    sb_wht_country_code = fields.Char(compute='_compute_sb_wht_country_code')
    sb_wht_agent = fields.Boolean(
        'Designated withholding agent', default=True,
        help='Uganda: the company is on the URA list of designated withholding agents and withholds 6% on goods and '
             'services over UGX 1,000,000. Professional fees are withheld either way.')
    sb_fiscal_registers = fields.Char(
        'Other fiscal registers',
        help='Ethiopia: letters of fiscal registers (or Ministry e-invoice references) besides those of the sales series and of '
             'the receipts already in the books, as printed, separated by commas: e.g. MOR, FS/FPDV2. A receipt number with '
             'other letters is refused, so a receipt cannot be entered twice under a slightly different number.')
    sb_number_without_year = fields.Boolean(
        'Numbers without the year',
        help='Document numbers count on from one year to the next and carry no year: PR/1742, GP/00179, TRF/0001 instead of '
             'PR/2026/1742 or TRF/26-27/0001. Documents already numbered keep their numbers; the count goes on after the '
             'highest one, with or without the year.')
    sb_vat_registered = fields.Boolean(
        'Registered for VAT', default=True,
        help='Untick for a company that charges no VAT (not registered, or all its sales are VAT-exempt, e.g. agricultural '
             'inputs): lines entered with a GL account and no item then get no VAT. Items keep their own taxes.')
    # Ethiopia, Turnover Tax Proclamation 308/2002 (as amended): 2% on goods sold locally; services 2% (contractors, grain
    # mills, tractors and combine harvesters) or 10% (other services). Supplies exempt from VAT are exempt from TOT.
    sb_tot_goods_rate = fields.Float('TOT rate - goods (%)', default=2.0)
    sb_tot_service_rate = fields.Float(
        'TOT rate - services (%)', default=10.0,
        help='2% for contractors, grain mills, tractors and combine harvesters; 10% for other services.')
    sb_tot_goods_exempt = fields.Boolean(
        'Goods sold are exempt from TOT',
        help='The goods the company sells are exempt (e.g. fertilizer and other agricultural inputs): the TOT declaration '
             'lists them as exempt sales. Lines with an exempt tax are exempt either way.')
    sb_wht_goods_threshold = fields.Monetary('Withholding Threshold - Goods', default=20000.0)
    sb_wht_service_threshold = fields.Monetary('Withholding Threshold - Services', default=10000.0)
    sb_wht_payable_account_id = fields.Many2one(
        'account.account', string='Withholding Payable Account',
        help='Credited when we withhold from a vendor (CPV).')
    sb_wht_receivable_account_id = fields.Many2one(
        'account.account', string='Withholding Receivable Account',
        help='Debited when a customer withholds from us (CRV).')

    sb_customer_advance_account_id = fields.Many2one(
        'account.account', string='Customer Advances Account',
        help='Money received before an invoice (or more than the invoices paid). Applied to invoices on later receipts.')
    sb_vendor_advance_account_id = fields.Many2one(
        'account.account', string='Vendor Advances Account',
        help='Money paid before a purchase voucher (or more than the purchase vouchers paid). Applied to purchase vouchers on later payments.')

    sb_cheque_on_hand_account_id = fields.Many2one(
        'account.account', string='Cheques on Hand Account', help='Received cheques (incl. post-dated) until deposited.')
    sb_cheque_outstanding_account_id = fields.Many2one(
        'account.account', string='Outstanding Cheques Account', help='Issued cheques until the bank pays them.')
    sb_bank_charges_account_id = fields.Many2one('account.account', string='Bank Charges Account')
    sb_bank_charges_vat = fields.Float(
        'VAT in bank charges (%)', help='Transfer and bank charges include VAT at this rate (Ethiopia: 15); the VAT part is '
                                        'booked to the VAT account below. 0: the whole charge is an expense.')
    sb_bank_charges_vat_account_id = fields.Many2one('account.account', string='VAT on bank charges account',
                                                     help='Usually VAT receivable on purchases.')
    sb_petty_cash_limit = fields.Monetary(
        'Petty cash limit', default=5000.0,
        help='Payments up to this amount are paid from petty cash by default; larger ones from the main bank.')
    sb_cash_negative = fields.Selection(
        [('warn', 'Warn'), ('block', 'Refuse')], string='Cash or petty cash below zero', default='warn', required=True,
        help='Posting a payment that takes a cash or petty cash account below zero: warn (the payment posts), or refuse it.')
    sb_discount_allowed_account_id = fields.Many2one(
        'account.account', string='Discounts Allowed Account', help='Settlement discounts and small differences written off on receipts.')
    sb_discount_received_account_id = fields.Many2one(
        'account.account', string='Discounts Received Account', help='Settlement discounts and small differences written off on payments.')
    sb_discount_limit = fields.Monetary(
        'Discount / write-off limit (Accountant)', default=1000.0,
        help='The most an Accountant may give as discount or write off on one receipt or payment, in the company currency. '
             'Clerks cannot enter discounts or write-offs; Managers have no limit. A reason is always required.')
    sb_opening_equity_account_id = fields.Many2one(
        'account.account', string='Opening Balance Clearing Account',
        help='Other side of the customer, vendor and item beginning balances while the opening balance is being entered. The opening balance is saved only when debits equal credits, so this account ends at zero.')
    sb_fs_policies = fields.Text(
        'Accounting policies (notes)', default=lambda s: DEFAULT_FS_POLICIES,
        help='The accounting policies note printed first in the Notes to the Financial Statements. One paragraph per line; '
             'edit it to describe the company\'s own policies.')

    def _sb_setup(self):
        from ..hooks import setup_company
        for company in self:
            setup_company(self.env, company)

    def action_sb_setup(self):
        self.check_access('write')
        self._sb_setup()
        return True

    @api.model
    def _sb_retire_withholding_taxes(self):
        """Run on every module update: the chart's old withholding taxes ("2% WH", "35% WH", "15% WH": a negative rate on an
        invoice line) are not how withholding is taken here (on the payment voucher, when the money is paid) and a line
        using one would sit among the VAT. Those never used are archived; one in use stays, and the VAT return leaves it out."""
        Tax = self.env['account.tax'].sudo()
        for tax in Tax.search([('amount_type', '=', 'percent'), ('amount', '<', 0)]):
            used = self.env['account.move.line'].sudo().search_count(['|', ('tax_ids', 'in', tax.id), ('tax_line_id', '=', tax.id)], limit=1) \
                or self.env['sb.voucher.line'].sudo().search_count([('tax_ids', 'in', tax.id)], limit=1) \
                or self.env['product.template'].sudo().with_context(active_test=False).search_count(
                    ['|', ('taxes_id', 'in', tax.id), ('supplier_taxes_id', 'in', tax.id)], limit=1)
            if not used:
                tax.active = False

    @api.depends('account_fiscal_country_id', 'country_id')
    def _compute_sb_wht_country_code(self):
        for c in self:
            c.sb_wht_country_code = (c.account_fiscal_country_id or c.country_id).code


class AccountMove(models.Model):
    _inherit = 'account.move'

    sb_date_ec = fields.Char('E.C. Date', compute='_compute_sb_date_ec')
    # withholding on payments brought over from the previous system: the entry has the tax line only; the base and the
    # receipt number are filled in afterwards for the declaration (they do not change the ledger)
    sb_wht_base = fields.Monetary('Withholding base', copy=False,
                                  help='Amount the tax was withheld on (before VAT), for the withholding declaration.')
    sb_wht_receipt_no = fields.Char('Withholding receipt no.', copy=False)
    sb_wht_receipt_date = fields.Date('Withholding receipt date', copy=False)
    sb_has_wht = fields.Boolean(compute='_compute_sb_has_wht')

    @api.depends('line_ids.account_id')
    def _compute_sb_has_wht(self):
        for move in self:
            accounts = move.company_id.sb_wht_payable_account_id | move.company_id.sb_wht_receivable_account_id
            move.sb_has_wht = bool(accounts & move.line_ids.account_id)

    def _get_starting_sequence(self):
        # Voucher journals number per fiscal year (CRV/26-27/0001), not per month.
        seq = super()._get_starting_sequence()
        if self.journal_id.type == 'general' and self.journal_id.code in ('CRV', 'CPV', 'JV', 'CS', 'CP', 'BT', 'OB'):
            parts = seq.split('/')
            if len(parts) == 4:
                seq = '/'.join([parts[0], parts[1], parts[3]])
        return seq

    def _get_last_sequence_domain(self, relaxed=False):
        # the VOID/<number> reversals of voided vouchers are not part of the series: never take the next number from them
        where_string, param = super()._get_last_sequence_domain(relaxed)
        if where_string != 'WHERE FALSE':
            where_string += " AND name NOT LIKE 'VOID/%%'"
        return where_string, param

    def _must_check_constrains_date_sequence(self):
        # a VOID/<number> reversal keeps the number of the entry it voids, which may be dated in a closed period while
        # the reversal is dated today: its number says nothing about its date
        return super()._must_check_constrains_date_sequence() and not (self.name or '').startswith('VOID/')

    @api.depends('date')
    def _compute_sb_date_ec(self):
        for move in self:
            move.sb_date_ec = format_ethiopian(move.date)

    def _sb_settled_by_payment(self):
        """Whether a receipt or payment (a plain entry, not a credit / debit note) helped settle this invoice. Odoo calls an
        invoice settled by a credit note plus a receipt "Reversed"; it is paid."""
        self.ensure_one()
        exchange = self.company_id.currency_exchange_journal_id
        lines = self.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))
        counterparts = (lines.matched_debit_ids.debit_move_id | lines.matched_credit_ids.credit_move_id).move_id - self
        return any(m.move_type == 'entry' and m.journal_id != exchange for m in counterparts)

    def _sb_void(self, date, ref):
        """Reversing entry of a void, named VOID/<number>: it never takes a number out of a strict series
        (Odoo's own journal sequence would hand it the series' next number, e.g. CS/26-27/0005)."""
        self.ensure_one()
        name = 'VOID/%s' % self.name if self.name and self.name != '/' else False
        if name and (self.search_count([('name', '=', name), ('journal_id', '=', self.journal_id.id)]) or self._sb_name_taken(name)):
            name = False
        vals = {'date': date, 'ref': ref}
        if self.is_invoice(include_receipts=True):
            vals['invoice_date'] = date  # a bill's or invoice's reversal is a credit note: it needs its own date
        if name:
            vals['name'] = name
        return self._reverse_moves([vals], cancel=True)
