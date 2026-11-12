from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date
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
MOVE_TO_VOUCHER = {v: k for k, v in MOVE_TYPES.items()}

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
    date_ec = fields.Char('E.C. Date', compute='_compute_date_ec')
    due_date = fields.Date()
    sb_due_label = fields.Char(compute='_compute_sb_due_label')
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
    sb_received_filled = fields.Monetary(copy=False, help='The amount received / paid as filled in from the ticked items; '
                                                         'typing another amount keeps the typed one.')
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
                                   domain="[('doc_type', '=', 'payment' if voucher_type == 'cash_purchase' else voucher_type), ('active', '=', True), ('prefix', 'not in', ('BR-R/', 'BR-P/'))]",
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

    amount_incl_vat = fields.Boolean('Amounts include VAT', default=lambda s: not s.env.company.sb_vat_registered and s.env.context.get('default_voucher_type') == 'payment',
                                     help='Receipt / payment: the line amounts are totals with VAT; the VAT is split out of them.')
    wht_supply = fields.Selection([('goods', 'Goods'), ('service', 'Services'), ('professional', 'Professional fees'),
                                   ('government', 'Government (taxes, duties, fees)')],
                                  'Goods or services',
                                  help='Withholding starts above the goods or the services threshold, before VAT (Ethiopia: '
                                       '20,000 / 10,000; Uganda: 1,000,000). Uganda: professional fees from the first shilling.')
    sb_wht_country = fields.Char(compute='_compute_sb_wht_country')
    wht_rate = fields.Float('Withholding Rate (%)', compute='_compute_wht_rule')
    wht_rule = fields.Char('Withholding rule', compute='_compute_wht_rule')
    sb_wht_warning = fields.Char(compute='_compute_wht_rule')
    withholding = fields.Boolean('Withholding', help='Receipt: customer withheld tax. Payment: we withhold from the vendor.')
    wht_base = fields.Monetary('Withholding Base', compute='_compute_wht', store=True, readonly=False)
    wht_amount = fields.Monetary('Withholding Amount', compute='_compute_wht', store=True, readonly=False)
    wht_certificate_no = fields.Char('Withholding Receipt No.')
    wht_certificate_date = fields.Date('Withholding Receipt Date')
    # money paid on top of the items and lines, kept as an advance: withheld on as it is paid (see _sb_wht_split)
    wht_advance_base = fields.Monetary('Advance withheld on', compute='_compute_wht', store=True, readonly=False)
    wht_advance_amount = fields.Monetary('Withholding on the advance', compute='_compute_wht', store=True, readonly=False)
    wht_exempt_reason = fields.Char(
        'Reason not withheld', copy=False,
        help='A payment over the threshold paid without withholding, or with less than the rule asks: the exemption '
             'certificate number, or why. Such payments are listed in the withholding register.')
    wht_due = fields.Monetary('Withholding due', readonly=True, copy=False,
                              help='What the rule asked for on a payment withheld less (or not at all), with a reason.')

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

    @api.depends('voucher_type', 'state', 'move_id.payment_state', 'move_id.amount_residual', 'move_id.line_ids.amount_residual')
    def _compute_payment_state(self):
        for v in self:
            if v.state != 'posted':
                v.payment_state = False
                v.amount_residual = 0.0
            elif v.voucher_type in INVOICE_TYPES and v.move_id and not v.move_id.is_invoice():
                # brought over from the previous system as a plain entry: Odoo tracks no payment on it, its open
                # customer / vendor lines tell
                lines = v.move_id.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))
                due, total = abs(sum(lines.mapped('amount_residual'))), abs(sum(lines.mapped('balance')))
                v.amount_residual = due
                v.payment_state = 'paid' if v.currency_id.is_zero(due) else 'not_paid' if \
                    v.currency_id.compare_amounts(due, total) >= 0 else 'partial'
            elif v.voucher_type in INVOICE_TYPES:
                v.payment_state = v.move_id.payment_state
                v.amount_residual = v.move_id.amount_residual
                if v.payment_state == 'reversed' and v.move_id._sb_settled_by_payment():
                    v.payment_state = 'paid'  # part credit note, part receipt / payment: paid, not reversed
            elif v.voucher_type in SETTLEMENT_TYPES:
                v.payment_state = 'paid'  # the voucher is the money itself
                v.amount_residual = 0.0
            else:
                v.payment_state = False
                v.amount_residual = 0.0

    @api.model
    def _sb_recompute_migrated_payment_state(self):
        """Invoices and bills brought over as plain entries: payment status from their open lines (run on upgrade)."""
        self.search([('sb_migrated', '=', True), ('state', '=', 'posted'), ('voucher_type', 'in', INVOICE_TYPES)])._compute_payment_state()

    # ------------------------------------------------------------------ defaults / computes
    def _default_cash_account(self, vtype=None):
        """The bank a new voucher is deposited to / paid from: the one this user used last on this kind of voucher (any
        kind), else the company's main bank: the cash account most money went through in the last six months. Never an
        account nothing is posted to, such as the chart's template "Bank"."""
        company = self.env.company
        vtype = vtype or self.env.context.get('default_voucher_type')
        Row = self.env['sb.voucher.cash']
        dom = [('company_id', '=', company.id), ('create_uid', '=', self.env.uid),
               ('account_id.account_type', '=', 'asset_cash'), ('account_id.active', '=', True)]
        last = (vtype and Row.search(dom + [('voucher_id.voucher_type', '=', vtype)], order='id desc', limit=1)) \
            or (vtype != 'cash_sale' and Row.search(dom, order='id desc', limit=1))
        if last:
            return last.account_id
        if vtype == 'cash_sale':
            # a cash sale is paid into a till: the cash box this user keeps, else the company's till (a cash box that is not
            # petty cash) money went into lately, else where the company's cash sales went lately (a company whose
            # customers pay into the bank), never the user's last bank payment
            boxes = self.env['account.account'].search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash')])
            tills = boxes.filtered(lambda a: Row._sb_method_for(a) == 'cash' and not self._sb_is_petty_box(a))
            since = fields.Date.subtract(fields.Date.context_today(self), months=6)
            usual = Row._read_group([('company_id', '=', company.id), ('voucher_id.voucher_type', '=', 'cash_sale'),
                                     ('voucher_id.state', '=', 'posted'), ('voucher_id.date', '>=', since)],
                                    ['account_id'], ['__count'], order='__count desc', limit=1)
            box = self._sb_my_petty_box() or (tills and self._sb_busiest_cash([('account_id', 'in', tills.ids)])) \
                or (usual and usual[0][0])
            if box:
                return box
        return self._sb_main_bank() or self._sb_busiest_cash()

    def _sb_busiest_cash(self, domain=None):
        company = self.env.company
        since = fields.Date.subtract(fields.Date.context_today(self), months=6)
        busiest = self.env['account.move.line'].sudo()._read_group(
            [('company_id', '=', company.id), ('account_id.account_type', '=', 'asset_cash'), ('parent_state', '=', 'posted'),
             ('date', '>=', since)] + (domain or []), ['account_id'], ['debit:sum'], order='debit:sum desc', limit=1)
        return busiest[0][0] if busiest else self.env['account.account']

    def _sb_main_bank(self):
        """The company's main bank: the bank account (not a cash box) most money went through in the last six months."""
        company = self.env.company
        Row = self.env['sb.voucher.cash']
        accounts = self.env['account.account'].search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash')])
        boxes = accounts.filtered(lambda a: Row._sb_method_for(a) == 'cash')
        return self._sb_busiest_cash([('account_id', 'not in', boxes.ids)]) or \
            self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1).default_account_id

    def _sb_petty_cash(self):
        """The petty cash box: the user's own box (Kept by), else the petty cash account most used lately, else the
        account of the petty cash series, else the first cash journal's."""
        company = self.env.company
        mine = self._sb_my_petty_box()
        if mine:
            return mine
        # else the petty cash box most used in the last six months (not an idle box of the chart that holds nothing)
        petty = self.env['account.account'].search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash'),
                                                    ('name', 'ilike', 'petty')], order='code')
        busiest = petty and self._sb_busiest_cash([('account_id', 'in', petty.ids)])
        if busiest:
            return busiest
        series = self.env['sb.doc.series'].search([('company_id', '=', company.id), ('doc_type', '=', 'payment'),
                                                   ('sb_cash_account_id', '!=', False)], limit=1)
        if series:
            return series.sb_cash_account_id
        if petty:
            return petty[:1]
        return self.env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', company.id)], limit=1).default_account_id

    @api.model
    def _sb_my_petty_box(self, user=None):
        """The cash box this user keeps: the one marked "Kept by" them, else a petty cash account named after them
        ("Petty cash - Zewdinesh Moges")."""
        user = user or self.env.user
        company = self.env.company
        Account = self.env['account.account']
        boxes = Account.search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash')])
        kept = boxes.filtered(lambda a: a.sb_custodian_id == user)
        if kept:
            return kept[:1]
        words = [w.lower() for w in (user.name or '').replace('-', ' ').split() if len(w) > 2]
        if not words:
            return Account
        named = boxes.filtered(lambda a: 'petty' in (a.name or '').lower() and not a.sb_custodian_id
                               and all(w in (a.name or '').lower() for w in words[:2]))
        return named[:1]

    def _sb_is_petty_box(self, account):
        return account.account_type == 'asset_cash' and (bool(account.sb_custodian_id) or 'petty' in (account.name or '').lower())

    def _sb_paid_from_for(self, amount):
        """Where a payment of this amount is paid from by default: petty cash up to the company's petty cash limit, the
        main bank above it."""
        limit = self.env.company.sb_petty_cash_limit
        if limit and amount and self.env.company.currency_id.compare_amounts(amount, limit) > 0:
            return self._sb_main_bank()
        return self._sb_petty_cash() if amount else self.env['account.account']

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        vtype = res.get('voucher_type') or self.env.context.get('default_voucher_type')
        if 'cash_line_ids' in fields_list and vtype in SETTLEMENT_TYPES and not res.get('cash_line_ids'):
            account = self._default_cash_account(vtype)
            if account:
                # a cash box is paid from in cash, not by "Bank transfer"; the account follows the amount until changed
                method = self.env['sb.voucher.cash']._sb_method_for(account)
                if vtype == 'cash_sale' and method == 'transfer':
                    method = 'deposit'  # a cash sale paid into the bank: the customer's cash deposited, not a transfer
                res['cash_line_ids'] = [Command.create({
                    'account_id': account.id, 'method': method,
                    'auto_account': vtype in ('payment', 'cash_purchase')})]
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
                 'apply_ids.discount', 'apply_ids.direction', 'wht_amount', 'wht_advance_amount', 'withholding', 'received_amount',
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
                    # an advance withheld on: the amount entered for it is before withholding, the bank pays it less the tax
                    on_advance = v.wht_advance_amount if v.withholding else 0.0
                    unapplied = v.received_amount - cash - on_advance
                    cash = v.received_amount - on_advance
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
        """What withholding is worked on: the amount before VAT (lines, and the share of the invoices paid).
        Where the rule has a threshold, only what passes it: a bill whose own value before VAT is over the threshold
        is withheld on whatever share of it is paid (a 30,000 goods bill paid in two halves of 15,000 is withheld on
        both), lines not paid against a bill when they pass it together. Nothing passing (withholding ticked by hand,
        e.g. one contract billed in small invoices): the whole amount."""
        over, whole = self._sb_wht_parts()
        return over or whole

    def _sb_wht_parts(self):
        """(base passing the threshold, whole base), before VAT, less what advances withheld when they were paid
        already cover; money kept as an advance counts too."""
        return self._sb_wht_split()[:2]

    def _sb_value_before_vat(self):
        """A bill's, cash purchase's or return's value before VAT, in the company currency at the rate of its own date:
        what withholding is tested against and worked on, for a bill paid later and a cash purchase alike. The voucher's
        lines keep their VAT when it went to cost (a company not registered for VAT, _sb_vat_in_cost): a 20,000 bill with
        3,000 of VAT in its cost is worth 20,000 here."""
        self.ensure_one()
        cur = self.amount_currency_id
        move = self.move_id
        if cur == self.currency_id:
            return self.amount_untaxed
        if move.is_invoice() and move.amount_total:
            # the rate the bill was booked at
            return self.currency_id.round(self.amount_untaxed * abs(move.amount_total_signed) / move.amount_total)
        return cur._convert(self.amount_untaxed, self.currency_id, self.company_id, self.date or fields.Date.context_today(self))

    def _sb_wht_bill_value(self, move, doc=None):
        """(value before VAT, the same after the bill's credit / debit notes, total in the bill's currency) of a bill or
        invoice a receipt / payment applies, in the company currency at the bill's own rate. A 30,000 bill reduced to
        15,000 by a debit note is tested at 15,000; a bill whose VAT went to cost at its price before VAT."""
        if doc:
            value, total = doc._sb_value_before_vat(), doc.amount_total
        else:
            value, total = abs(move.amount_untaxed_signed), move.amount_total
        returns = self.env['account.move'].search([('reversed_entry_id', '=', move.id), ('state', '=', 'posted'),
                                                   ('move_type', 'in', ('in_refund', 'out_refund'))]) if move.is_invoice() else move.browse()
        returns |= self.search([('origin_id', '=', doc.id), ('state', '=', 'posted'),
                                ('voucher_type', 'in', ('sale_return', 'purchase_return'))]).move_id if doc else returns.browse()
        rdocs = {v.move_id: v for v in self.search([('move_id', 'in', returns.ids)])}
        returned = 0.0
        for ret in returns:
            rdoc = rdocs.get(ret)
            if rdoc and rdoc.state != 'posted' or not rdoc and ret.payment_state == 'reversed':
                continue  # a voided note
            returned += rdoc._sb_value_before_vat() if rdoc else abs(ret.amount_untaxed_signed)
        return value, value - returned, total

    def _sb_wht_split(self):
        """(base passing the threshold, whole base, advance in the base, withholding on the items alone).
        The Proclamation withholds when the money is paid: money paid on top of the items and lines (kept as the
        supplier's advance) is withheld on as it is paid, tested against the threshold on its own, and not again when
        the advance is applied to a bill (_sb_wht_advance_covered)."""
        self.ensure_one()
        cur = self.currency_id
        rate, threshold, _rule = self._sb_wht_rule()
        if self.voucher_type in ('cash_sale', 'cash_purchase'):
            base = self._sb_value_before_vat()
            return (base if base > threshold else 0.0), base, 0.0, 0.0
        # only lines that buy something (expense, cost, asset): paying a tax, pension, salaries, a dividend or a loan
        # settles a liability and is no supply to withhold on
        lines = self.line_ids.filtered(lambda l: l.account_id.internal_group not in ('liability', 'equity')
                                       and l.account_id.account_type not in ('asset_receivable', 'asset_cash'))._sb_split_tax()[0]
        over, whole = (lines if lines > threshold else 0.0), lines
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        # the bills' own vouchers (bills brought over from the previous system as plain entries too): they keep the VAT
        # even when it went to cost
        docs = {v.move_id: v for v in self.search([('move_id', 'in', self.apply_ids.invoice_id.ids), ('voucher_type', 'in', INVOICE_TYPES),
                                                   '|', ('state', '=', 'posted'), ('sb_migrated', '=', True)])}
        # only the supplier's own items on a payment (the customer's on a receipt): a customer's credit refunded is no supply
        wrong = 'customer' if self.voucher_type == 'payment' else 'vendor'
        for ap in self.apply_ids.filtered(lambda a: a.direction == primary and (a.invoice_id.is_invoice() or a.invoice_id in docs)
                                          and self._sb_item_side(a.line_id) != wrong):
            value, net, total = self._sb_wht_bill_value(ap.invoice_id, docs.get(ap.invoice_id))
            if not total:
                continue
            # the share of the bill paid, before VAT, in the company currency at the bill's own rate: a USD bill paid when
            # the birr is weaker is withheld on what it was billed at, not on the exchange difference
            share = ap.amount_applied * value / total
            whole += share
            if net > threshold:  # Ethiopia: the threshold is per contract / invoice, after its credit / debit notes
                over += share
        covered = self._sb_wht_advance_covered()
        over, whole = cur.round(max(over - covered, 0.0)), cur.round(max(whole - covered, 0.0))
        # what the items withhold: the amount entered is net of it only when withholding is ticked (not ticked, the full
        # amount is paid and nothing of it is an advance: a 10,000 bill paid 11,500 with its VAT is no 3,000 advance)
        items_wht = cur.round((over or whole) * rate / 100.0) if self.withholding else 0.0
        advance = self._sb_wht_unapplied(items_wht)
        if not advance:
            return over, whole, 0.0, items_wht
        whole += advance
        if advance > threshold:
            over += advance
        return over, whole, (advance if advance > threshold or not over else 0.0), items_wht

    def _sb_wht_unapplied(self, items_wht):
        """Receipt / payment: the amount entered beyond what the items applied and the lines need after their own
        withholding: the advance, before withholding."""
        if self.voucher_type not in ('receipt', 'payment') or not self.received_amount:
            return 0.0
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        applied = sum(ap.amount_applied_company if ap.direction == primary else -ap.amount_applied_company
                      for ap in self.apply_ids)
        untaxed, tax = self.line_ids._sb_split_tax()
        return max(self.currency_id.round(self.received_amount - (applied + untaxed + tax - items_wht)), 0.0)

    def _sb_wht_advance_covered(self):
        """Advances applied on this voucher that were withheld on when they were paid (or received): the part they
        cover was withheld then, and is not withheld again on the bill."""
        self.ensure_one()
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        covered = 0.0
        for ap in self.apply_ids.filtered(lambda a: a.direction != primary and a.amount_applied_company):
            src = self.search([('move_id', '=', ap.line_id.move_id.id), ('state', '=', 'posted')], limit=1)
            if not src.withholding or not src.wht_amount:
                continue
            account, partner = ap.line_id.account_id, ap.line_id.partner_id
            # the advance paid as a line (e.g. the "Vendor advance" purpose) was withheld on, and money kept as an advance
            # when it was withheld on as it was paid
            withheld = src.line_ids.filtered(lambda l: l.account_id == account)._sb_split_tax()[0]
            if account in (src.company_id.sb_vendor_advance_account_id | src.company_id.sb_customer_advance_account_id):
                withheld += src.wht_advance_base
            advance = abs(sum(src.move_id.line_ids.filtered(
                lambda l: l.account_id == account and l.partner_id == partner).mapped('balance')))
            if withheld and advance:
                covered += ap.amount_applied_company * min(withheld / advance, 1.0)
        return covered

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
                 'partner_id.sb_wht_exempt_until', 'withholding')
    def _compute_wht_rule(self):
        for v in self:
            rate, _threshold, rule = v._sb_wht_rule()
            v.wht_rate, v.wht_rule = rate, rule or False
            v.sb_wht_warning = v._sb_wht_warning(rate)

    sb_wht_note = fields.Char(compute='_compute_sb_wht_note')

    @api.depends('withholding', 'wht_supply', 'voucher_type', 'partner_id', 'apply_ids.amount_applied_company', 'line_ids.amount',
                 'line_ids.price_subtotal', 'amount_incl_vat', 'received_amount', 'date', 'wht_exempt_reason')
    def _compute_sb_wht_note(self):
        """Why withholding is not ticked on a payment (it unticks itself under the threshold): said next to the tick."""
        for v in self:
            v.sb_wht_note = False
            if not v.sb_wht_country or v.voucher_type not in ('payment', 'cash_purchase') or v.state not in ('draft', False) \
                    or v.withholding or not v.partner_id or v.wht_exempt_reason:
                continue
            if not v.wht_supply:
                v.sb_wht_note = self.env._('Choose goods or services: the withholding threshold depends on it.')
                continue
            rate, threshold, _rule = v._sb_wht_rule()
            if not rate:
                continue
            over, whole = v._sb_wht_parts()
            if over <= 0:
                what = dict(v._fields['wht_supply']._description_selection(self.env)).get(v.wht_supply, '')
                v.sb_wht_note = self.env._(
                    'Not withheld: %(what)s of %(value)s before VAT are under the %(threshold)s threshold.',
                    what=what.lower(), value='{:,.2f}'.format(whole or 0.0), threshold='{:,.2f}'.format(threshold or 0.0))

    def _sb_wht_warning(self, rate):
        """Ethiopia: say so when a supplier is withheld at the individuals' rate (30%) because the contact has no TIN or
        trade licence, or is not marked as a company: usually the contact is incomplete, not the supplier an individual."""
        p = self.partner_id.commercial_partner_id
        co = self.company_id
        if self.sb_wht_country != 'ET' or self.voucher_type not in ('payment', 'cash_purchase') or not p \
                or not self.withholding or not rate or rate != co.sb_wht_individual_rate or rate == co.sb_wht_rate:
            return False
        missing = [what for what, ok in ((self.env._('is not marked as a company'), p.is_company),
                                         (self.env._('has no TIN'), p.vat),
                                         (self.env._('has no trade licence'), p.sb_trade_license)) if not ok]
        return self.env._('Withheld at %(rate)g%% because %(partner)s %(missing)s. A licensed company with a TIN is withheld '
                          '%(company_rate)g%%: complete the contact if it is one.', rate=rate, partner=p.name,
                          missing=self.env._(' and ').join(missing), company_rate=co.sb_wht_rate)

    @api.depends('withholding', 'apply_ids.amount_applied_company', 'apply_ids.line_id', 'apply_ids.direction', 'line_ids.amount',
                 'line_ids.price_subtotal', 'line_ids.tax_ids', 'voucher_type', 'amount_incl_vat', 'wht_supply', 'date',
                 'received_amount', 'partner_id.vat', 'partner_id.is_company', 'partner_id.sb_trade_license',
                 'partner_id.sb_wht_exempt_until')
    def _compute_wht(self):
        for v in self:
            if v.state not in ('draft', False) and v._origin:
                # posted: the amount is in the ledger; a later change to the supplier's TIN or licence leaves it alone
                o = v._origin
                v.wht_base, v.wht_amount = o.wht_base, o.wht_amount
                v.wht_advance_base, v.wht_advance_amount = o.wht_advance_base, o.wht_advance_amount
                continue
            if not v.withholding or v.voucher_type not in SETTLEMENT_TYPES:
                v.wht_base = v.wht_amount = v.wht_advance_base = v.wht_advance_amount = 0.0
                continue
            over, whole, advance, items_wht = v._sb_wht_split()
            base = over or whole
            v.wht_base = base
            v.wht_amount = v.currency_id.round(base * v._sb_wht_rule()[0] / 100.0)
            v.wht_advance_base = advance
            v.wht_advance_amount = v.wht_amount - items_wht if advance else 0.0

    def _sb_vat_in_cost(self, line):
        """(price per unit, taxes) a line posts with. A company not registered for VAT claims no input VAT: on a bill,
        cash purchase or payment the VAT paid is part of the cost, so the line posts at its price with VAT to its own
        expense / stock account and the VAT tax is dropped (nothing goes to VAT receivable)."""
        taxes = line.tax_ids
        if self.company_id.sb_vat_registered or self.voucher_type in SALE_SIDE + ('receipt',):
            return line.price_unit, taxes
        vat = taxes.filtered(lambda t: t.amount_type == 'percent' and t.amount > 0)
        if not vat:
            return line.price_unit, taxes
        keep = taxes - vat
        res = vat.compute_all(line.price_unit, line.currency_id or self.currency_id, 1.0, product=line.product_id,
                              partner=self.partner_id)
        return res['total_included'], keep

    def _sb_note_vat_in_cost(self):
        """Say on the voucher that its VAT went to cost (see _sb_vat_in_cost)."""
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.tax_ids and self._sb_vat_in_cost(l)[1] != l.tax_ids)
        if not lines:
            return
        vat = sum((l.price_total - l.price_subtotal) if self.voucher_type in ITEM_TYPES else l._sb_split_tax()[1] for l in lines)
        self.message_post(body=self.env._(
            'The company is not registered for VAT: the %(vat)s of VAT on this %(doc)s is not claimable. It is booked as part of '
            'the cost on %(accounts)s, not to VAT receivable.', vat='{:,.2f}'.format(vat),
            doc=dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type, '').lower(),
            accounts=', '.join(lines.account_id.mapped('display_name')) or self.env._('the lines\' accounts')))

    def _sb_default_tax(self):
        """The VAT a line with a GL account and no item gets: the company's default sale / purchase tax, none when the
        company is not registered for VAT (e.g. VAT-exempt agricultural inputs)."""
        self.ensure_one()
        company = self.company_id or self.env.company
        if not company.sb_vat_registered:
            return self.env['account.tax']
        return company.account_sale_tax_id if self.voucher_type in SALE_SIDE else company.account_purchase_tax_id

    # ------------------------------------------------------------------ onchange
    @api.onchange('line_ids', 'apply_ids', 'received_amount', 'withholding', 'wht_amount', 'partner_id', 'wht_supply',
                  'amount_incl_vat', 'date')
    def _onchange_sync_single_deposit(self):
        """One deposit row (the usual case) follows the voucher total, so single-bank entry stays one field.
        Ethiopia / Uganda, paying a supplier: withholding ticks itself when the rule withholds and the amount passes the
        goods / services threshold."""
        if self.state == 'draft':
            self._sb_cap_credits()
        if self.state == 'draft' and getattr(self, 'sb_wht_none', False):
            # paying taxes, pension, salaries, a dividend or a loan: nothing is withheld, nothing asked
            self.withholding, self.wht_supply = False, False
        elif self.sb_wht_country and self.state == 'draft' and not self.wht_supply and self._sb_wht_pays_supplies():
            self.wht_supply = self._sb_wht_guess_supply()
        if self.sb_wht_country and self.voucher_type in ('payment', 'cash_purchase') and self.state == 'draft' and self.wht_supply \
                and not self.wht_exempt_reason and not getattr(self, 'sb_wht_none', False):  # with a reason (an exemption certificate) the tick is the user's
            rate, _threshold, _rule = self._sb_wht_rule()
            self.withholding = bool(rate) and self._sb_wht_parts()[0] > 0
            self._sb_cap_credits()
        self._sb_fill_received()
        if self.voucher_type in SETTLEMENT_TYPES and len(self.cash_line_ids) == 1:
            row = self.cash_line_ids
            if row.auto_account and self.state == 'draft' and self.voucher_type in ('payment', 'cash_purchase') \
                    and not self.sb_series_id.sb_cash_account_id:  # a petty cash pad pays from its box
                # not chosen by the user: petty cash up to the petty cash limit, the main bank above it (a 30,000 bill is
                # not paid from petty cash because the last payment was)
                account = self._sb_paid_from_for(self.amount_cash)
                if account and account != row.account_id:
                    row.account_id = account
                    row.method = row._sb_method_for(account)
            if row.row_currency_id == self.currency_id:
                row.amount_currency = self.amount_cash
            elif row.row_currency_id and row.rate:
                # a foreign-currency account (e.g. the USD account): the same value in its currency at the row's rate
                row.amount_currency = row.row_currency_id.round(self.amount_cash / row.rate)

    def _sb_received_typed(self):
        """An amount received / paid the user typed (not one filled in from the ticked items)."""
        return bool(self.received_amount) and self.currency_id.compare_amounts(self.received_amount, self.sb_received_filled) != 0

    def _sb_fill_received(self):
        """Receipt / payment with no amount typed: "Amount received" shows what the ticked items come to, and follows
        them until the user types another amount."""
        if self.voucher_type not in ('receipt', 'payment') or self.state != 'draft' or self._sb_received_typed() \
                or isinstance(self.id, int):  # the form only: code that sets the amount keeps its own
            return
        self.received_amount = 0.0
        fill = self.amount_cash + (self.wht_advance_amount if self.withholding else 0.0)
        fill = self.currency_id.round(max(fill, 0.0))
        self.received_amount = fill
        self.sb_received_filled = fill

    def _sb_cap_credits(self):
        """Advances and credit notes ticked on a receipt / payment take only what the invoices and lines need, less the
        withholding: a 60,000 advance ticked against a 50,000 bill applies 50,000 and the rest stays open."""
        if self.voucher_type not in ('receipt', 'payment'):
            return
        cur = self.currency_id
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        for _pass in range(2):  # the withholding can change with the advances used (a withheld advance is not withheld again)
            excess = -(self.amount_total - (self.wht_amount if self.withholding else 0.0))
            if cur.compare_amounts(excess, 0.0) <= 0:
                return
            for ap in reversed(self.apply_ids.filtered(lambda a: a.direction != primary and a.amount_applied)):
                cut = min(excess, ap.amount_applied_company)
                ap.amount_applied = ap.item_currency_id.round(ap.amount_applied - cut / (ap.rate or 1.0))
                ap.pay = bool(ap.amount_applied)
                excess -= cut
                if cur.compare_amounts(excess, 0.0) <= 0:
                    break

    def _sb_terms(self):
        """The customer's (vendor's) payment terms, for an invoice or bill."""
        if self.voucher_type not in ('sale', 'bill') or not self.partner_id:
            return self.env['account.payment.term']
        p = self.partner_id.commercial_partner_id.with_company(self.company_id or self.env.company)
        return p.property_payment_term_id if self.voucher_type == 'sale' else p.property_supplier_payment_term_id

    @api.onchange('partner_id', 'date')
    def _onchange_partner_terms(self):
        """The due date from the customer's (vendor's) payment terms, shown before posting. With no terms the invoice is
        due on receipt (shown as such)."""
        if self.state != 'draft' or self.voucher_type not in ('sale', 'bill'):
            return
        term = self._sb_terms()
        if term and term.line_ids and self.date:
            self.due_date = max(term.line_ids.mapped(lambda l: l._get_due_date(self.date)))

    @api.depends('voucher_type', 'due_date', 'date')
    def _compute_sb_due_label(self):
        for v in self:
            immediate = v.voucher_type in ('sale', 'bill') and (not v.due_date or (v.date and v.due_date <= v.date))
            v.sb_due_label = self.env._('Due on receipt') if immediate else False

    @api.onchange('partner_id')
    def _onchange_partner_load_open_items(self):
        if self.voucher_type not in ('receipt', 'payment') or self.state != 'draft':
            return
        commands = [Command.clear()]
        if self.partner_id:
            ticked = self.env.context.get('sb_apply_move_ids') or []
            for vals in self._open_items_vals():
                line = self.env['account.move.line'].browse(vals['line_id'])
                if line.move_id.id in ticked:  # opened with Pay from the bill: that bill is ticked
                    residual = line.amount_residual_currency if line.currency_id and line.currency_id != line.company_currency_id \
                        else line.amount_residual
                    vals = dict(vals, pay=True, amount_applied=abs(residual))
                commands.append(Command.create(vals))
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
        staff = company.sb_pay_loan_account_id if 'sb_pay_loan_account_id' in company._fields else self.env['account.account']
        ticked = self.env.context.get('sb_apply_move_ids') or []

        def trade(l):
            # the customer's or vendor's trade account; staff debtors, advances and other accounts after it
            acc = l.account_id
            return acc.account_type in ('asset_receivable', 'liability_payable') and acc not in staff \
                and 'staff' not in (acc.name or '').lower()
        # the bill being paid first, then what they owe us (we owe them) on the trade account, oldest due first
        lines = lines.sorted(lambda l: (
            0 if l.move_id.id in ticked else 1, 0 if (l.amount_residual > 0) == (primary == 'debit') else 1,
            0 if trade(l) else 1, l.date_maturity or l.date, l.id))
        return [{'line_id': line.id, 'sequence': i} for i, line in enumerate(lines, 1)]

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
            price_unit, taxes = self._sb_vat_in_cost(line)
            vals = {
                'name': line.name or (line.product_id.display_name if line.product_id else '/'),
                'quantity': line.quantity,
                'price_unit': price_unit,
                'tax_ids': [Command.set(taxes.ids)],
            }
            if line.product_id:
                vals['product_id'] = line.product_id.id
            if line.account_id:
                vals['account_id'] = line.account_id.id
            lines.append(Command.create(vals))
        origin_move = self.origin_id.move_id
        term = False if self.due_date else self._sb_terms()
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

        def add_charge(amount, name, partner=False):
            """Transfer and bank charges; where the company says they include VAT, the VAT goes to its own account."""
            co = self.company_id
            vat = 0.0
            if co.sb_bank_charges_vat and co.sb_bank_charges_vat_account_id:
                vat = cur.round(amount - amount / (1 + co.sb_bank_charges_vat / 100.0))
            add(co.sb_bank_charges_account_id, amount - vat, 0.0, name, partner)
            if vat:
                add(co.sb_bank_charges_vat_account_id, vat, 0.0, self.env._('VAT in %s', name or ''), partner)

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
                    add_charge(charges_cur * rate, self.env._('Bank charges %s', label or ''), partner)

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
                if line.account_id == self.company_id.sb_bank_charges_account_id:
                    add_charge(line.amount, line.name or name, line.partner_id)
                else:
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
                taxes = self._sb_vat_in_cost(line)[1]
                amount = line.price_subtotal if taxes == line.tax_ids else line.price_total
                lines.append(Command.create({
                    'account_id': line.account_id.id,
                    'partner_id': partner.id or False,
                    'product_id': line.product_id.id or False,
                    'quantity': line.quantity,
                    'name': line.name or line.product_id.display_name or self.memo or '/',
                    'debit': cur.round(amount) if sign < 0 else 0.0,
                    'credit': cur.round(amount) if sign > 0 else 0.0,
                    'tax_ids': [Command.set(taxes.ids)],
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
            if sign < 0 and not line.tax_ids and line.account_id == self.company_id.sb_bank_charges_account_id and not any(
                    'vat' in (o.account_id.name or '').lower() for o in self.line_ids):  # VAT typed on its own line: as typed
                add_charge(line.amount, line.name, line.partner_id or partner)  # a bank charge paid: its VAT apart
                continue
            if line.tax_ids and self._sb_vat_in_cost(line)[1] != line.tax_ids:
                # not registered for VAT: the VAT paid is part of the cost, on the line's own account
                gross = sum(line._sb_split_tax())
                add(line.account_id, gross if sign < 0 else 0.0, gross if sign > 0 else 0.0, line.name, line.partner_id or partner)
                continue
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
        if self.state == 'draft' and self.sb_received_filled and not self._sb_received_typed():
            # filled in from the ticked items, not typed: the voucher is worth what it applies, whatever changed since
            self.write({'received_amount': 0.0, 'sb_received_filled': 0.0})
        lock = self.company_id._get_user_fiscal_lock_date(self._journal())
        if self.date <= lock:
            raise ValidationError(self.env._(
                'The books are closed through %(lock)s (%(ec)s). Change the date or reopen the period.',
                lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
        if vtype in INVOICE_TYPES and not self.partner_id:
            raise ValidationError(self.env._('Choose a customer or vendor.'))
        multi_payee = bool(getattr(self, 'payroll_run_id', False))  # a salary payment settles several employees at once
        if vtype in ('receipt', 'payment') and not self.partner_id and not multi_payee and self.apply_ids.filtered('amount_applied'):
            raise ValidationError(self.env._('Choose a customer or vendor.'))
        # the money received / paid, not the total applied: an advance (nothing applied yet) is a receipt / payment too
        if vtype in ('receipt', 'payment') and self.currency_id.is_zero(self.amount_cash) \
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
                # a negative amount applied would raise the item instead of settling it (a debt moved to another
                # invoice or account, re-dated today): money given back is a refund, its own voucher
                if float_compare(ap.amount_applied, 0.0, precision_rounding=ap.item_currency_id.rounding or cur.rounding) < 0 \
                        or float_compare(ap.discount, 0.0, precision_rounding=cur.rounding) < 0:
                    raise ValidationError(self.env._(
                        '%(inv)s: the amount applied (%(amt)s) cannot be negative. A receipt / payment only settles items; '
                        'money given back is a refund (a payment to the customer, a receipt from the vendor).',
                        inv=ap.document, amt='{:,.2f}'.format(ap.amount_applied if ap.amount_applied < 0 else ap.discount)))
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
            from .fiscal_print import SALE_VOUCHERS
            ref = (self.sb_pad_number or '').strip()
            if not ref:
                raise UserError(self.env._('Enter the fiscal receipt number (FS No.) or the Ministry e-invoice reference: '
                                           'it is the number of this %s.', dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)))
            # one fiscal receipt number space per company, whatever the voucher: FS00001847, FS-00001847, F.S. 1847,
            # "FS No. 00001847" and a bare 00001847 (the fiscal register's series) are the same receipt
            Series = self.env['sb.doc.series']
            series = self.sb_series_id or Series._for('cash_sale', company=self.company_id)
            Series._sb_check_fiscal_new(ref, series, self.company_id)
            key = Series._fiscal_key(ref, series)
            # one posting per receipt at a time: two users entering the same receipt at once would both pass the check
            # below (the key is the normalized number: FS-1,847 and FS00001847 wait for each other)
            self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, hashtext(%s))', (self.company_id.id, '%s|%s|%s' % key))
            if series and key[1] is not None and not Series._fiscal_key(ref)[0]:
                ref = series._format(key[1], self.date)  # a bare number is written as the register prints it
            same = self.sudo().search([('id', '!=', self.id), ('company_id', '=', self.company_id.id),
                                       ('voucher_type', 'in', SALE_VOUCHERS), '|', ('state', '=', 'posted'), ('sb_editing', '=', True),
                                       ('name', 'ilike', str(key[1]) if key[1] is not None else ref)])
            same = same.filtered(lambda v: Series._fiscal_key(v.name, v.sb_series_id or series) == key)
            if same:
                raise UserError(self.env._('%(ref)s is already recorded as %(other)s. Each fiscal receipt is entered once.',
                                           ref=ref, other=same[0].name))
            voided = self.sudo().search([('id', '!=', self.id), ('company_id', '=', self.company_id.id),
                                         ('voucher_type', 'in', SALE_VOUCHERS), ('state', '=', 'void'),
                                         ('name', 'ilike', str(key[1]) if key[1] is not None else ref)])
            voided = voided.filtered(lambda v: Series._fiscal_key(v.name, v.sb_series_id or series) == key)
            if voided or self.env['account.move'].sudo().search_count([
                    ('journal_id', '=', self.move_id.journal_id.id), ('name', '=', ref), ('id', '!=', self.move_id.id)]):
                raise UserError(self.env._('%(ref)s belongs to %(other)s, which was voided. A voided fiscal receipt is not '
                                           'issued again: enter the number of the new receipt.',
                                           ref=ref, other=voided[:1].name or ref))
            self.move_id.name = ref
            return
        series = self.env['sb.doc.series']._for(self.voucher_type, company=self.company_id, series=self.sb_series_id)
        if not series:
            return
        label = dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)
        name = series._take(self.date, manual=self.sb_pad_number, what=label)
        # a number typed on an open series (JV/BB/BANKS/2026-07-08) is not checked against the entry's date as a sequence
        self.move_id.with_context(sb_open_numbering=bool(series.sb_open_numbering)).name = name
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
            v._sb_wht_check_due()
            v._sb_note_vat_in_cost()
            v._sb_wht_receipt_number()
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
            v._sb_check_cash_negative()
        return True

    def _sb_check_cash_negative(self):
        """Money out of a cash box (cash, petty cash) that takes it below zero: a box cannot pay what it does not hold.
        Warned (the payment posts, the warning is logged on it) or refused, as the company chooses."""
        self.ensure_one()
        Row = self.env['sb.voucher.cash']
        boxes = self.move_id.line_ids.filtered(lambda l: l.credit and Row._sb_method_for(l.account_id) == 'cash').account_id
        cur = self.currency_id
        for account in boxes:
            balance = self.env['account.move.line']._read_group(
                [('account_id', '=', account.id), ('parent_state', '=', 'posted'), ('company_id', '=', self.company_id.id)],
                [], ['balance:sum'])[0][0] or 0.0
            if cur.compare_amounts(balance, 0.0) >= 0:
                continue
            msg = self.env._('%(account)s goes below zero: %(balance)s after %(doc)s. Record the cash put into it (a transfer '
                             'from the bank), or pay from another account.', account=account.display_name,
                             balance='{:,.2f}'.format(balance), doc=self.move_id.name)
            if self.company_id.sb_cash_negative == 'block' or (
                    self._sb_is_petty_box(account) and not self.env.user.has_group('sonador_books.group_sb_manager')):
                # a petty cash box cannot pay what it does not hold: only a manager lets it go below zero
                raise UserError(msg + ' ' + self.env._('Only a manager can let a petty cash box go below zero.'))
            self.message_post(body=msg)
            self.env.user._bus_send('simple_notification', {
                'type': 'warning', 'sticky': True, 'title': self.env._('Cash below zero'), 'message': msg})

    def _sb_wht_check_due(self):
        """Ethiopia / Uganda, paying a supplier over the threshold: withholding left out, or taken at less than the rule
        asks, is allowed (exemption certificates exist) with a reason, and listed in the withholding register."""
        self.ensure_one()
        due = 0.0
        if self.sb_wht_country and not self.wht_supply and self._sb_wht_pays_supplies():
            # whatever posts it (the form, Pay Several Bills, an import): goods or services decides the threshold
            self.wht_supply = self._sb_wht_guess_supply()
            if not self.wht_supply and self._sb_wht_parts()[0] > 0:  # with no choice the rule tests the lower threshold
                raise ValidationError(self.env._(
                    'Choose whether this %(doc)s is for goods or services: withholding starts above %(goods)s for goods and '
                    '%(services)s for services.',
                    doc=self.env._('cash purchase') if self.voucher_type == 'cash_purchase' else self.env._('payment'),
                    goods=self.currency_id.format(self.company_id.sb_wht_goods_threshold),
                    services=self.currency_id.format(self.company_id.sb_wht_service_threshold)))
        if self.sb_wht_country and self.voucher_type in ('payment', 'cash_purchase') and self.wht_supply:
            rate = self._sb_wht_rule()[0]
            due = self.currency_id.round(self._sb_wht_parts()[0] * rate / 100.0)
        taken = self.wht_amount if self.withholding else 0.0
        short = self.currency_id.compare_amounts(taken, due) < 0
        if short and not (self.wht_exempt_reason or '').strip():
            raise ValidationError(self.env._(
                'Withholding of %(due)s is due on this payment (%(rule)s); %(taken)s is withheld. To pay without it, or with '
                'less, enter the reason: the exemption certificate number, or a note.',
                due='{:,.2f}'.format(due), rule=self.wht_rule or '', taken='{:,.2f}'.format(taken)))
        self.wht_due = due if short else 0.0

    def _sb_wht_pays_supplies(self):
        """Withholding is worked out on a cash purchase, and on a payment that settles bills or pays a supplier in advance
        (money kept as an advance, or a line to the vendor advances account). Salaries, charges and other lines are not."""
        self.ensure_one()
        if self.voucher_type == 'cash_purchase':
            return True
        if self.voucher_type != 'payment' or getattr(self, 'payroll_run_id', False):
            return False
        advance = self.company_id.sb_vendor_advance_account_id
        # a customer's credit (credit note, advance) refunded by a payment is not a supply bought: no withholding
        return bool(self.apply_ids.filtered(lambda a: a.direction == 'credit' and a.amount_applied
                                            and self._sb_item_side(a.line_id) != 'customer')) \
            or self.currency_id.compare_amounts(self.amount_unapplied, 0.0) > 0 \
            or bool(advance and self.line_ids.filtered(lambda l: l.account_id == advance and l.amount))

    def _sb_item_side(self, line):
        """Whose an open item is: 'customer' (customer invoices / credit notes, accounts kept per customer, customer
        advances), 'vendor' (the same for vendors), else 'other' (staff advances, tax receivable ...)."""
        mtype = line.move_id.move_type
        if mtype in ('out_invoice', 'out_refund', 'out_receipt'):
            return 'customer'
        if mtype in ('in_invoice', 'in_refund', 'in_receipt'):
            return 'vendor'
        account = line.account_id
        company = line.company_id or self.company_id
        if account.sb_partner_side in ('customer', 'vendor'):
            return account.sb_partner_side
        if account == company.sb_customer_advance_account_id:
            return 'customer'
        if account == company.sb_vendor_advance_account_id:
            return 'vendor'
        return 'other'

    def _sb_wht_guess_supply(self):
        """Goods or services when the voucher does not say: from the items on the bills it pays (the cash purchase's own
        lines): services only -> services, any goods -> goods; else what the supplier's last payment said. False when
        nothing tells."""
        self.ensure_one()
        if self.voucher_type == 'cash_purchase':
            products = self.line_ids.product_id
        else:
            bills = self.apply_ids.filtered(lambda a: a.direction == 'credit' and a.amount_applied).invoice_id
            products = bills.invoice_line_ids.product_id | self.search([('move_id', 'in', bills.ids)]).line_ids.product_id
        if products:
            return 'service' if all(p.type == 'service' for p in products) else 'goods'
        # lines with a GL account only: stock, cost of sales or a fixed asset is goods; expense accounts are services
        if self.voucher_type == 'cash_purchase':
            accounts = self.line_ids.account_id
        else:
            bills = self.apply_ids.filtered(lambda a: a.direction == 'credit' and a.amount_applied).invoice_id
            accounts = bills.invoice_line_ids.account_id | self.search([('move_id', 'in', bills.ids)]).line_ids.account_id
        types = set(accounts.mapped('account_type'))
        if types & {'asset_current', 'asset_fixed', 'asset_non_current', 'expense_direct_cost'}:
            return 'goods'
        if types and types <= {'expense', 'expense_depreciation'}:
            return 'service'
        partner = self.partner_id.commercial_partner_id
        if not partner:
            return False
        last = self.search([('partner_id', 'child_of', partner.id), ('state', '=', 'posted'), ('wht_supply', '!=', False),
                            ('voucher_type', 'in', ('payment', 'cash_purchase')), ('id', '!=', self._origin.id or 0)],
                           order='date desc, id desc', limit=1)
        return last.wht_supply

    def _sb_wht_receipt_number(self):
        """Withholding we take from a supplier: the receipt we hand them is numbered (WHT/000001) unless the number of a
        printed receipt pad was typed in. The number can be filled or corrected after posting; the ledger keeps it only in
        the line's description."""
        self.ensure_one()
        if self.voucher_type not in MONEY_OUT or not self.withholding or not self.wht_amount:
            return
        if not self.wht_certificate_date:
            self.wht_certificate_date = self.date
        if self.wht_certificate_no:
            return
        Seq = self.env['ir.sequence'].sudo()
        seq = Seq.search([('code', '=', 'sb.wht.receipt'), ('company_id', '=', self.company_id.id)], limit=1) or Seq.create({
            'name': 'Withholding receipts', 'code': 'sb.wht.receipt', 'prefix': 'WHT/', 'padding': 6,
            'implementation': 'no_gap', 'company_id': self.company_id.id})
        self.wht_certificate_no = seq.next_by_id()

    def action_print_wht_receipt(self):
        self.ensure_one()
        if self.state != 'posted' or not self.withholding or not self.wht_amount:
            raise UserError(self.env._('Only a posted voucher with withholding has a withholding receipt.'))
        return self.env.ref('sonador_books.action_report_wht_receipt').report_action(self)

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

    def _check_not_bank_reconciled(self):
        """Bank lines ticked as cleared in a finished bank reconciliation stay as the bank saw them."""
        self.ensure_one()
        rec = self.move_id.line_ids.filtered(lambda l: l.sb_cleared and l.sb_rec_id.state == 'done').sb_rec_id[:1]
        if rec:
            raise UserError(self.env._(
                '%(v)s is cleared in the bank reconciliation %(rec)s. Reopen the reconciliation first.',
                v=self.name, rec=rec.name))

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
            v._check_not_bank_reconciled()
            if v.voucher_type not in INVOICE_TYPES:
                v._check_advance_unused()
                v.move_id.line_ids.filtered('reconciled').remove_move_reconcile()
            v.move_id.button_draft()
            v.cash_line_ids.filtered(lambda r: r.cheque_state == 'open').write({'cheque_state': 'none'})
            v.state = 'draft'
        return True

    sb_void_reason = fields.Char('Reason for voiding', copy=False, tracking=True,
                                 help='Why this voucher is voided: asked before voiding, kept in its history.')

    def action_void(self):
        """Void = automatic reversing entry; the voucher stays on file, marked Void.
        In a closed period the reversal is dated today (first open date). A reason is required."""
        self._sb_require('group_sb_accountant', self.env._('void posted vouchers'))
        for v in self:
            if v.state != 'posted':
                continue
            if not (v.sb_void_reason or '').strip() and self.env.context.get('sb_void_reason'):
                v.sudo().sb_void_reason = self.env.context['sb_void_reason']  # a flow that voids (payroll reset, year reopened)
            if not (v.sb_void_reason or '').strip():
                raise UserError(self.env._(
                    'Write why %s is voided in "Reason for voiding" (under the title), save, then click Void again.',
                    v.name))
            if v.voucher_type in INVOICE_TYPES and v.move_id.payment_state not in ('not_paid', False):
                raise UserError(self.env._('%(doc)s has payments or credits applied (%(by)s). Void those first.',
                                           doc=v.name, by=v._sb_applied_by()))
            v._check_cheques_open()
            v._check_not_bank_reconciled()
            void_date, void_note = v._sb_void_date()  # first open day after the books and the VAT returns are closed
            if v.voucher_type not in INVOICE_TYPES:
                # what it settled on other vouchers (a salary, a tax, an invoice) is open again
                v.move_id.line_ids.filtered(lambda l: l.matched_debit_ids or l.matched_credit_ids).remove_move_reconcile()
            v.move_id._sb_void(void_date, self.env._('Void of %s', v.name))
            v.cash_line_ids.filtered(lambda r: r.cheque_state == 'open').write({'cheque_state': 'none'})
            v.state = 'void'
            v.message_post(body=self.env._('Voided on %(date)s. Reason: %(reason)s', date=format_ethiopian(void_date),
                                           reason=v.sb_void_reason) + ('. ' + void_note if void_note else ''))
            if void_note:
                self.env.user._bus_send('simple_notification', {'type': 'info', 'title': self.env._('Voided'), 'message': void_note})
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
            'name': self.env._('Journal Entry'),
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

    def action_sb_pay_now(self):
        """Pay a posted bill (or receive a posted invoice): a payment (receipt) for the partner with this document
        ticked for its open amount. The user picks the bank and posts."""
        self.ensure_one()
        if self.state != 'posted' or self.currency_id.is_zero(self.amount_residual):
            raise UserError(self.env._('Nothing is left to pay on %s.', self.name))
        pay = self.voucher_type in ('bill', 'cash_purchase')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sb.voucher',
            'view_mode': 'form',
            'name': self.env._('Payment') if pay else self.env._('Receipt'),
            'context': {'default_voucher_type': 'payment' if pay else 'receipt', 'default_partner_id': self.partner_id.id,
                        'default_payer_type': 'vendor' if pay else 'customer',
                        'sb_apply_move_ids': self.move_id.ids},
        }

    @api.onchange('received_amount')
    def _onchange_received_auto_apply(self):
        """An amount received / paid typed with nothing ticked: it is applied to the open invoices (bills), oldest first,
        so 12,000 against one open 12,000 invoice settles it. The user can untick or change any row."""
        if self.voucher_type not in ('receipt', 'payment') or self.state != 'draft' or not self._sb_received_typed() \
                or any(self.apply_ids.mapped('pay')) or self.line_ids:
            return
        primary = 'debit' if self.voucher_type == 'receipt' else 'credit'
        rows = self.apply_ids.filtered(lambda a: a.direction == primary).sorted(
            lambda a: (a.line_id.date_maturity or a.line_id.date or fields.Date.today(), a.line_id.id))
        for ap in rows:
            if self.currency_id.compare_amounts(self.amount_unapplied, 0.0) <= 0:
                break
            ap.pay = True
            ap._onchange_pay()

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

    @api.model
    def sb_ledger_matches(self, text, limit=12):
        """The voucher lists' search, in the ledger: posted entries with no voucher behind them (Odoo 18's payments and
        receipts, such as CPV/10213) that match it, any date."""
        moves = self._sb_find_entries(text, fields.Date.to_date('1900-01-01'), fields.Date.to_date('2999-12-31'), limit=limit)
        return [{'id': m.id, 'name': m.name, 'date': m.date.strftime('%d %b %Y'),
                 'amount': '{:,.2f}'.format(m.amount_total)} for m in moves]

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
            'menu_actions': self._sb_menu_actions(),
        }

    @api.model
    def _sb_menu_actions(self):
        """{'known': [...], 'visible': [...]}: the screens (window actions, by external id) the menus open, and those this
        user's menus show. A shortcut to a screen the user has no menu for (a clerk's Journal Voucher) is not offered."""
        Menu = self.env['ir.ui.menu'].sudo()
        visible_ids = set(self.env['ir.ui.menu']._visible_menu_ids())
        known, visible = set(), set()
        for menu in Menu.search([('action', '!=', False)]):
            action = menu.action
            if not action or action._name != 'ir.actions.act_window':
                continue
            xmlid = action.get_external_id().get(action.id)
            if not xmlid:
                continue
            known.add(xmlid)
            if menu.id in visible_ids:
                visible.add(xmlid)
        return {'known': sorted(known), 'visible': sorted(visible)}


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
        self.tax_ids = self._sb_fiscal_position().map_tax(voucher._sb_default_tax())

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
                vals['tax_ids'] = [Command.set(voucher._sb_default_tax().ids)]
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
        records = super().create(vals_list)
        records._sb_fill_ticked()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get('pay') and not vals.get('amount_applied'):
            self._sb_fill_ticked()
        return res

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
            # one label for what the item is, whichever system made it: "Sales Invoice", never Odoo's "Customer Invoice"
            ap.item_type = dict(VOUCHER_TYPES).get(MOVE_TO_VOUCHER.get(move.move_type)) \
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
        """Apply ticked: the open amount, or what is left of the amount received / paid when one was typed."""
        if not self.pay:
            self.amount_applied = 0.0
            return
        self.amount_applied = self.amount_residual - self.discount_currency
        voucher = self.voucher_id
        primary = 'debit' if voucher.voucher_type == 'receipt' else 'credit'
        if not voucher._sb_received_typed() or self.direction != primary:
            return
        for _pass in range(2):  # the withholding moves with the amount applied
            short = -voucher.amount_unapplied
            if voucher.currency_id.compare_amounts(short, 0.0) <= 0:
                break
            self.amount_applied = self.item_currency_id.round(max(self.amount_applied - short / (self.rate or 1.0), 0.0))
        self.pay = bool(self.amount_applied)

    def _sb_fill_ticked(self):
        """Rows ticked without an amount (a client that saves before the onchange answers): the open amount."""
        for ap in self.filtered(lambda a: a.pay and not a.amount_applied and a.voucher_id.state == 'draft'):
            ap.amount_applied = ap.amount_residual - ap.discount_currency

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
