"""More reports on the engine: customer management detail, electronic payments, other outstanding bank items, the profit
tax worksheet, notes to the financial statements and the period close log."""
from collections import defaultdict

from odoo import fields, models

from .locale import locale_of

CIT = {'ET': 30.0, 'UG': 30.0, 'KE': 30.0}  # corporate income tax, resident companies


class SbReportMore(models.AbstractModel):
    _inherit = 'sb.report'

    # ------------------------------------------------------------------ customers
    def _report_cust_mgmt(self, opts):
        """Per customer: contact, terms, credit limit, balance, overdue, and each open invoice with its due date."""
        dt = self._d(opts['date_to'])
        c = self.env.company
        AML = self.env['account.move.line']
        # the customer accounts of the aged receivables, open as at the date (matching made later undone), so both agree
        lines = AML.search([('company_id', '=', c.id), ('parent_state', '=', 'posted'), ('date', '<=', dt)]
                           + self._partner_acc_dom('customer'), order='date_maturity, date')
        partials = self.env['account.partial.reconcile'].search([
            '|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids), ('max_date', '<=', dt)])
        settled = defaultdict(float)
        for pr in partials:
            settled[pr.debit_move_id.id] += pr.amount
            settled[pr.credit_move_id.id] -= pr.amount
        by = defaultdict(list)
        for l in lines:
            open_amt = (l.balance - settled.get(l.id, 0.0)) if l.account_id.reconcile else l.balance
            if abs(open_amt) > 0.005:
                by[l.partner_id.commercial_partner_id].append((l, open_amt))
        rows, grand = [], defaultdict(float)
        for p in sorted(by, key=lambda x: x.name or ''):
            if not self._search_ok(opts, p.name, p.phone, p.vat):
                continue
            items = by[p]
            bal = sum(amt for _l, amt in items)
            # overdue invoices less the customer's unapplied credits: never more than what is owed, nothing when in credit
            overdue = max(min(bal, sum(amt for l, amt in items if (l.date_maturity or l.date) < dt and amt > 0)), 0.0)
            limit = p.with_company(c).credit_limit
            grand['balance'] += bal
            grand['overdue'] += overdue
            pid = 'p%d' % (p.id or 0)
            name = p.name or 'No partner: items on a customer account without a customer'
            rows.append({'id': pid, 'parent': None, 'level': 0, 'kind': 'group', 'label': name, 'expandable': True,
                         'warning': bool(limit and bal > limit) or overdue > 0.005 or not p,
                         'values': {'name': name, 'contact': ' '.join(x for x in (p.phone, p.email) if x),
                                    'terms': p.with_company(c).property_payment_term_id.name or '', 'limit': limit or 0.0,
                                    'balance': bal, 'overdue': overdue}})
            for l, amt in items:
                days = (dt - (l.date_maturity or l.date)).days
                rows.append({'id': 'l%d' % l.id, 'parent': pid, 'level': 1, 'kind': 'detail', 'label': l.move_id.name,
                             'values': {'name': l.move_id.name, 'contact': fields.Date.to_string(l.date),
                                        'terms': 'due %s%s' % (fields.Date.to_string(l.date_maturity or l.date), (', %d days late' % days) if days > 0 else ''),
                                        'balance': amt, 'overdue': amt if days > 0 and amt > 0 else 0.0},
                             'open': {'model': 'account.move', 'id': l.move_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, name='Total')})
        cols = [{'key': 'name', 'label': 'Customer / invoice', 'type': 'text'}, {'key': 'contact', 'label': 'Contact / date', 'type': 'text'},
                {'key': 'terms', 'label': 'Terms / due', 'type': 'text'}, {'key': 'limit', 'label': 'Credit limit', 'type': 'monetary'},
                {'key': 'balance', 'label': 'Balance', 'type': 'monetary'}, {'key': 'overdue', 'label': 'Overdue', 'type': 'monetary'}]
        return self._doc('cust_mgmt', opts, cols, rows, 'As at %s' % self._period_name(dt, dt), 'Customer Management Detail')

    # ------------------------------------------------------------------ banking
    def _report_epay_register(self, opts):
        """Bank transfers paid out (payments, salary and transfers), by bank account."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows_cash = self.env['sb.voucher.cash'].search([('voucher_id.company_id', '=', self.env.company.id), ('voucher_id.state', '=', 'posted'),
                                                        ('voucher_id.date', '>=', df), ('voucher_id.date', '<=', dt), ('method', '=', 'transfer'),
                                                        ('voucher_id.voucher_type', 'in', ('payment', 'cash_purchase')),
                                                        ('account_id.account_type', '=', 'asset_cash')], order='account_id')
        rows, grand, acc_tot = [], 0.0, defaultdict(float)
        seen = []
        for r in rows_cash.sorted(lambda x: (x.account_id.code or '', x.voucher_id.date, x.voucher_id.name)):
            v = r.voucher_id
            if not self._search_ok(opts, v.name, v.partner_id.name, r.reference, v.memo):
                continue
            key = 'a%d' % r.account_id.id
            if key not in seen:
                seen.append(key)
                rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'group', 'label': r.account_id.display_name, 'expandable': True,
                             'values': {'date': r.account_id.display_name}})
            acc_tot[key] += r.amount
            grand += r.amount
            rows.append({'id': 'c%d' % r.id, 'parent': key, 'level': 1, 'kind': 'line', 'label': v.name,
                         'values': {'date': fields.Date.to_string(v.date), 'number': v.name, 'payee': v.partner_id.name or '',
                                    'ref': r.reference or v.reference or '', 'memo': v.memo or '', 'amount': r.amount},
                         'open': {'model': 'sb.voucher', 'id': v.id}})
        for r in rows:
            if r['kind'] == 'group':
                r['values']['amount'] = acc_tot[r['id']]
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Voucher', 'type': 'text'},
                {'key': 'payee', 'label': 'Paid to', 'type': 'text'}, {'key': 'ref', 'label': 'Transfer ref.', 'type': 'text'},
                {'key': 'memo', 'label': 'For', 'type': 'text'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('epay_register', opts, cols, rows, self._period_name(df, dt), 'Electronic Payment Register')

    def _report_other_outstanding(self, opts):
        """Bank items not cleared by the last reconciliation, other than cheques: deposits in transit, transfers, charges."""
        dt = self._d(opts['date_to'])
        c = self.env.company
        banks = self.env['account.account'].search([('company_ids', 'in', c.id), ('account_type', '=', 'asset_cash')])
        rows, grand = [], 0.0
        for b in banks:
            rec = self.env['sb.bank.rec'].search([('account_id', '=', b.id), ('state', '=', 'done'), ('date', '<=', dt)], order='date desc', limit=1)
            if not rec:
                continue
            open_lines = rec.line_ids.filtered(lambda l: not l.cleared)
            cheque_moves = self.env['sb.voucher.cash'].search([('method', '=', 'cheque')]).voucher_id.move_id
            open_lines = open_lines.filtered(lambda l: l.move_line_id.move_id not in cheque_moves)
            if not open_lines:
                continue
            key = 'b%d' % b.id
            tot = sum(open_lines.mapped('amount'))
            grand += tot
            rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'group', 'label': b.display_name, 'expandable': True,
                         'values': {'date': '%s (reconciled to %s)' % (b.display_name, fields.Date.to_string(rec.date)), 'amount': tot}})
            for l in open_lines:
                rows.append({'id': 'l%d' % l.id, 'parent': key, 'level': 1, 'kind': 'line', 'label': l.move_line_id.move_id.name,
                             'values': {'date': fields.Date.to_string(l.date), 'number': l.move_line_id.move_id.name,
                                        'what': l.name or l.ref or '', 'amount': l.amount,
                                        'kind': 'Deposit in transit' if l.amount > 0 else 'Payment not yet through'}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'text'}, {'key': 'number', 'label': 'Entry', 'type': 'text'},
                {'key': 'kind', 'label': 'Kind', 'type': 'text'}, {'key': 'what', 'label': 'Description', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('other_outstanding', opts, cols, rows, 'At the last reconciliation on or before %s' % fields.Date.to_string(dt),
                         'Other Outstanding Items')

    # ------------------------------------------------------------------ tax and statements
    def _report_profit_tax(self, opts):
        """Accounting profit to taxable profit: depreciation and provisions added back, capital allowances (from the tax
        depreciation schedule) taken off, tax at the country's corporate rate, less withholding credits."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        c = self.env.company
        A = self.env['account.account']
        profit = self._profit(df, dt)
        dep = -self._bal_accounts(A._sb_find('631', c, prefix=True), dt, df, closing=False)
        ecl = -self._bal_accounts(A._sb_find(('590200',), c), dt, df, closing=False)
        loss = -self._bal_accounts(A._sb_find(('590300',), c), dt, df, closing=False)
        gain = self._bal_accounts(A._sb_find(('120300',), c), dt, df, closing=False)
        allowances = float(opts.get('capital_allowances') or 0.0)
        taxable = profit + dep + ecl + loss - gain - allowances
        rate = CIT.get(c.country_id.code or 'ET', 30.0)
        tax = max(taxable, 0.0) * rate / 100.0
        wht = -self._bal_accounts(c.sb_wht_receivable_account_id, dt, df) if c.sb_wht_receivable_account_id else 0.0
        data = [('profit', 'Accounting profit before tax', profit), ('dep', 'Add: depreciation (not deductible; capital allowances instead)', dep),
                ('ecl', 'Add: expected credit loss provision (deductible only when written off)', ecl),
                ('loss', 'Add: loss on disposal of assets', loss), ('gain', 'Less: gain on disposal of assets', -gain),
                ('allow', 'Less: capital allowances (from the tax depreciation schedule; enter in the options)', -allowances),
                ('taxable', 'Taxable profit', taxable), ('tax', 'Income tax at %g%%' % rate, tax),
                ('wht', 'Less: withholding tax suffered in the period (credit)', -wht), ('due', 'Tax payable / (refundable)', tax - wht)]
        rows = [{'id': k, 'parent': None, 'level': 0, 'kind': 'total' if k in ('taxable', 'due') else 'line', 'label': label,
                 'values': {'label': label, 'amount': amt}} for k, label, amt in data]
        cols = [{'key': 'label', 'label': '', 'type': 'text'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('profit_tax', opts, cols, rows, self._period_name(df, dt), 'Profit Tax Worksheet')

    def _report_notes_pack(self, opts):
        """Notes to the financial statements: the accounting policies (Settings), one note per line of the statement of
        financial position with the accounts in it (from the statement itself: same IFRS mapping, signs, overdrafts and
        unclosed results, so each note equals its line), the income statement notes (revenue, cost of sales, expenses by
        nature, other income, finance, income tax) and the movement of property, plant and equipment. The comparatives
        follow the comparison chosen (previous period / same period last year), by default the previous fiscal year-end."""
        from datetime import timedelta
        dt = self._d(opts['date_to'])
        base = dict(opts, comparison='none', search='')
        if opts.get('comparison') in ('previous', 'last_year'):
            # the same dates as the statement of financial position with this comparison
            ends = [c['date_to'] for c in self._column_periods(opts, as_of=True) if not c.get('is_total')]
        else:
            ends = [dt, self._fy_start(dt) - timedelta(days=1)]
        keys = ['p%d' % i for i in range(len(ends))]
        docs = [self._report_balance_sheet(dict(base, date_from=fields.Date.to_string(self._fy_start(d)),
                                                date_to=fields.Date.to_string(d))) for d in ends]
        tops = {'assets': 'Assets', 'equity': 'Equity', 'liabilities': 'Liabilities'}
        groups, order, children, top_of = {}, [], defaultdict(list), {}
        parent_top = {}
        for n, doc in enumerate(docs):
            by_id = {r['id']: r for r in doc['rows']}
            for r in doc['rows']:
                p = r.get('parent')
                if r['kind'] == 'section' and p in tops:
                    parent_top[r['id']] = p
                if r['kind'] == 'group' and p:
                    if r['id'] not in groups:
                        groups[r['id']] = {'label': r['label'], 'code': r.get('code'), 'values': {}}
                        order.append(r['id'])
                        top_of[r['id']] = parent_top.get(p) or by_id.get(p, {}).get('parent')
                    groups[r['id']]['values'][keys[n]] = r['values'].get('p0', 0.0)
                elif p in groups and r['kind'] == 'line':
                    kids = children[p]
                    kid = next((k for k in kids if k['id'] == r['id']), None)
                    if not kid:
                        kid = {'id': r['id'], 'label': r['label'], 'code': r.get('code'), 'values': {}, 'drill': {}}
                        kids.append(kid)
                    kid['values'][keys[n]] = r['values'].get('p0', 0.0)
                    kid['drill'][keys[n]] = r.get('drill')  # each column drills to its own date's ledger
        rows, counter = [], [0]

        def note(sid, title, vals, lines, blurb=None):
            if vals is not None and not any(abs(vals.get(k, 0.0)) > 0.005 for k in keys) and not any(
                    any(abs(v.get(k, 0.0)) > 0.005 for k in keys) for _i, _l, v, _d, _k in lines):
                return
            if not self._search_ok(opts, title, *[l[1] for l in lines]):
                return
            counter[0] += 1
            label = '%d. %s' % (counter[0], title)
            rows.append({'id': sid, 'parent': None, 'level': 0, 'kind': 'section', 'label': label, 'expandable': True,
                         'values': dict(vals or {}, label=label)})
            if blurb:
                rows.append({'id': sid + '_blurb', 'parent': sid, 'level': 1, 'kind': 'note', 'label': blurb, 'values': {'label': blurb}})
            for lid, text, v, drill, kind in lines:
                kv = {k: v.get(k, 0.0) for k in keys} if v is not None else {}
                if kind == 'line' and not any(abs(x) > 0.005 for x in kv.values()):
                    continue
                drills = None
                if isinstance(drill, dict) and 'report' not in drill:
                    # one drill per column (a column's own date or fiscal year), never the first column's for all
                    drills = {k: drill.get(k) for k in keys}
                    drill = drills.get(keys[0]) or next((d for d in drills.values() if d), None)
                rows.append({'id': '%s_%s' % (sid, lid), 'parent': sid, 'level': 1, 'kind': kind, 'label': text,
                             'values': dict(kv, label=text), 'drill': drill, 'drills': drills})

        # 1. accounting policies: the text of Settings, one paragraph a line
        policies = [p.strip() for p in (self.env.company.sb_fs_policies or '').splitlines() if p.strip()]
        if policies and self._search_ok(opts, 'Accounting policies', *policies):
            counter[0] += 1
            label = '%d. Accounting policies' % counter[0]
            rows.append({'id': 'n_policies', 'parent': None, 'level': 0, 'kind': 'section', 'label': label, 'expandable': True,
                         'values': {'label': label}})
            for i, text in enumerate(policies):
                rows.append({'id': 'n_policies_%d' % i, 'parent': 'n_policies', 'level': 1, 'kind': 'note', 'label': text,
                             'values': {'label': text}})

        # 2. the statement of financial position, line by line
        for top in tops:
            for gid in [g for g in order if top_of.get(g) == top]:
                g = groups[gid]
                note('n_%s' % gid, g['label'], {k: g['values'].get(k, 0.0) for k in keys},
                     [(k['id'], k['label'], k['values'], k.get('drill'), 'line') for k in children[gid]])

        # 3. the income statement: each column the fiscal year to its date, as the statement of profit or loss
        pls = [self._report_profit_loss(dict(base, date_from=fields.Date.to_string(self._fy_start(d)), date_to=fields.Date.to_string(d)))
               for d in ends]
        pl_rows = {}
        for n, doc in enumerate(pls):
            for r in doc['rows']:
                e = pl_rows.setdefault(r['id'], {'row': r, 'values': {}, 'drills': {}})
                e['values'][keys[n]] = r['values'].get('p0', 0.0)
                e['drills'][keys[n]] = r.get('drill')
        pl_order = []
        for doc in pls:
            for r in doc['rows']:
                if r['id'] not in pl_order:
                    pl_order.append(r['id'])
        pl_titles = [('revenue', 'Revenue', None),
                     ('cost_of_sales', 'Cost of sales', None),
                     ('other_income', 'Other income', None),
                     ('operating_expense', 'Operating expenses by nature', 'By account, grouped by the line of the income statement.'),
                     ('finance', 'Finance income and costs', None),
                     ('income_tax', 'Income tax expense', None),
                     ('unmapped', 'Income and expenses not mapped to IFRS', None)]
        for sec, title, blurb in pl_titles:
            if sec not in pl_rows:
                continue
            lines = []
            for rid in pl_order:
                e = pl_rows[rid]
                r = e['row']
                parent = r.get('parent')
                if parent == sec and r['kind'] == 'group':
                    lines.append((rid, r['label'], e['values'], None, 'group'))
                    for cid in pl_order:
                        c = pl_rows[cid]['row']
                        if c.get('parent') == rid:
                            lines.append((cid, c['label'], pl_rows[cid]['values'], pl_rows[cid]['drills'], 'line'))
                elif parent == sec and r['kind'] == 'line':
                    lines.append((rid, r['label'], e['values'], e['drills'], 'line'))
            if sec != 'operating_expense':
                lines = [l for l in lines if l[4] == 'line'] if sum(1 for l in lines if l[4] == 'group') <= 1 else lines
            note('n_pl_%s' % sec, title, pl_rows[sec]['values'], lines, blurb)

        # 4. property, plant and equipment: cost and depreciation, beginning to end of each fiscal year
        ppe = self._ppe_movement(opts, ends)
        if ppe:
            k = keys
            mv = [('cost_open', 'Cost at the beginning of the year', 'line'), ('additions', 'Additions', 'line'),
                  ('disposals', 'Disposals and transfers out', 'line'), ('cost_close', 'Cost at the end of the year', 'group'),
                  ('dep_open', 'Accumulated depreciation at the beginning of the year', 'line'),
                  ('dep_charge', 'Depreciation charge for the year', 'line'),
                  ('dep_reversed', 'Depreciation reversed (corrections of earlier charges)', 'line'),
                  ('dep_disposals', 'Eliminated on disposals', 'line'),
                  ('dep_close', 'Accumulated depreciation at the end of the year', 'group'),
                  ('carrying', 'Carrying amount at the end of the year', 'group')]
            lines = [(key, text, {k[i]: ppe[i][key] for i in range(len(ends))},
                      {k[i]: ppe[i]['drills'].get(key) for i in range(len(ends))}, kind) for key, text, kind in mv]
            note('n_ppe', 'Property, plant and equipment: movement', {k[i]: ppe[i]['carrying'] for i in range(len(ends))},
                 lines, 'Cost and accumulated depreciation of the accounts on the property, plant and equipment line; the '
                        'carrying amount equals that line of the statement of financial position.')

        basis = 'Each note on the statement of financial position is its line of the same name, at the same date; the income ' \
                'statement notes are the fiscal year to that date.'
        rows.append({'id': 'basis', 'parent': None, 'level': 0, 'kind': 'note', 'label': basis, 'values': {'label': basis}})
        cols = [{'key': 'label', 'label': '', 'type': 'text'}] + [
            {'key': keys[i], 'label': 'As at %s' % d.strftime('%d %b %Y'), 'type': 'monetary',
             'sub': 'previous fiscal year-end' if i and opts.get('comparison') not in ('previous', 'last_year') else ''}
            for i, d in enumerate(ends)]
        return self._doc('notes_pack', opts, cols, rows, 'As at %s' % dt.strftime('%d %B %Y'), 'Notes to the Financial Statements')

    def _ppe_movement(self, opts, ends):
        """Per fiscal year to each date: the cost and the accumulated depreciation of the property, plant and equipment
        accounts (IFRS line SFP-NCA-01), opening, additions (debits), disposals (credits), depreciation, closing. Balances
        brought over from the old books count in the opening."""
        company = self.env.company
        accs = self.env['account.account'].search([('company_ids', 'in', company.id), ('sb_ifrs_line_id.code', '=', 'SFP-NCA-01')])
        if not accs:
            return []
        dep = accs & self._depreciation_accounts()
        cost = accs - dep
        brought = list(self._opening_move_ids())
        out = []
        for d in ends:
            df = self._fy_start(d)
            d0, d1 = fields.Date.to_string(df), fields.Date.to_string(d)
            dom = self._base_domain(dict(opts, exclude_closing=True, journal_ids=[], partner_ids=[], account_ids=[]), None, d)
            opening = self._sums(dom + [('account_id', 'in', accs.ids), '|', ('date', '<', df), ('move_id', 'in', brought)])
            moves = self._sums(dom + [('account_id', 'in', accs.ids), ('date', '>=', df), ('move_id', 'not in', brought)])

            def tot(src, ids, k):
                return sum(src.get(i, {}).get(k, 0.0) for i in ids)
            # depreciation by what each entry did: the charge, its corrections, and what disposals eliminated (a
            # correction of an earlier charge is no disposal), the same split as the statement of cash flows
            split = self._dep_split(dict(opts, journal_ids=[], partner_ids=[]), df, d, set(dep.ids), brought)
            r = {'cost_open': tot(opening, cost.ids, 'balance'), 'additions': tot(moves, cost.ids, 'debit'),
                 'disposals': -tot(moves, cost.ids, 'credit'),
                 'dep_open': tot(opening, dep.ids, 'balance'),
                 'dep_charge': -sum(v['charge'] for v in split.values()),
                 'dep_reversed': -sum(v['reversed'] for v in split.values()),
                 'dep_disposals': sum(v['disposal'] for v in split.values())}
            r['cost_close'] = r['cost_open'] + r['additions'] + r['disposals']
            # anything the split does not explain (none expected) stays with the charge, so the closing is the ledger's
            r['dep_charge'] += tot(moves, dep.ids, 'balance') - (r['dep_charge'] + r['dep_reversed'] + r['dep_disposals'])
            r['dep_close'] = r['dep_open'] + r['dep_charge'] + r['dep_reversed'] + r['dep_disposals']
            r['carrying'] = r['cost_close'] + r['dep_close']
            gl = lambda ids, **kw: dict({'report': 'general_ledger', 'account_ids': ids.ids, 'date_from': d0, 'date_to': d1}, **kw)  # noqa: E731
            r['drills'] = {'additions': gl(cost), 'disposals': gl(cost), 'dep_charge': gl(dep), 'dep_reversed': gl(dep),
                           'dep_disposals': gl(dep),
                           'cost_close': gl(cost), 'dep_close': gl(dep)}
            out.append(r)
        return out

    # ------------------------------------------------------------------ company
    def _report_period_log(self, opts):
        """Every close, reopen and lock date change (who, when, the date before and after), the lock dates now, and lock
        exceptions granted."""
        c = self.env.company
        rows = []
        Log = self.env['sb.lock.log'].sudo()
        events = dict(Log._fields['event']._description_selection(self.env))
        locks = dict(Log._fields['lock_field']._description_selection(self.env))
        for x in Log.search([('company_id', '=', c.id)], order='logged_at, id'):
            what = '%s: %s' % (events[x.event], locks.get(x.lock_field, '')) if x.lock_field else events[x.event]
            rows.append({'id': 'l%d' % x.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': what,
                         'values': {'what': what + (' (%s)' % x.note if x.note else ''),
                                    'was': fields.Date.to_string(x.old_date) if x.old_date else 'not set',
                                    'date': fields.Date.to_string(x.new_date) if x.new_date else '',
                                    'who': x.user_id.name, 'when': fields.Datetime.to_string(x.logged_at)[:16], 'amount': 0.0}})
        for v in self.env['sb.voucher'].search([('company_id', '=', c.id), ('reference', '=like', 'YEAR-END%')], order='date'):
            rows.append({'id': 'v%d' % v.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.reference,
                         'values': {'what': 'Closing entry %s: %s%s' % (v.name, v.reference, ' (void)' if v.state == 'void' else ''),
                                    'was': '', 'date': fields.Date.to_string(v.date), 'who': v.create_uid.name,
                                    'when': fields.Datetime.to_string(v.create_date)[:16],
                                    'amount': 0.0 if v.state == 'void' else v.amount_total},
                         'open': {'model': 'sb.voucher', 'id': v.id}})
        for f, label in (('fiscalyear_lock_date', 'Books locked for everyone up to'), ('tax_lock_date', 'Tax returns locked up to'),
                         ('sale_lock_date', 'Sales locked up to'), ('purchase_lock_date', 'Purchases locked up to'),
                         ('hard_lock_date', 'Books locked permanently up to')):
            if f in c._fields and c[f]:
                rows.append({'id': f, 'parent': None, 'level': 0, 'kind': 'line', 'label': label,
                             'values': {'what': 'Now: %s' % label, 'was': '', 'date': fields.Date.to_string(c[f]), 'who': '',
                                        'when': '', 'amount': 0.0}})
        if 'account.lock_exception' in self.env:
            for x in self.env['account.lock_exception'].sudo().search([('company_id', '=', c.id)], order='create_date'):
                rows.append({'id': 'x%d' % x.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': 'Exception',
                             'values': {'what': 'Lock exception: %s' % (x.reason or ''), 'was': '',
                                        'date': fields.Date.to_string(x.end_datetime.date()) if x.end_datetime else '',
                                        'who': x.create_uid.name, 'when': fields.Datetime.to_string(x.create_date)[:16], 'amount': 0.0}})
        cols = [{'key': 'what', 'label': 'Event', 'type': 'text'}, {'key': 'was', 'label': 'Was', 'type': 'text'},
                {'key': 'date', 'label': 'Period / date', 'type': 'date'},
                {'key': 'who', 'label': 'By', 'type': 'text'}, {'key': 'when', 'label': 'When', 'type': 'text'},
                {'key': 'amount', 'label': 'Result closed', 'type': 'monetary'}]
        return self._doc('period_log', opts, cols, rows, c.name, 'Period Close Log')
