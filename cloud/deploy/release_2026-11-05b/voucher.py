from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

from .ethiopian_calendar import format_ethiopian

VOUCHER_TYPES = [
    ('cash_sale', 'Cash Sale'),
    ('sale', 'Sales Invoice'),
    ('sale_return', 'Sales Return / Credit Note'),
    ('receipt', 'Receipt (CRV)'),
    ('cash_purchase', 'Cash Purchase'),
    ('bill', 'Purchase Bill'),
    ('purchase_return', 'Purchase Return / Debit Note'),
    ('payment', 'Payment (CPV)'),
    ('transfer', 'Bank Transfer'),
    ('journal', 'Journal Voucher'),
    ('opening', 'Opening Balances'),
]

INVOICE_TYPES = ('sale', 'bill', 'sale_return', 'purchase_return')
SERIES_TYPES = ('sale', 'cash_sale', 'sale_return', 'receipt', 'payment', 'journal')  # numbered by strict series (pads)
ITEM_TYPES = INVOICE_TYPES + ('cash_sale', 'cash_purchase')  # lines with item / qty / price / taxes
SALE_SIDE = ('sale', 'cash_sale', 'sale_return')
MONEY_IN = ('receipt', 'cash_sale')
MONEY_OUT = ('payment', 'cash_purchase')
SETTLEMENT_TYPES = MONEY_IN + MONEY_OUT  # vouchers with a deposits / payments grid
MOVE_TYPES = {'sale': 'out_invoice', 'bill': 'in_invoice', 'sale_return': 'out_refund', 'purchase_return': 'in_refund'}

PAYER_TYPES = [
    ('customer', 'Customer'),
    ('vendor', 'Vendor'),
    ('shareholder', 'Shareholder'),
    ('lender', 'Lender / Bank'),
    ('employee', 'Employee'),
    ('government', 'Tax authority / Government'),
    ('other', 'Other'),
]

VOUCHER_JOURNALS = {
    'cash_sale': 'CS',
    'cash_purchase': 'CP',
    'sale': 'INV',
    'sale_return': 'INV',
    'receipt': 'CRV',
    'bill': 'BILL',
    'purchase_return': 'BILL',
    'payment': 'CPV',
    'transfer': 'BT',
    'journal': 'JV',
    'opening': 'OB',
}


