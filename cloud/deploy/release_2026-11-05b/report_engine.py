"""the report engine: one engine, one JSON report document, three renderers (OWL screen, Excel, PDF).

A report document:
    {
      'key', 'title', 'subtitle', 'company', 'currency',
      'columns': [{'key', 'label', 'type': 'text'|'date'|'monetary'|'number', 'sub': ''}],
      'rows': [{'id', 'parent': id|None, 'level', 'kind': 'section'|'group'|'line'|'total'|'detail'|'note',
                'label', 'code', 'values': {col: value}, 'expandable', 'lazy': {...}|None,
                'drill': {...}|None, 'open': {...}|None}],
      'options': {...}   # the normalised filters actually used
    }
"""
from collections import defaultdict
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_description, fy_label, gregorian

REPORTS = {
    'trial_balance': 'Trial Balance',
    'general_ledger': 'General Ledger',
    'account_transactions': 'Account Transactions',
    'profit_loss': 'Income Statement',
    'balance_sheet': 'Statement of Financial Position',
    'aged': 'Aged Balances',
    'partner_ledger': 'Customer / Vendor Ledger',
    'cash_book': 'Cash and Bank Book',
}

ACCOUNT_SECTIONS = [
    ('assets', 'Assets', ('asset_receivable', 'asset_cash', 'asset_current', 'asset_non_current',
                          'asset_prepayments', 'asset_fixed')),
    ('liabilities', 'Liabilities', ('liability_payable', 'liability_credit_card', 'liability_current',
                                    'liability_non_current')),
    ('equity', 'Equity', ('equity', 'equity_unaffected')),
    ('income', 'Income', ('income', 'income_other')),
    ('expenses', 'Expenses', ('expense', 'expense_depreciation', 'expense_direct_cost', 'off_balance')),
]



