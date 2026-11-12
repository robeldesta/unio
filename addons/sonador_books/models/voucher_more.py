"""Voucher screen behaviour: editing a posted voucher without taking it off the books, sensible defaults on lines
(last price to the customer, the sales account the item is sold on), the journal voucher's balancing line, the petty
cash series and the purchase bill series."""
import json
import re

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare

from .voucher import INVOICE_TYPES, SALE_SIDE

# a voucher being edited keeps these as they were posted, to return to them on Discard
_SKIP = {'id', 'state', 'name', 'move_id', 'sb_editing', 'sb_edit_snapshot', 'sb_edit_uid', 'create_uid', 'create_date', 'write_uid',
         'write_date', 'voucher_id', 'display_name'}
_CHILDREN = ('line_ids', 'apply_ids', 'cash_line_ids')


def _record_vals(record):
    """Stored values a user can change, in write() form (JSON-safe)."""
    vals = {}
    for name, field in record._fields.items():
        if name in _SKIP or not field.store or field.type in ('one2many', 'binary') or field.related \
                or (field.compute and field.readonly) or name.startswith(('message_', 'activity_')):
            continue
        value = record[name]
        if field.type == 'many2one':
            vals[name] = value.id or False
        elif field.type == 'many2many':
            vals[name] = [Command.set(value.ids)]
        elif field.type in ('date', 'datetime'):
            vals[name] = field.to_string(value) if value else False
        else:
            vals[name] = value
    return vals


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    # once posted, the Apply tab shows what the voucher applied, not every item that was open at the time
    sb_applied_ids = fields.One2many('sb.voucher.apply', 'voucher_id', string='Applied',
                                     domain=['|', ('amount_applied', '!=', 0), ('discount', '!=', 0)])
    sb_editing = fields.Boolean('Being edited', copy=False, readonly=True, index=True,
                                help='A posted voucher reopened with Edit: its posted entry stays in the books, unchanged, until '
                                     'the changes are posted; Discard changes returns to it.')
    sb_edit_snapshot = fields.Text(copy=False, readonly=True)
    sb_edit_uid = fields.Many2one('res.users', 'Edit opened by', copy=False, readonly=True)
    sb_status = fields.Selection([('draft', 'Not posted'), ('editing', 'Being edited (still in the books)'), ('posted', 'Posted'),
                                  ('void', 'Void')], 'Status', compute='_compute_sb_status')

    sb_wht_supply_needed = fields.Boolean(compute='_compute_sb_wht_supply_needed')
    sb_wht_none = fields.Boolean(compute='_compute_sb_wht_none',
                                 help='Paying taxes, pension, salaries, a dividend or a loan: nothing bought, nothing withheld.')

    @api.depends('voucher_type', 'line_ids.account_id', 'line_ids.amount', 'apply_ids.amount_applied', 'apply_ids.line_id',
                 'received_amount')
    def _compute_sb_wht_none(self):
        """A payment that buys nothing (its lines settle liabilities: taxes, pension, salaries, dividends, loans; no bill
        applied, no advance to a supplier): no withholding, and nothing about it is asked."""
        for v in self:
            if v.voucher_type != 'payment':
                v.sb_wht_none = False
                continue
            supply = v.line_ids.filtered(lambda l: l.account_id and l.account_id.internal_group not in ('liability', 'equity')
                                         and l.account_id.account_type not in ('asset_receivable', 'asset_cash'))
            v.sb_wht_none = bool(v.line_ids or v.apply_ids) and not supply and not v._sb_wht_pays_supplies()

    @api.depends('withholding', 'wht_supply', 'apply_ids.amount_applied_company', 'apply_ids.line_id', 'line_ids.amount',
                 'line_ids.price_subtotal', 'line_ids.tax_ids', 'voucher_type', 'received_amount', 'state', 'partner_id')
    def _compute_sb_wht_supply_needed(self):
        """Goods or services must be chosen only when withholding can apply: a payment over the lower threshold (or paying
        an invoice over it), or withholding ticked. A 450 petty cash slip does not ask. Uganda: always (professional fees
        are withheld from the first shilling)."""
        for v in self:
            needed = bool(v.sb_wht_country and v.voucher_type in ('payment', 'cash_purchase') and v.state == 'draft'
                          and not v.sb_wht_none)
            if needed and v.sb_wht_country == 'ET' and not v.withholding and not v.wht_supply:
                needed = v._sb_wht_parts()[0] > 0  # with no choice yet the rule tests the lower (services) threshold
            v.sb_wht_supply_needed = needed

    @api.depends('state', 'sb_editing')
    def _compute_sb_status(self):
        for v in self:
            v.sb_status = 'editing' if v.sb_editing and v.state == 'draft' else v.state

    sb_search = fields.Char('Number, partner, reference, cheque or amount', compute='_compute_sb_search',
                            search='_search_sb_search')

    def _compute_sb_search(self):
        self.sb_search = False

    def _search_sb_search(self, operator, value):
        if operator not in ('ilike', 'like', '=', '=ilike') or not isinstance(value, str):
            return NotImplemented
        return self._sb_search_domain(value)

    @api.model
    def _sb_amount(self, text):
        """An amount as people type it: 25,500 / 25500.00 / Br 25,500 -> 25500.0, else None."""
        text = re.sub(r'(?i)\b(br|birr|etb)\b|\s', '', text or '').replace(',', '')
        try:
            return float(text) if re.fullmatch(r'-?\d+(\.\d+)?', text) else None
        except ValueError:
            return None

    @api.model
    def _sb_number_like(self, text):
        """'CPV/10213', 'CPV 10213' and 'cpv-10213' find each other: separators match anything."""
        return re.sub(r'[\s/\-_.]+', '%', text.strip())

    @api.model
    def _sb_search_domain(self, text):
        """What the voucher search (Enter) and Find Transactions match: number, partner, reference, description, the
        cheque / slip number of the bank rows, the withholding receipt number, and an amount typed as 25,500 or 25500.00."""
        text = (text or '').strip()
        if not text:
            return Domain.TRUE
        domains = [Domain('name', 'ilike', text), Domain('name', '=ilike', '%%%s%%' % self._sb_number_like(text)),
                   Domain('partner_id', 'ilike', text), Domain('reference', 'ilike', text), Domain('memo', 'ilike', text),
                   Domain('cash_line_ids.reference', 'ilike', text), Domain('wht_certificate_no', 'ilike', text),
                   Domain('line_ids.partner_id', 'ilike', text)]
        amount = self._sb_amount(text)
        if amount is not None:
            domains += [Domain('amount_total', '=', amount), Domain('amount_cash', '=', amount), Domain('total_debit', '=', amount)]
        return Domain.OR(domains)

    @api.model
    def _sb_find_entries(self, text, date_from, date_to, limit=2000):
        """Posted journal entries with no voucher behind them (payments and receipts brought over from Odoo 18 as entries,
        such as CPV/10213) matching the search: by number, reference, partner, line label (cheque numbers) or amount."""
        text = (text or '').strip()
        if not text:
            return self.env['account.move']
        domains = [Domain('name', 'ilike', text), Domain('name', '=ilike', '%%%s%%' % self._sb_number_like(text)),
                   Domain('ref', 'ilike', text), Domain('partner_id', 'ilike', text), Domain('line_ids.name', 'ilike', text),
                   Domain('line_ids.partner_id', 'ilike', text)]  # entries whose partner is on the lines only
        amount = self._sb_amount(text)
        if amount is not None:
            domains.append(Domain('amount_total', '=', amount))
        moves = self.env['account.move'].search(Domain.AND([
            [('company_id', '=', self.env.company.id), ('state', '=', 'posted'), ('date', '>=', date_from), ('date', '<=', date_to)],
            Domain.OR(domains)]), order='date desc, id desc', limit=limit)
        self.env.cr.execute('SELECT move_id FROM sb_voucher WHERE move_id = ANY(%s)', (moves.ids,))
        with_voucher = {r[0] for r in self.env.cr.fetchall()}
        return moves.filtered(lambda m: m.id not in with_voucher)

    def _sb_applied_by(self):
        """Names of the receipts, payments and credit notes applied to this invoice or bill (CRV 1080, CN 0003)."""
        self.ensure_one()
        lines = self.move_id.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))
        moves = (lines.matched_debit_ids.debit_move_id | lines.matched_credit_ids.credit_move_id).move_id - self.move_id
        vouchers = self.sudo().search([('move_id', 'in', moves.ids)])
        return ', '.join(vouchers.mapped('name') + (moves - vouchers.move_id).mapped('name'))

    # ------------------------------------------------------------------ edit without leaving the books
    def action_edit(self):
        """Reopen a posted voucher for changes. Its entry stays posted, in every report, until the edited voucher is posted
        again (the old entry is then rebuilt in the same step, same number); Discard changes returns to the posted voucher.
        Stock vouchers are handled by the inventory app (void and corrected copy)."""
        self._sb_require('group_sb_accountant', self.env._('edit posted vouchers'))
        if self.filtered('sb_migrated'):
            raise UserError(self.env._('%s was brought over from the previous system: void it and enter a new voucher.',
                                       ', '.join(self.filtered('sb_migrated').mapped('name'))))
        for v in self.filtered(lambda x: x.state == 'posted'):
            # payments applied to it, or its own lines settled by other vouchers: they are unlinked when the changes post
            # and linked again to the rebuilt entry (_sb_relink); nothing to undo first
            v._check_cheques_open()
            v._check_not_bank_reconciled()
            snapshot = {'voucher': _record_vals(v)}
            for child in _CHILDREN:
                snapshot[child] = [_record_vals(r) for r in v[child]]
            v.write({'state': 'draft', 'sb_editing': True, 'sb_edit_snapshot': json.dumps(snapshot, default=str),
                     'sb_edit_uid': self.env.user.id})
            v.message_post(body=self.env._('Opened for editing by %s. The posted entry stays in the books until the changes are '
                                           'posted.', self.env.user.name))
        return True

    def action_discard_edit(self):
        """Back to the voucher as it was posted (its entry was never changed)."""
        for v in self.filtered('sb_editing'):
            snapshot = json.loads(v.sb_edit_snapshot or '{}')
            vals = dict(snapshot.get('voucher') or {})
            for child in _CHILDREN:
                vals[child] = [Command.clear()] + [Command.create(r) for r in snapshot.get(child) or []]
            vals.update(state='posted', sb_editing=False, sb_edit_snapshot=False)
            opened_by = v.sb_edit_uid.name or '?'
            v.write(vals)
            v.message_post(body=self.env._('Changes discarded by %(by)s (opened for editing by %(opened)s): back to the posted '
                                           'voucher.', by=self.env.user.name, opened=opened_by))
        return True

    def _sb_unpost_for_edit(self):
        """Just before an edited voucher posts again: its old entry goes back to draft, to be rebuilt with the same number.
        Posting runs in one transaction, so if anything refuses the post the old entry stays posted."""
        from .move_guard import controlled
        editing = self.filtered(lambda x: x.sb_editing and x.state == 'draft')
        links = {}
        for v in editing.filtered(lambda x: x.move_id.state == 'posted'):
            flow = controlled(v)
            links[v.id] = v._sb_links_elsewhere()
            flow.move_id.line_ids.filtered(lambda l: l.matched_debit_ids or l.matched_credit_ids).remove_move_reconcile()
            flow.move_id.button_draft()
            v.cash_line_ids.filtered(lambda r: r.cheque_state == 'open').write({'cheque_state': 'none'})
        # from here the items it settled are open again: the apply lines show their real balance
        for v in editing:
            changes = v._sb_edit_changes()
            body = self.env._('Edited voucher posted by %(by)s (opened for editing by %(opened)s): the entry is '
                              'rebuilt with the changes.', by=self.env.user.name, opened=v.sb_edit_uid.name or '?')
            if changes:
                body = Markup('%s<ul>%s</ul>') % (body, Markup('').join(Markup('<li>%s</li>') % c for c in changes))
            v.message_post(body=body)
        editing.write({'sb_editing': False, 'sb_edit_snapshot': False})
        return links

    def _sb_links_elsewhere(self):
        """The settlements between this voucher's entry and other vouchers, apart from what it applies itself (those are
        applied again from its Apply lines): payments on an invoice, a tax or salary payment settling this journal's
        lines, an advance used later. [(account, partner, label, the other journal item, amount)]."""
        self.ensure_one()
        own = self.apply_ids.line_id
        out = []
        for line in self.move_id.line_ids:
            for part in line.matched_debit_ids | line.matched_credit_ids:
                other = part.debit_move_id if part.credit_move_id == line else part.credit_move_id
                if other in own or other.move_id == self.move_id:
                    continue
                out.append((line.account_id.id, line.partner_id.commercial_partner_id.id, line.name, other.id, part.amount))
        return out

    def _sb_relink(self, links):
        """After an edited voucher posts again: its new entry settles the same items of other vouchers as before, as far
        as both are still open (an amount made smaller settles less)."""
        self.ensure_one()
        AML = self.env['account.move.line']
        done = []
        for account_id, partner_id, label, other_id, _amount in links:
            other = AML.browse(other_id).exists()
            if not other or other.reconciled or other.parent_state != 'posted':
                continue
            mine = self.move_id.line_ids.filtered(lambda l: l.account_id.id == account_id and not l.reconciled
                                                  and l.partner_id.commercial_partner_id.id == partner_id
                                                  and (l.balance > 0) != (other.balance > 0))
            mine = mine.filtered(lambda l: l.name == label)[:1] or mine[:1]
            if mine:
                (mine | other).reconcile()
                done.append(other.move_id.name)
        if done:
            self.message_post(body=self.env._('Linked again to %s, as before the edit.', ', '.join(sorted(set(done)))))

    sb_fiscal_hint = fields.Char(compute='_compute_sb_fiscal_hint')
    sb_fiscal_bad = fields.Boolean(compute='_compute_sb_fiscal_hint')

    @api.depends('sb_pad_number', 'voucher_type', 'company_id', 'sb_fiscal_only', 'state')
    def _compute_sb_fiscal_hint(self):
        """Under the fiscal receipt number, before posting: the company's own format (the last receipt recorded, e.g.
        FS00001954), or what is wrong with the number typed (letters of another alphabet), at once."""
        from .series import NUMBER_WORD
        import unicodedata
        last = {}
        for v in self:
            v.sb_fiscal_hint, v.sb_fiscal_bad = False, False
            if not v.sb_fiscal_only or v.state not in ('draft', False):
                continue
            text = NUMBER_WORD.sub(' ', unicodedata.normalize('NFKC', str(v.sb_pad_number or '')))
            odd = sorted({c for c in text if ord(c) > 127})
            if odd:
                v.sb_fiscal_bad = True
                v.sb_fiscal_hint = self.env._('Type it with Latin letters and digits, as the register prints it: %s is not.',
                                              ', '.join('"%s"' % c for c in odd))
                continue
            company = v.company_id or self.env.company
            if company.id not in last:
                prev = self.sudo().search([('company_id', '=', company.id), ('voucher_type', 'in', ('cash_sale', 'sale')),
                                           ('state', '=', 'posted'), ('name', '=like', 'FS%')], order='date desc, id desc', limit=1)
                m = re.match(r'^[A-Za-z]{1,4}\s?\d+', prev.name or '')
                last[company.id] = m.group(0) if m else 'FS00001954'
            if not v.sb_pad_number:
                v.sb_fiscal_hint = self.env._('As printed on the receipt, e.g. %s', last[company.id])

    def _sb_edit_changes(self):
        """What an edit changed, old -> new, from the snapshot taken when it was opened: header fields, then each line
        ("Line 1 (6000-0011 Printing): Amount 5,000.00 -> 6,000.00"), lines added and removed."""
        self.ensure_one()
        snapshot = json.loads(self.sb_edit_snapshot or '{}')
        if not snapshot:
            return []
        quiet = {'state', 'sb_editing', 'sb_edit_snapshot', 'sb_edit_uid', 'sequence', 'name', 'move_id', 'voucher_id',
                 'company_id', 'currency_id', 'auto_account', 'sb_series_id'}

        def show(rec, fname, value):
            field = rec._fields[fname]
            if field.type == 'many2one':
                return self.env[field.comodel_name].browse(value).exists().display_name if value else '-'
            if field.type == 'many2many':
                ids = value[0][2] if value and isinstance(value[0], (list, tuple)) else (value or [])
                return ', '.join(self.env[field.comodel_name].browse(ids).exists().mapped('display_name')) or '-'
            if field.type in ('float', 'monetary'):
                return '{:,.2f}'.format(value or 0.0)
            if field.type == 'selection':
                return dict(field._description_selection(self.env)).get(value, value) or '-'
            if field.type == 'boolean':
                return self.env._('yes') if value else self.env._('no')
            return value or '-'

        def diff(rec, old):
            new = _record_vals(rec)
            out = []
            for fname, before in old.items():
                if fname in quiet or fname not in rec._fields or fname not in new:
                    continue
                after = new[fname]
                field = rec._fields[fname]
                if field.type in ('float', 'monetary'):
                    if abs((before or 0.0) - (after or 0.0)) < 0.005:
                        continue
                elif field.type == 'many2many':
                    if show(rec, fname, before) == show(rec, fname, after):
                        continue
                elif (before or False) == (after or False):
                    continue
                out.append('%s %s -> %s' % (field.string, show(rec, fname, before), show(rec, fname, after)))
            return out

        def label(rec, vals):
            account = vals.get('account_id') and self.env['account.account'].browse(vals['account_id']).exists()
            product = vals.get('product_id') and self.env['product.product'].browse(vals['product_id']).exists()
            return (product and product.display_name) or (account and account.display_name) or vals.get('name') or ''

        changes = diff(self, snapshot.get('voucher') or {})
        for child, title in (('line_ids', self.env._('Line')), ('cash_line_ids', self.env._('Paid / received'))):
            olds, recs = snapshot.get(child) or [], self[child]
            for i in range(max(len(olds), len(recs))):
                if i >= len(recs):
                    changes.append(self.env._('%(t)s %(n)s removed (%(what)s)', t=title, n=i + 1, what=label(self, olds[i])))
                elif i >= len(olds):
                    changes.append(self.env._('%(t)s %(n)s added (%(what)s)', t=title, n=i + 1,
                                              what=label(self, _record_vals(recs[i]))))
                else:
                    moved = diff(recs[i], olds[i])
                    if moved:
                        changes.append('%s %s (%s): %s' % (title, i + 1, label(self, olds[i]), '; '.join(moved)))
        return changes

    def action_post(self):
        links = self._sb_unpost_for_edit()
        res = super().action_post()
        for v in self.filtered(lambda x: links.get(x.id)):
            v._sb_relink(links[v.id])
        return res

    def unlink(self):
        if self.filtered('sb_editing'):
            raise UserError(self.env._('A voucher being edited is still posted: post the changes or discard them.'))
        return super().unlink()

    # ------------------------------------------------------------------ checks before posting
    def _check_before_post(self):
        cur = self.currency_id
        if self.voucher_type == 'journal' and self.line_ids and \
                float_compare(self.total_debit, self.total_credit, precision_rounding=cur.rounding):
            raise ValidationError(self.env._(
                'Out of balance: debits %(d)s, credits %(c)s (difference %(diff)s).',
                d=cur.format(self.total_debit), c=cur.format(self.total_credit),
                diff=cur.format(abs(self.total_debit - self.total_credit))))
        return super()._check_before_post()

    # ------------------------------------------------------------------ journal voucher: the next line balances
    @api.onchange('line_ids')
    def _onchange_sb_jv_balance(self):
        """A journal line given an account and no amount takes the amount that balances the voucher."""
        if self.voucher_type != 'journal' or self.state != 'draft':
            return
        diff = sum(self.line_ids.mapped('debit')) - sum(self.line_ids.mapped('credit'))
        blank = self.line_ids.filtered(lambda l: l.account_id and not l.debit and not l.credit)
        if blank and not self.currency_id.is_zero(diff):
            line = blank[-1]
            if diff > 0:
                line.credit = diff
            else:
                line.debit = -diff

    # ------------------------------------------------------------------ series: petty cash, purchase bills
    @api.onchange('sb_series_id')
    def _onchange_sb_series_cash(self):
        """A series kept for one cash box (e.g. petty cash payments) pays from that box."""
        account = self.sb_series_id.sb_cash_account_id
        if not account or self.state != 'draft':
            return
        if self._sb_is_petty_box(account):
            # the custodian pays from their own box; else the petty cash box in use, not an idle one
            account = self._sb_my_petty_box() or self._sb_petty_cash() or account
        self.cash_account_id = account
        if len(self.cash_line_ids) == 1:
            self.cash_line_ids.account_id = account
            self.cash_line_ids.method = 'cash'
        elif not self.cash_line_ids:
            self.cash_line_ids = [Command.create({'account_id': account.id, 'method': 'cash'})]

    def _sb_series_number(self):
        """Purchase bills, debit notes and bank transfers (when the company has a series for them: BNKTR01405) are
        numbered by their series like the other vouchers (BILL 0001), not by the journal's year sequence."""
        self.ensure_one()
        if self.sb_fiscal_only and self.move_id.name in (False, '/') and self.sb_pad_number:
            self.env['sb.doc.series']._sb_check_fiscal_text(self.sb_pad_number)
        if self.voucher_type == 'cash_purchase' and self.move_id.name in (False, '/'):
            # a cash purchase is money paid out: numbered on the payment voucher pad (CPV, or the petty cash pad it is
            # paid from), not by the journal's own year sequence (CP/26-27/0001)
            paid_from = self.cash_line_ids.account_id or self.cash_account_id
            Series = self.env['sb.doc.series']
            pad = len(paid_from) == 1 and Series.search([('doc_type', '=', 'payment'), ('company_id', '=', self.company_id.id),
                                                         ('active', '=', True), ('sb_cash_account_id', '=', paid_from.id)], limit=1)
            series = (self.sb_series_id if self.sb_series_id.doc_type == 'payment' else False) or pad or \
                Series._for('payment', company=self.company_id)
            if series:
                self.move_id.name = series._take(self.date, manual=self.sb_pad_number, what=self.env._('Cash purchase'))
                self.sb_series_id = series
                return
        if self.voucher_type == 'payment' and not self.sb_series_id and self.move_id.name in (False, '/'):
            # paid from a cash box that has its own pad (petty cash: PCPV): numbered in that pad
            paid_from = self.cash_line_ids.account_id or self.cash_account_id
            if len(paid_from) == 1:
                pad = self.env['sb.doc.series'].search([('doc_type', '=', 'payment'), ('company_id', '=', self.company_id.id),
                                                        ('active', '=', True), ('sb_cash_account_id', '=', paid_from.id)], limit=1)
                if pad:
                    self.sb_series_id = pad
        if self.voucher_type in ('bill', 'purchase_return', 'transfer') and self.move_id.name in (False, '/'):
            doc_type = 'bank_transfer' if self.voucher_type == 'transfer' else self.voucher_type
            series = self.env['sb.doc.series']._for(doc_type, company=self.company_id,
                                                    series=self.sb_series_id if self.sb_series_id.doc_type == doc_type else False)
            if series:
                label = dict(self._fields['voucher_type']._description_selection(self.env)).get(self.voucher_type)
                self.move_id.name = series._take(self.date, manual=self.sb_pad_number, what=label)
                self.sb_series_id = series
                return
        return super()._sb_series_number()