class SbVoucher(models.Model):
    _name = 'sb.voucher'
    _description = 'Voucher'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    voucher_type = fields.Selection(VOUCHER_TYPES, required=True, readonly=True, index=True)
    name = fields.Char('Number', default='New', copy=False, readonly=True, index=True,
                       help='Assigned from the journal sequence when the voucher is posted.')
    state = fields.Selection([('draft', 'Not posted'), ('posted', 'Posted'), ('void', 'Void')],
                             default='draft', required=True, copy=False, tracking=True, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    doc_currency_id = fields.Many2one(
        'res.currency', string='Invoice Currency', default=lambda s: s.env.company.currency_id,
        help='Invoices and bills can be issued in a foreign currency (e.g. USD). Receipts and payments '
             'settle them at the day\'s rate; Odoo books the realised exchange difference.')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_ec = fields.Char('Ethiopian Date', compute='_compute_date_ec')
    due_date = fields.Date()
    partner_id = fields.Many2one('res.partner', string='Customer / Vendor', tracking=True, check_company=True)
    payer_type = fields.Selection(PAYER_TYPES, string='Received from / Paid to')
    purpose_id = fields.Many2one(
        'sb.voucher.purpose', string='Purpose', check_company=True,
        domain="[('voucher_type', '=', voucher_type)]",
        help='Pre-fills a GL line (e.g. capital injection -> Share capital). The account stays editable.')
    origin_id = fields.Many2one(
        'sb.voucher', string='Against', check_company=True,
        domain="[('state', '=', 'posted'), ('partner_id', '=', partner_id), "
               "('voucher_type', 'in', ('sale', 'cash_sale') if voucher_type == 'sale_return' else ('bill', 'cash_purchase'))]",
        help='Original invoice / cash sale (or bill / cash purchase) being returned.')
    reference = fields.Char('Reference', help='Customer PO, vendor invoice no., cheque no. ...')
    memo = fields.Char('Description')
    received_amount = fields.Monetary(
        'Amount', help='Receipt / payment: the cash actually received or paid. Anything not applied to invoices '
                       'or GL lines is kept as an advance for this customer / vendor.\n'
                       'Bank transfer (single destination): the amount transferred.')
    cash_account_id = fields.Many2one(
        'account.account', string='Cash Account', check_company=True,
        domain="[('account_type', '=', 'asset_cash')]",
        default=lambda s: s._default_cash_account(),
        help='Bank transfer: the account the money leaves. Other vouchers: default account of the first deposit row.')
    cash_account_to_id = fields.Many2one(
        'account.account', string='Transfer to', check_company=True,
        domain="[('account_type', '=', 'asset_cash')]")
    payment_method = fields.Selection([('cash', 'Cash'), ('cheque', 'Cheque'), ('transfer', 'Bank transfer'),
                                       ('mobile', 'Mobile money')],
                                      default='transfer')
    pay_now = fields.Boolean('Paid now')

    sb_series_id = fields.Many2one('sb.doc.series', 'Series / pad', check_company=True, copy=False,
                                   domain="[('doc_type', '=', voucher_type), ('active', '=', True)]",
                                   help='Which pad or cash register numbers this voucher. Empty: the default series.')
    sb_migrated = fields.Boolean('Brought over', copy=False, readonly=True, index=True,
                                 help='A document brought over from the previous system with its journal entry as it was. '
                                      'It can be voided, not edited (its lines are a summary of the entry).')
    sb_pad_number = fields.Char('Pad number', copy=False,
                                help='The number printed on the pad or cash register receipt. It must be the next number of '
                                     'the series; leave it empty to take the next number automatically.')
    sb_fiscal_only = fields.Boolean(compute='_compute_sb_fiscal_only',
                                    help='Ethiopia: the sales invoice is the fiscal register receipt or the Ministry e-invoice; '
                                         'its number is this document\'s number.')
    line_ids = fields.One2many('sb.voucher.line', 'voucher_id', string='Lines', copy=True)
    apply_ids = fields.One2many('sb.voucher.apply', 'voucher_id', string='Apply to Invoices')
    cash_line_ids = fields.One2many('sb.voucher.cash', 'voucher_id', string='Deposits / Payments', copy=True)

    amount_incl_vat = fields.Boolean('Amounts include VAT',
                                     help='Receipt / payment: the line amounts are totals with VAT; the VAT is split out of them.')
    wht_supply = fields.Selection([('goods', 'Goods'), ('service', 'Services'), ('professional', 'Professional fees'),
                                   ('government', 'Government (taxes, duties, fees)')],
                                  'Goods or services',
                                  help='Withholding starts above the goods or the services threshold, before VAT (Ethiopia: '
                                       '20,000 / 10,000; Uganda: 1,000,000). Uganda: professional fees from the first shilling.')
    sb_wht_country = fields.Char(compute='_compute_sb_wht_country')
    wht_rate = fields.Float('Withholding Rate (%)', compute='_compute_wht_rule')
    wht_rule = fields.Char('Withholding rule', compute='_compute_wht_rule')
    withholding = fields.Boolean('Withholding', help='Receipt: customer withheld tax. Payment: we withhold from the vendor.')
    wht_base = fields.Monetary('Withholding Base', compute='_compute_wht', store=True, readonly=False)
    wht_amount = fields.Monetary('Withholding Amount', compute='_compute_wht', store=True, readonly=False)
    wht_certificate_no = fields.Char('Withholding Receipt No.')
    wht_certificate_date = fields.Date('Withholding Receipt Date')

    amount_currency_id = fields.Many2one('res.currency', compute='_compute_amount_currency', string='Amounts in',
                                         help='Invoices and bills: the invoice currency; receipts, payments and journals: the company currency.')
    amount_untaxed = fields.Monetary(compute='_compute_amounts', store=True, currency_field='amount_currency_id')
    amount_tax = fields.Monetary(compute='_compute_amounts', store=True, currency_field='amount_currency_id')
    amount_total = fields.Monetary('Total', compute='_compute_amounts', store=True, currency_field='amount_currency_id')
    amount_applied = fields.Monetary(compute='_compute_amounts', store=True)
    amount_credits = fields.Monetary('Advances / credits used', compute='_compute_amounts', store=True)
    amount_discount = fields.Monetary('Discounts / write-offs', compute='_compute_amounts', store=True)
    amount_unapplied = fields.Monetary('Unapplied (advance)', compute='_compute_amounts', store=True)
    amount_cash = fields.Monetary('Cash Amount', compute='_compute_amounts', store=True)
    amount_allocated = fields.Monetary('Allocated to accounts', compute='_compute_amounts', store=True)
    amount_to_allocate = fields.Monetary('Remaining to allocate', compute='_compute_amounts', store=True)
    total_debit = fields.Monetary(compute='_compute_amounts', store=True)
    total_credit = fields.Monetary(compute='_compute_amounts', store=True)

    move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True, copy=False, index=True)
    linked_voucher_id = fields.Many2one('sb.voucher', string='Linked Voucher', readonly=True, copy=False)
    payment_state = fields.Selection([
        ('not_paid', 'Not Paid'), ('partial', 'Partially Paid'), ('in_payment', 'In Payment'), ('paid', 'Paid'),
        ('reversed', 'Reversed'), ('blocked', 'Blocked'), ('invoicing_legacy', 'Invoicing App Legacy')],
        string='Payment Status', compute='_compute_payment_state', store=True,
        help='Invoices and bills: whether they have been settled. Cash sales, cash purchases, receipts and payments '
             'are money already received or paid, so they show as Paid.')
    amount_residual = fields.Monetary(compute='_compute_payment_state', store=True, string='Balance Due')

    @api.depends('voucher_type', 'state', 'move_id.payment_state', 'move_id.amount_residual')
    def _compute_payment_state(self):
        for v in self:
            if v.state != 'posted':
                v.payment_state = False
                v.amount_residual = 0.0
            elif v.voucher_type in INVOICE_TYPES:
                v.payment_state = v.move_id.payment_state
                v.amount_residual = v.move_id.amount_residual
            elif v.voucher_type in SETTLEMENT_TYPES:
                v.payment_state = 'paid'  # the voucher is the money itself
                v.amount_residual = 0.0
            else:
                v.payment_state = False
                v.amount_residual = 0.0

    # ------------------------------------------------------------------ defaults / computes
    def _default_cash_account(self):
        journal = self.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1)
        return journal.default_account_id

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        vtype = res.get('voucher_type') or self.env.context.get('default_voucher_type')
        if 'cash_line_ids' in fields_list and vtype in SETTLEMENT_TYPES and not res.get('cash_line_ids'):
            account = self._default_cash_account()
            if account:
                res['cash_line_ids'] = [Command.create({'account_id': account.id})]
        return res

    @api.depends('date')
    def _compute_date_ec(self):
        for v in self:
            v.date_ec = format_ethiopian(v.date)

    @api.depends('voucher_type', 'doc_currency_id', 'currency_id')
    def _compute_amount_currency(self):
        for v in self:
            v.amount_currency_id = (v.doc_currency_id or v.currency_id) if v.voucher_type in ITEM_TYPES else v.currency_id

    @api.depends('voucher_type', 'line_ids.amount', 'line_ids.price_subtotal', 'line_ids.price_total',
                 'line_ids.debit', 'line_ids.credit', 'apply_ids.amount_applied', 'apply_ids.amount_applied_company',
                 'apply_ids.discount', 'apply_ids.direction', 'wht_amount', 'withholding', 'received_amount',
                 'cash_line_ids.amount', 'doc_currency_id', 'date', 'line_ids.tax_ids', 'amount_incl_vat')
    def _compute_amounts(self):
        for v in self:
            untaxed = tax = applied = credits = discount = unapplied = cash = 0.0
            debit = sum(v.line_ids.mapped('debit'))
            credit = sum(v.line_ids.mapped('credit'))
            allocated = sum(v.cash_line_ids.mapped('amount'))
            vtype = v.voucher_type
            if vtype in ITEM_TYPES:
                untaxed = sum(v.line_ids.mapped('price_subtotal'))
                total = sum(v.line_ids.mapped('price_total'))
                tax = total - untaxed
                if vtype in ('cash_sale', 'cash_purchase'):
                    cash = total - (v.wht_amount if v.withholding else 0.0)
            elif vtype in ('receipt', 'payment'):
                primary = 'debit' if vtype == 'receipt' else 'credit'
                for ap in v.apply_ids:
                    if ap.direction == primary:
                        applied += ap.amount_applied_company
                    else:
                        credits += ap.amount_applied_company
                    discount += ap.discount
                untaxed, tax = v.line_ids._sb_split_tax()
                total = applied - credits + untaxed + tax
                cash = total - (v.wht_amount if v.withholding else 0.0)
                if v.received_amount:
                    unapplied = v.received_amount - cash
                    cash = v.received_amount
            elif vtype == 'transfer':
                untaxed = sum(v.line_ids.mapped('amount'))
                cash = allocated if v.cash_line_ids else v.received_amount
                total = cash + untaxed
            else:
                total = debit
            v.amount_untaxed = untaxed
            v.amount_tax = tax
            v.amount_total = total
            v.amount_applied = applied
            v.amount_credits = credits
            v.amount_discount = discount
            v.amount_unapplied = unapplied
            v.amount_cash = cash
            v.amount_allocated = allocated
            v.amount_to_allocate = cash - allocated if vtype in SETTLEMENT_TYPES else 0.0
            v.total_debit = debit
            v.total_credit = credit

    @api.depends('company_id')
    def _compute_sb_wht_country(self):
        """Countries whose withholding on supplier payments is worked out by rule: ET, UG."""
        for v in self:
            co = v.company_id
            code = (co.account_fiscal_country_id or co.country_id).code
            v.sb_wht_country = code if code in ('ET', 'UG') else False

    def _sb_wht_base_amount(self):
        """What withholding is worked on: the amount before VAT (lines, and the share of the invoices paid)."""
        self.ensure_one()
        if self.voucher_type in ('cash_sale', 'cash_purchase'):
            base = sum(self.line_ids.mapped('price_subtotal'))
        else:
            # only lines that buy something: paying a tax, pension, salaries, a dividend or a loan settles a liability
            base = self.line_ids.filtered(lambda l: l.account_id.internal_group not in ('liability', 'equity')
                                          and l.account_id.account_type not in ('asset_receivable', 'asset_cash'))._sb_split_tax()[0]
            primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
            for ap in self.apply_ids.filtered(lambda a: a.direction == primary and a.invoice_id.is_invoice()):
                inv = ap.invoice_id
                if inv.amount_total:
                    base += ap.amount_applied_company * inv.amount_untaxed / inv.amount_total
        return self.currency_id.round(base)

    def _sb_wht_rule(self):
        """(rate %, threshold, rule) for paying a supplier. Elsewhere, and on receipts: the company rate.
        Ethiopia (Income Tax Proclamation 1395/2025): a company with a TIN and a trade licence 3% above 20,000 for goods
        or 10,000 for services; an individual (TIN or not) or a supplier without a TIN or licence 30%.
        Uganda (Income Tax Act s. 119, URA): 6% of the payment above UGX 1,000,000 when the company is a designated
        withholding agent; professional fees 6% from the first shilling; none for a supplier on the URA exemption list
        (certificate valid on the payment date)."""
        self.ensure_one()
        co = self.company_id
        country = self.sb_wht_country
        if not country or self.voucher_type not in ('payment', 'cash_purchase'):
            return co.sb_wht_rate, 0.0, ''
        if self.wht_supply == 'government':
            # taxes, duties, licence and registration fees paid to a government office are not withheld on
            return 0.0, 0.0, self.env._('government: taxes, duties and fees are not withheld')
        p = self.partner_id.commercial_partner_id
        cur = self.currency_id
        fmt = lambda amount: cur.format(amount) if cur else amount  # noqa: E731
        if country == 'UG':
            if p.sb_wht_exempt_until and self.date and self.date <= p.sb_wht_exempt_until:
                return 0.0, 0.0, self.env._('exempt: URA certificate valid to %s', p.sb_wht_exempt_until)
            if self.wht_supply == 'professional':
                return co.sb_wht_rate, 0.0, self.env._('%g%%: professional fees', co.sb_wht_rate)
            if not co.sb_wht_agent:
                return 0.0, 0.0, self.env._('not a designated withholding agent (accounting settings)')
            threshold = co.sb_wht_goods_threshold if self.wht_supply == 'goods' else co.sb_wht_service_threshold
            return co.sb_wht_rate, threshold, self.env._('%(rate)g%%: goods and services over %(threshold)s',
                                                         rate=co.sb_wht_rate, threshold=fmt(threshold))
        if self.wht_supply == 'goods':
            threshold, what = co.sb_wht_goods_threshold, self.env._('goods')
        else:
            threshold, what = co.sb_wht_service_threshold, self.env._('services')
        if p.is_company and p.vat and p.sb_trade_license:
            rate, who = co.sb_wht_rate, self.env._('licensed company')
        else:
            rate, who = co.sb_wht_individual_rate, self.env._('individual, or no TIN / trade licence')
        return rate, threshold, self.env._('%(rate)g%%: %(who)s, %(what)s over %(threshold)s',
                                           rate=rate, who=who, what=what, threshold=fmt(threshold))

    @api.depends('voucher_type', 'wht_supply', 'date', 'partner_id.vat', 'partner_id.is_company', 'partner_id.sb_trade_license',
                 'partner_id.sb_wht_exempt_until')
    def _compute_wht_rule(self):
        for v in self:
            rate, _threshold, rule = v._sb_wht_rule()
            v.wht_rate, v.wht_rule = rate, rule or False

    @api.depends('withholding', 'apply_ids.amount_applied_company', 'line_ids.amount', 'line_ids.price_subtotal',
                 'line_ids.tax_ids', 'voucher_type', 'amount_incl_vat', 'wht_supply', 'date', 'partner_id.vat',
                 'partner_id.is_company', 'partner_id.sb_trade_license', 'partner_id.sb_wht_exempt_until')
    def _compute_wht(self):
        for v in self:
            if v.state not in ('draft', False) and v._origin:
                # posted: the amount is in the ledger; a later change to the supplier's TIN or licence leaves it alone
                v.wht_base, v.wht_amount = v._origin.wht_base, v._origin.wht_amount
                continue
            if not v.withholding or v.voucher_type not in SETTLEMENT_TYPES:
                v.wht_base = v.wht_amount = 0.0
                continue
            base = v._sb_wht_base_amount()
            v.wht_base = base
            v.wht_amount = v.currency_id.round(base * v._sb_wht_rule()[0] / 100.0)

    sb_wht_none = fields.Boolean(compute='_compute_sb_wht_none',
                                 help='Paying taxes, pension, salaries, a dividend or a loan: nothing bought, nothing withheld.')

    @api.depends('voucher_type', 'line_ids.account_id', 'line_ids.amount', 'apply_ids.amount_applied', 'apply_ids.line_id')
    def _compute_sb_wht_none(self):
        """A payment that buys nothing (its lines settle liabilities: taxes, pension, salaries, dividends, loans; no bill
        applied): no withholding, and nothing about it is asked."""
        for v in self:
            if v.voucher_type != 'payment':
                v.sb_wht_none = False
                continue
            supply = v.line_ids.filtered(lambda l: l.account_id and l.account_id.internal_group not in ('liability', 'equity')
                                         and l.account_id.account_type not in ('asset_receivable', 'asset_cash'))
            bills = v.apply_ids.filtered(lambda a: a.amount_applied and a.invoice_id.is_invoice())
            v.sb_wht_none = bool(v.line_ids or v.apply_ids) and not supply and not bills

    # ------------------------------------------------------------------ onchange
    @api.onchange('line_ids', 'apply_ids', 'received_amount', 'withholding', 'wht_amount', 'partner_id', 'wht_supply',
                  'amount_incl_vat', 'date')
    def _onchange_sync_single_deposit(self):
        """One deposit row (the usual case) follows the voucher total, so single-bank entry stays one field.
        Ethiopia / Uganda, paying a supplier: withholding ticks itself when the rule withholds and the amount passes the
        goods / services threshold."""
        if self.state == 'draft' and self.sb_wht_none:
            # paying taxes, pension, salaries, a dividend or a loan: nothing is withheld, nothing asked
            self.withholding, self.wht_supply = False, False
        elif self.sb_wht_country and self.voucher_type in ('payment', 'cash_purchase') and self.state == 'draft' and self.wht_supply:
            rate, threshold, _rule = self._sb_wht_rule()
            self.withholding = bool(rate) and self._sb_wht_base_amount() > threshold
        if self.voucher_type in SETTLEMENT_TYPES and len(self.cash_line_ids) == 1:
            row = self.cash_line_ids
            if row.row_currency_id == self.currency_id:
                row.amount_currency = self.amount_cash
            elif row.row_currency_id and row.rate:
                # a foreign-currency account (e.g. the USD account): the same value in its currency at the row's rate
                row.amount_currency = row.row_currency_id.round(self.amount_cash / row.rate)

    @api.onchange('partner_id')
    def _onchange_partner_load_open_items(self):
        if self.voucher_type not in ('receipt', 'payment') or self.state != 'draft':
            return
        commands = [Command.clear()]
        if self.partner_id:
            commands += [Command.create(vals) for vals in self._open_items_vals()]
            if not self.payer_type:
                self.payer_type = 'customer' if self.voucher_type == 'receipt' else 'vendor'
        self.apply_ids = commands

    def _open_items_vals(self):
        """Every open receivable / payable / advance item of the partner: invoices and bills first,
        then advances, credit notes, debit notes, staff advances, opening balances ..."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        company = self.company_id
        advance_accounts = company.sb_customer_advance_account_id | company.sb_vendor_advance_account_id
        lines = self.env['account.move.line'].search([
            ('company_id', '=', company.id),
            ('partner_id', '=', partner.id),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('amount_residual', '!=', 0),
            ('move_id', '!=', self.move_id.id or False),
            '|', ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
            '|', ('account_id', 'in', advance_accounts.ids), ('account_id.sb_partner_account', '=', True),
        ], order='date_maturity, date, id')
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        lines = lines.sorted(lambda l: (
            0 if (l.amount_residual > 0) == (primary == 'debit') else 1, l.date_maturity or l.date, l.id))
        return [{'line_id': line.id} for line in lines]

    @api.onchange('purpose_id')
    def _onchange_purpose(self):
        purpose = self.purpose_id
        if not purpose or self.state != 'draft':
            return
        if purpose.payer_type:
            self.payer_type = purpose.payer_type
        if purpose.account_id and purpose.account_id not in self.line_ids.account_id:
            remaining = 0.0
            if self.received_amount:
                remaining = max(self.received_amount - self.amount_cash + self.amount_unapplied, 0.0)
            self.line_ids = [Command.create({
                'account_id': purpose.account_id.id,
                'name': purpose.name,
                'partner_id': self.partner_id.commercial_partner_id.id or False,
                'amount': remaining,
            })]
            self._onchange_sync_single_deposit()

    @api.onchange('origin_id')
    def _onchange_origin(self):
        """Returns: copy the original document's lines (quantities can be reduced)."""
        if not self.origin_id or self.voucher_type not in ('sale_return', 'purchase_return') or self.line_ids:
            return
        self.line_ids = [Command.create({
            'product_id': l.product_id.id, 'name': l.name, 'account_id': l.account_id.id,
            'quantity': l.quantity, 'price_unit': l.price_unit, 'tax_ids': [Command.set(l.tax_ids.ids)],
        }) for l in self.origin_id.line_ids]

    # ------------------------------------------------------------------ posting helpers
    def _journal(self):
        self.ensure_one()
        code = VOUCHER_JOURNALS[self.voucher_type]
        journal = self.env['account.journal'].search(
            [('code', '=', code), ('company_id', '=', self.company_id.id)], limit=1)
        if not journal:
            raise UserError(self.env._('Journal %s is missing. Re-run the Accounting setup.', code))
        return journal

    def _prepare_invoice_vals(self):
        self.ensure_one()
        lines = []
        for line in self.line_ids:
            vals = {
                'name': line.name or (line.product_id.display_name if line.product_id else '/'),
                'quantity': line.quantity,
                'price_unit': line.price_unit,
                'tax_ids': [Command.set(line.tax_ids.ids)],
            }
            if line.product_id:
                vals['product_id'] = line.product_id.id
            if line.account_id:
                vals['account_id'] = line.account_id.id
            lines.append(Command.create(vals))
        origin_move = self.origin_id.move_id
        term = False
        if not self.due_date and self.partner_id and self.voucher_type in ('sale', 'bill'):
            p = self.partner_id.commercial_partner_id.with_company(self.company_id)
            term = p.property_payment_term_id if self.voucher_type == 'sale' else p.property_supplier_payment_term_id
        return {
            'move_type': MOVE_TYPES[self.voucher_type],
            'journal_id': self._journal().id,
            'partner_id': self.partner_id.id,
            'currency_id': (self.doc_currency_id or self.currency_id).id,
            'invoice_date': self.date,
            'date': self.date,
            # the due date typed on the voucher, else the customer's / vendor's terms, else the invoice date
            **({'invoice_date_due': self.due_date} if self.due_date else {'invoice_payment_term_id': term.id} if term else {'invoice_date_due': self.date}),
            'ref': ' '.join(filter(None, [self.reference, origin_move.name and self.env._('Return of %s', origin_move.name)])) or False,
            'narration': self.memo,
            'reversed_entry_id': origin_move.id if origin_move.is_invoice() else False,
            'invoice_line_ids': lines,
        }

    def _deposit_rows(self):
        """Deposit / payment rows; a voucher saved without rows uses its cash account for the whole amount."""
        self.ensure_one()
        rows = self.cash_line_ids
        if len(rows) == 1 and rows.row_currency_id == self.currency_id:
            rows.amount_currency = self.amount_cash
        if not rows and self.voucher_type in SETTLEMENT_TYPES:
            if not self.cash_account_id:
                raise UserError(self.env._('Choose the bank or cash account.'))
            rows = self.env['sb.voucher.cash'].create({
                'voucher_id': self.id, 'account_id': self.cash_account_id.id, 'amount_currency': self.amount_cash})
        return rows

    def _prepare_entry_lines(self):
        """Balanced move lines for every non-invoice voucher."""
        self.ensure_one()
        cur = self.currency_id
        lines = []

        def add(account, debit, credit, name, partner=False, extra=None):
            if debit < 0 or credit < 0:
                debit, credit = max(debit, 0.0) - min(credit, 0.0), max(credit, 0.0) - min(debit, 0.0)
            if float_is_zero(debit, precision_rounding=cur.rounding) and \
                    float_is_zero(credit, precision_rounding=cur.rounding):
                return
            vals = {
                'account_id': account.id,
                'partner_id': partner.id if partner else False,
                'name': name or self.memo or self.name,
                'debit': cur.round(debit),
                'credit': cur.round(credit),
            }
            vals.update(extra or {})
            lines.append(Command.create(vals))

        if self.voucher_type in ('journal', 'opening'):
            for line in self.line_ids:
                extra = {'date_maturity': line.due_date} if line.due_date else {}
                add(line.account_id, line.debit, line.credit, line.name, line.partner_id, extra)
            if self.voucher_type == 'opening':
                diff = sum(self.line_ids.mapped('debit')) - sum(self.line_ids.mapped('credit'))
                if not float_is_zero(diff, precision_rounding=cur.rounding):
                    add(self.company_id.sb_opening_equity_account_id, max(-diff, 0.0), max(diff, 0.0),
                        self.env._('Out of balance: opening balance not complete'))
            return lines

        partner = self.partner_id.commercial_partner_id
        name_default = self.memo or self.reference

        def add_rows(direction):
            """direction +1: money arrives in the row's account (debit), -1: leaves it (credit)."""
            for row in self._deposit_rows():
                account = row._posting_account(direction)
                gross_cur, charges_cur = row.amount_currency, row.charges
                net_cur = gross_cur - charges_cur if direction > 0 else gross_cur + charges_cur
                rate = row.rate if row.row_currency_id != cur else 1.0
                extra = {}
                if row.row_currency_id != cur:
                    extra = {'currency_id': row.row_currency_id.id, 'amount_currency': direction * net_cur}
                label = row._label() or name_default
                add(account, direction * net_cur * rate if direction > 0 else 0.0,
                    -direction * net_cur * rate if direction < 0 else 0.0, label, partner, extra)
                if charges_cur:
                    add(self.company_id.sb_bank_charges_account_id, charges_cur * rate, 0.0,
                        self.env._('Bank charges %s', label or ''), partner)

        if self.voucher_type == 'transfer':
            name = self.memo or self.env._('Transfer %s', self.reference or '')
            fees = sum(self.line_ids.mapped('amount'))
            if self.cash_line_ids:
                add_rows(+1)
                amount = self.amount_allocated
            else:
                amount = self.received_amount
                add(self.cash_account_to_id, amount, 0.0, name)
            add(self.cash_account_id, 0.0, amount + fees, name)
            for line in self.line_ids:
                add(line.account_id, line.amount, 0.0, line.name or name, line.partner_id)
            return lines

        sign = 1 if self.voucher_type in MONEY_IN else -1
        add_rows(sign)
        wht = self.wht_amount if self.withholding else 0.0
        if wht:
            wht_account = (self.company_id.sb_wht_receivable_account_id if sign > 0
                           else self.company_id.sb_wht_payable_account_id)
            if not wht_account:
                raise UserError(self.env._('Set the withholding accounts in Accounting settings.'))
            add(wht_account, wht if sign > 0 else 0.0, wht if sign < 0 else 0.0,
                self.env._('Withholding %s', self.wht_certificate_no or self.reference or ''), partner)
        if self.voucher_type in ('cash_sale', 'cash_purchase'):
            # revenue / expense lines carry the taxes; Odoo adds the VAT lines with their tax tags
            for line in self.line_ids:
                amount = line.price_subtotal
                lines.append(Command.create({
                    'account_id': line.account_id.id,
                    'partner_id': partner.id or False,
                    'product_id': line.product_id.id or False,
                    'quantity': line.quantity,
                    'name': line.name or line.product_id.display_name or self.memo or '/',
                    'debit': cur.round(amount) if sign < 0 else 0.0,
                    'credit': cur.round(amount) if sign > 0 else 0.0,
                    'tax_ids': [Command.set(line.tax_ids.ids)],
                }))
            return lines
        # open items settled: each line is the mirror of the item's residual
        for ap in self.apply_ids.filtered(lambda a: a.amount_applied or a.discount):
            open_line = ap.line_id
            item_sign = 1 if open_line.amount_residual > 0 else -1  # debit balance being cleared -> credit it
            company_amount = ap.amount_applied_company + ap.discount
            extra = {}
            if ap.item_currency_id != cur:
                extra = {'currency_id': ap.item_currency_id.id,
                         'amount_currency': -item_sign * (ap.amount_applied + ap.discount_currency)}
            add(open_line.account_id, company_amount if item_sign < 0 else 0.0,
                company_amount if item_sign > 0 else 0.0, ap._match_label(), open_line.partner_id, extra)
            if ap.discount:
                disc_account = (self.company_id.sb_discount_allowed_account_id if item_sign > 0
                                else self.company_id.sb_discount_received_account_id)
                add(disc_account, ap.discount if item_sign > 0 else 0.0, ap.discount if item_sign < 0 else 0.0,
                    self.env._('Discount / write-off %s', ap.document or ''), open_line.partner_id)
        for line in self.line_ids:
            if line.tax_ids:
                # the amount before VAT, with its taxes: Odoo adds the VAT lines with their tax tags, as on an invoice
                net = line._sb_split_tax()[0]
                add(line.account_id, net if sign < 0 else 0.0, net if sign > 0 else 0.0, line.name, line.partner_id or partner,
                    {'tax_ids': [Command.set(line.tax_ids.ids)]})
                continue
            add(line.account_id, line.amount if sign < 0 else 0.0, line.amount if sign > 0 else 0.0,
                line.name, line.partner_id or partner)
        if self.amount_unapplied > 0:
            advance = (self.company_id.sb_customer_advance_account_id if sign > 0
                       else self.company_id.sb_vendor_advance_account_id)
            add(advance, self.amount_unapplied if sign < 0 else 0.0, self.amount_unapplied if sign > 0 else 0.0,
                self.env._('Advance %s', self.reference or ''), partner)
        return lines

    def _check_before_post(self):
        self.ensure_one()
        cur = self.currency_id
        vtype = self.voucher_type
        lock = self.company_id._get_user_fiscal_lock_date(self._journal())
        if self.date <= lock:
            raise ValidationError(self.env._(
                'The books are closed through %(lock)s (%(ec)s). Change the date or reopen the period.',
                lock=lock, ec=format_ethiopian(lock)))
        if vtype in INVOICE_TYPES and not self.partner_id:
            raise ValidationError(self.env._('Choose a customer or vendor.'))
        multi_payee = bool(getattr(self, 'payroll_run_id', False))  # a salary payment settles several employees at once
        if vtype in ('receipt', 'payment') and not self.partner_id and not multi_payee and self.apply_ids.filtered('amount_applied'):
            raise ValidationError(self.env._('Choose a customer or vendor.'))
        if vtype in ('receipt', 'payment') and self.currency_id.is_zero(self.amount_total) \
                and not self.apply_ids.filtered('amount_applied') and not self.line_ids.filtered('amount'):
            raise ValidationError(self.env._('This %s is for nothing: enter the amount, or tick the items it pays.',
                                             'receipt' if vtype == 'receipt' else 'payment'))
        if vtype in ('receipt', 'payment') and self.currency_id.compare_amounts(self.amount_total, 0.0) < 0:
            raise ValidationError(self.env._(
                'This %s comes to %s: more advances / credits are applied than the invoices they settle. Lower the credits applied.',
                'receipt' if vtype == 'receipt' else 'payment', '{:,.2f}'.format(self.amount_total)))
        if vtype in ITEM_TYPES:
            if not self.line_ids:
                raise ValidationError(self.env._('Add at least one line.'))
            if vtype in ('cash_sale', 'cash_purchase') and any(not l.account_id for l in self.line_ids):
                raise ValidationError(self.env._('Every line needs a GL account (or an item that has one).'))
        for line in self.line_ids.filtered('account_id'):
            acc = line.account_id
            beginning = vtype == 'opening' and self.sb_ob_kind == 'account' \
                and acc.account_type not in ('asset_receivable', 'liability_payable')
            if (acc.account_type in ('asset_receivable', 'liability_payable') or acc.reconcile) \
                    and not (line.partner_id or self.partner_id) and vtype not in ITEM_TYPES and not beginning:
                raise ValidationError(self.env._(
                    '%(acc)s keeps a balance per person or company: choose who it is for.', acc=acc.display_name))
        if vtype in ('receipt', 'payment', 'transfer', 'journal', 'opening') and any(not l.account_id for l in self.line_ids):
            raise ValidationError(self.env._('Every line needs a GL account.'))
        if vtype in ('receipt', 'payment'):
            for ap in self.apply_ids:
                if float_compare(ap.amount_applied + ap.discount_currency, ap.amount_residual,
                                 precision_rounding=ap.item_currency_id.rounding or cur.rounding) > 0:
                    raise ValidationError(self.env._(
                        '%(inv)s: amount applied is more than the balance.', inv=ap.document))
            if float_compare(self.amount_unapplied, 0.0, precision_rounding=cur.rounding) < 0:
                raise ValidationError(self.env._(
                    'The amount (%(amt)s) is less than what is applied (%(app)s).',
                    amt=self.received_amount, app=self.received_amount - self.amount_unapplied))
            if self.amount_unapplied > 0 and not self.partner_id:
                raise ValidationError(self.env._('Choose who the unapplied advance belongs to, or apply the whole amount.'))
            if float_is_zero(self.amount_cash, precision_rounding=cur.rounding) \
                    and not self.apply_ids.filtered(lambda a: a.amount_applied or a.discount):
                raise ValidationError(self.env._('Enter an amount, apply invoices or add GL lines.'))
        if vtype in SETTLEMENT_TYPES:
            if float_compare(self.amount_cash, 0.0, precision_rounding=cur.rounding) < 0:
                raise ValidationError(self.env._('Advances and credits used are more than the items settled.'))
            rows = self.cash_line_ids
            if len(rows) > 1 or (rows and rows.row_currency_id != cur):
                if not float_is_zero(self.amount_allocated - self.amount_cash, precision_rounding=cur.rounding):
                    raise ValidationError(self.env._(
                        'The bank / cash rows add up to %(rows)s but the voucher needs %(cash)s. '
                        'Remaining to allocate: %(rest)s.',
                        rows=self.amount_allocated, cash=self.amount_cash, rest=self.amount_cash - self.amount_allocated))
            for row in rows:
                if not row.account_id:
                    raise ValidationError(self.env._('Every deposit / payment row needs a bank or cash account.'))
                if row.row_currency_id != cur and not row.rate:
                    raise ValidationError(self.env._('Enter the exchange rate for %s.', row.account_id.display_name))
            if any(r.method == 'cheque' for r in rows):
                company = self.company_id
                needed = company.sb_cheque_on_hand_account_id if vtype in MONEY_IN else company.sb_cheque_outstanding_account_id
                if not needed:
                    raise ValidationError(self.env._('Set the cheque accounts in Accounting settings.'))
            if any(r.charges for r in rows) and not self.company_id.sb_bank_charges_account_id:
                raise ValidationError(self.env._('Set the bank charges account in Accounting settings.'))
        if vtype == 'transfer':
            if not self.cash_account_id:
                raise ValidationError(self.env._('Choose the account the money leaves.'))
            dest = self.cash_line_ids.account_id or self.cash_account_to_id
            if not dest or self.cash_account_id in dest:
                raise ValidationError(self.env._('Choose destination accounts different from the source.'))
            if float_compare(self.amount_cash, 0.0, precision_rounding=cur.rounding) <= 0:
                raise ValidationError(self.env._('Enter the amount transferred.'))
        if vtype == 'journal':
            if not self.line_ids:
                raise ValidationError(self.env._('Add journal lines.'))
            if float_compare(self.total_debit, self.total_credit, precision_rounding=cur.rounding):
                raise ValidationError(self.env._(
                    'Out of balance: debits %(d)s, credits %(c)s.', d=self.total_debit, c=self.total_credit))
            if float_is_zero(self.total_debit, precision_rounding=cur.rounding):
                raise ValidationError(self.env._('The journal voucher is empty.'))
        if vtype == 'opening':
            if not self.line_ids:
                raise ValidationError(self.env._('Add opening balance lines.'))
            if not self.company_id.sb_opening_equity_account_id:
                raise ValidationError(self.env._('Set the opening balance equity account in settings.'))

    def _sb_require(self, group, what):
        if not self.env.su and not self.env.user.has_group('sonador_books.%s' % group):
            role = {'group_sb_clerk': 'Clerk', 'group_sb_accountant': 'Accountant', 'group_sb_manager': 'Manager'}[group]
            raise UserError(self.env._('Only users with the %(role)s role (or higher) can %(what)s.', role=role, what=what))

    @api.depends('voucher_type', 'company_id')
    def _compute_sb_fiscal_only(self):
        from .fiscal_print import SALE_VOUCHERS, fiscal_only
        for v in self:
            v.sb_fiscal_only = v.voucher_type in SALE_VOUCHERS and fiscal_only(v.company_id or self.env.company)

    def _sb_series_number(self):
        """Strict numbering: the first posting takes the next number of the voucher's series (pad).
        Ethiopia, sales side: the number is the fiscal register receipt or the Ministry e-invoice reference typed in."""
        self.ensure_one()
        if self.voucher_type not in SERIES_TYPES:
            return
        # a sale edited with another fiscal receipt number takes that number as its own (the receipt is its number)
        typed = (self.sb_pad_number or '').strip()
        renumber = self.sb_fiscal_only and typed and self.move_id.name not in (False, '/') and typed != self.move_id.name
        if self.move_id.name not in (False, '/') and not renumber:
            return
        if self.sb_fiscal_only:
            ref = (self.sb_pad_number or '').strip()
            if not ref:
                raise UserError(self.env._('Enter the fiscal receipt number (FS No.) or the Ministry e-invoice reference: '
                                           'it is the number of this %s.', dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)))
            if self.search_count([('id', '!=', self.id), ('company_id', '=', self.company_id.id), ('state', '=', 'posted'),
                                  ('voucher_type', '=', self.voucher_type), ('name', '=', ref)]):
                raise UserError(self.env._('%s is already recorded. Each fiscal receipt is entered once.', ref))
            self.move_id.name = ref
            return
        series = self.env['sb.doc.series']._for(self.voucher_type, company=self.company_id, series=self.sb_series_id)
        if not series:
            return
        label = dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)
        self.move_id.name = series._take(self.date, manual=self.sb_pad_number, what=label)
        self.sb_series_id = series

    def _sb_require_post(self):
        """Who may post this voucher; departmental apps (Sales) extend it."""
        self._sb_require('group_sb_clerk', self.env._('post vouchers'))

    def action_post(self):
        """Peachtree real-time posting: saving the voucher posts it to the ledger."""
        for v in self:
            if v.state != 'draft':
                continue
            if v.voucher_type in ('journal', 'opening'):
                v._sb_require('group_sb_accountant', self.env._('post journal vouchers and opening balances'))
            else:
                v._sb_require_post()
            v._check_before_post()
            if v.voucher_type in INVOICE_TYPES:
                vals = v._prepare_invoice_vals()
                if v.move_id:
                    vals['invoice_line_ids'] = [Command.clear()] + vals['invoice_line_ids']
                    v.move_id.write(vals)
                else:
                    v.move_id = self.env['account.move'].create(vals)
            else:
                vals = {
                    'move_type': 'entry',
                    'journal_id': v._journal().id,
                    'date': v.date,
                    'ref': ' '.join(filter(None, [v.reference, v.partner_id.name])) or False,
                    'narration': v.memo,
                    'partner_id': v.partner_id.commercial_partner_id.id or False,
                    'line_ids': v._prepare_entry_lines(),
                }
                if v.move_id:
                    # the whole entry is rebuilt, VAT lines included
                    vals['line_ids'] = [Command.clear()] + vals['line_ids']
                    v.move_id.with_context(dynamic_unlink=True).write(vals)
                else:
                    v.move_id = self.env['account.move'].create(vals)
                if v.amount_incl_vat:
                    v._sb_vat_rounding()
            v._sb_series_number()
            v.move_id.action_post()
            if not v.due_date and v.move_id.is_invoice() and v.move_id.invoice_date_due:
                v.due_date = v.move_id.invoice_date_due
            v.write({'state': 'posted', 'name': v.move_id.name})
            v.cash_line_ids.filtered(lambda r: r.method == 'cheque' and r.cheque_state in (False, 'none')) \
                .write({'cheque_state': 'open'})
            v._reconcile_applied()
            v._reconcile_return()
        return True

    def _sb_vat_rounding(self):
        """VAT split out of totals: Odoo computes the VAT on the amount before VAT, may land a cent off the total typed and
        parks the cent on an automatic balancing line (suspense). The cent goes on the largest VAT line instead."""
        move = self.move_id
        balancing = move.line_ids.filtered(lambda l: l.name == self.env._('Automatic Balancing Line'))
        tax_lines = move.line_ids.filtered('tax_repartition_line_id')
        if not balancing or not tax_lines or abs(sum(balancing.mapped('balance'))) > 0.05 * len(self.line_ids):
            return
        diff = sum(balancing.mapped('balance'))
        line = max(tax_lines, key=lambda l: abs(l.balance))
        move.with_context(check_move_validity=False, dynamic_unlink=True).write({'line_ids': [Command.unlink(b.id) for b in balancing] + [
            Command.update(line.id, {'balance': line.balance + diff, 'amount_currency': line.amount_currency + diff})]})

    def action_post_new(self):
        self.action_post()
        if self.env.context.get('sb_in_drawer'):
            return {'type': 'ir.actions.act_window_close'}
        label = dict(self._fields['voucher_type']._description_selection(self.env)).get(self[:1].voucher_type)
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.voucher_new',
                'params': {'voucher_type': self[:1].voucher_type, 'name': label}}

    def action_post_close(self):
        self.action_post()
        if self.env.context.get('sb_in_drawer'):
            return {'type': 'ir.actions.act_window_close'}
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.back'}

    def _reconcile_applied(self):
        self.ensure_one()
        if self.voucher_type not in ('receipt', 'payment'):
            return
        for ap in self.apply_ids.filtered(lambda a: a.amount_applied or a.discount):
            open_line = ap.line_id
            mine = self.move_id.line_ids.filtered(
                lambda l: l.account_id == open_line.account_id and not l.reconciled
                and l.name == ap._match_label())[:1]
            (open_line | mine).reconcile()

    def _reconcile_return(self):
        """A return against an open invoice / bill settles it straight away (remaining credit stays open)."""
        self.ensure_one()
        if self.voucher_type not in ('sale_return', 'purchase_return') or not self.origin_id.move_id.is_invoice():
            return
        types = ('asset_receivable', 'liability_payable')
        mine = self.move_id.line_ids.filtered(lambda l: l.account_id.account_type in types and not l.reconciled)
        theirs = self.origin_id.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type in types and not l.reconciled)
        for line in mine:
            other = theirs.filtered(lambda t: t.account_id == line.account_id)
            if other:
                (line | other).reconcile()

    # ------------------------------------------------------------------ edit / void
    def _check_advance_unused(self):
        """An advance created by this voucher must not already be applied elsewhere."""
        self.ensure_one()
        company = self.company_id
        advance_accounts = company.sb_customer_advance_account_id | company.sb_vendor_advance_account_id
        applied_labels = {ap._match_label() for ap in self.apply_ids}
        for line in self.move_id.line_ids:
            if line.account_id.account_type in ('asset_receivable', 'liability_payable') or line.account_id in advance_accounts \
                    or line.account_id.sb_partner_account:
                if line.name in applied_labels:
                    continue
                if line.matched_debit_ids or line.matched_credit_ids:
                    raise UserError(self.env._(
                        '%s has an advance or credit that is already applied on another voucher. '
                        'Edit or void that one first.', self.name))

    def _check_cheques_open(self):
        self.ensure_one()
        if self.cash_line_ids.filtered(lambda r: r.cheque_state in ('cleared', 'bounced')):
            raise UserError(self.env._(
                '%s has cheques that were already deposited, cleared or bounced. Undo those first.', self.name))

    def action_edit(self):
        """Reopen a posted voucher (Peachtree lets you edit; the number is kept)."""
        self._sb_require('group_sb_accountant', self.env._('edit posted vouchers'))
        if self.filtered('sb_migrated'):
            raise UserError(self.env._('%s was brought over from the previous system: void it and enter a new voucher.',
                                       ', '.join(self.filtered('sb_migrated').mapped('name'))))
        for v in self:
            if v.state != 'posted':
                continue
            if v.voucher_type in INVOICE_TYPES and v.move_id.payment_state not in ('not_paid', False):
                raise UserError(self.env._('%s has payments or credits applied. Edit those first.', v.name))
            v._check_cheques_open()
            if v.voucher_type not in INVOICE_TYPES:
                v._check_advance_unused()
                v.move_id.line_ids.filtered('reconciled').remove_move_reconcile()
            v.move_id.button_draft()
            v.cash_line_ids.filtered(lambda r: r.cheque_state == 'open').write({'cheque_state': 'none'})
            v.state = 'draft'
        return True

    def action_void(self):
        """Void = automatic reversing entry; the voucher stays on file, marked Void.
        In a closed period the reversal is dated today (first open date)."""
        self._sb_require('group_sb_accountant', self.env._('void posted vouchers'))
        for v in self:
            if v.state != 'posted':
                continue
            if v.voucher_type in INVOICE_TYPES and v.move_id.payment_state not in ('not_paid', False):
                raise UserError(self.env._('%s has payments or credits applied. Void those first.', v.name))
            v._check_cheques_open()
            lock = v.company_id._get_user_fiscal_lock_date(v.move_id.journal_id)
            void_date = v.date if v.date > lock else fields.Date.context_today(v)
            if v.voucher_type not in INVOICE_TYPES:
                v._check_advance_unused()
                v.move_id.line_ids.filtered('reconciled').remove_move_reconcile()
            v.move_id._sb_void(void_date, self.env._('Void of %s', v.name))
            v.cash_line_ids.filtered(lambda r: r.cheque_state == 'open').write({'cheque_state': 'none'})
            v.state = 'void'
            v.message_post(body=self.env._('Voided on %s', void_date))
        return True

    # ------------------------------------------------------------------ quick links (stat buttons)
    @api.model
    def _sb_open(self, records, title):
        """Open one record as a form, several as a list."""
        action = {'type': 'ir.actions.act_window', 'res_model': records._name, 'name': title, 'context': {'create': False}}
        if len(records) == 1:
            return dict(action, res_id=records.id, view_mode='form', views=[(False, 'form')])
        return dict(action, domain=[('id', 'in', records.ids)], view_mode='list,form', views=[(False, 'list'), (False, 'form')])

    sb_paid_by_count = fields.Integer(compute='_compute_sb_links', string='Payments')
    sb_paid_count = fields.Integer(compute='_compute_sb_links', string='Invoices paid')
    sb_return_count = fields.Integer(compute='_compute_sb_links', string='Returns')

    def _sb_paid_by(self):
        """Receipts / payments applied to this invoice or bill."""
        moves = self.move_id
        if not moves:
            return self.browse()
        return self.env['sb.voucher.apply'].search([('invoice_id', 'in', moves.ids), ('amount_applied', '>', 0)]).voucher_id \
            .filtered(lambda v: v.state == 'posted')

    def _sb_paid(self):
        """Invoices / bills this receipt or payment paid."""
        moves = self.apply_ids.filtered(lambda a: a.amount_applied > 0).invoice_id
        return self.search([('move_id', 'in', moves.ids)]) if moves else self.browse()

    def _compute_sb_links(self):
        for v in self:
            v.sb_paid_by_count = len(v._sb_paid_by()) if v.voucher_type in ('sale', 'bill') and v.state == 'posted' else 0
            v.sb_paid_count = len(v._sb_paid()) if v.voucher_type in ('receipt', 'payment') else 0
            v.sb_return_count = self.search_count([('origin_id', '=', v.id)]) if v.id and v.voucher_type in (
                'sale', 'cash_sale', 'bill', 'cash_purchase') else 0

    def action_sb_view_paid_by(self):
        return self._sb_open(self._sb_paid_by(), self.env._('Payments'))

    def action_sb_view_paid(self):
        return self._sb_open(self._sb_paid(), self.env._('Invoices paid'))

    def action_sb_view_returns(self):
        return self._sb_open(self.search([('origin_id', '=', self.id)]), self.env._('Returns'))

    def action_sb_view_origin(self):
        return self._sb_open(self.origin_id, self.origin_id.name)

    def action_view_move(self):
        """Peachtree's 'Accounting Behind the Screens'."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'name': self.env._('Behind the Screens'),
        }

    def action_new_return(self):
        """Start a sales / purchase return against this document."""
        self.ensure_one()
        rtype = 'sale_return' if self.voucher_type in ('sale', 'cash_sale') else 'purchase_return'
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sb.voucher',
            'view_mode': 'form',
            'name': self.env._('Return'),
            'context': {'default_voucher_type': rtype, 'default_partner_id': self.partner_id.id,
                        'default_origin_id': self.id, 'default_doc_currency_id': self.doc_currency_id.id},
        }

    @api.autovacuum
    def _gc_empty_drafts(self):
        """Forms opened with New and left empty are autosaved by the web client; drop them."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        self.search([('state', '=', 'draft'), ('move_id', '=', False), ('partner_id', '=', False),
                     ('line_ids', '=', False), ('apply_ids', '=', False), ('received_amount', '=', 0),
                     ('create_date', '<', limit)]).unlink()

    def unlink(self):
        if any(v.state != 'draft' or v.move_id for v in self):
            raise UserError(self.env._('Posted vouchers cannot be deleted. Use Void.'))
        return super().unlink()

    # ------------------------------------------------------------------ navigation center data
    @api.model
    def sb_nav_data(self):
        from .ethiopian_calendar import fiscal_periods, fiscal_year_of, fy_label
        company = self.env.company
        aml = self.env['account.move.line']
        base = [('parent_state', '=', 'posted'), ('company_id', '=', company.id)]

        def total(domain, field='balance'):
            return aml._read_group(base + domain, [], [field + ':sum'])[0][0] or 0.0

        today = fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        period = next((p for p in fiscal_periods(fy) if p[2] <= today <= p[3]), None)
        drafts = self._read_group([('state', '=', 'draft')], ['voucher_type'], ['__count'])
        cheques = self.env['sb.voucher.cash'].search_count([('cheque_state', '=', 'open')])
        return {
            'company': company.name,
            'currency': company.currency_id.symbol or company.currency_id.name,
            'cash': total([('account_id.account_type', '=', 'asset_cash')]),
            'receivable': total([('account_id.account_type', '=', 'asset_receivable')], 'amount_residual'),
            'payable': -total([('account_id.account_type', '=', 'liability_payable')], 'amount_residual'),
            'today_ec': format_ethiopian(today),
            'fiscal_year': fy, 'fy_label': fy_label(fy),
            'period': '%d - %s' % (period[0], period[1]) if period else '',
            'lock_date': fields.Date.to_string(company.fiscalyear_lock_date) if company.fiscalyear_lock_date else '',
            'lock_date_ec': format_ethiopian(company.fiscalyear_lock_date),
            'drafts': {vt: count for vt, count in drafts},
            'cheques_open': cheques,
        }