class SbReport(models.AbstractModel):
    _name = 'sb.report'
    _description = 'Reports engine'

    # ================================================================== periods for the filter bar
    @api.model
    def get_periods(self):
        today = fields.Date.context_today(self)
        current = fiscal_year_of(today)
        years = []
        for fy in range(current - 3, current + 2):
            periods = fiscal_periods(fy)
            months = [{'id': '%d-%02d' % (fy, no), 'fy': fy, 'no': no, 'name': name,
                       'date_from': fields.Date.to_string(start), 'date_to': fields.Date.to_string(end),
                       'label': '%s (%s - %s)' % (name, start.strftime('%d %b %Y'), end.strftime('%d %b %Y'))}
                      for no, name, start, end in periods]
            quarters = []
            for q in range(4):
                chunk = months[q * 3:q * 3 + 3]
                quarters.append({'id': '%d-Q%d' % (fy, q + 1), 'fy': fy, 'name': 'Q%d FY %d (%s - %s)' % (
                    q + 1, fy, chunk[0]['name'].split()[0], chunk[-1]['name'].split()[0]),
                    'date_from': chunk[0]['date_from'], 'date_to': chunk[-1]['date_to']})
            years.append({'fy': fy, 'name': fy_label(fy), 'date_from': months[0]['date_from'],
                          'date_to': months[-1]['date_to'], 'months': months, 'quarters': quarters})
        current_period = next((m for y in years for m in y['months']
                               if m['date_from'] <= fields.Date.to_string(today) <= m['date_to']), None)
        return {'years': years, 'current_fy': current, 'current_period': current_period,
                'today': fields.Date.to_string(today), 'today_ec': format_ethiopian(today)}

    # ================================================================== options
    def _options(self, options):
        opts = dict(options or {})
        today = fields.Date.context_today(self)
        if not opts.get('date_to'):
            opts['date_to'] = fields.Date.to_string(today)
        if not opts.get('date_from'):
            fy = fiscal_year_of(fields.Date.to_date(opts['date_to']))
            opts['date_from'] = fields.Date.to_string(fiscal_periods(fy)[0][2])
        opts.setdefault('posted_only', True)
        opts.setdefault('hide_zero', True)
        opts.setdefault('comparison', 'none')
        opts.setdefault('comparison_count', 1)
        opts.setdefault('journal_ids', [])
        opts.setdefault('partner_ids', [])
        opts.setdefault('account_ids', [])
        opts.setdefault('search', '')
        return opts

    def _d(self, value):
        return fields.Date.to_date(value)

    def _label_range(self, date_from, date_to):
        df, dt = self._d(date_from), self._d(date_to)
        return '%s - %s' % (df.strftime('%d %b %Y'), dt.strftime('%d %b %Y'))

    def _label_range_ec(self, date_from, date_to):
        return '%s - %s' % (format_ethiopian(self._d(date_from)), format_ethiopian(self._d(date_to)))

    def _column_periods(self, opts, as_of=False):
        """Date ranges for the value columns: the main period plus comparisons.
        Returns [{'key', 'label', 'sub', 'date_from', 'date_to'}]."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        cols = [{'key': 'p0', 'date_from': df, 'date_to': dt}]
        mode, count = opts.get('comparison'), max(1, min(int(opts.get('comparison_count') or 1), 24))
        if mode == 'previous':
            length = (dt - df).days + 1
            cur_from, cur_to = df, dt
            for i in range(count):
                # previous period of the same Ethiopian-month shape when the range is whole periods
                prev_to = cur_from - timedelta(days=1)
                prev_from = self._shift_back(cur_from, cur_to, prev_to, length)
                cols.append({'key': 'p%d' % (i + 1), 'date_from': prev_from, 'date_to': prev_to})
                cur_from, cur_to = prev_from, prev_to
        elif mode == 'last_year':
            for i in range(count):
                cols.append({'key': 'p%d' % (i + 1), 'date_from': self._minus_years(df, i + 1),
                             'date_to': self._minus_years(dt, i + 1)})
        elif mode == 'months':
            # one column per Ethiopian month inside the selected range (e.g. Hamle ... Sene)
            months = []
            fy0, fy1 = fiscal_year_of(df), fiscal_year_of(dt)
            for fy in range(fy0, fy1 + 1):
                for no, name, start, end in fiscal_periods(fy):
                    if end >= df and start <= dt:
                        months.append((name, max(start, df), min(end, dt)))
            cols = [{'key': 'm%d' % i, 'date_from': s, 'date_to': e, 'label': n} for i, (n, s, e) in enumerate(months)]
        for c in cols:
            if as_of:
                c.setdefault('label', 'As at %s' % c['date_to'].strftime('%d %b %Y'))
                c['sub'] = format_ethiopian(c['date_to'])
            else:
                c.setdefault('label', self._period_name(c['date_from'], c['date_to']))
                c['sub'] = self._label_range(c['date_from'], c['date_to'])
        if mode == 'months' and len(cols) > 1:
            cols.append({'key': 'total', 'label': 'Total', 'sub': self._label_range(df, dt),
                         'date_from': df, 'date_to': dt, 'is_total': True})
        return cols

    def _shift_back(self, cur_from, cur_to, prev_to, length):
        # when the current range is exactly N Ethiopian periods, go back N periods
        def period_index(d):
            fy = fiscal_year_of(d)
            for no, _n, s, e in fiscal_periods(fy):
                if s <= d <= e:
                    return fy, no, s, e
        a, b = period_index(cur_from), period_index(cur_to)
        if a and b and a[2] == cur_from and b[3] == cur_to:
            n = (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1
            fy, no = a[0], a[1]
            for _ in range(n):
                no -= 1
                if no == 0:
                    fy, no = fy - 1, 12
            return fiscal_periods(fy)[no - 1][2]
        return prev_to - timedelta(days=length - 1)

    def _minus_years(self, d, n):
        try:
            return d.replace(year=d.year - n)
        except ValueError:
            return d.replace(year=d.year - n, day=28)

    def _period_name(self, df, dt):
        """'Tikimt 2019', 'FY 2019 E.C.', 'Hamle - Tikimt 2019' or a date range."""
        fy0, fy1 = fiscal_year_of(df), fiscal_year_of(dt)
        p0 = next(((no, n) for no, n, s, e in fiscal_periods(fy0) if s == df), None)
        p1 = next(((no, n) for no, n, s, e in fiscal_periods(fy1) if e == dt), None)
        if p0 and p1:
            if fy0 == fy1 and p0[0] == 1 and p1[0] == 12:
                return fy_label(fy0)
            if p0 == p1 and fy0 == fy1:
                return p0[1]
            return '%s - %s' % (p0[1], p1[1])
        return self._label_range(df, dt)

    # ================================================================== ledger access
    def _base_domain(self, opts, date_from=None, date_to=None, include_from=True):
        company = self.env.company
        dom = [('company_id', '=', company.id), ('display_type', 'not in', ('line_section', 'line_note'))]
        dom.append(('parent_state', '=', 'posted') if opts.get('posted_only', True)
                   else ('parent_state', 'in', ('posted', 'draft')))
        if date_from:
            dom.append(('date', '>=' if include_from else '>', date_from))
        if date_to:
            dom.append(('date', '<=', date_to))
        if opts.get('journal_ids'):
            dom.append(('journal_id', 'in', opts['journal_ids']))
        if opts.get('partner_ids'):
            dom.append(('partner_id', 'in', opts['partner_ids']))
        if opts.get('account_ids'):
            dom.append(('account_id', 'in', opts['account_ids']))
        if opts.get('exclude_closing'):
            dom.append(('move_id.sb_closing', '=', False))
        return dom

    def _sums(self, domain, groupby=('account_id',)):
        aml = self.env['account.move.line']
        res = {}
        for row in aml._read_group(domain, list(groupby), ['debit:sum', 'credit:sum', 'balance:sum']):
            key = tuple(r.id if hasattr(r, 'id') else r for r in row[:len(groupby)])
            key = key[0] if len(groupby) == 1 else key
            res[key] = {'debit': row[-3] or 0.0, 'credit': row[-2] or 0.0, 'balance': row[-1] or 0.0}
        return res

    def _fy_start(self, d):
        return fiscal_periods(fiscal_year_of(d))[0][2]

    def _opening(self, opts, date_from, groupby=('account_id',), extra=None):
        """Opening balances: balance sheet accounts since the beginning, P&L accounts since the fiscal year start."""
        extra = extra or []
        before = date_from - timedelta(days=1)
        bs = self._sums(self._base_domain(opts, None, before) + extra
                        + [('account_id.include_initial_balance', '=', True)], groupby)
        fy_start = self._fy_start(date_from)
        if fy_start < date_from:
            pl = self._sums(self._base_domain(opts, fy_start, before) + extra
                            + [('account_id.include_initial_balance', '=', False)], groupby)
            for k, v in pl.items():
                bs[k] = v
        return bs

    def _accounts(self, ids=None):
        Account = self.env['account.account'].with_company(self.env.company)
        dom = [('company_ids', 'in', self.env.company.id)]
        if ids is not None:
            dom.append(('id', 'in', list(ids)))
        return Account.search(dom).sorted(lambda a: a.code or '')

    def _search_ok(self, opts, *texts):
        s = (opts.get('search') or '').strip().lower()
        return not s or any(s in (t or '').lower() for t in texts)

    def _doc(self, key, opts, columns, rows, subtitle=None, title=None):
        company = self.env.company
        if gregorian():
            # the Ethiopian-date column only repeats the date in a Gregorian database
            columns = [c for c in columns if not c.get('key', '').endswith('_ec')]
        return {
            'key': key,
            'title': title or REPORTS.get(key, key),
            'subtitle': subtitle or '',
            'company': company.name,
            'currency': company.currency_id.symbol or company.currency_id.name,
            'columns': columns,
            'rows': rows,
            'options': {k: (fields.Date.to_string(v) if isinstance(v, date) else v) for k, v in opts.items()},
            'printed': fields.Datetime.to_string(fields.Datetime.now()),
            'printed_ec': format_ethiopian(fields.Date.context_today(self)),
        }

    # ================================================================== entry point
    @api.model
    def get_report(self, key, options=None, expanded=None):
        opts = self._options(options)
        method = getattr(self, '_report_%s' % key, None)
        if not method:
            raise UserError(self.env._('Unknown report %s', key))
        doc = method(opts)
        if opts.get('_title'):
            doc['title'] = opts['_title']
        if expanded:
            self._resolve_expanded(doc, set(expanded))
        return doc

    @api.model
    def get_children(self, lazy, options=None):
        """Rows for a lazily expanded row (e.g. GL lines of one account)."""
        opts = self._options(options)
        method = getattr(self, '_lazy_%s' % lazy['kind'])
        return method(opts, lazy)

    def _resolve_expanded(self, doc, expanded):
        out = []
        for row in doc['rows']:
            out.append(row)
            if row.get('lazy') and row['id'] in expanded:
                children = self.get_children(row['lazy'], doc['options'])
                for c in children:
                    c['parent'] = row['id']
                out.extend(children)
                row['loaded'] = True
        doc['rows'] = out

    # ================================================================== Trial balance
    def _report_trial_balance(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        opening = self._opening(opts, df)
        moves = self._sums(self._base_domain(opts, df, dt))
        cols = [
            {'key': 'code', 'label': 'Account', 'type': 'text'},
            {'key': 'name', 'label': 'Description', 'type': 'text'},
            {'key': 'opening', 'label': 'Opening Balance', 'type': 'monetary', 'sub': 'before %s' % df.strftime('%d %b %Y')},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary', 'sub': self._period_name(df, dt)},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary', 'sub': self._period_name(df, dt)},
            {'key': 'closing', 'label': 'Closing Balance', 'type': 'monetary', 'sub': 'at %s' % dt.strftime('%d %b %Y')},
            {'key': 'closing_dr', 'label': 'Closing Debit', 'type': 'monetary'},
            {'key': 'closing_cr', 'label': 'Closing Credit', 'type': 'monetary'},
        ]
        rows, grand = [], defaultdict(float)
        accounts = self._accounts(set(opening) | set(moves))
        for skey, slabel, types in ACCOUNT_SECTIONS:
            sec_accounts = accounts.filtered(lambda a: a.account_type in types)
            sec_rows, tot = [], defaultdict(float)
            for a in sec_accounts:
                o = opening.get(a.id, {}).get('balance', 0.0)
                m = moves.get(a.id, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
                c = o + m['balance']
                vals = {'code': a.code, 'name': a.name, 'opening': o, 'debit': m['debit'], 'credit': m['credit'],
                        'closing': c, 'closing_dr': c if c > 0 else 0.0, 'closing_cr': -c if c < 0 else 0.0}
                if opts.get('hide_zero') and not any(abs(vals[k]) > 0.005 for k in ('opening', 'debit', 'credit', 'closing')):
                    continue
                if not self._search_ok(opts, a.code, a.name):
                    continue
                for k in ('opening', 'debit', 'credit', 'closing', 'closing_dr', 'closing_cr'):
                    tot[k] += vals[k]
                sec_rows.append({'id': 'a%d' % a.id, 'parent': skey, 'level': 1, 'kind': 'line', 'label': a.name,
                                 'code': a.code, 'values': vals,
                                 'drill': {'report': 'general_ledger', 'account_ids': [a.id],
                                           'date_from': opts['date_from'], 'date_to': opts['date_to']}})
            if skey == 'equity':
                # results of earlier fiscal years not yet closed to retained earnings (P&L accounts restart each year)
                fy_start = self._fy_start(df)
                old = self._sums(self._base_domain(opts, None, fy_start - timedelta(days=1))
                                 + [('account_id.include_initial_balance', '=', False)], ())
                prior = old.get((), {}).get('balance', 0.0) if old else 0.0
                if abs(prior) > 0.005:
                    vals = {'code': '', 'name': "Prior years' results not yet closed", 'opening': prior, 'debit': 0.0,
                            'credit': 0.0, 'closing': prior, 'closing_dr': prior if prior > 0 else 0.0,
                            'closing_cr': -prior if prior < 0 else 0.0}
                    for k in ('opening', 'closing', 'closing_dr', 'closing_cr'):
                        tot[k] += vals[k]
                    sec_rows.append({'id': 'prior_results', 'parent': skey, 'level': 1, 'kind': 'line',
                                     'label': '', 'values': vals,
                                     'drill': {'report': 'profit_loss', 'date_from': '1900-01-01',
                                               'date_to': fields.Date.to_string(fy_start - timedelta(days=1))}})
            if not sec_rows:
                continue
            rows.append({'id': skey, 'parent': None, 'level': 0, 'kind': 'section', 'label': slabel,
                         'values': {'code': slabel, **dict(tot)}, 'expandable': True})
            rows += sec_rows
            for k, v in tot.items():
                grand[k] += v
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'code': 'Total', **dict(grand)}})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('trial_balance', opts, cols, rows, sub)

    # ================================================================== General ledger
    def _report_general_ledger(self, opts, cash_only=False):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        extra = [('account_id.account_type', '=', 'asset_cash')] if cash_only else []
        opening = self._opening(opts, df, extra=extra)
        moves = self._sums(self._base_domain(opts, df, dt) + extra)
        cols = [
            {'key': 'date', 'label': 'Date', 'type': 'date'},
            {'key': 'date_ec', 'label': 'E.C. Date', 'type': 'text'},
            {'key': 'number', 'label': 'Reference', 'type': 'text'},
            {'key': 'jrnl', 'label': 'Jrnl', 'type': 'text'},
            {'key': 'partner', 'label': 'Customer / Vendor', 'type': 'text'},
            {'key': 'name', 'label': 'Description', 'type': 'text'},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary'},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary'},
            {'key': 'balance', 'label': 'Balance', 'type': 'monetary'},
        ]
        rows, grand = [], defaultdict(float)
        ids = set(opening) | set(moves)
        if opts.get('account_ids'):
            ids &= set(opts['account_ids'])
        for a in self._accounts(ids):
            if cash_only and a.account_type != 'asset_cash':
                continue
            o = opening.get(a.id, {}).get('balance', 0.0)
            m = moves.get(a.id, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
            c = o + m['balance']
            if opts.get('hide_zero') and abs(o) < 0.005 and abs(m['debit']) < 0.005 and abs(m['credit']) < 0.005:
                continue
            if not self._search_ok(opts, a.code, a.name):
                continue
            rows.append({'id': 'a%d' % a.id, 'parent': None, 'level': 0, 'kind': 'group',
                         'label': '%s %s' % (a.code, a.name), 'code': a.code,
                         'values': {'date': '', 'number': a.code, 'name': a.name, 'debit': m['debit'],
                                    'credit': m['credit'], 'balance': c, 'opening': o},
                         'expandable': True,
                         'lazy': {'kind': 'gl_lines', 'account_id': a.id, 'opening': o}})
            grand['debit'] += m['debit']
            grand['credit'] += m['credit']
            grand['balance'] += c
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'number': 'Total', **dict(grand)}})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('cash_book' if cash_only else 'general_ledger', opts, cols, rows, sub)

    def _report_cash_book(self, opts):
        return self._report_general_ledger(opts, cash_only=True)

    def _voucher_map(self, moves):
        vouchers = self.env['sb.voucher'].search([('move_id', 'in', moves.ids)])
        return {v.move_id.id: v for v in vouchers}

    def _lazy_gl_lines(self, opts, lazy):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        aml = self.env['account.move.line'].search(
            self._base_domain(opts, df, dt) + [('account_id', '=', lazy['account_id'])], order='date, move_name, id')
        vmap = self._voucher_map(aml.move_id)
        bal = lazy.get('opening', 0.0)
        out = [{'id': 'o%d' % lazy['account_id'], 'level': 1, 'kind': 'detail', 'label': 'Opening balance',
                'values': {'name': 'Opening balance', 'balance': bal}}]
        for l in aml:
            bal += l.balance
            v = vmap.get(l.move_id.id)
            out.append({
                'id': 'l%d' % l.id, 'level': 1, 'kind': 'detail', 'label': l.move_id.name,
                'values': {'date': fields.Date.to_string(l.date), 'date_ec': format_ethiopian(l.date),
                           'number': l.move_id.name, 'jrnl': l.journal_id.code,
                           'partner': l.partner_id.name or '', 'name': l.name or l.move_id.ref or '',
                           'debit': l.debit, 'credit': l.credit, 'balance': bal},
                'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id},
            })
        out.append({'id': 'c%d' % lazy['account_id'], 'level': 1, 'kind': 'detail', 'label': 'Closing balance',
                    'values': {'name': 'Closing balance', 'balance': bal}, 'closing': True})
        return out

    # ================================================================== one account's transactions (Chart of Accounts > View)
    def _report_account_transactions(self, opts):
        """One account, flat: balance brought forward, every entry (date, number, customer / vendor, description, debit,
        credit, running balance; a double-click opens its voucher), balance carried forward."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        account = self.env['account.account'].browse((opts.get('account_ids') or [0])[:1]).exists()
        if not account:
            raise UserError(self.env._('Choose an account.'))
        opening = self._opening(opts, df).get(account.id, {}).get('balance', 0.0)
        cols = [
            {'key': 'date', 'label': 'Date', 'type': 'date'},
            {'key': 'date_ec', 'label': 'E.C. Date', 'type': 'text'},
            {'key': 'number', 'label': 'Number', 'type': 'text'},
            {'key': 'partner', 'label': 'Customer / Vendor', 'type': 'text'},
            {'key': 'name', 'label': 'Description', 'type': 'text'},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary'},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary'},
            {'key': 'balance', 'label': 'Balance', 'type': 'monetary'},
        ]
        lines = self._lazy_gl_lines(opts, {'kind': 'gl_lines', 'account_id': account.id, 'opening': opening})
        rows, dr, cr = [], 0.0, 0.0
        for l in lines:
            r = dict(l, parent=None, level=0, kind='line')
            if l['id'].startswith('o'):
                r['values'] = dict(l['values'], name='Balance brought forward', date=fields.Date.to_string(df),
                                   date_ec=format_ethiopian(df))
                r['kind'] = 'group'
            elif l['id'].startswith('c'):
                continue
            else:
                if not r['values'].get('partner'):
                    r['values']['partner'] = ''
                dr += l['values'].get('debit') or 0.0
                cr += l['values'].get('credit') or 0.0
            rows.append(r)
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': fields.Date.to_string(dt),
                     'values': {'date': fields.Date.to_string(dt), 'date_ec': format_ethiopian(dt), 'number': '', 'name': 'Balance carried forward',
                                'debit': dr, 'credit': cr, 'balance': opening + dr - cr, 'opening': opening}})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('account_transactions', opts, cols, rows, sub, title='%s %s' % (account.code, account.name))

    # ================================================================== Income statement (IFRS lines)
    def _ifrs_lines(self, statement):
        return self.env['sb.ifrs.line'].search([('statement', '=', statement)])

    def _report_profit_loss(self, opts):
        opts = dict(opts, exclude_closing=True)  # year-end closing entries are not income or expense
        cols_p = self._column_periods(opts)
        acc_sums = {}
        for c in cols_p:
            if c.get('is_total'):
                continue
            acc_sums[c['key']] = self._sums(self._base_domain(opts, c['date_from'], c['date_to'])
                                            + [('account_id.include_initial_balance', '=', False)])
        if any(c.get('is_total') for c in cols_p):
            total = defaultdict(lambda: {'balance': 0.0})
            for k, sums in acc_sums.items():
                for aid, v in sums.items():
                    total[aid] = {'balance': total[aid]['balance'] + v['balance']}
            acc_sums['total'] = dict(total)
        keys = [c['key'] for c in cols_p]
        all_ids = set().union(*[set(s) for s in acc_sums.values()]) if acc_sums else set()
        accounts = self._accounts(all_ids)
        lines = self._ifrs_lines('pl')
        structure = [
            ('revenue', 'Revenue', ('revenue',)),
            ('cost_of_sales', 'Cost of sales', ('cost_of_sales',)),
            ('gross', 'Gross profit', ('revenue', 'cost_of_sales')),
            ('other_income', 'Other income', ('other_income',)),
            ('operating_expense', 'Operating expenses', ('operating_expense',)),
            ('operating', 'Operating profit', ('revenue', 'cost_of_sales', 'other_income', 'operating_expense')),
            ('finance', 'Finance income and costs', ('finance',)),
            ('pbt', 'Profit before tax', ('revenue', 'cost_of_sales', 'other_income', 'operating_expense', 'finance')),
            ('income_tax', 'Income tax expense', ('income_tax',)),
            ('profit', 'Profit for the period', ('revenue', 'cost_of_sales', 'other_income', 'operating_expense', 'finance', 'income_tax')),
        ]
        section_totals = {}
        rows = []

        def value(aid, k):
            return -acc_sums.get(k, {}).get(aid, {}).get('balance', 0.0)  # income positive

        unmapped = accounts.filtered(lambda a: not a.sb_ifrs_line_id or a.sb_ifrs_line_id.statement != 'pl')
        for skey, slabel, parts in structure:
            if len(parts) > 1 or skey in ('gross', 'operating', 'pbt', 'profit'):
                vals = {k: sum(section_totals.get(p, {}).get(k, 0.0) for p in parts) for k in keys}
                rows.append({'id': skey, 'parent': None, 'level': 0, 'kind': 'total', 'label': slabel, 'values': vals})
                continue
            sec_vals = defaultdict(float)
            sec_rows = []
            for line in lines.filtered(lambda l: l.section == skey):
                line_accounts = accounts.filtered(lambda a: a.sb_ifrs_line_id == line)
                lvals = defaultdict(float)
                acc_rows = []
                for a in line_accounts:
                    avals = {k: value(a.id, k) for k in keys}
                    if opts.get('hide_zero') and not any(abs(v) > 0.005 for v in avals.values()):
                        continue
                    for k, v in avals.items():
                        lvals[k] += v
                    acc_rows.append({'id': 'a%d' % a.id, 'parent': 'il%d' % line.id, 'level': 2, 'kind': 'line',
                                     'label': '%s %s' % (a.code, a.name), 'code': a.code, 'values': avals,
                                     'drill': {'report': 'general_ledger', 'account_ids': [a.id],
                                               'date_from': opts['date_from'], 'date_to': opts['date_to']}})
                if not acc_rows:
                    continue
                for k, v in lvals.items():
                    sec_vals[k] += v
                sec_rows.append({'id': 'il%d' % line.id, 'parent': skey, 'level': 1, 'kind': 'group',
                                 'label': line.name, 'code': line.code, 'values': dict(lvals), 'expandable': True})
                sec_rows += acc_rows
            section_totals[skey] = dict(sec_vals)
            rows.append({'id': skey, 'parent': None, 'level': 0, 'kind': 'section', 'label': slabel,
                         'values': dict(sec_vals), 'expandable': True})
            rows += sec_rows
        if opts.get('oci'):
            oci_lines = self._ifrs_lines('oci')
            oci_vals = defaultdict(float)
            oci_rows = []
            for line in oci_lines:
                l_accounts = self._accounts().filtered(lambda a: a.sb_ifrs_line_id == line)
                lvals = defaultdict(float)
                for a in l_accounts:
                    for c in cols_p:
                        if c.get('is_total'):
                            continue
                        b = self._sums(self._base_domain(opts, c['date_from'], c['date_to']) + [('account_id', '=', a.id)], ())
                        lvals[c['key']] += -(b.get((), {}).get('balance', 0.0)) if b else 0.0
                for k, v in lvals.items():
                    oci_vals[k] += v
                oci_rows.append({'id': 'oci%d' % line.id, 'parent': 'oci', 'level': 1, 'kind': 'line',
                                 'label': line.name, 'values': dict(lvals)})
            rows.append({'id': 'oci', 'parent': None, 'level': 0, 'kind': 'section',
                         'label': 'Other comprehensive income', 'values': dict(oci_vals), 'expandable': True})
            rows += oci_rows
            profit_vals = next(r['values'] for r in rows if r['id'] == 'profit')
            rows.append({'id': 'tci', 'parent': None, 'level': 0, 'kind': 'total',
                         'label': 'Total comprehensive income for the period',
                         'values': {k: profit_vals.get(k, 0.0) + oci_vals.get(k, 0.0) for k in keys}})
        if unmapped:
            uvals = defaultdict(float)
            urows = []
            for a in unmapped:
                avals = {k: value(a.id, k) for k in keys}
                if not any(abs(v) > 0.005 for v in avals.values()):
                    continue
                for k, v in avals.items():
                    uvals[k] += v
                urows.append({'id': 'a%d' % a.id, 'parent': 'unmapped', 'level': 1, 'kind': 'line',
                              'label': '%s %s' % (a.code, a.name), 'values': avals,
                              'drill': {'report': 'general_ledger', 'account_ids': [a.id],
                                        'date_from': opts['date_from'], 'date_to': opts['date_to']}})
            if urows:
                rows.append({'id': 'unmapped', 'parent': None, 'level': 0, 'kind': 'section', 'warning': True,
                             'label': 'Not mapped to IFRS (fix in Setup > IFRS Mapping Check)',
                             'values': dict(uvals), 'expandable': True})
                rows += urows
        columns = [{'key': 'label', 'label': '', 'type': 'text'}] + [
            {'key': c['key'], 'label': c['label'], 'sub': c['sub'], 'type': 'monetary'} for c in cols_p]
        if opts.get('comparison') in ('previous', 'last_year') and len(cols_p) == 2:
            columns.append({'key': 'var', 'label': 'Variance', 'type': 'monetary'})
            columns.append({'key': 'var_pct', 'label': '%', 'type': 'percent'})
            for r in rows:
                v = r['values']
                a, b = v.get('p0', 0.0), v.get('p1', 0.0)
                v['var'] = a - b
                v['var_pct'] = ((a - b) / abs(b) * 100.0) if abs(b) > 0.005 else None
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        title = 'Statement of Profit or Loss and Other Comprehensive Income' if opts.get('oci') else None
        return self._doc('profit_loss', opts, columns, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Balance sheet (IFRS lines)
    def _report_balance_sheet(self, opts):
        cols_p = [c for c in self._column_periods(opts, as_of=True) if not c.get('is_total')]
        keys = [c['key'] for c in cols_p]
        balances, earnings, prior = {}, {}, {}
        for c in cols_p:
            dt = c['date_to']
            balances[c['key']] = self._sums(self._base_domain(opts, None, dt)
                                            + [('account_id.include_initial_balance', '=', True)])
            fy_start = self._fy_start(dt)
            cur = self._sums(self._base_domain(opts, fy_start, dt) + [('account_id.include_initial_balance', '=', False)], ())
            old = self._sums(self._base_domain(opts, None, fy_start - timedelta(days=1))
                             + [('account_id.include_initial_balance', '=', False)], ())
            earnings[c['key']] = -(cur.get((), {}).get('balance', 0.0)) if cur else 0.0
            prior[c['key']] = -(old.get((), {}).get('balance', 0.0)) if old else 0.0
        all_ids = set().union(*[set(b) for b in balances.values()]) if balances else set()
        accounts = self._accounts(all_ids)
        lines = self._ifrs_lines('sfp')
        rows = []
        structure = [
            ('assets', 'ASSETS', 1, [('non_current_asset', 'Non-current assets'), ('current_asset', 'Current assets')]),
            ('equity', 'EQUITY', -1, [('equity', 'Equity')]),
            ('liabilities', 'LIABILITIES', -1, [('non_current_liability', 'Non-current liabilities'),
                                                ('current_liability', 'Current liabilities')]),
        ]
        top_totals = {}
        for top, top_label, sign, sections in structure:
            top_vals = defaultdict(float)
            top_rows = []
            for skey, slabel in sections:
                s_vals = defaultdict(float)
                s_rows = []
                for line in lines.filtered(lambda l: l.section == skey):
                    l_accounts = accounts.filtered(lambda a: a.sb_ifrs_line_id == line)
                    l_vals = defaultdict(float)
                    a_rows = []
                    for a in l_accounts:
                        avals = {k: sign * balances[k].get(a.id, {}).get('balance', 0.0) for k in keys}
                        if opts.get('hide_zero') and not any(abs(v) > 0.005 for v in avals.values()):
                            continue
                        for k, v in avals.items():
                            l_vals[k] += v
                        a_rows.append({'id': 'a%d' % a.id, 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                       'label': '%s %s' % (a.code, a.name), 'code': a.code, 'values': avals,
                                       'drill': {'report': 'general_ledger', 'account_ids': [a.id],
                                                 'date_from': None, 'date_to': opts['date_to']}})
                    if line.code == 'SFP-EQ-03':
                        for k in keys:
                            l_vals[k] += earnings[k] + prior[k]
                        a_rows.append({'id': 'cye', 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                       'label': 'Profit for the current fiscal year', 'values': dict(earnings),
                                       'drill': {'report': 'profit_loss', 'date_from': fields.Date.to_string(self._fy_start(self._d(opts['date_to']))),
                                                 'date_to': opts['date_to']}})
                        if any(abs(v) > 0.005 for v in prior.values()):
                            a_rows.append({'id': 'pye', 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                           'label': 'Prior years\' results not yet closed', 'values': dict(prior)})
                    if not a_rows:
                        continue
                    for k, v in l_vals.items():
                        s_vals[k] += v
                    s_rows.append({'id': 'il%d' % line.id, 'parent': 'sec_' + skey, 'level': 2, 'kind': 'group',
                                   'label': line.name, 'code': line.code, 'values': dict(l_vals), 'expandable': True})
                    s_rows += a_rows
                if not s_rows:
                    continue
                for k, v in s_vals.items():
                    top_vals[k] += v
                top_rows.append({'id': 'sec_' + skey, 'parent': top, 'level': 1, 'kind': 'section', 'label': slabel,
                                 'values': dict(s_vals), 'expandable': True})
                top_rows += s_rows
            top_totals[top] = dict(top_vals)
            rows.append({'id': top, 'parent': None, 'level': 0, 'kind': 'section', 'label': top_label,
                         'values': dict(top_vals), 'expandable': True})
            rows += top_rows
            rows.append({'id': top + '_total', 'parent': None, 'level': 0, 'kind': 'total',
                         'label': 'Total %s' % top_label.lower(), 'values': dict(top_vals)})
        el = {k: top_totals.get('equity', {}).get(k, 0.0) + top_totals.get('liabilities', {}).get(k, 0.0) for k in keys}
        rows.append({'id': 'el_total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total equity and liabilities',
                     'values': el})
        diff = {k: top_totals.get('assets', {}).get(k, 0.0) - el[k] for k in keys}
        unmapped = accounts.filtered(lambda a: not a.sb_ifrs_line_id or a.sb_ifrs_line_id.statement != 'sfp')
        if any(abs(v) > 0.005 for v in diff.values()):
            rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Out of balance (unmapped accounts: %s)' % ', '.join(unmapped.mapped('code')[:10]),
                         'values': diff})
        columns = [{'key': 'label', 'label': '', 'type': 'text'}] + [
            {'key': c['key'], 'label': c['label'], 'sub': c['sub'], 'type': 'monetary'} for c in cols_p]
        dt = self._d(opts['date_to'])
        return self._doc('balance_sheet', opts, columns, rows,
                         'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)))

    # ================================================================== Aged receivables / payables
    def _report_aged(self, opts):
        dt = self._d(opts['date_to'])
        kind = opts.get('partner_type') or 'customer'
        atype = 'asset_receivable' if kind == 'customer' else 'liability_payable'
        buckets = [int(b) for b in (opts.get('buckets') or [30, 60, 90, 120])]
        sign = 1 if kind == 'customer' else -1
        dom = self._base_domain(opts, None, dt) + [('account_id.account_type', '=', atype)]
        if atype == 'liability_payable':
            dom = self._base_domain(opts, None, dt) + [('account_id.sb_liability_group', '=', 'vendor')]
        lines = self.env['account.move.line'].search(dom)
        # residual as at the report date: subtract reconciliations made after it
        partials = self.env['account.partial.reconcile'].search([
            '|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids), ('max_date', '<=', dt)])
        settled = defaultdict(float)
        settled_cur = defaultdict(float)
        for p in partials:
            settled[p.debit_move_id.id] += p.amount
            settled[p.credit_move_id.id] -= p.amount
            settled_cur[p.debit_move_id.id] += p.debit_amount_currency
            settled_cur[p.credit_move_id.id] -= p.credit_amount_currency
        company_cur = self.env.company.currency_id
        foreign = defaultdict(lambda: defaultdict(float))  # partner -> currency -> amount still owed in that currency
        keys = ['current'] + ['b%d' % i for i in range(len(buckets))] + ['older']
        labels = ['Not due'] + ['%d-%d' % ((buckets[i - 1] + 1) if i else 1, b) for i, b in enumerate(buckets)] + ['> %d' % buckets[-1]]
        per_partner = defaultdict(lambda: defaultdict(float))
        names = {}
        for l in lines:
            open_amt = l.balance - settled.get(l.id, 0.0)
            if abs(open_amt) < 0.005:
                continue
            due = l.date_maturity or l.date
            days = (dt - due).days
            if days <= 0:
                k = 'current'
            else:
                k = 'older'
                for i, b in enumerate(buckets):
                    if days <= b:
                        k = 'b%d' % i
                        break
            pid = l.partner_id.id or 0
            names[pid] = l.partner_id.name or 'No partner'
            if l.currency_id and l.currency_id != company_cur:
                foreign[pid][l.currency_id] += sign * (l.amount_currency - settled_cur.get(l.id, 0.0))
            per_partner[pid][k] += sign * open_amt
            per_partner[pid]['total'] += sign * open_amt
        rows, grand = [], defaultdict(float)
        for pid in sorted(per_partner, key=lambda p: names[p].lower()):
            vals = dict(per_partner[pid])
            if abs(vals.get('total', 0.0)) < 0.005 or not self._search_ok(opts, names[pid]):
                continue
            for k, v in vals.items():
                grand[k] += v
            fc = '; '.join('%s %s' % (cur.name, '{:,.2f}'.format(amt)) for cur, amt in foreign[pid].items() if abs(amt) >= 0.005)
            rows.append({'id': 'p%d' % pid, 'parent': None, 'level': 0, 'kind': 'line', 'label': names[pid],
                         'values': {'partner': names[pid], **vals, 'fc': fc},
                         'drill': {'report': 'partner_ledger', 'partner_ids': [pid] if pid else [],
                                   'partner_type': kind, 'date_to': opts['date_to']}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'partner': 'Total', **dict(grand)}})
        columns = [{'key': 'partner', 'label': 'Customer' if kind == 'customer' else 'Vendor', 'type': 'text'}] + [
            {'key': k, 'label': lab, 'type': 'monetary', 'sub': 'days' if k not in ('current',) else ''}
            for k, lab in zip(keys, labels)] + [{'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        if any(foreign.values()):
            columns.append({'key': 'fc', 'label': 'In foreign currency', 'type': 'text'})
        title = 'Aged Receivables' if kind == 'customer' else 'Aged Payables'
        return self._doc('aged', opts, columns, rows, 'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)), title)

    # ================================================================== Partner ledger
    def _report_partner_ledger(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        kind = opts.get('partner_type') or 'customer'
        atype = 'asset_receivable' if kind == 'customer' else 'liability_payable'
        extra = [('account_id.account_type', '=', atype)]
        before = df - timedelta(days=1)
        opening = self._sums(self._base_domain(opts, None, before) + extra, ('partner_id',))
        moves = self._sums(self._base_domain(opts, df, dt) + extra, ('partner_id',))
        sign = 1 if kind == 'customer' else -1
        Partner = self.env['res.partner']
        rows, grand = [], defaultdict(float)
        pids = set(opening) | set(moves)
        # balances in foreign currencies at the end date (USD bills, export invoices ...)
        company_cur = self.env.company.currency_id
        fc = defaultdict(list)
        for partner, cur, amt in self.env['account.move.line']._read_group(
                self._base_domain(opts, None, dt) + extra + [('currency_id', '!=', company_cur.id)],
                ['partner_id', 'currency_id'], ['amount_currency:sum']):
            if abs(amt or 0.0) >= 0.005:
                fc[partner.id or False].append('%s %s' % (cur.name, '{:,.2f}'.format(sign * amt)))
        partners = Partner.browse([p for p in pids if p]).sorted(lambda p: (p.name or '').lower())
        for p in list(partners) + ([Partner] if (0 in pids or False in pids) else []):
            pid = p.id or False
            o = opening.get(pid, {}).get('balance', 0.0)
            m = moves.get(pid, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
            c = o + m['balance']
            if opts.get('hide_zero') and abs(o) < 0.005 and abs(m['debit']) < 0.005 and abs(m['credit']) < 0.005:
                continue
            name = p.name or 'No partner'
            if not self._search_ok(opts, name):
                continue
            vals = {'name': name, 'opening': sign * o, 'debit': m['debit'], 'credit': m['credit'], 'balance': sign * c,
                    'fc': '; '.join(fc.get(pid, []))}
            for k in ('debit', 'credit'):
                grand[k] += vals[k]
            grand['opening'] += vals['opening']
            grand['balance'] += vals['balance']
            rows.append({'id': 'p%d' % (pid or 0), 'parent': None, 'level': 0, 'kind': 'group', 'label': name,
                         'values': vals, 'expandable': True,
                         'lazy': {'kind': 'partner_lines', 'partner_id': pid, 'opening': sign * o, 'sign': sign,
                                  'account_type': atype}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'name': 'Total', **dict(grand)}})
        columns = [
            {'key': 'date', 'label': 'Date', 'type': 'date'},
            {'key': 'number', 'label': 'Reference', 'type': 'text'},
            {'key': 'name', 'label': 'Customer' if kind == 'customer' else 'Vendor', 'type': 'text'},
            {'key': 'opening', 'label': 'Opening', 'type': 'monetary'},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary'},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary'},
            {'key': 'balance', 'label': 'Balance', 'type': 'monetary'},
        ]
        if fc:
            columns.append({'key': 'fc', 'label': 'In foreign currency', 'type': 'text'})
        title = 'Customer Ledger' if kind == 'customer' else 'Vendor Ledger'
        return self._doc('partner_ledger', opts, columns, rows,
                         '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    def _lazy_partner_lines(self, opts, lazy):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        aml = self.env['account.move.line'].search(
            self._base_domain(opts, df, dt) + [('account_id.account_type', '=', lazy['account_type']),
                                               ('partner_id', '=', lazy['partner_id'])], order='date, move_name, id')
        vmap = self._voucher_map(aml.move_id)
        bal = lazy.get('opening', 0.0)
        sign = lazy.get('sign', 1)
        out = [{'id': 'po%s' % lazy['partner_id'], 'level': 1, 'kind': 'detail', 'label': 'Opening balance',
                'values': {'name': 'Opening balance', 'balance': bal}}]
        for l in aml:
            bal += sign * l.balance
            v = vmap.get(l.move_id.id)
            out.append({'id': 'pl%d' % l.id, 'level': 1, 'kind': 'detail', 'label': l.move_id.name,
                        'values': {'date': fields.Date.to_string(l.date), 'number': l.move_id.name,
                                   'name': l.name or l.move_id.ref or '', 'debit': l.debit, 'credit': l.credit,
                                   'balance': bal,
                                   'fc': '%s %s' % (l.currency_id.name, '{:,.2f}'.format(l.amount_currency))
                                   if l.currency_id and l.currency_id != l.company_currency_id else ''},
                        'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id}})
        return out


class SbReportView(models.Model):
    """Saved report views ('My monthly TB'), per user or shared."""
    _name = 'sb.report.view'
    _description = 'Saved Report View'
    _order = 'report, name'

    name = fields.Char(required=True)
    report = fields.Char(required=True, index=True)
    catalog_key = fields.Char(help='Report Center entry this custom report was copied from.')
    options = fields.Json()
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, index=True)
    shared = fields.Boolean(help='Visible to every user.')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    @api.model
    def list_views(self, report):
        views = self.search([('report', '=', report), '|', ('user_id', '=', self.env.uid), ('shared', '=', True),
                             ('company_id', '=', self.env.company.id)])
        return [{'id': v.id, 'name': v.name, 'options': v.options, 'shared': v.shared,
                 'mine': v.user_id.id == self.env.uid} for v in views]

    @api.model
    def save_view(self, report, name, options, shared=False, catalog_key=False):
        view = self.create({'report': report, 'name': name, 'options': options, 'shared': shared,
                            'catalog_key': catalog_key})
        return view.id

    def remove_view(self):
        self.filtered(lambda v: v.user_id.id == self.env.uid).unlink()
        return True


class SbReportPdf(models.AbstractModel):
    _name = 'report.sonador_books.report_document'
    _description = 'Reports PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        return {'doc': (data or {}).get('doc'), 'fmt': self._fmt}

    @staticmethod
    def _fmt(value, kind):
        if value is None or value == '':
            return ''
        if kind in ('monetary', 'number'):
            if abs(value) < 0.005:
                return '-'
            text = '{:,.2f}'.format(abs(value))
            return '(%s)' % text if value < 0 else text
        if kind == 'percent':
            return '%.1f%%' % value
        if kind == 'number_int':
            return '{:,.0f}'.format(value) if value else ''
        return value
