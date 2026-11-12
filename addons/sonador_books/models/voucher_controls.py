"""Receivables, payables and VAT controls on the vouchers.

- Discounts / write-offs on receipts and payments: Clerks cannot give them, Accountants up to the company limit per voucher,
  Managers any amount; a reason is always required and goes to the chatter.
- A filed VAT period (the tax lock date) is closed to every VAT document (invoices, cash sales, bills, credit and debit
  notes, with or without VAT lines): nothing is posted, edited or voided in it. A void of such a document is dated on the
  first open day after the books and the VAT returns are closed (today when later).
- A credit / debit note against a document is for the same customer / vendor, in its currency, and the notes against it
  never credit more than its total.
- A company not registered for VAT charges no output VAT.
- A journal voucher that lowers a customer's / vendor's balance (not a reclass between the same partner's accounts of one
  side, not money) is a write-off under the same rule; an entry in Odoo's own screen may not do it at all, nor touch a
  sales or VAT account in a filed VAT period (neither may a journal voucher).
- Sales documents book to sales accounts only (or the item's); debit notes to what bills book to. Credit notes carry a
  credit-note register number; a withholding receipt number is used once per side; amounts applied are never negative;
  Odoo's own Register Payment is for system administrators only.
"""
import re
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date

from .ethiopian_calendar import format_ethiopian
from .voucher import INVOICE_TYPES, ITEM_TYPES, SALE_SIDE, SETTLEMENT_TYPES

# VAT documents: whatever they carry, they are part of the VAT return of their month
VAT_DOCS = ITEM_TYPES
# withholding within this of the amount worked out is rounding (customers round to the birr / shilling)
WHT_TOLERANCE = 1.0
# a reason for a write-off or for withholding less is a sentence, not "x"
REASON_MIN = 10
RETURN_ORIGINS = {'sale_return': ('sale', 'cash_sale'), 'purchase_return': ('bill', 'cash_purchase')}


def _amount(value):
    return '{:,.2f}'.format(value)


# accounts a sales document's lines may use: sales and contra-sales (returns, discounts allowed) accounts
SALE_ACCOUNT_TYPES = ('income',)
# accounts a purchase return / debit note may credit: what bills are booked to (cost, expense, stock, assets)
PURCHASE_ACCOUNT_TYPES = ('expense', 'expense_direct_cost', 'expense_depreciation', 'asset_current', 'asset_fixed',
                          'asset_non_current', 'asset_prepayments')


# the Year-End Wizard posting the closing entry (it closes the sales accounts, whatever VAT months are filed)
CLOSING_POST = object()
# the Finance Charges wizard's invoices (interest on late payment, to other income): a Python object, not claimable over RPC
FINANCE_CHARGE = object()


def _money_accounts(company):
    """Bank, cash and cheque accounts: money, not a write-off, when it is the other side of a partner line."""
    return (company.sb_cheque_on_hand_account_id | company.sb_cheque_outstanding_account_id).ids