class SbVoucherLine(models.Model):
    _name = 'sb.voucher.line'
    _description = 'Voucher Line'
    _order = 'sequence, id'
    _check_company_auto = True

    voucher_id = fields.Many2one('sb.voucher', required=True, ondelete='cascade', index=True)
    voucher_type = fields.Selection(related='voucher_id.voucher_type')
    company_id = fields.Many2one(related='voucher_id.company_id', store=True)
    currency_id = fields.Many2one(related='voucher_id.currency_id')
    doc_currency_id = fields.Many2one(related='voucher_id.amount_currency_id', string='Line currency')
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Item', check_company=True)
    name = fields.Char('Description')
    account_id = fields.Many2one('account.account', string='GL Account', check_company=True,
                                 domain="[('active', '=', True)]")
    partner_id = fields.Many2one('res.partner', string='Partner', check_company=True)
    due_date = fields.Date('Due Date', help='Opening balances: due date of the open invoice / bill.')
    quantity = fields.Float(default=1.0, digits='Product Unit')
    price_unit = fields.Float('Unit Price', digits='Product Price')
    tax_ids = fields.Many2many('account.tax', string='Taxes', check_company=True)
    price_subtotal = fields.Monetary(compute='_compute_price', store=True, currency_field='doc_currency_id')
    price_total = fields.Monetary(compute='_compute_price', store=True, currency_field='doc_currency_id')
    amount = fields.Monetary(help='Receipt / payment distribution amount')
    debit = fields.Monetary()
    credit = fields.Monetary()

    @api.depends('quantity', 'price_unit', 'tax_ids', 'voucher_id.doc_currency_id')
    def _compute_price(self):
        for line in self:
            res = line.tax_ids.compute_all(line.price_unit, line.doc_currency_id or line.currency_id, line.quantity,
                                           product=line.product_id, partner=line.voucher_id.partner_id)
            line.price_subtotal = res['total_excluded']
            line.price_total = res['total_included']

    def _sb_split_tax(self):
        """Receipt / payment lines: (amount before VAT, VAT) of the lines' amounts, which include the VAT when the voucher says
        so. A line with no tax is all amount."""
        untaxed = tax = 0.0
        for line in self:
            if not line.tax_ids:
                untaxed += line.amount
                continue
            if line.voucher_id.amount_incl_vat:
                res = line.tax_ids._get_tax_details(line.amount, 1.0, precision_rounding=line.currency_id.rounding,
                                                    special_mode='total_included')
            else:
                res = line.tax_ids.compute_all(line.amount, line.currency_id, 1.0, partner=line.voucher_id.partner_id)
            excluded = line.currency_id.round(res['total_excluded'])
            untaxed += excluded
            tax += line.currency_id.round(res['total_included']) - excluded
        return untaxed, tax

    @api.onchange('product_id')
    def _onchange_product(self):
        if not self.product_id:
            return
        product = self.product_id
        accounts = product.product_tmpl_id.get_product_accounts()
        fpos = self._sb_fiscal_position()
        # a new voucher's type may not reach the line's onchange (not in the form): the menu's default carries it
        vtype = self.voucher_type or self.voucher_id.voucher_type or self.env.context.get('default_voucher_type')
        if vtype in SALE_SIDE:
            self.price_unit = product.lst_price
            self.tax_ids = fpos.map_tax(product.taxes_id.filtered(lambda t: t.company_id == self.company_id))
            self.account_id = accounts.get('income')
        elif vtype in ('bill', 'cash_purchase', 'purchase_return'):
            self.price_unit = product.standard_price
            self.tax_ids = fpos.map_tax(product.supplier_taxes_id.filtered(lambda t: t.company_id == self.company_id))
            self.account_id = accounts.get('expense')
        self.name = product.display_name

    def _sb_fiscal_position(self):
        """The partner's tax rule (e.g. imports: no VAT on a foreign supplier's bill, the VAT is paid at customs)."""
        voucher = self.voucher_id
        partner = voucher.partner_id
        FP = self.env['account.fiscal.position']
        if not partner:
            return FP
        return FP.with_company(voucher.company_id or self.env.company)._get_fiscal_position(partner)

    @api.onchange('account_id')
    def _onchange_account_default_tax(self):
        """New item lines get the company's default VAT on screen (as they do when created in code)."""
        voucher = self.voucher_id
        if self._origin.id or self.product_id or self.tax_ids or voucher.voucher_type not in ITEM_TYPES:
            return
        company = voucher.company_id or self.env.company
        self.tax_ids = self._sb_fiscal_position().map_tax(company.account_sale_tax_id if voucher.voucher_type in SALE_SIDE else company.account_purchase_tax_id)

    @api.onchange('debit')
    def _onchange_debit(self):
        if self.debit:
            self.credit = 0.0

    @api.onchange('credit')
    def _onchange_credit(self):
        if self.credit:
            self.debit = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            voucher = self.env['sb.voucher'].browse(vals.get('voucher_id'))
            if voucher.voucher_type in ITEM_TYPES and 'tax_ids' not in vals and not vals.get('product_id'):
                tax = (voucher.company_id.account_sale_tax_id if voucher.voucher_type in SALE_SIDE
                       else voucher.company_id.account_purchase_tax_id)
                vals['tax_ids'] = [Command.set(tax.ids)]
        return super().create(vals_list)


