"""Beginning balances, the Peachtree way.

Chart of accounts: one opening balance, the trial balance on the day before the books start (Sage: Chart of Accounts
Beginning Balances). Debits must equal credits: it is saved only when it balances, nothing is pushed into equity.
(Prior-year history by period, e.g. last year's income statement month by month, stays in the books for comparatives.)
Customers / vendors: the open invoices, bills and GRNs they owe or are owed, one dated item each.
Inventory items (sonador_inventory): quantity and cost per item.

The subledgers drive their control accounts: receivables and payables come only from the customer and vendor
items, inventory accounts from the items (plus any part of the GL balance that is not itemised). Every entry is an
Opening Balances voucher. The opening balance clearing account only carries the customer, vendor and item entries until
the chart of accounts is completed: when the opening trial balance balances, the clearing account is zero.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

from .ethiopian_calendar import MONTHS, fiscal_periods, fiscal_year_of, format_ethiopian, to_ethiopian

OB_KINDS = [('account', 'Chart of accounts'), ('customer', 'Customer'), ('vendor', 'Vendor')]
CONTROL = {'asset_receivable': 'customer', 'liability_payable': 'vendor'}


def _d(value):
    return fields.Date.to_date(value) if value else None


def _fmt(d):
    return d.strftime('%-d %b %Y') if d else ''


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_ob_kind = fields.Selection(OB_KINDS, string='Beginning balance of', index=True, copy=False,
                                  help='Opening Balances vouchers made by the Beginning Balances screen.')
    sb_ob_period = fields.Date('Beginning balance period', index=True, copy=False,
                               help='Last day of the period the beginning balance or prior-year adjustment belongs to.')


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    # customer / vendor items kept as lines of the one opening voucher
    sb_ob_kind = fields.Selection([('customer', 'Customer'), ('vendor', 'Vendor')], string='Beginning balance of', copy=False)
    sb_doc_date = fields.Date('Document date', copy=False, help='Date of the open invoice / purchase voucher / advance.')
    sb_doc_ref = fields.Char('Customer PO / reference', copy=False)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_books_start = fields.Date(
        'Books start', help='First day of the first fiscal year kept in the books. Beginning balances are entered '
                            'for the periods of the year before it (Peachtree: "Before" and the prior-year periods).')


class SbBeginning(models.AbstractModel):
    _name = 'sb.beginning'
    _description = 'Beginning Balances'

    # ================================================================== periods
    @api.model
    def _books_start(self):
        company = self.env.company
        if company.sb_books_start:
            return company.sb_books_start
        aml = self.env['account.move.line']
        base = [('company_id', '=', company.id), ('parent_state', '=', 'posted')]
        first_real = aml.search(base + [('journal_id.code', '!=', 'OB')], order='date', limit=1).date
        first_ob = aml.search(base + [('journal_id.code', '=', 'OB')], order='date', limit=1).date
        day = first_real or fields.Date.context_today(self)
        if first_ob and day <= first_ob:
            day = first_ob + timedelta(days=1)
        start = company.compute_fiscalyear_dates(day)['date_from']
        company.sudo().sb_books_start = start
        return start

    @api.model
    def _months(self, date_from, date_to):
        """[(start, end, label)] of one fiscal year: Ethiopian months when the year ends on 7 July."""
        company = self.env.company
        if (int(company.fiscalyear_last_month), company.fiscalyear_last_day) == (7, 7):
            return [(s, e, n) for _no, n, s, e in fiscal_periods(fiscal_year_of(date_to))]
        out, s = [], date_from
        while s <= date_to:
            e = min(s + relativedelta(months=1, days=-1), date_to)
            out.append((s, e, s.strftime('%B %Y')))
            s = e + timedelta(days=1)
        return out

    @api.model
    def _periods(self):
        start = self._books_start()
        company = self.env.company
        prior = company.compute_fiscalyear_dates(start - timedelta(days=1))
        before_end = prior['date_from'] - timedelta(days=1)
        periods = [{'key': fields.Date.to_string(before_end), 'start': False, 'end': fields.Date.to_string(before_end),
                    'label': 'Before %s' % _fmt(prior['date_from']), 'ec': format_ethiopian(before_end),
                    'title': 'Beginning balances as of %s' % _fmt(before_end), 'kind': 'before'}]
        for s, e, name in self._months(prior['date_from'], prior['date_to']):
            periods.append({'key': fields.Date.to_string(e), 'start': fields.Date.to_string(s), 'end': fields.Date.to_string(e),
                            'label': 'From %s through %s' % (_fmt(s), _fmt(e)), 'ec': name,
                            'title': 'Prior year adjustments as of %s' % _fmt(e), 'kind': 'period'})
        lock = company._get_user_fiscal_lock_date(self.env['account.journal'].search(
            [('code', '=', 'OB'), ('company_id', '=', company.id)], limit=1))
        for p in periods:
            p['end_label'] = _fmt(_d(p['end']))
            p['locked'] = bool(lock and _d(p['end']) <= lock)
        known = {p['key'] for p in periods}
        extra = set(self.env['sb.voucher'].search([('voucher_type', '=', 'opening'), ('state', '=', 'posted'),
                                                   ('company_id', '=', company.id), ('sb_ob_kind', '=', 'account')]).mapped('sb_ob_period'))
        extra |= set(self._extra_periods())
        for e in sorted(d for d in extra if d and fields.Date.to_string(d) not in known):
            periods.append({'key': fields.Date.to_string(e), 'start': False, 'end': fields.Date.to_string(e),
                            'label': 'Adjustment as of %s' % _fmt(e), 'ec': format_ethiopian(e),
                            'title': 'Balances entered as of %s' % _fmt(e), 'kind': 'extra', 'end_label': _fmt(e),
                            'locked': bool(lock and e <= lock)})
        return periods

    def _extra_periods(self):
        """Other modules (inventory) add the dates of their beginning balance documents."""
        return []

    @api.model
    def _period(self, key):
        for p in self._periods():
            if p['key'] == key:
                return p
        raise UserError(self.env._('Unknown period %s.', key))

    @api.model
    def _period_of(self, day):
        """The beginning-balance period a dated item (invoice, bill) falls in."""
        periods = self._periods()
        if day <= _d(periods[0]['end']):
            return periods[0]
        for p in periods[1:]:
            if p['start'] and _d(p['start']) <= day <= _d(p['end']):
                return p
        return None

    @api.model
    def _last_day(self):
        return self._books_start() - timedelta(days=1)

    # ================================================================== screen
    @api.model
    def bb_context(self):
        self._adopt()
        company = self.env.company
        periods = self._periods()
        V = self.env['sb.voucher']
        used = set(V.search([('voucher_type', '=', 'opening'), ('state', '=', 'posted'), ('company_id', '=', company.id),
                             ('sb_ob_kind', '=', 'account')]).mapped('sb_ob_period')) | set(self._extra_periods())
        for p in periods:
            p['used'] = _d(p['key']) in used
        opening = fields.Date.to_string(self._last_day())
        equity = company.sb_opening_equity_account_id
        return {
            'periods': periods, 'default_period': opening, 'opening': opening, 'opening_label': _fmt(self._last_day()),
            'opening_ec': format_ethiopian(self._last_day()), 'tb': self._opening_tb(),
            'books_start': fields.Date.to_string(self._books_start()), 'books_start_label': _fmt(self._books_start()),
            'equity': {'id': equity.id, 'code': equity.code, 'name': equity.name} if equity else False,
            'equity_balance': self._gl_balance([equity.id], None).get(equity.id, 0.0) if equity else 0.0,
            'has_inventory': self._has_inventory(),
            'currency': company.currency_id.name,
            'can_edit': self.env.su or self.env.user.has_group('sonador_books.group_sb_accountant'),
            'items_locked': periods[-1]['locked'] if periods[-1]['kind'] != 'extra' else any(
                p['locked'] for p in periods if p['kind'] == 'period'),
            'receivable_accounts': self._account_choices('asset_receivable'),
            'payable_accounts': self._account_choices('liability_payable'),
            'partner_accounts': self._account_choices(False),
        }

    def _has_inventory(self):
        return False

    @api.model
    def _account_choices(self, atype):
        dom = [('account_type', '=', atype)] if atype else [('sb_partner_account', '=', True)]
        accs = self.env['account.account'].search(dom + [('active', '=', True), ('company_ids', 'in', self.env.company.id)],
                                                  order='code')
        return [{'id': a.id, 'code': a.code, 'name': a.name} for a in accs]

    @api.model
    def _gl_balance(self, account_ids, day):
        dom = [('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted')]
        if account_ids is not None:
            dom.append(('account_id', 'in', list(account_ids)))
        if day:
            dom.append(('date', '<=', day))
        out = {}
        for acc, bal in self.env['account.move.line']._read_group(dom, ['account_id'], ['balance:sum']):
            out[acc.id] = bal
        return out

    # ================================================================== chart of accounts
    def _account_vouchers(self, end):
        return self.env['sb.voucher'].search([('voucher_type', '=', 'opening'), ('sb_ob_kind', '=', 'account'),
                                              ('sb_ob_period', '=', end), ('state', '=', 'posted'),
                                              ('company_id', '=', self.env.company.id)], order='id')

    @api.model
    def _control_accounts(self):
        """{account_id: 'customer' | 'vendor'}: receivable / payable accounts kept per customer or vendor (their default
        accounts and any account a customer / vendor item uses). Other receivable / payable accounts (salaries, pension,
        staff advances) take a balance on the chart of accounts."""
        company = self.env.company
        out = {}
        Partner = self.env['res.partner'].with_company(company)
        for kind, field, dom in (('customer', 'property_account_receivable_id', [('customer_rank', '>', 0)]),
                                 ('vendor', 'property_account_payable_id', [('supplier_rank', '>', 0)])):
            accs = Partner.search(dom).mapped(field)
            default = self.env['ir.default']._get('res.partner', field, company_id=company.id)
            if default:
                accs |= self.env['account.account'].browse(default)
            for a in accs:
                out[a.id] = kind
        for a in self.env['account.account'].search([('sb_partner_account', '=', True), ('company_ids', 'in', company.id)]):
            out.setdefault(a.id, 'customer' if a.internal_group == 'asset' else 'vendor')
        for kind in ('customer', 'vendor'):
            for i in self._all_items(kind):
                out[i['line'].account_id.id] = kind
        return out

    def _inventory_accounts(self):
        """account ids whose balance is made of inventory items (sonador_inventory)."""
        return set()

    def _items_by_account(self, end):
        """{account_id: value} of the inventory beginning balances of the period (sonador_inventory)."""
        return {}

    def _partner_items_by_account(self, period):
        """{account_id: balance} of the customer and vendor items dated in the period."""
        V = self.env['sb.voucher']
        dom = [('voucher_type', '=', 'opening'), ('sb_ob_kind', 'in', ('customer', 'vendor')), ('state', '=', 'posted'),
               ('company_id', '=', self.env.company.id), ('date', '<=', period['end'])]
        if period['start']:
            dom.append(('date', '>=', period['start']))
        out = defaultdict(float)
        for v in V.search(dom):
            for l in v.move_id.line_ids:
                if self._is_partner_account(l.account_id):
                    out[l.account_id.id] += l.balance
        if _d(period['end']) == self._last_day():
            for kind in ('customer', 'vendor'):
                for vl in self._item_lines(kind):
                    out[vl.account_id.id] += vl.debit - vl.credit
        return out

    @api.model
    def _opening_tb(self):
        """Opening trial balance totals (clearing account left out): debits, credits, out of balance (debit +)."""
        equity = self.env.company.sb_opening_equity_account_id
        dr = cr = 0.0
        for acc, bal in self._gl_balance(None, self._last_day()).items():
            if equity and acc == equity.id:
                continue
            if bal > 0:
                dr += bal
            else:
                cr -= bal
        cur = self.env.company.currency_id
        return {'debit': cur.round(dr), 'credit': cur.round(cr), 'diff': cur.round(dr - cr)}

    def _opening_key(self, key):
        return key or fields.Date.to_string(self._last_day())

    @api.model
    def bb_accounts(self, key=None):
        """The opening trial balance: every account's balance on the opening date (customers, vendors and items
        included), in debit / credit."""
        key = self._opening_key(key)
        period = self._period(key)
        end = _d(period['end'])
        company = self.env.company
        subs = self._partner_items_by_account(period)
        items = self._items_by_account(end)
        inventory = self._inventory_accounts()
        control = self._control_accounts()
        balances = self._gl_balance(None, end)
        equity = company.sb_opening_equity_account_id
        types = dict(self.env['account.account']._fields['account_type']._description_selection(self.env))
        rows = []
        for a in self.env['account.account'].search([('company_ids', 'in', company.id)], order='code'):
            if a.account_type == 'off_balance' or a == equity:
                continue
            if not a.active and not balances.get(a.id):
                continue
            source = control.get(a.id) or ('inventory' if a.id in inventory else False)
            sub = subs.get(a.id, 0.0) if source in ('customer', 'vendor') else items.get(a.id, 0.0)
            rows.append({
                'id': a.id, 'code': a.code, 'name': a.name, 'type': types.get(a.account_type, ''),
                'group': a.internal_group, 'debit_side': a.internal_group in ('asset', 'expense'),
                'pl': a.internal_group in ('income', 'expense'),
                'amount': balances.get(a.id, 0.0),
                'sub': sub if source else 0.0, 'source': source or '',
                'inactive': not a.active,
            })
        return {'period': period, 'rows': rows, 'tb': self._opening_tb(),
                'vouchers': [{'id': v.id, 'name': v.name} for v in self._account_vouchers(end)]}

    @api.model
    def bb_accounts_save(self, key, amounts):
        """amounts: {account_id: balance on the opening date (debit +)} for the rows the user changed.
        Saved only when the opening trial balance then balances (debits = credits)."""
        self._require()
        key = self._opening_key(key)
        period = self._period(key)
        end = _d(period['end'])
        company = self.env.company
        equity = company.sb_opening_equity_account_id
        if not equity:
            raise UserError(self.env._('Set the opening balance clearing account in Settings first.'))
        cur = company.currency_id
        control = self._control_accounts()
        balances = self._gl_balance(None, end)
        vouchers = self._account_vouchers(end)
        voucher = vouchers[-1:] if vouchers else self.env['sb.voucher']
        Account = self.env['account.account']
        changes = {}
        for acc_id, total in (amounts or {}).items():
            acc = Account.browse(int(acc_id))
            if acc.id in control or acc == equity:
                continue
            delta = cur.round(cur.round(float(total or 0.0)) - balances.get(acc.id, 0.0))
            if not float_is_zero(delta, precision_rounding=cur.rounding):
                changes[acc] = delta
        if not changes:
            return self.bb_accounts(key)
        tb = self._opening_tb()
        diff = cur.round(tb['diff'] + sum(changes.values())) if end == self._last_day() else 0.0
        if not float_is_zero(diff, precision_rounding=cur.rounding):
            dr = tb['debit'] + sum(v for v in changes.values() if v > 0)
            raise UserError(self.env._(
                'The opening balance does not balance: debits exceed credits by %(diff)s. Debits must equal credits '
                'before it can be saved.', diff='{:,.2f}'.format(diff)) if diff > 0 else self.env._(
                'The opening balance does not balance: credits exceed debits by %(diff)s. Debits must equal credits '
                'before it can be saved.', diff='{:,.2f}'.format(-diff)))
        pl = [a for a in changes if a.internal_group in ('income', 'expense')]
        if pl and not self.env.context.get('sb_bb_keep_year_result'):
            # (sb_bb_keep_year_result: a migration that only spreads the year's income and expenses over its periods)
            fy = company.compute_fiscalyear_dates(end)
            closed = self.env['account.move'].search_count([
                ('company_id', '=', company.id), ('sb_closing', '=', True), ('state', '=', 'posted'),
                ('date', '>=', fy['date_from']), ('date', '<=', fy['date_to']), ('reversal_move_ids', '=', False),
                ('reversed_entry_id', '=', False)])  # the reversal made by reopening the year is not a closing
            if closed:
                raise UserError(self.env._(
                    'The year is closed (its profit or loss is in retained earnings). Reopen it in the Year-End wizard '
                    'before changing income or expense accounts: %s', ', '.join(a.code for a in pl)))
        label = 'Opening balance' if end == self._last_day() else 'Prior year adjustment'
        lines = [self._vline_vals(l) for l in voucher.line_ids]
        for acc, delta in changes.items():
            mine = [i for i, l in enumerate(lines) if l['account_id'] == acc.id and not l.get('sb_ob_kind')]
            if len(mine) == 1:
                l = lines[mine[0]]
                net = l['debit'] - l['credit'] + delta
                l.update(debit=max(net, 0.0), credit=max(-net, 0.0))
            else:
                lines.append({'account_id': acc.id, 'partner_id': False, 'name': label if not mine else '%s (change)' % label,
                              'due_date': False, 'debit': max(delta, 0.0), 'credit': max(-delta, 0.0)})
        if end == self._last_day():
            self._post_opening(voucher, lines)
        else:
            lines = [l for l in lines if not float_is_zero(l['debit'] - l['credit'], precision_rounding=cur.rounding)]
            if not voucher:
                voucher = self.env['sb.voucher'].create({
                    'voucher_type': 'opening', 'sb_ob_kind': 'account', 'sb_ob_period': end, 'date': end,
                    'memo': 'Prior year adjustments as of %s' % _fmt(end), 'line_ids': [Command.create(l) for l in lines]})
                voucher.action_post()
            else:
                self._repost(voucher, lines)
        return self.bb_accounts(key)

    # ================================================================== customers / vendors
    @api.model
    def _kind_domain(self, kind):
        return [('customer_rank', '>', 0)] if kind == 'customer' else [('supplier_rank', '>', 0)]

    def _items(self, kind, partner=None):
        """Item vouchers of the earlier layout (one voucher per invoice); new items are lines of the opening voucher."""
        dom = [('voucher_type', '=', 'opening'), ('sb_ob_kind', '=', kind), ('state', '=', 'posted'),
               ('company_id', '=', self.env.company.id)]
        if partner is not None:
            dom.append(('partner_id', '=', partner.id))
        return self.env['sb.voucher'].search(dom, order='date, id')

    @api.model
    def _opening_voucher(self):
        return self._account_vouchers(self._last_day())[-1:]

    @api.model
    def _item_lines(self, kind, partner=None):
        """Customer / vendor items that are lines of the opening voucher."""
        lines = self._opening_voucher().line_ids.filtered(lambda l: l.sb_ob_kind == kind)
        if partner is not None:
            lines = lines.filtered(lambda l: l.partner_id.commercial_partner_id == partner.commercial_partner_id)
        return lines

    @api.model
    def _move_line(self, vl):
        """The journal item of an opening voucher line (same account, partner and number)."""
        return vl.voucher_id.move_id.line_ids.filtered(
            lambda m: m.account_id == vl.account_id and m.partner_id == vl.partner_id and m.name == vl.name)[:1]

    @api.model
    def _all_items(self, kind, partner=None):
        """Every customer / vendor beginning item: {key, partner, line (journal item), date, po}."""
        out = []
        for v in self._items(kind, partner):
            out.append({'key': 'v%d' % v.id, 'voucher': v, 'partner': v.partner_id, 'line': self._control_line(v),
                        'date': v.date, 'po': v.reference or ''})
        for vl in self._item_lines(kind, partner):
            out.append({'key': 'l%d' % vl.id, 'vline': vl, 'partner': vl.partner_id, 'line': self._move_line(vl),
                        'date': vl.sb_doc_date or vl.voucher_id.date, 'po': vl.sb_doc_ref or ''})
        return sorted(out, key=lambda i: (i['date'], i['key']))

    @api.model
    def _control_line(self, v):
        return v.move_id.line_ids.filtered(lambda l: self._is_partner_account(l.account_id))[:1]

    @api.model
    def _is_partner_account(self, account):
        return account.account_type in CONTROL or account.sb_partner_account

    @api.model
    def bb_partners(self, kind, search=''):
        sign = 1 if kind == 'customer' else -1
        totals, counts = defaultdict(float), defaultdict(int)
        for i in self._all_items(kind):
            totals[i['partner'].id] += sign * i['line'].balance
            counts[i['partner'].id] += 1
        Partner = self.env['res.partner']
        dom = ['|', ('id', 'in', list(totals)), '&', ('parent_id', '=', False)] + self._kind_domain(kind)
        if search:
            dom = ['&'] + dom + ['|', ('name', 'ilike', search), ('ref', 'ilike', search)]
        partners = Partner.search(dom, order='name')
        accounts = [a for a, k in self._control_accounts().items() if k == kind]
        current = sign * sum(self._gl_balance(accounts, None).values())
        return {
            'partners': [{'id': p.id, 'ref': p.ref or '', 'name': p.name, 'amount': totals.get(p.id, 0.0),
                          'count': counts.get(p.id, 0)} for p in partners],
            'total': sum(totals.values()), 'current_balance': current,
        }

    @api.model
    def bb_partner_items(self, kind, partner_id):
        partner = self.env['res.partner'].browse(int(partner_id)).exists()
        if not partner:
            return {}
        sign = 1 if kind == 'customer' else -1
        rows = []
        for i in self._all_items(kind, partner):
            line = i['line']
            amount = sign * line.balance
            residual = sign * line.amount_residual
            period = self._period_of(i['date'])
            rows.append({'id': i['key'], 'number': line.name or i['po'] or '', 'date': fields.Date.to_string(i['date']),
                         'due': fields.Date.to_string(line.date_maturity) if line.date_maturity else False,
                         'po': i['po'], 'amount': amount, 'account_id': line.account_id.id,
                         'paid': amount - residual, 'voucher': line.move_id.name,
                         'period': period['ec'] or period['label'] if period else ''})
        acc = self._default_item_account(kind, partner)
        return {'partner': {'id': partner.id, 'name': partner.name, 'ref': partner.ref or ''},
                'rows': rows, 'default_account': acc.id,
                'balance': sum(r['amount'] for r in rows), 'last_day': fields.Date.to_string(self._last_day()),
                'last_day_label': _fmt(self._last_day())}

    @api.model
    def _default_item_account(self, kind, partner):
        """The account of the customer's / vendor's existing items, else the one most items of that kind use,
        else the contact's receivable / payable account."""
        from collections import Counter
        mine = Counter(i['line'].account_id for i in self._all_items(kind, partner))
        if mine:
            return mine.most_common(1)[0][0]
        allc = Counter(i['line'].account_id for i in self._all_items(kind))
        if allc:
            return allc.most_common(1)[0][0]
        return partner.property_account_receivable_id if kind == 'customer' else partner.property_account_payable_id

    @api.model
    def bb_partner_save(self, kind, partner_id, rows):
        """rows: [{id?, number, date, due, po, amount, account_id}] - the full list for the customer / vendor:
        items missing from it are removed. Everything is checked before anything is posted.
        Items are lines of the one opening voucher (items of the earlier layout keep their own voucher)."""
        self._require()
        partner = self.env['res.partner'].browse(int(partner_id))
        company = self.env.company
        cur = company.currency_id
        sign = 1 if kind == 'customer' else -1
        last_day = self._last_day()
        existing = {i['key']: i for i in self._all_items(kind, partner)}
        by_number = {(i['line'].name or '').lower(): k for k, i in existing.items()}
        claimed = {str(r.get('id')) for r in rows if r.get('id')}
        Account = self.env['account.account']
        plan, numbers = [], set()
        for r in rows:
            number = (r.get('number') or '').strip()
            amount = cur.round(float(r.get('amount') or 0.0))
            if not number and not amount:
                continue
            if not number:
                raise ValidationError(self.env._('Enter the invoice number for every amount.'))
            if number.lower() in numbers:
                raise ValidationError(self.env._('%s is entered twice.', number))
            numbers.add(number.lower())
            day = _d(r.get('date')) or last_day
            if day > last_day:
                raise ValidationError(self.env._(
                    '%(no)s: beginning balances are dated on or before %(d)s; later invoices are entered as normal transactions.',
                    no=number, d=_fmt(last_day)))
            if float_is_zero(amount, precision_rounding=cur.rounding):
                raise ValidationError(self.env._('%s: enter the amount.', number))
            account = Account.browse(int(r.get('account_id') or 0)).exists() or self._default_item_account(kind, partner)
            due = _d(r.get('due')) or self._due(partner, kind, day)
            key = str(r.get('id') or '')
            if key and key not in existing and key.isdigit():
                key = 'v' + key  # ids of the earlier layout
            if not key and by_number.get(number.lower()) not in (None, *claimed):
                key = by_number[number.lower()]  # same number typed again: it is that item
                claimed.add(key)
            item = existing.get(key)
            balance = sign * amount
            line = {'account_id': account.id, 'partner_id': partner.commercial_partner_id.id, 'name': number,
                    'due_date': due, 'debit': max(balance, 0.0), 'credit': max(-balance, 0.0),
                    'sb_ob_kind': kind, 'sb_doc_date': day, 'sb_doc_ref': r.get('po') or False}
            if item:
                ctl = item['line']
                paid = sign * (ctl.balance - ctl.amount_residual)
                same = (ctl.name == number and item['date'] == day and ctl.date_maturity == due and item['po'] == (r.get('po') or '')
                        and ctl.account_id == account and cur.is_zero(sign * ctl.balance - amount))
                if not same and not cur.is_zero(paid):
                    if ctl.account_id != account:
                        raise ValidationError(self.env._('%s already has payments applied: its account cannot change.', number))
                    if abs(amount) < abs(paid) - cur.rounding or (amount > 0) != (paid > 0):
                        raise ValidationError(self.env._('%(no)s already has %(paid)s applied: the amount cannot be less.',
                                                         no=number, paid=paid))
            plan.append((item, line, bool(item) and same))
        keep = {i['key'] for i, _l, _s in plan if i}
        drop = [i for k, i in existing.items() if k not in keep]
        for i in drop:
            ctl = i['line']
            if not cur.is_zero(ctl.balance - ctl.amount_residual):
                raise ValidationError(self.env._('%s already has payments applied: it cannot be removed.', ctl.name))
        # items of the earlier layout: their own voucher
        for i in drop:
            if i.get('voucher'):
                self._remove(i['voucher'])
        new_lines = []
        for item, line, same in plan:
            if item and item.get('voucher'):
                if not same:
                    v = item['voucher']
                    v.write({'date': line['sb_doc_date'], 'due_date': line['due_date'], 'reference': line['sb_doc_ref']})
                    self._repost(v, [{k: line[k] for k in ('account_id', 'partner_id', 'name', 'due_date', 'debit', 'credit')}])
            else:
                new_lines.append(line)
        # items as lines of the opening voucher: rewrite this partner's lines, keep everything else
        mine = self._item_lines(kind, partner)
        if new_lines or mine:
            voucher = self._opening_voucher()
            others = [self._vline_vals(l) for l in voucher.line_ids if l not in mine]
            self._post_opening(voucher, others + new_lines)
        field = 'customer_rank' if kind == 'customer' else 'supplier_rank'
        if plan and not partner[field]:
            partner[field] = 1
        return self.bb_partner_items(kind, partner.id)

    @api.model
    def _vline_vals(self, l):
        return {'account_id': l.account_id.id, 'partner_id': l.partner_id.id, 'name': l.name, 'due_date': l.due_date,
                'debit': l.debit, 'credit': l.credit, 'sb_ob_kind': l.sb_ob_kind, 'sb_doc_date': l.sb_doc_date,
                'sb_doc_ref': l.sb_doc_ref}

    @api.model
    def _post_opening(self, voucher, lines):
        """Write the one opening voucher (created when missing)."""
        end = self._last_day()
        cur = self.env.company.currency_id
        lines = [l for l in lines if not float_is_zero(l['debit'] - l['credit'], precision_rounding=cur.rounding)]
        if voucher:
            return self._repost(voucher, lines)
        if not lines:
            return voucher
        voucher = self.env['sb.voucher'].create({
            'voucher_type': 'opening', 'sb_ob_kind': 'account', 'sb_ob_period': end, 'date': end,
            'reference': 'Opening balances', 'memo': 'Opening balances as of %s' % _fmt(end),
            'line_ids': [Command.create(l) for l in lines]})
        voucher.action_post()
        return voucher

    @api.model
    def _due(self, partner, kind, day):
        term = partner.property_payment_term_id if kind == 'customer' else partner.property_supplier_payment_term_id
        if term:
            try:
                return term._compute_terms(day, self.env.company.currency_id, self.env.company, 0, 0, 1.0, 1.0, 1.0, 1.0)[
                    'line_ids'][-1]['date']
            except Exception:  # noqa: BLE001 - keep entry working whatever the terms look like
                return day
        return day

    # ================================================================== partner history (Maintain Customers / Vendors > History)
    @api.model
    def bb_partner_history(self, partner_id, fy_offset=None, anchor=None):
        """Peachtree History tab, from the customer's / vendor's journal items on its A/R and A/P accounts:
        customers: debits are sales, credits receipts; vendors: credits are purchases, debits payments."""
        partner = self.env['res.partner'].browse(int(partner_id)).exists()
        if not partner:
            return {}
        company = self.env.company
        cp = partner.commercial_partner_id
        control = self._control_accounts()
        lines = self.env['account.move.line'].search([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('partner_id', '=', cp.id),
            ('account_id', 'in', list(control))], order='date, id')
        today = fields.Date.context_today(self)
        anchor = _d(anchor) or today
        if fy_offset is None:
            # open on the year of the latest activity (Peachtree opens on the current year, which may be empty)
            fy_offset = 0
            if lines and lines[-1].date < company.compute_fiscalyear_dates(today)['date_from']:
                anchor = lines[-1].date
        fy = company.compute_fiscalyear_dates(anchor)
        if fy_offset:
            fy = company.compute_fiscalyear_dates(fy['date_from'] + relativedelta(years=int(fy_offset)))
        months = self._months(fy['date_from'], fy['date_to'])
        out = {'partner': partner.display_name, 'fy_label': '%s - %s' % (_fmt(fy['date_from']), _fmt(fy['date_to'])),
               'fy_offset': int(fy_offset), 'anchor': fields.Date.to_string(anchor),
               'can_next': fy['date_to'] < company.compute_fiscalyear_dates(today)['date_to'], 'sides': []}
        for kind, labels in (('customer', ('Sales', 'Receipts')), ('vendor', ('Purchases', 'Payments'))):
            sign = 1 if kind == 'customer' else -1
            mine = lines.filtered(lambda l: control.get(l.account_id.id) == kind)
            items = self._all_items(kind, cp)
            rank = partner.customer_rank if kind == 'customer' else partner.supplier_rank
            if not (rank or mine):
                continue
            real = mine.filtered(lambda l: l.journal_id.code != 'OB')
            ups = real.filtered(lambda l: sign * l.balance > 0)     # invoices / bills
            downs = real.filtered(lambda l: sign * l.balance < 0)   # receipts / payments, credit notes
            rows = []
            for st, en, name in months:
                inside = lambda l: st <= l.date <= en  # noqa: E731
                rows.append({'label': _fmt(en), 'ec': name, 'in': sum(sign * l.balance for l in ups if inside(l)),
                             'out': -sum(sign * l.balance for l in downs if inside(l)), 'current': st <= today <= en})
            first = (mine[:1].date if mine else None) or partner.create_date.date()
            last_inv, last_pay = ups[-1:], downs.filtered(lambda l: l.journal_id.type in ('bank', 'cash') or
                                                         l.move_id.journal_id.code in ('CRV', 'CPV', 'CS', 'CP'))[-1:]
            out['sides'].append({
                'kind': kind, 'labels': labels, 'since': fields.Date.to_string(first), 'since_label': _fmt(first),
                'last_invoice': {'date': _fmt(last_inv.date), 'amount': sign * sum(
                    real.filtered(lambda l: l.move_id == last_inv.move_id and sign * l.balance > 0).mapped('balance'))} if last_inv else False,
                'last_payment': {'date': _fmt(last_pay.date), 'amount': -sign * sum(
                    real.filtered(lambda l: l.move_id == last_pay.move_id and sign * l.balance < 0).mapped('balance'))} if last_pay else False,
                'avg_days': self._avg_days(cp, kind),
                'balance': sign * sum(mine.mapped('balance')),
                'rows': list(reversed(rows)),
                'total_in': sum(r['in'] for r in rows), 'total_out': sum(r['out'] for r in rows),
                'beginning': sum(sign * i['line'].balance for i in items),
                'beginning_count': len(items),
            })
        return out

    @api.model
    def _paid(self, v):
        if not v:
            return 0.0
        if v.voucher_type in ('cash_sale', 'cash_purchase'):
            return v.amount_total
        return v.received_amount or v.amount_cash

    @api.model
    def _avg_days(self, partner, kind):
        """Average days from invoice to settlement, weighted by amount (fully settled items only)."""
        accounts = [a for a, k in self._control_accounts().items() if k == kind]
        lines = self.env['account.move.line'].search([
            ('company_id', '=', self.env.company.id), ('partner_id', '=', partner.id), ('parent_state', '=', 'posted'),
            ('account_id', 'in', accounts), ('reconciled', '=', True),
            ('balance', '>' if kind == 'customer' else '<', 0)], limit=500, order='date desc')
        weight = days = 0.0
        for l in lines:
            partials = l.matched_credit_ids if kind == 'customer' else l.matched_debit_ids
            if not partials:
                continue
            settled = max(partials.mapped('max_date'))
            amt = abs(l.balance)
            weight += amt
            days += amt * max((settled - (l.move_id.invoice_date or l.date)).days, 0)
        return round(days / weight) if weight else None

    # ================================================================== posting helpers
    @api.model
    def _require(self):
        if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can enter beginning balances.'))

    @api.model
    def _repost(self, voucher, lines):
        """Rewrite a posted opening voucher in place: the number and the journal items are kept (receipts and
        payments point at them), only their amounts, accounts and dates change. Payments already applied are
        applied again."""
        move = voucher.move_id
        keep = []
        for l in move.line_ids:
            for p in l.matched_debit_ids:
                keep.append((l, p.debit_move_id))
            for p in l.matched_credit_ids:
                keep.append((l, p.credit_move_id))
        voucher.write({'line_ids': [Command.clear()] + [Command.create(dict(l)) for l in lines]})
        voucher._check_before_post()
        move.line_ids.remove_move_reconcile()
        move.button_draft()
        old = list(move.line_ids)
        cmds = []
        for cmd in voucher._prepare_entry_lines():
            vals = cmd[2]
            same = lambda o: o.account_id.id == vals['account_id'] and (o.partner_id.id or False) == (vals.get('partner_id') or False)  # noqa: E731
            match = next((o for o in old if same(o) and o.name == vals.get('name')), None) or next((o for o in old if same(o)), None)
            if match:
                old.remove(match)
                cmds.append(Command.update(match.id, vals))
            else:
                cmds.append(Command.create(vals))
        self._release_lines(self.env['account.move.line'].concat(*old))
        cmds += [Command.delete(o.id) for o in old]
        move.write({'date': voucher.date, 'line_ids': cmds,
                    'ref': ' '.join(filter(None, [voucher.reference, voucher.partner_id.name])) or False})
        move.action_post()
        voucher.write({'name': move.name})
        for line, other in keep:
            if line.exists() and other.exists() and not line.reconciled and not other.reconciled \
                    and line.account_id == other.account_id:
                (line | other).reconcile()
        return voucher

    @api.model
    def _release_lines(self, lines):
        """Journal items about to disappear: drop them from receipts / payments not posted yet."""
        if not lines:
            return
        Apply = self.env['sb.voucher.apply'].sudo()
        used = Apply.search([('line_id', 'in', lines.ids)])
        if used.filtered(lambda a: a.voucher_id.state == 'posted' and (a.amount_applied or a.discount)):
            raise ValidationError(self.env._('%s is applied on a posted receipt or payment.', used[:1].document))
        used.unlink()

    @api.model
    def _remove(self, voucher):
        ctl = self._control_line(voucher)
        if ctl and not voucher.currency_id.is_zero(ctl.balance - ctl.amount_residual):
            raise ValidationError(self.env._('%s already has payments applied: remove those first.', ctl.name or voucher.name))
        move = voucher.move_id
        self._release_lines(move.line_ids)
        move.button_draft()
        voucher.write({'state': 'draft', 'move_id': False})
        move.with_context(force_delete=True).unlink()
        voucher.unlink()

    # ================================================================== one opening voucher
    @api.model
    def merge_into_opening(self):
        """Fold customer / vendor item vouchers of the earlier layout into the one opening voucher (as its lines).
        Items with payments applied stay where they are. Returns the number of items moved."""
        self._require()
        cur = self.env.company.currency_id
        moved = []
        lines = []
        for kind in ('customer', 'vendor'):
            for v in self._items(kind):
                ctl = self._control_line(v)
                if v.date > self._last_day() or not cur.is_zero(ctl.balance - ctl.amount_residual):
                    continue
                lines.append({'account_id': ctl.account_id.id, 'partner_id': ctl.partner_id.id, 'name': ctl.name,
                              'due_date': ctl.date_maturity, 'debit': ctl.debit, 'credit': ctl.credit,
                              'sb_ob_kind': kind, 'sb_doc_date': v.date, 'sb_doc_ref': v.reference or False})
                moved.append(v)
        if not moved:
            return 0
        for v in moved:
            self._remove(v)
        voucher = self._opening_voucher()
        self._post_opening(voucher, [self._vline_vals(l) for l in voucher.line_ids] + lines)
        return len(moved)

    # ================================================================== adopting earlier opening vouchers
    @api.model
    def _adopt(self):
        """Opening vouchers entered before this screen: tag them, and move customer / vendor lines out of the
        chart-of-accounts vouchers into one item per customer / vendor (they keep their date)."""
        V = self.env['sb.voucher']
        company = self.env.company
        for v in V.search([('voucher_type', '=', 'opening'), ('sb_ob_kind', '=', False), ('company_id', '=', company.id)]):
            v.write({'sb_ob_kind': 'account', 'sb_ob_period': v.date})
            if v.state != 'posted':
                continue
            control = self._control_accounts()
            ctl = v.line_ids.filtered(lambda l: l.account_id.account_type in CONTROL and l.partner_id and (
                l.account_id.id in control or l.partner_id['customer_rank' if l.account_id.account_type == 'asset_receivable'
                                                            else 'supplier_rank']))
            if not ctl:
                continue
            moved = []
            for l in ctl:
                ml = v.move_id.line_ids.filtered(lambda m: m.account_id == l.account_id and m.partner_id == l.partner_id
                                                 and m.name == l.name and not m.reconciled)
                if len(ml) != 1 and any(m.reconciled for m in v.move_id.line_ids.filtered(
                        lambda m: m.account_id == l.account_id and m.partner_id == l.partner_id)):
                    continue
                moved.append(l)
            if not moved:
                continue
            for l in moved:
                kind = CONTROL[l.account_id.account_type]
                item = V.create({'voucher_type': 'opening', 'sb_ob_kind': kind, 'partner_id': l.partner_id.id, 'date': v.date,
                                 'due_date': l.due_date or v.date, 'memo': 'Beginning balance (from %s)' % v.name,
                                 'line_ids': [Command.create({'account_id': l.account_id.id, 'partner_id': l.partner_id.id,
                                                              'name': l.name or 'Opening balance', 'due_date': l.due_date or v.date,
                                                              'debit': l.debit, 'credit': l.credit})]})
                item.action_post()
                rank = 'customer_rank' if kind == 'customer' else 'supplier_rank'
                if not l.partner_id[rank]:
                    l.partner_id[rank] = 1
            rest = [{'account_id': l.account_id.id, 'partner_id': l.partner_id.id, 'name': l.name, 'due_date': l.due_date,
                     'debit': l.debit, 'credit': l.credit} for l in v.line_ids if l not in moved]
            if rest:
                self._repost(v, rest)
            else:
                self._remove(v)