def partner_writeoff(rows, company):
    """(amount, [(partner, side, lowered)]) written off by a journal entry's lines: rows are (account, partner, balance).
    On the customer / vendor accounts (accounts with a side), per partner and side, what the entry lowers (a customer owes
    less: credits; we owe a vendor less: debits) is a write-off, except what money received / paid on the entry covers. A
    move between the same partner's accounts of one side (AR to AR, advance to trade) nets to nothing: a reclass. Raising
    a balance (a charge, a bounced cheque) is not a write-off."""
    cur = company.currency_id
    nets = {}
    money = {'dr': 0.0, 'cr': 0.0}
    money_ids = set(_money_accounts(company))
    for account, partner, balance in rows:
        side = account.sb_partner_side
        if side:
            key = (partner.commercial_partner_id, side)
            nets[key] = nets.get(key, 0.0) + balance
        elif account.account_type in ('asset_cash', 'liability_credit_card') or account.id in money_ids:
            money['dr' if balance > 0 else 'cr'] += abs(balance)
    lowered = {'customer': 0.0, 'vendor': 0.0}
    detail = []
    for (partner, side), net in nets.items():
        low = -net if side == 'customer' else net
        if cur.compare_amounts(low, 0.0) > 0:
            lowered[side] += low
            detail.append((partner, side, cur.round(low)))
    total = max(lowered['customer'] - money['dr'], 0.0) + max(lowered['vendor'] - money['cr'], 0.0)
    return cur.round(total), detail


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_discount_reason = fields.Char(
        'Reason for discount / write-off', copy=False, tracking=True,
        help='Required whenever a discount or write-off is given on a receipt or payment: who agreed it and why.')

    # ------------------------------------------------------------------ discounts / write-offs
    def _sb_discount_rows(self):
        cur = self.currency_id
        return self.apply_ids.filtered(lambda a: not cur.is_zero(a.discount))

    def _sb_negative_lines(self):
        """GL lines with a negative amount on a receipt / payment: they lower the money received (paid) against the items
        applied, so they are a write-off whatever account they go to."""
        cur = self.currency_id
        if self.voucher_type not in ('receipt', 'payment'):
            return self.env['sb.voucher.line']
        return self.line_ids.filtered(lambda l: cur.compare_amounts(l.amount, 0.0) < 0)

    def _sb_wht_expected(self):
        """(due, expected, cap) for the withholding on this voucher: due = what the rule requires (over the threshold);
        expected = what the screen works out once withholding is ticked; cap = the highest legal rate (individuals,
        30%) on the whole base: nobody withholds more."""
        cur = self.currency_id
        if self.voucher_type not in SETTLEMENT_TYPES:
            return 0.0, 0.0, 0.0
        rate = self._sb_wht_rule()[0]
        over, whole = self._sb_wht_split()[:2]
        co = self.company_id
        top = max(rate, co.sb_wht_rate or 0.0, co.sb_wht_individual_rate or 0.0)
        return cur.round(over * rate / 100.0), cur.round((over or whole) * rate / 100.0), cur.round(whole * top / 100.0)

    def _sb_wht_excess(self):
        """Withholding taken above what the rule works out (beyond rounding): money the customer did not pay (or the
        vendor is not paid) booked to the tax account: a write-off as much as a discount is."""
        if not self.withholding or self.voucher_type not in SETTLEMENT_TYPES:
            return 0.0
        excess = self.wht_amount - self._sb_wht_expected()[1]
        return excess if excess > WHT_TOLERANCE else 0.0

    def _sb_jv_writeoff(self):
        """(amount, [(partner, side, lowered)]) a journal voucher writes off customer / vendor balances (partner_writeoff)."""
        if self.voucher_type != 'journal':
            return 0.0, []
        return partner_writeoff([(l.account_id, l.partner_id or self.partner_id, (l.debit or 0.0) - (l.credit or 0.0))
                                 for l in self.line_ids.filtered('account_id')], self.company_id)

    def _sb_writeoff_parts(self):
        """(discounts on the items, negative GL lines or the balances a journal voucher writes off, withholding over the
        rule): together the voucher's write-off."""
        return (sum(self._sb_discount_rows().mapped('discount')),
                -sum(self._sb_negative_lines().mapped('amount')) + self._sb_jv_writeoff()[0], self._sb_wht_excess())

    def _sb_writeoff_partners(self):
        partner = self.partner_id.commercial_partner_id
        if partner:
            return partner
        return self.env['res.partner'].union(*[p for p, _side, _low in self._sb_jv_writeoff()[1]])

    def _sb_writeoff_before(self, domain):
        """Write-offs already posted on the vouchers of this domain (vouchers posted before the write-off total was kept:
        their discounts)."""
        prior = self.sudo().search(domain + [('state', '=', 'posted'), ('id', '!=', self.id or 0),
                                             ('voucher_type', 'in', ('receipt', 'payment', 'cash_sale', 'cash_purchase', 'journal'))])
        return sum(max(v.sb_writeoff, v.amount_discount) for v in prior), prior

    def _sb_check_discount(self):
        """Who may give a discount or write off a balance, and how much (server side: the screen hides it from clerks).
        A write-off is a discount on an item, a negative GL line on a receipt / payment, or withholding above the rule.
        The Accountant's limit counts per voucher, per open item (all the vouchers that settle it) and per customer /
        vendor per day, so it cannot be split over several vouchers."""
        cur = self.currency_id
        rows = self._sb_discount_rows()
        if any(cur.compare_amounts(r.discount, 0.0) < 0 for r in rows):
            raise ValidationError(self.env._('A discount / write-off cannot be negative (%s).',
                                             ', '.join(rows.filtered(lambda r: r.discount < 0).mapped('document'))))
        disc, neg, excess = self._sb_writeoff_parts()
        total = cur.round(disc + neg + excess)
        if cur.is_zero(total):
            return
        what = []
        if not cur.is_zero(disc):
            what.append(self.env._('discounts %s', _amount(disc)))
        if not cur.is_zero(neg):
            what.append(self.env._('customer / vendor balances lowered by the journal %s', _amount(neg))
                        if self.voucher_type == 'journal' else self.env._('negative GL lines %s', _amount(neg)))
        if not cur.is_zero(excess):
            what.append(self.env._('withholding %s above the rule', _amount(excess)))
        what = ', '.join(what)
        user = self.env.user
        if not self.env.su and not user.has_group('sonador_books.group_sb_manager'):
            if not user.has_group('sonador_books.group_sb_accountant'):
                raise UserError(self.env._(
                    'Only an Accountant or a Manager can give a discount or write off a balance (%(amt)s on this voucher: '
                    '%(what)s). Apply what was received or paid; the rest stays open.', amt=_amount(total), what=what))
            limit = self.company_id.sb_discount_limit
            if cur.compare_amounts(total, limit) > 0:
                raise UserError(self.env._(
                    'Discounts / write-offs on this voucher come to %(amt)s (%(what)s), more than the %(limit)s an Accountant '
                    'may give (Settings). A Manager posts it.', amt=_amount(total), what=what, limit=_amount(limit)))
            for ap in self.apply_ids.filtered(lambda a: a.amount_applied or a.discount):
                before, prior = self._sb_writeoff_before([('apply_ids.line_id', '=', ap.line_id.id)])
                if cur.compare_amounts(before + total, limit) > 0:
                    raise UserError(self.env._(
                        '%(doc)s already has %(before)s written off (%(vouchers)s): with %(amt)s more it comes to more than the '
                        '%(limit)s an Accountant may write off on one item. A Manager posts it.', doc=ap.document,
                        before=_amount(before), vouchers=', '.join(prior.mapped('name')), amt=_amount(total), limit=_amount(limit)))
            for partner in self._sb_writeoff_partners():
                before, prior = self._sb_writeoff_before(['|', ('partner_id', 'child_of', partner.id),
                                                          ('line_ids.partner_id', 'child_of', partner.id), ('date', '=', self.date)])
                if cur.compare_amounts(before + total, limit) > 0:
                    raise UserError(self.env._(
                        '%(partner)s already has %(before)s written off on %(date)s (%(vouchers)s): with %(amt)s more it comes to '
                        'more than the %(limit)s an Accountant may write off per customer / vendor per day. A Manager posts it.',
                        partner=partner.display_name, before=_amount(before), date=self.date,
                        vouchers=', '.join(prior.mapped('name')), amt=_amount(total), limit=_amount(limit)))
        if len((self.sb_discount_reason or '').strip()) < REASON_MIN:
            raise ValidationError(self.env._(
                'Give the reason for the discount / write-off of %(amt)s (%(what)s): who agreed it and why, at least '
                '%(n)s characters.', amt=_amount(total), what=what, n=REASON_MIN))

    def _sb_note_discount(self):
        disc, neg, excess = self._sb_writeoff_parts()
        cur = self.currency_id
        total = cur.round(disc + neg + excess)
        if cur.is_zero(total):
            return
        detail = ['%s %s' % (r.document, _amount(r.discount)) for r in self._sb_discount_rows()]
        detail += ['%s %s' % (l.account_id.display_name, _amount(-l.amount)) for l in self._sb_negative_lines()]
        detail += ['%s (%s) %s' % (p.display_name, self.env._('customer') if side == 'customer' else self.env._('vendor'),
                                   _amount(low)) for p, side, low in self._sb_jv_writeoff()[1]]
        if not cur.is_zero(excess):
            detail.append(self.env._('withholding above the rule %s', _amount(excess)))
        self.message_post(body=self.env._(
            'Discount / write-off %(amt)s (%(detail)s) given by %(user)s. Reason: %(reason)s',
            amt=_amount(total), detail=', '.join(detail), user=self.env.user.name, reason=self.sb_discount_reason))

    # ------------------------------------------------------------------ withholding
    def _sb_wht_check_due(self):
        res = super()._sb_wht_check_due()
        self._sb_check_withholding()
        return res

    def _sb_check_withholding(self):
        """Withholding on both sides: never negative nor above the highest legal rate of the base; a customer's
        withholding needs the receipt (certificate) number it gave; less than the rule works out (a supplier not withheld,
        a customer that withheld less) only by an Accountant with a reason, which is logged. More than the rule counts as a
        write-off (_sb_check_discount)."""
        if self.voucher_type not in SETTLEMENT_TYPES:
            return
        cur = self.currency_id
        taken = self.wht_amount if self.withholding else 0.0
        if cur.compare_amounts(taken, 0.0) < 0:
            raise ValidationError(self.env._('The withholding amount cannot be negative.'))
        due, expected, cap = self._sb_wht_expected()
        money_in = self.voucher_type in ('receipt', 'cash_sale')
        if cur.compare_amounts(taken, 0.0) > 0:
            if cur.compare_amounts(taken, cap + WHT_TOLERANCE) > 0:
                raise ValidationError(self.env._(
                    'Withholding of %(taken)s is more than the highest legal rate allows on a base of %(base)s (at most '
                    '%(cap)s). Enter what the withholding receipt says; anything else is not withholding.',
                    taken=_amount(taken), base=_amount(self._sb_wht_split()[1]), cap=_amount(cap)))
            if money_in and not (self.wht_certificate_no or '').strip():
                raise ValidationError(self.env._(
                    'Enter the number of the withholding receipt the customer gave for the %s withheld.', _amount(taken)))
        # a customer withholds or not (unticked: paid in full); a supplier is withheld what the rule says
        floor = (expected if self.withholding else 0.0) if money_in else due
        short = floor - taken
        if short <= WHT_TOLERANCE:
            return
        user = self.env.user
        if not self.env.su and not user.has_group('sonador_books.group_sb_accountant'):
            raise UserError(self.env._(
                'Withholding of %(floor)s is worked out on this voucher and %(taken)s is entered. Only an Accountant or a '
                'Manager can post less (with the exemption certificate or the reason).', floor=_amount(floor), taken=_amount(taken)))
        reason = (self.wht_exempt_reason or '').strip()
        if len(reason) < REASON_MIN:
            raise ValidationError(self.env._(
                'Withholding of %(floor)s is worked out and %(taken)s is entered: write the reason in "Reason not withheld" '
                '(the exemption certificate number, or why), at least %(n)s characters.',
                floor=_amount(floor), taken=_amount(taken), n=REASON_MIN))
        self.message_post(body=self.env._(
            'Withholding %(taken)s instead of %(floor)s (%(short)s less) posted by %(user)s. Reason: %(reason)s',
            taken=_amount(taken), floor=_amount(floor), short=_amount(short), user=user.name, reason=reason))

    # ------------------------------------------------------------------ whose items, which side
    sb_setoff = fields.Boolean(
        'Set off receivable against payable', copy=False, tracking=True,
        help='Accountant and up: settle what this customer / vendor owes us against what we owe them on one voucher '
             '(e.g. a customer receipt that also clears the same company\'s bill). Logged on the voucher.')

    def _sb_check_apply_parties(self):
        """A receipt / payment settles its own customer's / vendor's open items only, on the right side. Receivables
        against payables (set-off) only when "Set off" is ticked, by an Accountant, and logged."""
        if self.voucher_type not in ('receipt', 'payment') or getattr(self, 'payroll_run_id', False):
            return
        rows = self.apply_ids.filtered(lambda a: a.amount_applied or a.discount)
        if not rows:
            return
        me = self.partner_id.commercial_partner_id
        lines = rows.line_id.sudo()
        bad = lines.filtered(lambda l: l.partner_id.commercial_partner_id != me or l.company_id != self.company_id
                             or l.parent_state != 'posted'
                             or not (l.account_id.reconcile or l.account_id.account_type in ('asset_receivable', 'liability_payable')))
        if bad:
            raise ValidationError(self.env._(
                'This %(doc)s is for %(partner)s: it cannot settle %(items)s, which belong to %(other)s. Each customer\'s '
                '(vendor\'s) items are settled on their own receipt (payment).',
                doc=self.env._('receipt') if self.voucher_type == 'receipt' else self.env._('payment'),
                partner=me.display_name or self.env._('nobody'), items=', '.join(bad.move_id.mapped('name')),
                other=', '.join(bad.partner_id.commercial_partner_id.mapped('display_name')) or self.env._('no one')))
        sides = {self._sb_item_side(l) for l in lines}
        if {'customer', 'vendor'} <= sides:
            if not self.sb_setoff:
                raise ValidationError(self.env._(
                    'This voucher sets what %s owes as a customer against what is owed to it as a vendor. That is a set-off: '
                    'an Accountant ticks "Set off receivable against payable" (it is logged), or settle each side on its own '
                    'voucher.', me.display_name))
            self._sb_require('group_sb_accountant', self.env._('set off receivables against payables'))
            self.message_post(body=self.env._(
                'Set-off of receivables against payables of %(partner)s (%(items)s) by %(user)s.', partner=me.display_name,
                items=', '.join(rows.mapped('document')), user=self.env.user.name))

    def _sb_check_income_lines(self):
        """Sales are booked on a Cash Sale or a Sales Invoice (fiscal receipt number, VAT / TOT returns), never as a GL line
        of a receipt or payment."""
        if self.voucher_type not in ('receipt', 'payment') or self.env.su:
            return
        sales = self.line_ids.filtered(lambda l: l.account_id.account_type == 'income')
        if sales:
            raise ValidationError(self.env._(
                '%(accounts)s is a sales account: sales are entered as a Cash Sale (money received now) or a Sales Invoice, '
                'which carry the fiscal receipt number and go into the VAT and TOT returns (returns: a Sales Return). '
                'Use Other income for income that is not a sale.', accounts=', '.join(sales.account_id.mapped('display_name'))))

    @api.model
    def _sb_item_accounts(self, product):
        """The accounts an item books to (income, expense, stock), from the item or its category."""
        if not product:
            return self.env['account.account']
        try:
            accounts = product.product_tmpl_id.get_product_accounts()
        except Exception:  # noqa: BLE001 - an item without accounting set up books to none
            return self.env['account.account']
        return self.env['account.account'].union(*[a for a in accounts.values() if isinstance(a, models.BaseModel)
                                                   and a._name == 'account.account'])

    def _sb_check_document_accounts(self):
        """Sales documents (cash sales, invoices, credit notes) book to sales accounts only (sales, sales returns, discounts
        allowed: account type Income) or the item's own income account; a purchase return / debit note to what bills are
        booked to (the original bill's accounts or the item's, cost / expense / stock / asset accounts). Anything else on a
        credit note writes off a customer's balance (Dr expense / Cr customer), and on a sale it books money that is no sale
        under a fiscal receipt number, out of the VAT return: for every role (a write-off is a discount on a receipt, by an
        Accountant, with a reason)."""
        vtype = self.voucher_type
        if self.env.su or vtype not in SALE_SIDE + ('purchase_return',):
            return
        bad = self.env['sb.voucher.line']
        for line in self.line_ids.filtered('account_id'):
            acc = line.account_id
            item = self._sb_item_accounts(line.product_id)
            if vtype in SALE_SIDE:
                ok = acc.account_type in SALE_ACCOUNT_TYPES or acc in item.filtered(lambda a: a.account_type in (
                    'income', 'income_other')) or (vtype == 'sale' and acc.account_type == 'income_other'
                                                   and self.env.context.get('sb_finance_charge') is FINANCE_CHARGE)
            else:
                money = acc.account_type in ('asset_cash', 'liability_credit_card') or acc.id in _money_accounts(self.company_id)
                tax = acc in self._sb_tax_accounts()
                ok = (acc in item or acc in self.origin_id.line_ids.account_id or acc.account_type in PURCHASE_ACCOUNT_TYPES) \
                    and not acc.sb_partner_side and not money and not tax \
                    and acc not in (self.company_id.sb_wht_receivable_account_id | self.company_id.sb_wht_payable_account_id)
            if not ok:
                bad |= line
        if not bad:
            return
        doc = dict(self._fields['voucher_type']._description_selection(self.env)).get(vtype)
        if vtype in SALE_SIDE:
            raise ValidationError(self.env._(
                'A %(doc)s books to sales accounts only (sales, sales returns, discounts allowed) or the item\'s income '
                'account: %(accounts)s is not one. A balance written off is a discount / write-off on a Receipt (Accountant, '
                'with the reason); other income or money that is no sale goes on a Receipt or a Journal Voucher.',
                doc=doc, accounts=', '.join(bad.account_id.mapped('display_name'))))
        raise ValidationError(self.env._(
            'A %(doc)s books back to what the bill was booked to (cost, expense, stock or asset accounts, or the item\'s '
            'accounts): %(accounts)s is not one. A payable written off is a discount on a Payment (Accountant, with the '
            'reason).', doc=doc, accounts=', '.join(bad.account_id.mapped('display_name'))))

    @api.model
    def _sb_tax_accounts(self, company=None):
        """The VAT / tax accounts of the company's taxes."""
        company = company or self.env.company
        return self.env['account.tax.repartition.line'].sudo().search(
            [('company_id', '=', company.id), ('account_id', '!=', False)]).account_id

    # ------------------------------------------------------------------ VAT periods
    def _sb_vat_doc(self):
        """Part of a VAT return: sales and purchase documents always, other vouchers when a line carries a tax, and a
        journal voucher that touches a sales (income) or a VAT account: a sale or a VAT correction booked by journal is
        in the VAT period of its date as much as a sales document is."""
        self.ensure_one()
        if self.voucher_type in VAT_DOCS or bool(self.line_ids.tax_ids):
            return True
        if self.voucher_type == 'journal':
            if self.move_id.sb_closing or self.env.context.get('sb_closing_post') is CLOSING_POST:
                return False  # the year-end closing entry moves the year's result, it is no sale
            accounts = self.line_ids.account_id
            return bool(accounts.filtered(lambda a: a.account_type == 'income')
                        or accounts & self._sb_tax_accounts(self.company_id))
        return False

    def _sb_tax_lock(self):
        """The VAT returns filed through this date (the user's lock-date exceptions count), or None."""
        self.ensure_one()
        lock = self.company_id.user_tax_lock_date
        return lock if lock and lock > date.min else None

    def _sb_tax_locked(self, day):
        lock = self._sb_tax_lock()
        return lock if lock and day and day <= lock and self._sb_vat_doc() else None

    def _sb_void_date(self):
        """(date, note): the void is dated with the voucher when its period is open; otherwise on the first open day after
        both the books and the VAT returns are closed, or today when that is later."""
        self.ensure_one()
        journal = self.move_id.journal_id or self._journal()
        lock = self.company_id._get_user_fiscal_lock_date(journal)
        tax = self._sb_tax_lock() if self._sb_vat_doc() else None
        closed = max(lock, tax) if tax else lock
        if self.date > closed:
            return self.date, None
        day = max(closed + timedelta(days=1), fields.Date.context_today(self))
        if tax and tax >= lock:
            why = self.env._('the VAT returns are filed through %(lock)s (%(ec)s)', lock=tax, ec=format_ethiopian(tax))
        else:
            why = self.env._('the books are closed through %(lock)s (%(ec)s)', lock=format_date(self.env, lock), ec=format_ethiopian(lock))
        return day, self.env._('%(doc)s is dated %(date)s and %(why)s: the reversal is dated %(day)s (%(ec)s), in the first '
                               'open period.', doc=self.name, date=self.date, why=why, day=day, ec=format_ethiopian(day))

    # ------------------------------------------------------------------ credit / debit notes
    @api.model
    def _sb_note_room(self, company, partner, day, side, exclude_moves=None):
        """(invoiced, credited) for a customer (side 'customer') or vendor over the 12 months to day, in the company
        currency: sales invoices and cash sales (bills and cash purchases) against every credit (debit) note, whether
        entered here or in Odoo's own invoicing. A credit / debit note against no document is capped by what is left."""
        partner = partner.commercial_partner_id
        since = day - timedelta(days=365)
        docs, notes = (('sale', 'cash_sale'), 'sale_return') if side == 'customer' else (('bill', 'cash_purchase'), 'purchase_return')
        mdoc, mnote = ('out_invoice', 'out_refund') if side == 'customer' else ('in_invoice', 'in_refund')
        exclude = exclude_moves.ids if exclude_moves else []
        V = self.sudo()
        dom = [('company_id', '=', company.id), ('partner_id', 'child_of', partner.id), ('state', '=', 'posted'),
               ('date', '>=', since), ('date', '<=', day), ('move_id', 'not in', exclude)]

        def value(v):
            return abs(v.move_id.amount_total_signed) if v.move_id.is_invoice() else v.amount_total

        sold = V.search(dom + [('voucher_type', 'in', docs)])
        credited = V.search(dom + [('voucher_type', '=', notes)])
        mine = V.search([('company_id', '=', company.id), ('move_id', '!=', False), ('partner_id', 'child_of', partner.id)]).move_id
        moves = self.env['account.move'].sudo().search([
            ('company_id', '=', company.id), ('partner_id', 'child_of', partner.id), ('state', '=', 'posted'),
            ('move_type', 'in', (mdoc, mnote)), ('date', '>=', since), ('date', '<=', day),
            ('id', 'not in', mine.ids + exclude)])
        invoiced = sum(value(v) for v in sold) + sum(abs(m.amount_total_signed) for m in moves if m.move_type == mdoc)
        given = sum(value(v) for v in credited) + sum(abs(m.amount_total_signed) for m in moves if m.move_type == mnote)
        return invoiced, given

    def _sb_check_unlinked_note(self):
        """A credit / debit note against no document: Accountant and up, and no more than the customer (vendor) was
        invoiced (billed) in the last 12 months less the notes already given."""
        self._sb_require('group_sb_accountant', self.env._('issue a credit / debit note that is not against a document '
                                                          '(choose the invoice or bill in "Against")'))
        side = 'customer' if self.voucher_type == 'sale_return' else 'vendor'
        if not self.partner_id:
            return
        invoiced, given = self._sb_note_room(self.company_id, self.partner_id, self.date, side, self.move_id)
        cur = self.currency_id
        amount = self.amount_total
        if (self.doc_currency_id or cur) != cur:
            amount = self.doc_currency_id._convert(amount, cur, self.company_id, self.date)
        left = cur.round(invoiced - given)
        if cur.compare_amounts(amount, left) > 0:
            raise ValidationError(self.env._(
                '%(partner)s was %(what)s %(invoiced)s in the 12 months to %(date)s and %(given)s of notes were already given: '
                'a note against no document can be at most %(left)s, not %(amt)s. Choose the document it is against in '
                '"Against".', partner=self.partner_id.commercial_partner_id.display_name,
                what=self.env._('invoiced') if side == 'customer' else self.env._('billed'), invoiced=_amount(invoiced),
                date=self.date, given=_amount(given), left=_amount(max(left, 0.0)), amt=_amount(amount)))

    def _sb_check_return(self):
        origin = self.origin_id
        if self.voucher_type in RETURN_ORIGINS and not origin:
            return self._sb_check_unlinked_note()
        if self.voucher_type not in RETURN_ORIGINS or not origin:
            return
        cur = self.doc_currency_id or self.currency_id
        if origin.state != 'posted' or origin.voucher_type not in RETURN_ORIGINS[self.voucher_type]:
            raise ValidationError(self.env._('%s is not a posted document this return can be made against.', origin.display_name))
        if origin.partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            raise ValidationError(self.env._(
                '%(doc)s is for %(theirs)s, not %(mine)s: a return is made against the customer\'s / vendor\'s own document.',
                doc=origin.name, theirs=origin.partner_id.display_name, mine=self.partner_id.display_name or '-'))
        if (origin.doc_currency_id or origin.currency_id) != cur:
            raise ValidationError(self.env._('%(doc)s is in %(cur)s: make the return in the same currency.',
                                             doc=origin.name, cur=(origin.doc_currency_id or origin.currency_id).name))
        others = self.sudo().search([('origin_id', '=', origin.id), ('id', '!=', self.id), ('voucher_type', '=', self.voucher_type),
                                     '|', ('state', '=', 'posted'), ('sb_editing', '=', True)])
        before = sum(others.mapped('amount_total'))
        if cur.compare_amounts(before + self.amount_total, origin.amount_total) > 0:
            raise ValidationError(self.env._(
                '%(doc)s comes to %(total)s and %(before)s is already credited on it (%(notes)s): this return can be at most '
                '%(left)s, not %(amt)s.', doc=origin.name, total=_amount(origin.amount_total), before=_amount(before),
                notes=', '.join(others.mapped('name')) or self.env._('none'),
                left=_amount(max(origin.amount_total - before, 0.0)), amt=_amount(self.amount_total)))

    # ------------------------------------------------------------------ VAT registration
    def _sb_check_output_vat(self):
        if self.company_id.sb_vat_registered or self.voucher_type not in SALE_SIDE + ('receipt',):
            return
        charged = self.line_ids.filtered(lambda l: l.tax_ids.filtered(lambda t: t.type_tax_use == 'sale' and t.amount))
        if self.voucher_type == 'sale_return' and self.origin_id:
            # a credit note gives back VAT the original document did charge
            charged = charged.filtered(lambda l: l.tax_ids - self.origin_id.line_ids.tax_ids)
        if charged:
            raise ValidationError(self.env._(
                '%(company)s is not registered for VAT, so it charges no VAT: remove %(taxes)s from %(lines)s (Settings: '
                '"Registered for VAT").', company=self.company_id.name,
                taxes=', '.join(charged.tax_ids.filtered(lambda t: t.amount).mapped('name')),
                lines=', '.join(l.name or l.product_id.display_name or '?' for l in charged[:3])))

    # ------------------------------------------------------------------ credit note numbers
    def _sb_check_note_number(self):
        """A credit note carries a number of the company's credit-note register (series of Sales Returns, CN 0001), written
        as the register prints it (the series' digits, zero-padded) or as a bare number; an invoice / cash register number
        (FS…, CSI…) is refused on a credit note, and a credit-note number on an invoice or cash sale. The number is stored
        in the register's format, so CN1, CN 0001 and 1 are one note."""
        if self.env.su or self.voucher_type not in SALE_SIDE or not self.sb_fiscal_only or self.move_id.name not in (False, '/'):
            return
        ref = (self.sb_pad_number or '').strip()
        if not ref:
            return
        from .series import FISCAL_NUMBER, NUMBER_WORD, _ascii_text, _letters
        Series = self.env['sb.doc.series']
        notes = Series.with_context(active_test=False).sudo().search([('company_id', '=', self.company_id.id),
                                                                     ('doc_type', '=', 'sale_return')])
        note_regs = {(_letters(s.prefix), _letters(s.suffix)) for s in notes}
        text = NUMBER_WORD.sub(' ', _ascii_text(ref)).strip()
        found = list(FISCAL_NUMBER.finditer(text))
        if not found:
            return
        m = found[-1]
        head, tail = _letters(text[:m.start()]), _letters(text[m.end():])
        digits = re.sub(r'\D', '', m.group(0))
        number = int(digits)
        if self.voucher_type != 'sale_return':
            if head and (head, tail) in note_regs:
                raise ValidationError(self.env._(
                    '%(ref)s is a credit-note number: a %(doc)s carries the fiscal receipt or invoice number.', ref=ref,
                    doc=dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)))
            return
        series = self.sb_series_id if self.sb_series_id.doc_type == 'sale_return' else Series._for('sale_return', company=self.company_id)
        if not series:
            return
        example = series._format(1, self.date)
        if head and (head, tail) not in note_regs:
            raise ValidationError(self.env._(
                '%(ref)s is not a credit-note number: a credit note is numbered in the credit-note register (%(example)s), '
                'not with an invoice or fiscal receipt number.', ref=ref, example=example))
        width = max(series.padding or 0, len(str(number)))
        if head and len(digits) != width:
            raise ValidationError(self.env._(
                '%(ref)s is not written as the credit-note register prints it: type it as %(right)s (or the number alone).',
                ref=ref, right=series._format(number, self.date)))
        self.sb_pad_number = series._format(number, self.date)
        if self.sb_series_id != series:
            self.sb_series_id = series

    # ------------------------------------------------------------------ withholding receipt numbers
    def _sb_check_wht_certificate(self):
        """A withholding receipt number is used once per company and side: the receipts customers give us, and the ones
        we give suppliers (WH-555, wh 555 and WH-0555 are one receipt). A voided voucher's receipt can be entered again."""
        if self.voucher_type not in SETTLEMENT_TYPES or not self.withholding or not self.wht_amount:
            return
        from .move_guard import norm_name
        number = (self.wht_certificate_no or '').strip()
        key = norm_name(number)
        if not key:
            return
        types = ('receipt', 'cash_sale') if self.voucher_type in ('receipt', 'cash_sale') else ('payment', 'cash_purchase')
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, hashtext(%s))', (self.company_id.id, 'wht|%s|%s' % (types[0], key)))
        digits = re.findall(r'\d+', key)
        others = self.sudo().search([('company_id', '=', self.company_id.id), ('id', '!=', self.id or 0),
                                     ('voucher_type', 'in', types), ('withholding', '=', True),
                                     '|', ('state', '=', 'posted'), ('sb_editing', '=', True),
                                     ('wht_certificate_no', 'ilike', digits[-1].lstrip('0') or '0' if digits else number)])
        others = others.filtered(lambda v: norm_name(v.wht_certificate_no) == key)
        if others:
            raise ValidationError(self.env._(
                'Withholding receipt %(no)s is already entered on %(doc)s (%(partner)s). Each withholding receipt is entered '
                'once: check the number on the receipt.', no=number, doc=others[0].name,
                partner=others[0].partner_id.display_name or '-'))

    def write(self, vals):
        res = super().write(vals)
        if 'wht_certificate_no' in vals:
            for v in self.filtered(lambda x: x.state == 'posted'):
                v._sb_check_wht_certificate()
        return res

    # ------------------------------------------------------------------ hooks
    def _check_before_post(self):
        res = super()._check_before_post()
        lock = self._sb_tax_locked(self.date)
        if lock:
            raise ValidationError(self.env._(
                'The VAT returns are filed through %(lock)s (%(ec)s): a %(doc)s dated %(date)s cannot be posted in that period. '
                'Date it after %(lock)s; a correction goes into the next VAT return.',
                lock=format_date(self.env, lock), ec=format_ethiopian(lock), date=format_date(self.env, self.date),
                doc=dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)))
        if getattr(self, 'sb_editing', False) and self.move_id.state == 'posted':
            lock = self._sb_tax_locked(self.move_id.date)
            if lock:
                raise ValidationError(self.env._(
                    '%(doc)s is in the VAT return filed through %(lock)s: it is not changed. Discard the changes and void it '
                    '(the reversal goes into the next VAT period), then enter it again.', doc=self.name, lock=format_date(self.env, lock)))
        self._sb_check_apply_parties()
        self._sb_check_income_lines()
        self._sb_check_document_accounts()
        self._sb_check_discount()
        self._sb_check_return()
        self._sb_check_output_vat()
        self._sb_check_note_number()
        self._sb_check_wht_certificate()
        return res

    sb_writeoff = fields.Monetary('Written off', copy=False, readonly=True,
                                  help='Discounts, negative GL lines and withholding above the rule on this voucher when it was '
                                       'posted: what the Accountant\'s write-off limit counts per item and per day.')

    def action_post(self):
        todo = self.filtered(lambda v: v.state == 'draft')
        parts = {v.id: v._sb_writeoff_parts() for v in todo}
        res = super().action_post()
        for v in todo.filtered(lambda v: v.state == 'posted'):
            total = v.currency_id.round(sum(parts[v.id]))
            if not v.currency_id.is_zero(total):
                v.sb_writeoff = total
                v._sb_note_discount()
            elif v.sb_writeoff:
                v.sb_writeoff = 0.0
        return res

    def action_edit(self):
        for v in self.filtered(lambda x: x.state == 'posted'):
            lock = v._sb_tax_locked(v.move_id.date or v.date)
            if lock:
                raise UserError(self.env._(
                    '%(doc)s is in the VAT return filed through %(lock)s (%(ec)s): it cannot be edited. Void it (the '
                    'reversal goes into the next VAT period) and enter it again.', doc=v.name, lock=format_date(self.env, lock),
                    ec=format_ethiopian(lock)))
        return super().action_edit()