class SbVoucherApply(models.Model):
    _name = 'sb.voucher.apply'
    _description = 'Voucher Open Item'
    _order = 'sequence, id'

    voucher_id = fields.Many2one('sb.voucher', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(related='voucher_id.currency_id')
    line_id = fields.Many2one('account.move.line', string='Open Item', required=True)
    invoice_id = fields.Many2one(related='line_id.move_id', string='Source Entry', store=True)
    item_currency_id = fields.Many2one('res.currency', compute='_compute_item', string='Item Currency', compute_sudo=True)
    direction = fields.Selection([('debit', 'They owe us'), ('credit', 'We owe them')],
                                 compute='_compute_direction', store=True)
    kind = fields.Selection([('invoice', 'Invoice / Bill'), ('credit', 'Advance / Credit')],
                            compute='_compute_direction', store=True)
    document = fields.Char(compute='_compute_item', string='Document', compute_sudo=True)
    item_type = fields.Char(compute='_compute_item', string='Type', compute_sudo=True)
    invoice_date = fields.Date(compute='_compute_item', string='Date', compute_sudo=True)
    invoice_date_due = fields.Date(related='line_id.date_maturity', string='Due Date')
    invoice_ref = fields.Char(compute='_compute_item', string='Their Ref.', compute_sudo=True)
    amount_total = fields.Monetary(compute='_compute_item', string='Original', currency_field='item_currency_id', compute_sudo=True)
    amount_residual = fields.Monetary(compute='_compute_residual', string='Balance', currency_field='item_currency_id')
    pay = fields.Boolean('Apply')
    amount_applied = fields.Monetary('Amount Applied', currency_field='item_currency_id')
    rate = fields.Float('Rate', digits=(12, 6), compute='_compute_rate', store=True, readonly=False,
                        help='Company currency per unit of the item currency, at the voucher date.')
    amount_applied_company = fields.Monetary('Applied (company currency)', compute='_compute_company_amount', store=True)
    discount = fields.Monetary('Discount / Write-off', help='Settlement discount or small difference written off.')
    discount_currency = fields.Monetary(compute='_compute_discount_currency', currency_field='item_currency_id')

    @api.model_create_multi
    def create(self, vals_list):
        # convenience: allow invoice_id instead of the open line
        for vals in vals_list:
            if not vals.get('line_id') and vals.get('invoice_id'):
                move = self.env['account.move'].browse(vals['invoice_id'])
                types = ('asset_receivable', 'liability_payable')
                lines = move.line_ids.filtered(lambda l: l.account_id.account_type in types)
                vals['line_id'] = (lines.filtered(lambda l: not l.reconciled)[:1] or lines[:1]).id
            if not vals.get('line_id') and vals.get('credit_line_id'):
                vals['line_id'] = vals['credit_line_id']
            vals.pop('credit_line_id', None)
            vals.pop('kind', None)
        return super().create(vals_list)

    @api.depends('line_id')
    def _compute_direction(self):
        for ap in self:
            ap.direction = 'debit' if ap.line_id.balance > 0 else 'credit'
            line = ap.line_id
            owed = (line.account_id.account_type == 'asset_receivable' and line.balance > 0) or \
                (line.account_id.account_type == 'liability_payable' and line.balance < 0)
            beginning = line.move_id.journal_id.code == 'OB' and owed
            ap.kind = 'invoice' if line.move_id.move_type in ('out_invoice', 'in_invoice') or beginning else 'credit'

    @api.depends('line_id')
    def _compute_item(self):
        for ap in self:
            line = ap.line_id
            move = line.move_id
            ap.item_currency_id = line.currency_id or ap.voucher_id.currency_id
            ap.document = move.name
            ap.item_type = dict(move._fields['move_type'].selection).get(move.move_type) \
                if move.is_invoice() else line.account_id.name
            ap.invoice_date = move.invoice_date or line.date
            ap.invoice_ref = move.ref or (line.name if line.name != move.name else '')
            if move.journal_id.code == 'OB' and line.name:
                # beginning balance item: show the invoice / bill / GRN number it was entered with
                ap.document = line.name
                ap.item_type = self.env._('Beginning balance')
                ap.invoice_ref = move.ref or ''
                # items kept as lines of the one opening voucher carry their own document date and reference
                vl = self.env['sb.voucher.line'].sudo().search([
                    ('voucher_id.move_id', '=', move.id), ('sb_ob_kind', '!=', False), ('account_id', '=', line.account_id.id),
                    ('partner_id', '=', line.partner_id.id), ('name', '=', line.name)], limit=1)
                if vl:
                    ap.invoice_date = vl.sb_doc_date or ap.invoice_date
                    ap.invoice_ref = vl.sb_doc_ref or ''
            ap.amount_total = abs(line.amount_currency) if line.currency_id else abs(line.balance)

    @api.depends('line_id.amount_residual', 'line_id.amount_residual_currency', 'voucher_id.state')
    def _compute_residual(self):
        for ap in self:
            line = ap.line_id
            residual = abs(line.amount_residual_currency if line.currency_id and line.currency_id != line.company_currency_id
                           else line.amount_residual)
            if ap.voucher_id.state == 'posted':
                residual += ap.amount_applied + ap.discount_currency
            ap.amount_residual = residual

    @api.depends('line_id', 'voucher_id.date')
    def _compute_rate(self):
        for ap in self:
            cur = ap.item_currency_id
            company_cur = ap.voucher_id.currency_id
            if not cur or cur == company_cur:
                ap.rate = 1.0
            else:
                ap.rate = cur._get_conversion_rate(cur, company_cur, ap.voucher_id.company_id,
                                                   ap.voucher_id.date or fields.Date.today())

    @api.depends('amount_applied', 'rate')
    def _compute_company_amount(self):
        for ap in self:
            ap.amount_applied_company = ap.voucher_id.currency_id.round(ap.amount_applied * (ap.rate or 1.0))

    @api.depends('discount', 'rate')
    def _compute_discount_currency(self):
        for ap in self:
            ap.discount_currency = ap.discount / (ap.rate or 1.0)

    @api.onchange('pay')
    def _onchange_pay(self):
        self.amount_applied = (self.amount_residual - self.discount_currency) if self.pay else 0.0

    @api.onchange('amount_applied')
    def _onchange_amount_applied(self):
        self.pay = bool(self.amount_applied)

    def _match_label(self):
        self.ensure_one()
        if self.kind == 'invoice':
            if self.line_id.move_id.journal_id.code == 'OB' and self.line_id.name:
                return self.line_id.name
            return self.line_id.move_id.name
        return self.env._('Applied %s', self.line_id.move_id.name)


class SbVoucherPurpose(models.Model):
    _name = 'sb.voucher.purpose'
    _description = 'Receipt / Payment Purpose'
    _order = 'voucher_type, sequence, id'
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    voucher_type = fields.Selection([('receipt', 'Receipt (CRV)'), ('payment', 'Payment (CPV)')], required=True)
    payer_type = fields.Selection(PAYER_TYPES, string='Received from / Paid to')
    account_id = fields.Many2one('account.account', string='GL Account', required=True, check_company=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    active = fields.Boolean(default=True)
    note = fields.Char()