class SbVoucherApply(models.Model):
    _inherit = 'sb.voucher.apply'

    # the grid shows what each item is worth to this voucher: invoices it settles positive, credits it uses negative
    sb_original = fields.Monetary('Original', compute='_compute_sb_signed', currency_field='item_currency_id')
    sb_open = fields.Monetary('Open', compute='_compute_sb_signed', currency_field='item_currency_id')

    @api.depends('amount_total', 'amount_residual', 'direction', 'voucher_id.voucher_type')
    def _compute_sb_signed(self):
        for ap in self:
            primary = 'debit' if ap.voucher_id.voucher_type == 'receipt' else 'credit'
            sign = 1 if ap.direction == primary else -1
            ap.sb_original, ap.sb_open = sign * ap.amount_total, sign * ap.amount_residual

    @api.depends('line_id')
    def _compute_direction(self):
        """An item the partner owes (or we owe them) is an invoice or bill, whatever entry carries it: an invoice brought
        over from Odoo 18 as a plain entry is not an advance."""
        super()._compute_direction()
        for ap in self:
            line = ap.line_id
            if (line.account_id.account_type == 'asset_receivable' and line.balance > 0) or \
                    (line.account_id.account_type == 'liability_payable' and line.balance < 0):
                ap.kind = 'invoice'

    @api.depends('line_id')
    def _compute_item(self):
        """Documents brought over from the previous system as plain entries show as what they are (Sales Invoice FS00001956),
        not as the receivable account."""
        super()._compute_item()
        moves = self.line_id.move_id.filtered(lambda m: not m.is_invoice())
        if not moves:
            return
        vouchers = {v.move_id: v for v in self.env['sb.voucher'].sudo().search([('move_id', 'in', moves.ids)])}
        labels = dict(self.env['sb.voucher']._fields['voucher_type']._description_selection(self.env))
        for ap in self:
            v = vouchers.get(ap.line_id.move_id)
            if v and ap.line_id.move_id.journal_id.code != 'OB':
                ap.item_type = labels.get(v.voucher_type)
                ap.document = v.name or ap.document

    @api.depends('line_id.amount_residual', 'line_id.amount_residual_currency', 'voucher_id.state', 'voucher_id.sb_editing')
    def _compute_residual(self):
        super()._compute_residual()
        # a voucher being edited still settles its items in the books: what it applied is available to it again
        for ap in self.filtered(lambda a: a.voucher_id.sb_editing):
            ap.amount_residual += ap.amount_applied + ap.discount_currency


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    @api.onchange('product_id')
    def _onchange_product(self):
        """Selling: the price this customer was last charged for the item (else its sales price), and the sales account
        the item is usually sold on with this kind of voucher (a credit invoice and a cash register sale can use different
        sales accounts), else the item's own."""
        res = super()._onchange_product()
        product = self.product_id
        voucher = self.voucher_id
        vtype = self.voucher_type or voucher.voucher_type or self.env.context.get('default_voucher_type')
        if not product or vtype not in SALE_SIDE:
            return res
        Line = self.env['sb.voucher.line']
        base = [('product_id', '=', product.id), ('voucher_id.state', '=', 'posted'),
                ('company_id', '=', (voucher.company_id or self.env.company).id)]
        partner = voucher.partner_id.commercial_partner_id
        sold = base + [('voucher_id.voucher_type', 'in', ('sale', 'cash_sale')),
                       ('voucher_id.doc_currency_id', '=', (voucher.doc_currency_id or voucher.currency_id).id), ('price_unit', '>', 0)]
        # the last price to this customer, else the last price the item was sold at to anyone (a walk-in), else its sales price
        last = partner and Line.search(sold + [('voucher_id.partner_id', 'child_of', partner.id)], order='id desc', limit=1)
        last = last or Line.search(sold, order='id desc', limit=1)
        if last:
            self.price_unit = last.price_unit
        if vtype == 'sale_return':
            return res
        # the sales account this item is usually sold on with this kind of voucher (its latest 50 sales)
        used = Line.search(base + [('voucher_id.voucher_type', '=', vtype), ('account_id.account_type', '=', 'income')],
                           order='id desc', limit=50)
        if used:
            counts = {}
            for l in used:
                counts[l.account_id] = counts.get(l.account_id, 0) + 1
            self.account_id = max(counts, key=counts.get)
        return res