class SbVoucherApply(models.Model):
    _inherit = 'sb.voucher.apply'

    @api.model_create_multi
    def create(self, vals_list):
        self._sb_check_discount_rights(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._sb_check_discount_rights([vals])
        return super().write(vals)

    def _sb_check_discount_rights(self, vals_list):
        """Clerks do not type discounts / write-offs at all (posting checks the limit and the reason)."""
        if self.env.su or self.env.user.has_group('sonador_books.group_sb_accountant'):
            return
        if any(v.get('discount') for v in vals_list):
            raise UserError(self.env._('Only an Accountant or a Manager can give a discount or write off a balance.'))


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    @api.onchange('product_id')
    def _onchange_product(self):
        res = super()._onchange_product()
        voucher = self.voucher_id
        company = voucher.company_id or self.env.company
        vtype = self.voucher_type or voucher.voucher_type or self.env.context.get('default_voucher_type')
        if self.product_id and vtype in SALE_SIDE and not company.sb_vat_registered:
            # not registered for VAT: an item's own sales VAT is not charged
            self.tax_ids = self.tax_ids.filtered(lambda t: not (t.type_tax_use == 'sale' and t.amount))
        return res


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _compute_payment_state(self):
        """Odoo calls an invoice settled by a credit note plus a receipt (or a credit note refunded in cash) "Reversed": it is
        paid. Only a document settled by credit notes / its void alone stays "Reversed"."""
        res = super()._compute_payment_state()
        for move in self.filtered(lambda m: m.payment_state == 'reversed' and m.move_type in (
                'out_invoice', 'in_invoice', 'out_refund', 'in_refund')):
            if move._sb_settled_by_payment():
                move.payment_state = 'paid'
        return res

    def action_register_payment(self):
        _refuse_native_payment(self.env)
        return super().action_register_payment()

    # ------------------------------------------------------------------ Odoo's own invoices, bills and notes
    def _post(self, soft=True):
        self._sb_check_native_documents()
        self._sb_check_native_entries()
        return super()._post(soft)

    def _sb_check_native_entries(self):
        """Journal entries posted in Odoo's own screen (not a voucher, not one of the app's flows) meet the journal voucher's
        rules: nothing touching a sales (income) or VAT account dated in a filed VAT period, and no customer / vendor
        balance written off (partner_writeoff): that is a Journal Voucher's job, where the reason, the Accountant's limit
        and the log apply. Exchange differences (Odoo's own, on matching) are not write-offs."""
        from .move_guard import in_flow
        if in_flow(self.env):
            return
        Voucher = self.env['sb.voucher']
        entries = self.filtered(lambda m: m.move_type == 'entry' and not m.sb_closing)
        docs = entries._sb_documents() if entries else {}
        for move in entries.filtered(lambda m: m not in docs):
            company = move.company_id
            if move.journal_id in (company.currency_exchange_journal_id | company.tax_cash_basis_journal_id) \
                    or move.origin_payment_id or move.statement_line_id:
                continue
            lock = company.user_tax_lock_date
            accounts = move.line_ids.account_id
            if lock and lock > date.min and move.date and move.date <= lock and (
                    accounts.filtered(lambda a: a.account_type == 'income') or accounts & Voucher._sb_tax_accounts(company)):
                raise ValidationError(self.env._(
                    'The VAT returns are filed through %(lock)s (%(ec)s): %(entry)s touches sales or VAT accounts and is dated '
                    '%(date)s, in that period. Date it after %(lock)s; a correction goes into the next VAT return.',
                    lock=format_date(self.env, lock), ec=format_ethiopian(lock), entry=move.ref or move.name or '-',
                    date=format_date(self.env, move.date)))
            amount, detail = partner_writeoff([(l.account_id, l.partner_id, l.balance) for l in move.line_ids], company)
            if not company.currency_id.is_zero(amount):
                raise ValidationError(self.env._(
                    '%(entry)s lowers %(who)s by %(amt)s without money received or paid: a write-off. Enter it as a Journal '
                    'Voucher, with the reason (an Accountant up to the write-off limit, a Manager above it); it is logged. '
                    'Moving a balance between the same customer\'s (vendor\'s) accounts is not a write-off.',
                    entry=move.ref or move.name or '-', amt=_amount(amount),
                    who=', '.join('%s %s' % (p.display_name or '-', _amount(low)) for p, _s, low in detail)))

    def _sb_check_native_documents(self):
        """Invoices, bills and credit / debit notes entered in Odoo's own invoicing (not a voucher) meet the vouchers' rules
        when posted: no output VAT for a company not registered for VAT, nothing dated in a filed VAT period, and a credit
        / debit note never more than its document (or, against none, than the 12 months' invoicing less notes given)."""
        from .move_guard import in_flow
        if in_flow(self.env):
            return
        moves = self.filtered(lambda m: m.is_invoice(include_receipts=True))
        if not moves:
            return
        docs = moves._sb_documents()
        Voucher = self.env['sb.voucher']
        for move in moves.filtered(lambda m: m not in docs):
            company = move.company_id
            cur = company.currency_id
            sale = move.move_type in ('out_invoice', 'out_refund', 'out_receipt')
            if sale and not company.sb_vat_registered:
                vat = move.invoice_line_ids.tax_ids.filtered(lambda t: t.type_tax_use == 'sale' and t.amount > 0)
                if vat:
                    raise ValidationError(self.env._(
                        '%(company)s is not registered for VAT, so it charges no VAT: remove %(taxes)s from %(doc)s.',
                        company=company.name, taxes=', '.join(vat.mapped('name')), doc=move.display_name))
            products = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product' and l.account_id)
            if sale:
                # the vouchers' rule (_sb_check_document_accounts): sales documents book to sales accounts only
                bad = products.filtered(lambda l: l.account_id.account_type not in SALE_ACCOUNT_TYPES and l.account_id not in
                                        Voucher._sb_item_accounts(l.product_id))
                if bad:
                    raise ValidationError(self.env._(
                        '%(doc)s books to %(accounts)s: an invoice or credit note books to sales accounts only (sales, sales '
                        'returns, discounts allowed) or the item\'s income account. A balance written off is a discount on a '
                        'Receipt (Accountant, with the reason).', doc=move.display_name,
                        accounts=', '.join(bad.account_id.mapped('display_name'))))
            elif move.move_type == 'in_refund':
                bad = products.filtered(lambda l: l.account_id.account_type not in PURCHASE_ACCOUNT_TYPES
                                        and l.account_id not in Voucher._sb_item_accounts(l.product_id)
                                        and l.account_id not in move.reversed_entry_id.invoice_line_ids.account_id
                                        or l.account_id.sb_partner_side)
                if bad:
                    raise ValidationError(self.env._(
                        '%(doc)s books to %(accounts)s: a debit note books back to what the bill was booked to (cost, expense, '
                        'stock or asset accounts). A payable written off is a discount on a Payment (Accountant, with the '
                        'reason).', doc=move.display_name, accounts=', '.join(bad.account_id.mapped('display_name'))))
            lock = company.user_tax_lock_date
            day = move.invoice_date or move.date
            if lock and lock > date.min and day and (day <= lock or move.date <= lock):
                raise ValidationError(self.env._(
                    'The VAT returns are filed through %(lock)s (%(ec)s): %(doc)s dated %(date)s cannot be posted in that '
                    'period. Date it after %(lock)s; a correction goes into the next VAT return.', lock=format_date(self.env, lock),
                    ec=format_ethiopian(lock), doc=move.display_name, date=day))
            if move.move_type not in ('out_refund', 'in_refund'):
                continue
            side = 'customer' if move.move_type == 'out_refund' else 'vendor'
            amount = abs(move.amount_total_signed)
            origin = move.reversed_entry_id
            if origin:
                if origin.commercial_partner_id != move.commercial_partner_id:
                    raise ValidationError(self.env._('%(doc)s is for %(theirs)s, not %(mine)s.', doc=origin.display_name,
                                                     theirs=origin.commercial_partner_id.display_name,
                                                     mine=move.commercial_partner_id.display_name))
                before = sum(abs(m.amount_total_signed) for m in self.sudo().search([
                    ('reversed_entry_id', '=', origin.id), ('state', '=', 'posted'), ('id', '!=', move.id),
                    ('move_type', '=', move.move_type)]))
                if cur.compare_amounts(before + amount, abs(origin.amount_total_signed)) > 0:
                    raise ValidationError(self.env._(
                        '%(doc)s comes to %(total)s and %(before)s is already credited on it: %(note)s can be at most %(left)s.',
                        doc=origin.display_name, total=_amount(abs(origin.amount_total_signed)), before=_amount(before),
                        note=move.display_name, left=_amount(max(abs(origin.amount_total_signed) - before, 0.0))))
                continue
            Voucher._sb_require('group_sb_accountant', self.env._('post a credit / debit note that is not against a document'))
            invoiced, given = Voucher._sb_note_room(company, move.partner_id, move.date, side, move)
            left = cur.round(invoiced - given)
            if cur.compare_amounts(amount, left) > 0:
                raise ValidationError(self.env._(
                    '%(partner)s was invoiced / billed %(invoiced)s in the last 12 months and %(given)s of notes were already '
                    'given: %(note)s, against no document, can be at most %(left)s, not %(amt)s. Enter it as a Sales Return '
                    '(Purchase Return) against its document.', partner=move.commercial_partner_id.display_name,
                    invoiced=_amount(invoiced), given=_amount(given), note=move.display_name, left=_amount(max(left, 0.0)),
                    amt=_amount(amount)))


# ---------------------------------------------------------------------- Odoo's own Register Payment
def _refuse_native_payment(env):
    """Odoo's Register Payment (with its write-off, without withholding, outside the receipt / payment controls) is for
    system administrators only: everyone else receives and pays on the vouchers."""
    if env.su or env.user.has_group('base.group_system'):
        return
    raise UserError(env._('Payments are entered on a Receipt (Receive Payment) or a Payment (Pay Bills) voucher, which apply '
                          'withholding, discounts and their limits. Odoo\'s own Register Payment is not used.'))


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    @api.model
    def default_get(self, fields_list):
        _refuse_native_payment(self.env)
        return super().default_get(fields_list)

    @api.model_create_multi
    def create(self, vals_list):
        _refuse_native_payment(self.env)
        return super().create(vals_list)

    def action_create_payments(self):
        _refuse_native_payment(self.env)
        return super().action_create_payments()


# ---------------------------------------------------------------------- matching in a closed period
def _books_lock(company):
    """The date the books are closed through (everyone's lock and the permanent one)."""
    days = [d for d in (company.fiscalyear_lock_date, company.hard_lock_date) if d]
    return max(days) if days else None


class AccountPartialReconcile(models.Model):
    """Matching and unmatching never rewrite the as-at history of a closed period.
    - A match made today between items both dated in a closed period (Match Old Items, or by hand) counts from today in the
      as-at ageing (sb_as_of), not from the later item's date: the ageing at the closed date stays as it was reported.
    - A match that counts in a closed period is not removed: Accountants are refused; a Manager may, with a reason
      (context sb_unmatch_reason), which is written on both entries."""
    _inherit = 'account.partial.reconcile'

    sb_as_of = fields.Date('Counts from', readonly=True, copy=False, index=True,
                           help='Set when the match was made after the period of both items was closed: the as-at ageing '
                                'counts it from this day, so closed periods keep the ageing they were closed with.')

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        today = fields.Date.context_today(self)
        late = [p.id for p in partials if p.max_date and (lock := _books_lock(p.company_id)) and p.max_date <= lock]
        if late:
            self.env.cr.execute('UPDATE account_partial_reconcile SET sb_as_of = %s WHERE id IN %s', (today, tuple(late)))
            partials.invalidate_recordset(['sb_as_of'])
        return partials

    def _sb_counts_from(self):
        return self.sb_as_of or self.max_date

    def unlink(self):
        from .move_guard import in_flow
        if self and not in_flow(self.env):
            closed = self.sudo().filtered(lambda p: (lock := _books_lock(p.company_id)) and p._sb_counts_from()
                                          and p._sb_counts_from() <= lock)
            if closed:
                lock = _books_lock(closed[0].company_id)
                docs = ', '.join(sorted({'%s / %s' % (p.debit_move_id.move_id.name, p.credit_move_id.move_id.name) for p in closed}))
                reason = (self.env.context.get('sb_unmatch_reason') or '').strip()
                if not self.env.user.has_group('sonador_books.group_sb_manager'):
                    raise UserError(self.env._(
                        'The match of %(docs)s is part of the books closed through %(lock)s (%(ec)s): removing it would change '
                        'the ageing reported for that period. A Manager can remove it, with a reason.',
                        docs=docs, lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
                if len(reason) < REASON_MIN:
                    raise UserError(self.env._(
                        'The match of %(docs)s is in the books closed through %(lock)s: give the reason for removing it '
                        '(at least %(n)s characters).', docs=docs, lock=format_date(self.env, lock), n=REASON_MIN))
                author = self.env.user.partner_id.id
                for move in (closed.debit_move_id.move_id | closed.credit_move_id.move_id).sudo():
                    move.message_post(body=self.env._(
                        'Match in the closed period (through %(lock)s) removed by %(user)s: %(docs)s. Reason: %(reason)s',
                        lock=format_date(self.env, lock), user=self.env.user.name, docs=docs, reason=reason), author_id=author)
        return super().unlink()
