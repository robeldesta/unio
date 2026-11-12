from odoo import api, fields, models

from .ethiopian_calendar import format_ethiopian


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
        help='Money paid before a bill (or more than the bills paid). Applied to bills on later payments.')

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
    sb_discount_allowed_account_id = fields.Many2one(
        'account.account', string='Discounts Allowed Account', help='Settlement discounts and small differences written off on receipts.')
    sb_discount_received_account_id = fields.Many2one(
        'account.account', string='Discounts Received Account', help='Settlement discounts and small differences written off on payments.')
    sb_opening_equity_account_id = fields.Many2one(
        'account.account', string='Opening Balance Clearing Account',
        help='Other side of the customer, vendor and item beginning balances while the opening balance is being entered. The opening balance is saved only when debits equal credits, so this account ends at zero.')

    def _sb_setup(self):
        from ..hooks import setup_company
        for company in self:
            setup_company(self.env, company)

    def action_sb_setup(self):
        self.check_access('write')
        self._sb_setup()
        return True

    @api.depends('account_fiscal_country_id', 'country_id')
    def _compute_sb_wht_country_code(self):
        for c in self:
            c.sb_wht_country_code = (c.account_fiscal_country_id or c.country_id).code


class AccountMove(models.Model):
    _inherit = 'account.move'

    sb_date_ec = fields.Char('Ethiopian Date', compute='_compute_sb_date_ec')

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

    @api.depends('date')
    def _compute_sb_date_ec(self):
        for move in self:
            move.sb_date_ec = format_ethiopian(move.date)

    def _sb_void(self, date, ref):
        """Reversing entry of a void, named VOID/<number>: it never takes a number out of a strict series
        (Odoo's own journal sequence would hand it the series' next number, e.g. CS/26-27/0005)."""
        self.ensure_one()
        name = 'VOID/%s' % self.name if self.name and self.name != '/' else False
        if name and self.search_count([('name', '=', name), ('journal_id', '=', self.journal_id.id)]):
            name = False
        vals = {'date': date, 'ref': ref}
        if name:
            vals['name'] = name
        return self._reverse_moves([vals], cancel=True)