class SbDocSeries(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('bill', 'Purchase Voucher'), ('purchase_return', 'Purchase Return / Debit Note'),
                                               ('bank_transfer', 'Bank Transfer')],
                                ondelete={'bill': 'cascade', 'purchase_return': 'cascade', 'bank_transfer': 'cascade'})
    sb_cash_account_id = fields.Many2one(
        'account.account', 'Cash / bank account', check_company=True, domain="[('account_type', '=', 'asset_cash')]",
        help='Payments numbered in this series are paid from this account (e.g. the petty cash box for petty cash vouchers).')

    @api.model
    def _default_specs(self):
        return super()._default_specs() + [(('bill', 'regular'), 'BILL ', 'account_move', 'name'),
                                           (('purchase_return', 'regular'), 'DN ', 'account_move', 'name')]

    @api.model
    def _sb_extra_name_tables(self):
        # bank transfers: numbered by the journal's year sequence (BT/2026/0001) unless the company adds a series
        return dict(super()._sb_extra_name_tables(), bank_transfer=('account_move', 'name'))

    def _sb_type_names(self):
        """Bills and debit notes numbered by Odoo's journal sequence (BILL/26-27/0003, RBILL/...) before the series."""
        res = super()._sb_type_names()
        move_type = {'bill': 'in_invoice', 'purchase_return': 'in_refund'}.get(self.doc_type)
        if not move_type:
            return res
        codes = {self._sb_doc_code(self.prefix), 'BILL' if move_type == 'in_invoice' else 'RBILL'}
        self.env['account.move'].flush_model(['name', 'date', 'move_type'])
        self.env.cr.execute("SELECT name, date FROM account_move WHERE company_id = %s AND move_type = %s AND name != '/'",
                            (self.company_id.id, move_type))
        return res + [(n, d) for n, d in self.env.cr.fetchall() if self._sb_doc_code(n).removeprefix('VOID') in codes]

    @api.model
    def _sb_link_petty_cash(self):
        """Petty cash series pay from the petty cash account (the first one by code) unless one is set."""
        for s in self.search([('doc_type', '=', 'payment'), ('sb_cash_account_id', '=', False), '|',
                              ('name', 'ilike', 'petty'), ('prefix', 'ilike', 'PCPV')]):
            account = self.env['account.account'].search([('company_ids', 'in', s.company_id.id), ('account_type', '=', 'asset_cash'),
                                                          ('name', 'ilike', 'petty')], order='code', limit=1)
            if account:
                s.sb_cash_account_id = account


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # a new item has no price until one is given (Odoo's default of 1.00 ends up on every invoice)
    list_price = fields.Float(default=0.0)

    @api.model
    def _sb_clear_default_price(self):
        """Items still carrying Odoo's old default sales price of 1.00, never actually sold at 1.00, get no price: a sale
        then takes the last price sold at instead of 1.00. Run on every upgrade; items already cleared are left alone."""
        self.env.cr.execute("""
            SELECT t.id FROM product_template t
             WHERE t.list_price = 1.0
               AND NOT EXISTS (SELECT 1 FROM sb_voucher_line l JOIN sb_voucher v ON v.id = l.voucher_id
                                 JOIN product_product p ON p.id = l.product_id
                                WHERE p.product_tmpl_id = t.id AND l.price_unit = 1.0 AND v.state = 'posted'
                                  AND v.voucher_type IN ('sale', 'cash_sale'))
               AND NOT EXISTS (SELECT 1 FROM account_move_line ml JOIN account_move m ON m.id = ml.move_id
                                 JOIN product_product p ON p.id = ml.product_id
                                WHERE p.product_tmpl_id = t.id AND ml.price_unit = 1.0 AND m.state = 'posted'
                                  AND m.move_type IN ('out_invoice', 'out_receipt'))""")
        ids = [r[0] for r in self.env.cr.fetchall()]
        if ids:
            self.with_context(active_test=False).browse(ids).write({'list_price': 0.0})
        return len(ids)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        """Items found by part of their name: the exact name (or code) first, then names starting with what was typed,
        then items that are sold (on sales documents) or bought (on purchase documents), then the rest."""
        if not name or operator not in ('ilike', 'like', '=ilike'):
            return super().name_search(name, domain, operator, limit)
        res = super().name_search(name, domain, operator, limit=max(limit, 80) if limit else limit)
        side = self.env.context.get('sb_item_side')
        typed = name.strip().lower()
        products = self.browse([r[0] for r in res])
        traded = set()
        if side in ('sale', 'purchase') and products:
            types = ('sale', 'cash_sale') if side == 'sale' else ('bill', 'cash_purchase')
            rows = self.env['sb.voucher.line'].sudo()._read_group(
                [('product_id', 'in', products.ids), ('voucher_id.voucher_type', 'in', types), ('voucher_id.state', '=', 'posted')],
                ['product_id'], ['__count'])
            traded = {p.id for p, _count in rows}
        by_id = {p.id: p for p in products}

        def rank(item):
            p = by_id[item[0]]
            pname, code = (p.name or '').lower(), (p.default_code or '').lower()
            return (0 if typed in (pname, code) else 1,
                    0 if pname.startswith(typed) or code.startswith(typed) else 1,
                    0 if p.id in traded else 1,
                    0 if (p.purchase_ok if side == 'purchase' else p.sale_ok) else 1)
        res = sorted(res, key=rank)
        return res[:limit] if limit else res