def _bb_action(env, **params):
    return {'type': 'ir.actions.client', 'tag': 'sonador_books.beginning_balances', 'name': env._('Beginning Balances'),
            'params': params}


class AccountAccount(models.Model):
    _inherit = 'account.account'

    sb_partner_account = fields.Boolean(
        'Kept per customer / vendor',
        help='Balances on this account belong to a customer or vendor (Peachtree: the A/R or A/P account of the '
             'customer / vendor), e.g. a purchase payable or a supplier advance account. Its journal items carry the '
             'customer / vendor, show on receipts and payments as open items and in the customer / vendor ledgers.')

    sb_liability_group = fields.Selection(
        [('vendor', 'Vendors'), ('tax', 'Taxes and statutory'), ('employee', 'Employees'), ('other', 'Other liability')],
        string='Owed to', compute='_compute_sb_liability_group', store=True, readonly=False,
        help='Who the balance is owed to, for the Business Status cards: vendors (We owe vendors), taxes and statutory '
             '(VAT, PAYE, pension, withholding, profit tax), employees (salaries) or other.')

    @api.depends('account_type', 'name', 'sb_partner_account')
    def _compute_sb_liability_group(self):
        for a in self:
            name = (a.name or '').lower()
            if not (a.account_type or '').startswith('liability') and a.account_type != 'liability_payable':
                a.sb_liability_group = False
            elif re.search(r'salar|wage|net pay', name):
                a.sb_liability_group = 'employee'
            elif re.search(r'\btax|vat\b|pension|with+olding|paye|\btot\b|levy|social security|stamp duty', name) \
                    and 'deferred' not in name and 'variance' not in name:
                a.sb_liability_group = 'tax'
            elif a.account_type == 'liability_payable' or a.sb_partner_account or re.search(
                    r'(purchase|trade|retention|supplier|vendor|accounts) payable|creditor', name):
                a.sb_liability_group = 'vendor'  # kept per vendor (purchase, trade and retention payables)
            else:
                a.sb_liability_group = 'other'

    sb_partner_side = fields.Selection(
        [('customer', 'Customers'), ('vendor', 'Vendors')], string='Customer / vendor account',
        compute='_compute_sb_partner_side', store=True, readonly=False,
        help='Whose balances it holds, for the customer and vendor reports (aged, ledgers, statements): receivables '
             'and customer deposits are customers; payables and advances paid to suppliers are vendors. Empty: '
             'not kept per customer or vendor (staff advances, suspense, tax receivable ...).')

    @api.depends('account_type', 'name', 'sb_partner_account', 'sb_liability_group')
    def _compute_sb_partner_side(self):
        for a in self:
            name = (a.name or '').lower()
            atype = a.account_type or ''
            if not (atype in ('asset_receivable', 'liability_payable') or a.sb_partner_account
                    or a.sb_liability_group == 'vendor'
                    or (atype.startswith('liability') and re.search(r'customer advance|advance from customer|customer deposit', name))):
                a.sb_partner_side = False
            elif atype.startswith('liability'):
                a.sb_partner_side = 'vendor' if a.sb_liability_group == 'vendor' else (
                    'customer' if re.search(r'customer|deposit|advance', name) else False)
            elif re.search(r'staff|employee|suspense|with+olding|shortage|\bvat\b|\btax', name):
                a.sb_partner_side = False  # receivable-type in the chart, but no customer's account
            elif re.search(r'advance payment|supplier|vendor|prepaid|prepayment', name):
                a.sb_partner_side = 'vendor'  # money paid ahead to suppliers, kept per supplier (Peachtree)
            elif re.search(r'advance|cash register', name):
                # an advance named after someone: a supplier's when the people on it supply us, else not a customer
                # account at all (staff fuel advances, the tills)
                a.sb_partner_side = 'vendor' if 'advance' in name and a._sb_mostly_suppliers() else False
            elif atype != 'asset_receivable' and a._sb_mostly_suppliers():
                a.sb_partner_side = 'vendor'  # an account kept per supplier, named after the supplier
            else:
                a.sb_partner_side = 'customer'

    @api.model
    def _sb_recompute_partner_side(self):
        """On upgrade: re-derive who owes and who is owed on every account, as the rules improve."""
        accounts = self.sudo().with_context(active_test=False).search([])
        accounts._compute_sb_liability_group()
        accounts._compute_sb_partner_side()

    def _sb_mostly_suppliers(self):
        """Whether the partners on this account's entries are mostly suppliers (an advance account named after one)."""
        if not self.id:
            return False
        self.env.cr.execute("""
            SELECT COUNT(*) FILTER (WHERE p.supplier_rank > 0),
                   COUNT(*) FILTER (WHERE COALESCE(p.supplier_rank, 0) = 0 AND p.customer_rank > 0)
              FROM (SELECT DISTINCT partner_id FROM account_move_line WHERE account_id = %s AND partner_id IS NOT NULL) l
              JOIN res_partner p ON p.id = l.partner_id""", (self.id,))
        sup, cus = self.env.cr.fetchone()
        return sup > cus

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('sb_partner_account'):
                vals['reconcile'] = True
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('sb_partner_account'):
            vals = dict(vals, reconcile=True)
        return super().write(vals)

    def action_sb_beginning(self):
        self.ensure_one()
        return _bb_action(self.env, tab='accounts', account_id=self.id)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_sb_beginning(self):
        self.ensure_one()
        tab = 'vendors' if self.env.context.get('sb_vendor') or (self.supplier_rank and not self.customer_rank) else 'customers'
        return _bb_action(self.env, tab=tab, partner_id=self.commercial_partner_id.id)
