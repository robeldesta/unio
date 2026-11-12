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
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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



class _DraftRollback(Exception):
    """Undoes the draft vouchers' preview entries once an unposted report is built."""


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
        opts.setdefault('analytic_ids', [])
        opts.setdefault('analytic_plan_ids', [])
        opts.setdefault('search', '')
        opts.setdefault('hide_voided', True)  # a void and its reversal cancel out: left out unless asked
        return opts

    def _analytic_ids(self, opts):
        """Analytic accounts chosen in the filter: the accounts picked plus every account of the plans picked."""
        ids = set(int(x) for x in (opts.get('analytic_ids') or []))
        if opts.get('analytic_plan_ids'):
            ids |= set(self.env['account.analytic.account'].search(
                [('plan_id', 'child_of', [int(x) for x in opts['analytic_plan_ids']])]).ids)
        return sorted(ids)

    def _d(self, value):
        return fields.Date.to_date(value)

    def _label_range(self, date_from, date_to):
        df, dt = self._d(date_from), self._d(date_to)
        if df.year <= 1900:  # "from the beginning" (a drill from a balance as at a date)
            return 'Up to %s' % dt.strftime('%d %b %Y')
        return '%s - %s' % (df.strftime('%d %b %Y'), dt.strftime('%d %b %Y'))

    def _label_range_ec(self, date_from, date_to):
        if self._d(date_from).year <= 1900:
            return 'up to %s' % format_ethiopian(self._d(date_to))
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
                cols.append({'key': 'p%d' % (i + 1), 'date_from': self._same_period_back(df, i + 1),
                             'date_to': self._same_period_back(dt, i + 1, end=True)})
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

    def _same_period_back(self, d, n, end=False):
        """The same day of the same fiscal period n fiscal years earlier: Meskerem 2020 compares with Meskerem 2019,
        whatever the leap years (one Gregorian year back would land a day off after a six-day Pagume). The last day of
        a period stays the last day; a Gregorian calendar is simply a year back."""
        if gregorian():
            return self._minus_years(d, n)
        fy = fiscal_year_of(d)
        no, start, stop = next((no, s, e) for no, _n, s, e in fiscal_periods(fy) if s <= d <= e)
        _no, _name, p_start, p_stop = fiscal_periods(fy - n)[no - 1]
        if end and d == stop:
            return p_stop
        return min(p_start + timedelta(days=(d - start).days), p_stop)

    def _minus_years(self, d, n):
        try:
            return d.replace(year=d.year - n)
        except ValueError:
            return d.replace(year=d.year - n, day=28)

    def _period_name(self, df, dt):
        """'Tikimt 2019', 'FY 2019 E.C.', 'Hamle - Tikimt 2019' or a date range."""
        if df.year <= 1900:
            return 'All entries up to %s' % dt.strftime('%d %b %Y')
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
        if opts.get('analytic_ids') or opts.get('analytic_plan_ids'):
            # lines whose analytic distribution names one of the accounts (an empty choice of a plan matches nothing)
            dom.append(('analytic_distribution', 'in', self._analytic_ids(opts) or [0]))
        if opts.get('move_ids'):
            dom.append(('move_id', 'in', [int(x) for x in opts['move_ids']]))
        if opts.get('exclude_closing'):
            dom.append(('move_id.sb_closing', '=', False))
        elif opts.get('exclude_closing_since'):
            # the balance sheet's view: a year's own closing is left out (drilled from its year-end figures)
            dom += ['|', ('move_id.sb_closing', '=', False), ('date', '<', opts['exclude_closing_since'])]
        return dom

    @staticmethod
    def _analytic_share(distribution, ids):
        """The part of a line that belongs to the analytic accounts chosen, as a fraction: a line split 50/50 between
        two cost centres is half under each, so the two filtered reports add up to the whole. A key may name several
        accounts of different plans ("12,15"); it counts once. Capped at the whole line."""
        if not distribution:
            return 0.0
        share = 0.0
        for key, pct in distribution.items():
            if any(int(x) in ids for x in str(key).split(',') if x.strip().isdigit()):
                share += float(pct or 0.0)
        return min(share, 100.0) / 100.0

    def _analytic_leaf(self, domain):
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3 and leaf[0] == 'analytic_distribution' and leaf[1] == 'in':
                return set(int(x) for x in leaf[2])
        return None

    def _sums(self, domain, groupby=('account_id',)):
        aml = self.env['account.move.line']
        res = {}
        ids = self._analytic_leaf(domain)
        if ids is not None:
            # an analytic filter weighs each line by its share in the accounts chosen (the analytic percentages)
            fields_ = [g for g in groupby]
            for line in aml.search_fetch(domain, fields_ + ['analytic_distribution', 'debit', 'credit', 'balance']):
                f = self._analytic_share(line.analytic_distribution, ids)
                if not f:
                    continue
                keys = [()]
                for g in groupby:
                    v = line[g]
                    vals = (v.ids or [False]) if g in ('tax_ids',) else [v.id if hasattr(v, 'id') else v]
                    keys = [k + (x,) for k in keys for x in vals]
                for key in keys:
                    key = key[0] if len(groupby) == 1 else key
                    acc = res.setdefault(key, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
                    acc['debit'] += line.debit * f
                    acc['credit'] += line.credit * f
                    acc['balance'] += line.balance * f
            return res
        for row in aml._read_group(domain, list(groupby), ['debit:sum', 'credit:sum', 'balance:sum']):
            key = tuple(r.id if hasattr(r, 'id') else r for r in row[:len(groupby)])
            key = key[0] if len(groupby) == 1 else key
            res[key] = {'debit': row[-3] or 0.0, 'credit': row[-2] or 0.0, 'balance': row[-1] or 0.0}
        return res

    def _fy_start(self, d):
        return fiscal_periods(fiscal_year_of(d))[0][2]

    def _unclosed_since(self, fy_start):
        """First day of the results not yet closed before fy_start: the day after the fiscal year of the last year-end
        closing entry (the years up to it are in retained earnings already), or the first P&L entry when none was closed."""
        company = self.env.company
        # a closing that was reversed (the year reopened) closed nothing: neither it nor its reversal counts
        last = self.env['account.move'].search([('company_id', '=', company.id), ('sb_closing', '=', True),
                                                ('state', '=', 'posted'), ('date', '<', fy_start),
                                                ('reversed_entry_id', '=', False),
                                                ('reversal_move_ids', 'not any', [('state', '=', 'posted')])],
                                               order='date desc', limit=1)
        if last:
            return fiscal_periods(fiscal_year_of(last.date))[-1][3] + timedelta(days=1)
        first = self.env['account.move.line'].search([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                                      ('account_id.include_initial_balance', '=', False)], order='date', limit=1)
        return first.date or fy_start

    def _prior_results(self, opts, fy_start):
        """Income and expense of the fiscal years before fy_start not yet closed into retained earnings (debit positive)."""
        old = self._sums(self._base_domain(opts, None, fy_start - timedelta(days=1))
                         + [('account_id.include_initial_balance', '=', False)], ())
        return old.get((), {}).get('balance', 0.0) if old else 0.0

    def _prior_results_drill(self, fy_start):
        since = self._unclosed_since(fy_start)
        if since >= fy_start:
            # nothing left unclosed before fy_start: no range to open (never an inverted one)
            return None
        return {'report': 'profit_loss', 'date_from': fields.Date.to_string(since),
                'date_to': fields.Date.to_string(fy_start - timedelta(days=1))}

    def _pre_closing(self, opts, date_from=None):
        """'Leave out year-end closing entries' on a trial balance or ledger: only the period's own closing (dated on or
        after its first day) is left out. Earlier years' closings stay, so the opening balances are those carried
        forward (retained earnings after the earlier closes) and earlier years' results are not shown as unclosed."""
        if not opts.get('exclude_closing'):
            return opts
        return dict(opts, exclude_closing=False,
                    exclude_closing_since=fields.Date.to_string(self._d(date_from or opts['date_from'])))

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

    def _opening_move_ids(self):
        """The entries that bring balances over from the old books (opening vouchers, and anything posted against the
        opening balance equity clearing account): not activity of the period they are dated in."""
        company = self.env.company
        ids = set(self.env['sb.voucher'].search([('voucher_type', '=', 'opening'), ('company_id', '=', company.id),
                                                 ('move_id', '!=', False)]).move_id.ids)
        if company.sb_opening_equity_account_id:
            ids |= {m.id for [m] in self.env['account.move.line']._read_group(
                [('account_id', '=', company.sb_opening_equity_account_id.id), ('company_id', '=', company.id)], ['move_id'])}
        return ids

    def _accounts(self, ids=None):
        Account = self.env['account.account'].with_company(self.env.company)
        dom = [('company_ids', 'in', self.env.company.id)]
        if ids is not None:
            dom.append(('id', 'in', list(ids)))
        return Account.search(dom).sorted(lambda a: a.code or '')

    def _depreciation_accounts(self):
        """Accumulated depreciation and amortisation accounts, by mapping rather than by name: the depreciation accounts
        of the asset categories, and the non-current asset accounts credited by the entries that charge an expense on
        the depreciation and amortisation line of the income statement (IFRS PL-06)."""
        company = self.env.company
        ids = set()
        if 'account.asset.category' in self.env:
            cats = self.env['account.asset.category'].sudo().search([('company_id', 'in', (company.id, False))])
            ids |= set(cats.account_depreciation_id.filtered(
                lambda a: a.account_type in ('asset_fixed', 'asset_non_current')).ids)
        charge = self.env['account.account'].search([('company_ids', 'in', company.id), ('sb_ifrs_line_id.code', '=', 'PL-06')])
        if charge:
            moves = self.env['account.move.line'].sudo()._read_group(
                [('company_id', '=', company.id), ('account_id', 'in', charge.ids), ('debit', '>', 0)], ['move_id'])
            if moves:
                ids |= {a.id for [a] in self.env['account.move.line'].sudo()._read_group(
                    [('move_id', 'in', [m.id for [m] in moves]), ('credit', '>', 0),
                     ('account_id.account_type', 'in', ('asset_fixed', 'asset_non_current'))], ['account_id'])}
        return self.env['account.account'].browse(sorted(ids))

    def _dep_split(self, opts, df, dt, dep_ids, exclude_moves=()):
        """The movement of the accumulated depreciation accounts in a period, by what the entry did, per account:
        'charge' (credit, positive) and 'reversed' (debit) from entries that touch no asset cost account (the charge and
        its corrections, wherever the expense went: the depreciation line or production cost), and 'disposal' (debit
        positive: depreciation eliminated) from entries that also credit an asset cost account (disposals, write-offs,
        transfers). Each entry counts by its net on each account."""
        out = defaultdict(lambda: {'charge': 0.0, 'reversed': 0.0, 'disposal': 0.0})
        if not dep_ids:
            return out
        dom = self._base_domain(dict(opts, exclude_closing=True, account_ids=[]), df, dt) + [('account_id', 'in', list(dep_ids))]
        if exclude_moves:
            dom.append(('move_id', 'not in', list(exclude_moves)))
        per = self._sums(dom, ('move_id', 'account_id'))
        moves = {m for m, _a in per}
        disposal_moves = set()
        if moves:
            disposal_moves = {m.id for [m] in self.env['account.move.line']._read_group(
                [('move_id', 'in', list(moves)), ('credit', '>', 0), ('account_id', 'not in', list(dep_ids)),
                 ('account_id.account_type', 'in', ('asset_fixed', 'asset_non_current'))], ['move_id'])}
        for (m, aid), v in per.items():
            if m in disposal_moves:
                out[aid]['disposal'] += v['balance']
            elif v['balance'] < 0:
                out[aid]['charge'] += -v['balance']
            else:
                out[aid]['reversed'] += -v['balance']
        return out

    def _is_bank_account(self, account):
        """A bank account (it can be overdrawn), as opposed to a cash box (petty cash, cash on hand), by the same rule as
        the vouchers' payment method: a cash journal's account, a box with a custodian or a petty cash box is cash."""
        if account.account_type != 'asset_cash':
            return False
        if getattr(account, 'sb_custodian_id', False):
            return False
        return self.env['sb.voucher.cash']._sb_method_for(account) != 'cash'

    def _search_ok(self, opts, *texts):
        s = (opts.get('search') or '').strip().lower()
        return not s or any(s in (t or '').lower() for t in texts)

    def _printed_now(self):
        """Now, in the reader's time zone (the user's, else the company's), not the server's UTC."""
        tz = self.env.user.tz or self.env.context.get('tz') or self.env.company.partner_id.tz or 'Africa/Addis_Ababa'
        return fields.Datetime.context_timestamp(self.with_context(tz=tz), fields.Datetime.now()).strftime('%Y-%m-%d %H:%M:%S')

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
            'printed': self._printed_now(),
            'printed_ec': format_ethiopian(fields.Date.context_today(self)),
        }

    # ================================================================== entry point
    # reports on salaries: the HR role (or a payroll manager) only, whatever the accounting role
    PAYROLL_REPORTS = {'payroll_register', 'paye_declaration', 'poessa_declaration', 'employee_earnings', 'employee_list',
                       'payroll_journal', 'payroll_cheques', 'payslip'}
    PAYROLL_GROUPS = ('sonador_books.group_sb_hr', 'hr.group_hr_manager', 'hr_payroll.group_hr_payroll_manager')

    def _payroll_report_keys(self):
        keys = set(self.PAYROLL_REPORTS)
        if hasattr(self, '_extra_catalog'):
            from .report_catalog import CATALOG
            keys |= {e['report'] for e in CATALOG + self._extra_catalog() if e.get('category') == 'payroll'}
        return keys

    @api.model
    def _report_allowed(self, key, opts=None):
        """May the user open report `key` with these options? Apps narrow it for their own roles (Sales: the customer side)."""
        if self.env.su:
            return True
        user = self.env.user
        if key in self._payroll_report_keys():
            return any(self.env.ref(g, raise_if_not_found=False) and user.has_group(g) for g in self.PAYROLL_GROUPS)
        # every other report reads the ledger: an accounting role (an HR officer alone sees payroll, not the books)
        return self.env['account.move.line'].has_access('read')

    @api.model
    def _check_report_access(self, key, opts=None):
        """The one access rule for a report, on screen, in packs and on the export route."""
        if not hasattr(self, '_report_%s' % key) and key not in self.PAYROLL_REPORTS:
            raise UserError(self.env._('Unknown report %s', key))
        if not self._report_allowed(key, opts):
            if key in self._payroll_report_keys():
                raise AccessError(self.env._(
                    'This is a payroll report: it needs the HR Officer role (or payroll manager). The accounting roles '
                    'see the payroll only as its journal entries in the ledger.'))
            raise AccessError(self.env._(
                'This is an accounting report: it needs an accounting role (auditor, clerk, accountant or manager). '
                'The HR Officer role alone opens the payroll reports.'))

    def _filter_lines(self, opts):
        """The filters in force, in words, for the printed heading: a PDF or a workbook says what it leaves out."""
        out = []
        if opts.get('journal_ids'):
            j = self.env['account.journal'].browse(opts['journal_ids']).exists()
            out.append('Journals: %s' % ', '.join(j.mapped('code')))
        if opts.get('partner_ids'):
            p = self.env['res.partner'].browse(opts['partner_ids']).exists()
            names = p.mapped('name')
            out.append('Customers / vendors: %s%s' % (', '.join(names[:5]), ' and %d more' % (len(names) - 5) if len(names) > 5 else ''))
        if opts.get('account_ids'):
            a = self.env['account.account'].browse(opts['account_ids']).exists()
            codes = a.mapped('code')
            out.append('Accounts: %s%s' % (', '.join(codes[:8]), ' and %d more' % (len(codes) - 8) if len(codes) > 8 else ''))
        if opts.get('analytic_ids') or opts.get('analytic_plan_ids'):
            parts = []
            if opts.get('analytic_plan_ids'):
                parts += ['plan %s' % x for x in self.env['account.analytic.plan'].browse(opts['analytic_plan_ids']).exists().mapped('name')]
            if opts.get('analytic_ids'):
                parts += self.env['account.analytic.account'].browse(opts['analytic_ids']).exists().mapped('display_name')
            out.append('Analytic: %s' % ', '.join(parts))
        if opts.get('move_ids'):
            out.append('Selected entries only (%d)' % len(opts['move_ids']))
        if (opts.get('search') or '').strip():
            out.append('Search "%s"' % opts['search'].strip())
        if opts.get('hide_voided'):
            out.append('Voided entries and their reversals left out')
        if opts.get('exclude_closing') and opts.get('_report') in ('trial_balance', 'general_ledger', 'cash_book'):
            out.append('Year-end closing entries left out (before closing)')
        return out

    def _filter_chips(self, opts):
        """The narrowing filters a drill-down or a pick sets, one removable chip each on screen."""
        chips = []
        for a in self.env['account.account'].browse([int(x) for x in opts.get('account_ids') or []]).exists():
            chips.append({'key': 'account_ids', 'id': a.id, 'label': 'Account %s %s' % (a.code or '', a.name)})
        for p in self.env['res.partner'].browse([int(x) for x in opts.get('partner_ids') or []]).exists():
            chips.append({'key': 'partner_ids', 'id': p.id, 'label': p.name or ''})
        if opts.get('move_ids'):
            chips.append({'key': 'move_ids', 'label': '%d selected entries' % len(opts['move_ids'])})
        if opts.get('exclude_closing'):
            chips.append({'key': 'exclude_closing', 'label': 'Year-end closing entries left out'})
        elif opts.get('exclude_closing_since'):
            chips.append({'key': 'exclude_closing_since', 'label': 'Year-end closing from %s left out' % (
                self._d(opts['exclude_closing_since']).strftime('%d %b %Y'))})
        return chips

    # ================================================================== draft vouchers in an "unposted" report
    DRAFT_LIMIT = 500

    def _with_draft_vouchers(self, opts, build):
        """Run build() with the draft vouchers in the ledger: a voucher saved but not posted has no journal entry, so
        its would-be entry is made (as a draft, the way posting would make it) inside a savepoint that is rolled back
        once the report is built. Nothing is kept; vouchers too incomplete to make an entry are listed instead."""
        if opts.get('posted_only', True) or self.env.context.get('sb_drafts_in'):
            return build(self)
        V = self.env['sb.voucher'].sudo()
        dom = [('company_id', '=', self.env.company.id), ('state', '=', 'draft'), ('move_id', '=', False),
               ('date', '<=', opts['date_to'])]
        drafts = V.search(dom, order='date, id', limit=self.DRAFT_LIMIT)
        if not drafts:
            return build(self.with_context(sb_drafts_in={'moves': {}, 'skipped': []}))
        info = {'moves': {}, 'skipped': []}
        if len(drafts) == self.DRAFT_LIMIT:
            # a cap keeps the preview quick: said in the filters line, never silent
            info['left_out'] = V.search_count(dom) - len(drafts)
        result = {}
        self.env.flush_all()
        try:
            with self.env.cr.savepoint(flush=False):
                from .voucher import INVOICE_TYPES  # posted as invoices / bills: the entry comes from the invoice lines
                Move = self.env['account.move'].sudo().with_context(sb_draft_preview=True, tracking_disable=True,
                                                                    mail_create_nolog=True, mail_notrack=True)
                for v in drafts:
                    try:
                        with self.env.cr.savepoint():
                            if v.voucher_type in INVOICE_TYPES:
                                vals = v._prepare_invoice_vals()
                            else:
                                vals = {'move_type': 'entry', 'journal_id': v._journal().id, 'date': v.date,
                                        'ref': ' '.join(filter(None, [v.reference, v.partner_id.name])) or False,
                                        'narration': v.memo, 'partner_id': v.partner_id.commercial_partner_id.id or False,
                                        'line_ids': v._prepare_entry_lines()}
                            move = Move.create(vals)
                            if not move.line_ids or move.currency_id.compare_amounts(
                                    sum(move.line_ids.mapped('debit')), sum(move.line_ids.mapped('credit'))):
                                raise UserError('unbalanced')
                            info['moves'][move.id] = v.id
                    except Exception as e:  # an incomplete draft: said, not guessed
                        info['skipped'].append('%s (%s)' % (v.display_name or v.id, str(e).split('\n')[0][:80]))
                self.env.flush_all()
                result['doc'] = build(self.with_context(sb_drafts_in=info))
                raise _DraftRollback()
        except _DraftRollback:
            pass
        self.env.invalidate_all()
        return result['doc']

    @api.model
    def get_report(self, key, options=None, expanded=None):
        opts = self._options(options)
        self._check_report_access(key, opts)
        return self._with_draft_vouchers(opts, lambda R: R._get_report_built(key, opts, expanded))

    def _get_report_built(self, key, opts, expanded):
        doc = getattr(self, '_report_%s' % key)(opts)
        if opts.get('_title'):
            doc['title'] = opts['_title']
        # the basis, said on every rendering: drafts included, and the filters in force
        doc['unposted'] = not opts.get('posted_only', True)
        if doc['unposted'] and 'unposted' not in doc['title'].lower():
            doc['title'] += ' (including unposted entries)'
            doc['subtitle'] = 'DRAFT - includes unposted entries and draft vouchers  |  ' + (doc.get('subtitle') or '')
        doc['filters'] = self._filter_lines(dict(opts, _report=key))
        drafts = self.env.context.get('sb_drafts_in') or {}
        if doc['unposted'] and drafts.get('left_out'):
            doc['filters'].append('Draft vouchers: the first %d by date are included, %d later ones are not' % (
                self.DRAFT_LIMIT, drafts['left_out']))
        if doc['unposted'] and drafts.get('skipped'):
            doc['filters'].append('Draft vouchers too incomplete to include (%d): %s' % (
                len(drafts['skipped']), '; '.join(drafts['skipped'][:6])))
        doc['chips'] = self._filter_chips(opts)
        if expanded:
            self._resolve_expanded(doc, set(expanded))
        from .display_clean import clean_doc
        return clean_doc(doc)

    @api.model
    def get_children(self, lazy, options=None):
        """Rows for a lazily expanded row (e.g. GL lines of one account)."""
        opts = self._options(options)
        self._check_lazy_access(lazy, opts)
        return self._with_draft_vouchers(opts, lambda R: getattr(R, '_lazy_%s' % lazy['kind'])(opts, lazy))

    @api.model
    def _check_lazy_access(self, lazy, opts):
        """The rows under an expanded row come from the ledger: the same rule as the reports (apps narrow it)."""
        if not hasattr(self, '_lazy_%s' % (lazy or {}).get('kind', '')):
            raise UserError(self.env._('Unknown report rows.'))
        if not self.env.su and not self.env['account.move.line'].has_access('read'):
            raise AccessError(self.env._('This is an accounting report: it needs an accounting role.'))

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
        q = self._pre_closing(opts)  # before closing: the period's own closing only
        opening = self._opening(q, df)
        moves = self._sums(self._base_domain(q, df, dt) + self._voided_dom(opts, df, dt))
        # comparatives: each account's closing balance at the end of the previous period(s) / the same period last year
        cmp_periods = [c for c in self._column_periods(opts) if c['key'] != 'p0' and not c.get('is_total')] \
            if opts.get('comparison') in ('previous', 'last_year') else []
        cmp_close, cmp_prior = {}, {}
        for c in cmp_periods:
            cq = self._pre_closing(opts, c['date_from'])
            o = self._opening(cq, c['date_from'])
            m = self._sums(self._base_domain(cq, c['date_from'], c['date_to']))
            cmp_close[c['key']] = {aid: o.get(aid, {}).get('balance', 0.0) + m.get(aid, {}).get('balance', 0.0) for aid in set(o) | set(m)}
            cmp_prior[c['key']] = self._prior_results(cq, self._fy_start(c['date_from']))
        cur_keys = ('opening', 'debit', 'credit', 'closing', 'closing_dr', 'closing_cr')

        def col_drills(current, per_cmp):
            # every value column drills to its own period: the comparatives to the ledger of their period
            out = {k: current for k in cur_keys}
            for c_ in cmp_periods:
                out['cmp_%s' % c_['key']] = per_cmp(c_)
            return out
        cmp_keys = ['cmp_%s' % c['key'] for c in cmp_periods]
        cols = [
            {'key': 'code', 'label': 'Account', 'type': 'text'},
            {'key': 'name', 'label': 'Description', 'type': 'text'},
            {'key': 'opening', 'label': 'Opening Balance', 'type': 'monetary', 'sub': 'before %s' % df.strftime('%d %b %Y')},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary', 'sub': self._period_name(df, dt)},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary', 'sub': self._period_name(df, dt)},
            {'key': 'closing', 'label': 'Closing Balance', 'type': 'monetary', 'sub': 'at %s' % dt.strftime('%d %b %Y')},
        ] + [{'key': 'cmp_%s' % c['key'], 'label': 'Closing Balance', 'type': 'monetary',
              'sub': 'at %s' % c['date_to'].strftime('%d %b %Y')} for c in cmp_periods] + [
            {'key': 'closing_dr', 'label': 'Closing Debit', 'type': 'monetary'},
            {'key': 'closing_cr', 'label': 'Closing Credit', 'type': 'monetary'},
        ]
        sum_keys = ('opening', 'debit', 'credit', 'closing', 'closing_dr', 'closing_cr', *cmp_keys)
        rows, grand = [], defaultdict(float)
        accounts = self._accounts(set(opening) | set(moves) | {aid for v in cmp_close.values() for aid in v})
        for skey, slabel, types in ACCOUNT_SECTIONS:
            sec_accounts = accounts.filtered(lambda a: a.account_type in types)
            sec_rows, tot = [], defaultdict(float)
            for a in sec_accounts:
                o = opening.get(a.id, {}).get('balance', 0.0)
                m = moves.get(a.id, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
                c = o + m['balance']
                vals = {'code': a.code, 'name': a.name, 'opening': o, 'debit': m['debit'], 'credit': m['credit'],
                        'closing': c, 'closing_dr': c if c > 0 else 0.0, 'closing_cr': -c if c < 0 else 0.0}
                for c_ in cmp_periods:
                    vals['cmp_%s' % c_['key']] = cmp_close[c_['key']].get(a.id, 0.0)
                if opts.get('hide_zero') and not any(abs(vals[k]) > 0.005 for k in ('opening', 'debit', 'credit', 'closing', *cmp_keys)):
                    continue
                if not self._search_ok(opts, a.code, a.name):
                    continue
                for k in sum_keys:
                    tot[k] += vals[k]
                drill = {'report': 'general_ledger', 'account_ids': [a.id],
                         'date_from': opts['date_from'], 'date_to': opts['date_to']}
                sec_rows.append({'id': 'a%d' % a.id, 'parent': skey, 'level': 1, 'kind': 'line', 'label': a.name,
                                 'code': a.code, 'values': vals, 'drill': drill,
                                 'drills': col_drills(drill, lambda c_, a=a: {
                                     'report': 'general_ledger', 'account_ids': [a.id], 'comparison': 'none',
                                     'date_from': fields.Date.to_string(c_['date_from']),
                                     'date_to': fields.Date.to_string(c_['date_to'])}) if cmp_periods else None})
            prior_label = "Prior years' results not yet closed"
            if skey == 'equity' and self._search_ok(opts, prior_label):
                # results of earlier fiscal years not yet closed to retained earnings (P&L accounts restart each year);
                # under a search it shows only when the search finds it, like any account
                fy_start = self._fy_start(df)
                prior = self._prior_results(q, fy_start)
                if abs(prior) > 0.005 or any(abs(v) > 0.005 for v in cmp_prior.values()):
                    vals = {'code': '', 'name': prior_label, 'opening': prior, 'debit': 0.0,
                            'credit': 0.0, 'closing': prior, 'closing_dr': prior if prior > 0 else 0.0,
                            'closing_cr': -prior if prior < 0 else 0.0}
                    for c_ in cmp_periods:
                        vals['cmp_%s' % c_['key']] = cmp_prior[c_['key']]
                    for k in ('opening', 'closing', 'closing_dr', 'closing_cr', *cmp_keys):
                        tot[k] += vals[k]
                    sec_rows.append({'id': 'prior_results', 'parent': skey, 'level': 1, 'kind': 'line',
                                     'label': prior_label, 'values': vals,
                                     'drill': self._prior_results_drill(fy_start),
                                     'drills': col_drills(self._prior_results_drill(fy_start), lambda c_: self._prior_results_drill(
                                         self._fy_start(c_['date_from']))) if cmp_periods else None})
            if not sec_rows:
                continue
            rows.append({'id': skey, 'parent': None, 'level': 0, 'kind': 'section', 'label': slabel,
                         'values': {'code': slabel, **dict(tot)}, 'expandable': True})
            rows += sec_rows
            for k, v in tot.items():
                grand[k] += v
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'code': 'Total', **dict(grand)},
                     'sum_of': [(r['id'], 1) for r in rows if r['kind'] == 'section']})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('trial_balance', opts, cols, rows, sub)

    # ================================================================== General ledger
    def _report_general_ledger(self, opts, cash_only=False):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        extra = [('account_id.account_type', '=', 'asset_cash')] if cash_only else []
        q = self._pre_closing(opts)  # before closing: the period's own closing only
        opening = self._opening(q, df, extra=extra)
        moves = self._sums(self._base_domain(q, df, dt) + extra + self._voided_dom(opts, df, dt))
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
        # a journal's ledger is that journal's movements: only the accounts it moved, no opening balances (an
        # "opening balance" made of one journal's earlier entries means nothing)
        journal_only = bool(opts.get('journal_ids'))
        ids = set(moves) if journal_only else set(opening) | set(moves)
        if opts.get('account_ids'):
            ids &= set(opts['account_ids'])
        # a search finds an account by its number or name, or the entries by number, reference, customer / vendor
        # or description: an account found only through its entries lists just those entries
        found = {}
        if (opts.get('search') or '').strip():
            found = self._sums(self._base_domain(q, df, dt) + extra + self._voided_dom(opts, df, dt)
                               + self._gl_search_dom(opts))
        for a in self._accounts(ids):
            if cash_only and a.account_type != 'asset_cash':
                continue
            o = 0.0 if journal_only else opening.get(a.id, {}).get('balance', 0.0)
            m = moves.get(a.id, {'debit': 0.0, 'credit': 0.0, 'balance': 0.0})
            lazy = {'kind': 'gl_lines', 'account_id': a.id, 'opening': o}
            if journal_only:
                lazy['journal_only'] = True
            if not self._search_ok(opts, a.code, a.name):
                if a.id not in found:
                    continue
                o, m = 0.0, found[a.id]
                lazy.update(opening=0.0, search_only=True)
            c = o + m['balance']
            if opts.get('hide_zero') and abs(o) < 0.005 and abs(m['debit']) < 0.005 and abs(m['credit']) < 0.005:
                continue
            rows.append({'id': 'a%d' % a.id, 'parent': None, 'level': 0, 'kind': 'group',
                         'label': '%s %s' % (a.code, a.name), 'code': a.code,
                         'values': {'date': '', 'number': a.code, 'name': a.name, 'debit': m['debit'],
                                    'credit': m['credit'], 'balance': c, 'opening': o},
                         'expandable': True, 'lazy': lazy})
            grand['debit'] += m['debit']
            grand['credit'] += m['credit']
            grand['balance'] += c
        if not cash_only and not opts.get('account_ids') and not opts.get('search') and not journal_only:
            # income and expense accounts restart each year: the results of earlier years not yet closed are one line,
            # as on the trial balance, so the ledger balances to zero
            fy_start = self._fy_start(df)
            prior = self._prior_results(q, fy_start)
            if abs(prior) > 0.005:
                rows.append({'id': 'prior_results', 'parent': None, 'level': 0, 'kind': 'line',
                             'drill': self._prior_results_drill(fy_start),
                             'label': "Prior years' results not yet closed",
                             'values': {'number': '', 'name': "Prior years' results not yet closed", 'debit': 0.0,
                                        'credit': 0.0, 'balance': prior, 'opening': prior}})
                grand['balance'] += prior
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'number': 'Total', **dict(grand)}})
        if opts.get('exclude_closing_since') and not opts.get('exclude_closing'):
            label = 'Year-end closing entries dated from %s are left out, as on the statement of financial position' % (
                self._d(opts['exclude_closing_since']).strftime('%d %b %Y'))
            rows.append({'id': 'closing_note', 'parent': None, 'level': 0, 'kind': 'note', 'label': label,
                         'values': {'name': label}})
        if journal_only:
            label = 'Movements in the journals chosen only: no opening balances, and the balance column is the net movement'
            rows.append({'id': 'journal_note', 'parent': None, 'level': 0, 'kind': 'note', 'label': label,
                         'values': {'name': label}})
        if found:
            label = 'Accounts found through their entries list only the entries that match the search, without opening balances'
            rows.append({'id': 'search_note', 'parent': None, 'level': 0, 'kind': 'note', 'label': label,
                         'values': {'name': label}})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('cash_book' if cash_only else 'general_ledger', opts, cols, rows, sub)

    # ================================================================== one account's transactions (Chart of Accounts > View)
    def _report_account_transactions(self, opts):
        """One account, flat: balance brought forward, every entry (date, number, customer / vendor, description, debit,
        credit, running balance; a double-click opens its voucher), balance carried forward."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        account = self.env['account.account'].browse((opts.get('account_ids') or [0])[:1]).exists()
        if not account:
            raise UserError(self.env._('Choose an account.'))
        q = self._pre_closing(opts)
        opening = self._opening(q, df).get(account.id, {}).get('balance', 0.0)
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
        lines = self._lazy_gl_lines(dict(opts, _report='general_ledger'), {'kind': 'gl_lines', 'account_id': account.id, 'opening': opening})
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
                dr += l['values'].get('debit') or 0.0
                cr += l['values'].get('credit') or 0.0
            rows.append(r)
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': fields.Date.to_string(dt),
                     'values': {'date': fields.Date.to_string(dt), 'date_ec': format_ethiopian(dt), 'number': '', 'name': 'Balance carried forward', 'debit': dr, 'credit': cr,
                                'balance': opening + dr - cr, 'opening': opening}})
        sub = '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))
        return self._doc('account_transactions', opts, cols, rows, sub, title='%s %s' % (account.code, account.name))

    def _gl_search_dom(self, opts):
        s = (opts.get('search') or '').strip()
        if not s:
            return []
        return ['|', '|', '|', '|', ('move_id.name', 'ilike', s), ('move_id.ref', 'ilike', s), ('name', 'ilike', s),
                ('partner_id.name', 'ilike', s), ('move_id.partner_id.name', 'ilike', s)]

    def _voided_dom(self, opts, df, dt):
        """'Hide voided pairs': a voided entry and its reversal, both inside the period, cancel out on every account;
        leaving both out changes no balance, only the debit and credit turnover. A pair split across the period stays."""
        if not opts.get('hide_voided'):
            return []
        company = self.env.company
        rev = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted'),
                                               ('reversed_entry_id', '!=', False), ('date', '>=', df), ('date', '<=', dt),
                                               ('reversed_entry_id.state', '=', 'posted'),
                                               ('reversed_entry_id.date', '>=', df), ('reversed_entry_id.date', '<=', dt)])
        void_origins = set(self.env['sb.voucher'].search([('company_id', '=', company.id), ('state', '=', 'void'),
                                                          ('move_id', 'in', rev.reversed_entry_id.ids)]).move_id.ids)
        ids = set()
        for r in rev:
            if r.reversed_entry_id.id in void_origins or (r.name or '').startswith('VOID/'):
                ids |= {r.id, r.reversed_entry_id.id}
        return [('move_id', 'not in', list(ids))] if ids else []

    def _report_cash_book(self, opts):
        return self._report_general_ledger(opts, cash_only=True)

    def _line_label(self, line, voucher=None):
        """A ledger line's description; a bare word left by an empty reference ('Advance ') gets the voucher number."""
        text = (line.name or line.move_id.ref or '').strip()
        if text.lower() in ('advance', 'payment', 'receipt'):
            number = voucher.name if voucher else line.move_id.name
            return ('%s %s' % (text, number)).strip() if number and number not in text else text
        return text

    def _voucher_map(self, moves):
        vouchers = self.env['sb.voucher'].search([('move_id', 'in', moves.ids)])
        out = {v.move_id.id: v for v in vouchers}
        # a draft voucher's would-be entry (an unposted report): its lines open the voucher
        drafts = (self.env.context.get('sb_drafts_in') or {}).get('moves') or {}
        for mid, vid in drafts.items():
            if mid in moves.ids:
                out[mid] = self.env['sb.voucher'].browse(vid)
        return out

    def _lazy_gl_lines(self, opts, lazy):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        aml = self.env['account.move.line'].search(
            self._base_domain(opts, df, dt) + self._voided_dom(opts, df, dt) + [('account_id', '=', lazy['account_id'])]
            + (self._gl_search_dom(opts) if lazy.get('search_only') else []),
            order='date, debit desc, move_name, id')
        vmap = self._voucher_map(aml.move_id)
        bal = lazy.get('opening', 0.0)
        first = 'Opening balance'
        if lazy.get('search_only'):
            first = 'Entries matching the search (no opening balance)'
        elif lazy.get('journal_only'):
            first = 'Movements in the journals chosen (no opening balance)'
        out = [{'id': 'o%d' % lazy['account_id'], 'level': 1, 'kind': 'detail', 'label': first,
                'values': {'name': first, 'balance': bal}}]
        an_ids = set(self._analytic_ids(opts)) if (opts.get('analytic_ids') or opts.get('analytic_plan_ids')) else None
        for l in aml:
            # under an analytic filter a line counts for its share in the accounts chosen, as in the totals
            f = 1.0 if an_ids is None else self._analytic_share(l.analytic_distribution, an_ids)
            if not f:
                continue
            bal += l.balance * f
            v = vmap.get(l.move_id.id)
            label = self._line_label(l, v)
            if f < 0.9999:
                label = ('%s (%s%% of %s)' % (label, ('%.2f' % (f * 100)).rstrip('0').rstrip('.'), '{:,.2f}'.format(abs(l.balance)))).strip()
            out.append({
                'id': 'l%d' % l.id, 'level': 1, 'kind': 'detail', 'label': l.move_id.name,
                'values': {'date': fields.Date.to_string(l.date), 'date_ec': format_ethiopian(l.date),
                           'number': l.move_id.name if l.move_id.name not in (False, '/') or not v
                           else 'Draft %s' % (v.name if v.name not in (False, '/', 'New') else 'voucher #%d' % v.id),
                           'jrnl': l.journal_id.code,
                           'partner': l.partner_id.name or l.move_id.partner_id.name or '',
                           'name': label, 'debit': l.debit * f, 'credit': l.credit * f, 'balance': bal},
                'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id},
            })
        out.append({'id': 'c%d' % lazy['account_id'], 'level': 1, 'kind': 'detail', 'label': 'Closing balance',
                    'values': {'name': 'Closing balance', 'balance': bal}, 'closing': True})
        return out

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

        def gl_drills(aid):
            # each column opens the ledger of its own period (a comparative its own dates, not the current period's)
            if len(cols_p) < 2:
                return None
            return {c['key']: {'report': 'general_ledger', 'account_ids': [aid], 'exclude_closing': True,
                               'date_from': fields.Date.to_string(c['date_from']),
                               'date_to': fields.Date.to_string(c['date_to'])} for c in cols_p}
        for skey, slabel, parts in structure:
            if len(parts) > 1 or skey in ('gross', 'operating', 'pbt', 'profit'):
                vals = {k: sum(section_totals.get(p, {}).get(k, 0.0) for p in parts) for k in keys}
                rows.append({'id': skey, 'parent': None, 'level': 0, 'kind': 'total', 'label': slabel, 'values': vals,
                             'sum_of': [(p, 1) for p in parts]})  # costs are negative: gross profit = revenue + cost of sales
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
                    # the ledger leaves out the year-end closing too (as this statement does), so it ends on this figure
                    acc_rows.append({'id': 'a%d' % a.id, 'parent': 'il%d' % line.id, 'level': 2, 'kind': 'line',
                                     'label': '%s %s' % (a.code, a.name), 'code': a.code, 'values': avals,
                                     'drill': {'report': 'general_ledger', 'account_ids': [a.id], 'exclude_closing': True,
                                               'date_from': opts['date_from'], 'date_to': opts['date_to']},
                                     'drills': gl_drills(a.id)})
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
            rows.append({'id': 'tci', 'parent': None, 'level': 0, 'kind': 'total', 'sum_of': [('profit', 1), ('oci', 1)],
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
                              'drill': {'report': 'general_ledger', 'account_ids': [a.id], 'exclude_closing': True,
                                        'date_from': opts['date_from'], 'date_to': opts['date_to']},
                              'drills': gl_drills(a.id)})
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
            fy_start = self._fy_start(dt)
            # this year's own closing (dated on its last day) is left out: the year's profit shows as profit for the
            # year, as on the income statement, and not already inside retained earnings
            balances[c['key']] = self._sums(self._base_domain(opts, None, dt)
                                            + [('account_id.include_initial_balance', '=', True),
                                               '|', ('move_id.sb_closing', '=', False), ('date', '<', fy_start)])
            cur = self._sums(self._base_domain(dict(opts, exclude_closing=True), fy_start, dt)
                             + [('account_id.include_initial_balance', '=', False)], ())
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
        overdrafts = []  # (bank account, {column: amount overdrawn})
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
                        if a.account_type == 'asset_cash' and any(v < -0.005 for v in avals.values()) and self._is_bank_account(a):
                            # a bank account in credit is an overdraft: a liability here (IAS 1.32), cash on the
                            # statement of cash flows
                            overdrafts.append((a, {k: max(-v, 0.0) for k, v in avals.items()}))
                            avals = {k: max(v, 0.0) for k, v in avals.items()}
                        if opts.get('hide_zero') and not any(abs(v) > 0.005 for v in avals.values()):
                            continue
                        for k, v in avals.items():
                            l_vals[k] += v
                        drills = {c['key']: self._bs_account_drill(a, c['date_to']) for c in cols_p}
                        a_rows.append({'id': 'a%d' % a.id, 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                       'label': '%s %s' % (a.code, a.name), 'code': a.code, 'values': avals,
                                       'drill': drills['p0'], 'drills': drills if len(cols_p) > 1 else None})
                    if line.code == 'SFP-EQ-03':
                        for k in keys:
                            l_vals[k] += earnings[k] + prior[k]
                        cye = {c['key']: {'report': 'profit_loss', 'date_from': fields.Date.to_string(self._fy_start(c['date_to'])),
                                          'date_to': fields.Date.to_string(c['date_to'])} for c in cols_p}
                        a_rows.append({'id': 'cye', 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                       'label': 'Profit for the current fiscal year', 'values': dict(earnings),
                                       'drill': cye['p0'], 'drills': cye if len(cols_p) > 1 else None})
                        if any(abs(v) > 0.005 for v in prior.values()):
                            pye = {c['key']: self._prior_results_drill(self._fy_start(c['date_to'])) for c in cols_p}
                            a_rows.append({'id': 'pye', 'parent': 'il%d' % line.id, 'level': 3, 'kind': 'line',
                                           'label': 'Prior years\' results not yet closed', 'values': dict(prior),
                                           'drill': pye['p0'], 'drills': pye if len(cols_p) > 1 else None})
                    if not a_rows:
                        continue
                    for k, v in l_vals.items():
                        s_vals[k] += v
                    s_rows.append({'id': 'il%d' % line.id, 'parent': 'sec_' + skey, 'level': 2, 'kind': 'group',
                                   'label': line.name, 'code': line.code, 'values': dict(l_vals), 'expandable': True})
                    s_rows += a_rows
                if skey == 'current_liability' and overdrafts:
                    o_vals = {k: sum(v[k] for _a, v in overdrafts) for k in keys}
                    for k, v in o_vals.items():
                        s_vals[k] += v
                    s_rows.append({'id': 'overdrafts', 'parent': 'sec_' + skey, 'level': 2, 'kind': 'group',
                                   'label': 'Bank overdrafts', 'values': o_vals, 'expandable': True})
                    s_rows += [{'id': 'od%d' % a.id, 'parent': 'overdrafts', 'level': 3, 'kind': 'line',
                                'label': '%s %s' % (a.code, a.name), 'code': a.code, 'values': v,
                                'drill': self._bs_account_drill(a, cols_p[0]['date_to']),
                                'drills': {c['key']: self._bs_account_drill(a, c['date_to']) for c in cols_p}
                                if len(cols_p) > 1 else None} for a, v in overdrafts]
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
            rows.append({'id': top + '_total', 'parent': None, 'level': 0, 'kind': 'total', 'sum_of': [(top, 1)],
                         'label': 'Total %s' % top_label.lower(), 'values': dict(top_vals)})
        el = {k: top_totals.get('equity', {}).get(k, 0.0) + top_totals.get('liabilities', {}).get(k, 0.0) for k in keys}
        rows.append({'id': 'el_total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total equity and liabilities',
                     'values': el, 'sum_of': [('equity_total', 1), ('liabilities_total', 1)]})
        diff = {k: top_totals.get('assets', {}).get(k, 0.0) - el[k] for k in keys}
        unmapped = accounts.filtered(lambda a: not a.sb_ifrs_line_id or a.sb_ifrs_line_id.statement != 'sfp')
        if any(abs(v) > 0.005 for v in diff.values()) and (opts.get('analytic_ids') or opts.get('analytic_plan_ids')):
            # an analytic filter keeps only the lines tagged to the accounts chosen; an entry's other side (bank,
            # payable, VAT) is seldom tagged, so assets need not equal equity and liabilities: said, not an error
            rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note',
                         'label': 'Analytic filter: only the lines tagged to the analytic accounts chosen count (by their '
                                  'share), and the other side of an entry is seldom tagged, so this statement does not '
                                  'balance. Remove the analytic filter for the full statement. Difference:',
                         'values': diff})
        elif any(abs(v) > 0.005 for v in diff.values()):
            rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Out of balance (unmapped accounts: %s)' % ', '.join(unmapped.mapped('code')[:10]),
                         'values': diff})
        # suspense and clearing accounts should be empty at a reporting date: say so when they are not, at any date shown
        def at(a, k):
            return balances[k].get(a.id, {}).get('balance', 0.0)

        def dates_of(a, test):
            return ', '.join('%s at %s' % ('{:,.2f}'.format(at(a, c['key'])), c['date_to'].strftime('%d %b %Y'))
                             for c in cols_p if test(at(a, c['key'])))
        susp = accounts.filtered(lambda a: re.search(r'suspense|clearing', (a.name or '').lower())
                                 and any(abs(at(a, k)) > 0.005 for k in keys))
        if susp:
            rows.append({'id': 'suspense', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Suspense / clearing accounts not cleared, included in the lines above: %s' % '; '.join(
                             '%s %s %s' % (a.code, a.name, dates_of(a, lambda b: abs(b) > 0.005)) for a in susp[:6]),
                         'values': {}})
        if overdrafts:
            rows.append({'id': 'overdraft_note', 'parent': None, 'level': 0, 'kind': 'note', 'values': {},
                         'label': 'Bank overdrafts: bank accounts in credit at the date are shown under current liabilities, '
                                  'not netted in cash; the statement of cash flows keeps them in cash and cash equivalents, '
                                  'as overdrafts repayable on demand (IAS 7.8): %s' % '; '.join(
                                      '%s at %s %s' % (a.code, c['date_to'].strftime('%d %b %Y'), '{:,.2f}'.format(v[c['key']]))
                                      for a, v in overdrafts for c in cols_p if v[c['key']] > 0.005)})
        # accounts on the wrong side (an advance in credit, a payable in debit), at any date shown: left where they
        # are, but said
        contra = r'depreciation|amorti[sz]ation|allowance|provision|impairment'

        def wrong_side(a, b):
            return b < -0.005 if a.account_type.startswith('asset') else b > 0.005
        # a cash box (petty cash) in credit cannot be an overdraft: it stays in cash, said here; accumulated depreciation
        # is a contra account by its mapping, not its name
        dep_ids = set(self._depreciation_accounts().ids)
        wrong = [a for a in accounts if a.account_type.startswith(('asset', 'liability'))
                 and (a.account_type != 'asset_cash' or not self._is_bank_account(a))
                 and a.id not in dep_ids and not re.search(contra, (a.name or '').lower())
                 and any(wrong_side(a, at(a, k)) for k in keys)]
        boxes = [a for a in wrong if a.account_type == 'asset_cash']
        if boxes:
            # a cash box cannot be overdrawn: a petty cash in credit is an entry missing or misposted, kept in cash
            rows.append({'id': 'cash_box_credit', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'values': {},
                         'label': 'Cash boxes in credit, kept in cash and cash equivalents (a cash box cannot be overdrawn: '
                                  'check for missing or misposted entries): %s' % '; '.join(
                                      '%s %s %s' % (a.code, a.name, dates_of(a, lambda b: b < -0.005)) for a in boxes)})
            wrong = [a for a in wrong if a.account_type != 'asset_cash']
        if wrong:
            worst = sorted(wrong, key=lambda a: -max(abs(at(a, k)) for k in keys if wrong_side(a, at(a, k))))
            rows.append({'id': 'wrong_side', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'values': {},
                         'label': 'Accounts on the wrong side, included in the lines above (%d): %s' % (len(wrong), '; '.join(
                             '%s %s %s' % (a.code, a.name, dates_of(a, lambda b, a=a: wrong_side(a, b))) for a in worst[:8]))})
        columns = [{'key': 'label', 'label': '', 'type': 'text'}] + [
            {'key': c['key'], 'label': c['label'], 'sub': c['sub'], 'type': 'monetary'} for c in cols_p]
        dt = self._d(opts['date_to'])
        return self._doc('balance_sheet', opts, columns, rows,
                         'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)))

    def _bs_account_drill(self, account, as_at):
        """The ledger behind a balance as at a date: every entry up to that date (no start date), ending on the figure
        of that column."""
        drill = {'report': 'general_ledger', 'account_ids': [account.id], 'date_from': None,
                 'date_to': fields.Date.to_string(as_at)}
        if account.account_type in ('equity', 'equity_unaffected'):
            # the ledger leaves out that year's own closing too, so it ends on this figure
            drill['exclude_closing_since'] = fields.Date.to_string(self._fy_start(as_at))
        return drill

    def _partner_acc_dom(self, kind):
        """The accounts kept per customer / per vendor (account.sb_partner_side), advances included: money received or
        paid before the invoice is netted on the partner's account, as in Sage. A purchase payable is often a current
        liability rather than a payable-type account, and a supplier advance an asset; staff advances or tax receivable
        are receivable-type but no customer's. Every partner report uses this one set, so aged, ledgers and lists agree."""
        company = self.env.company
        if kind == 'customer':
            return ['|', ('account_id.sb_partner_side', '=', 'customer'),
                    ('account_id', 'in', company.sb_customer_advance_account_id.ids)]
        return ['|', ('account_id.sb_partner_side', '=', 'vendor'),
                ('account_id', 'in', company.sb_vendor_advance_account_id.ids)]

    def _settled_as_at(self, lines, dt):
        """({line id: amount settled as at dt}, the same in the line currency): the matches that count on dt. A match
        counts from its later item's date, or, made after the period of both items was closed, from the day it was made
        (account.partial.reconcile.sb_as_of): a closed period's ageing stays as it was reported."""
        partials = self.env['account.partial.reconcile'].search([
            '|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids),
            '|', ('sb_as_of', '<=', dt), '&', ('sb_as_of', '=', False), ('max_date', '<=', dt)])
        settled = defaultdict(float)
        settled_cur = defaultdict(float)
        for p in partials:
            settled[p.debit_move_id.id] += p.amount
            settled[p.credit_move_id.id] -= p.amount
            settled_cur[p.debit_move_id.id] += p.debit_amount_currency
            settled_cur[p.credit_move_id.id] -= p.credit_amount_currency
        return settled, settled_cur

    # ================================================================== Aged receivables / payables
    def _report_aged(self, opts):
        dt = self._d(opts['date_to'])
        kind = opts.get('partner_type') or 'customer'
        atype = 'asset_receivable' if kind == 'customer' else 'liability_payable'
        buckets = [int(b) for b in (opts.get('buckets') or [30, 60, 90, 120])]
        by_invoice = opts.get('aged_by') == 'invoice'  # days since the document date, instead of days overdue
        sign = 1 if kind == 'customer' else -1
        dom = self._base_domain(opts, None, dt) + self._partner_acc_dom(kind)
        lines = self.env['account.move.line'].search(dom)
        settled, settled_cur = self._settled_as_at(lines, dt)
        company_cur = self.env.company.currency_id
        foreign = defaultdict(lambda: defaultdict(float))  # partner -> currency -> amount still owed in that currency
        keys = ['current'] + ['b%d' % i for i in range(len(buckets))] + ['older']
        labels = ['Current' if by_invoice else 'Not due'] + ['%d-%d' % ((buckets[i - 1] + 1) if i else 1, b) for i, b in enumerate(buckets)] + ['> %d' % buckets[-1]]
        per_partner = defaultdict(lambda: defaultdict(float))
        names = {}
        for l in lines:
            open_amt = l.balance - settled.get(l.id, 0.0)
            if abs(open_amt) < 0.005:
                continue
            due = l.date if by_invoice else (l.date_maturity or l.date)
            days = (dt - due).days
            if sign * open_amt < 0:
                k = 'credit'  # advances and payments not yet matched: their own column, not netted into an age band
            elif days <= 0:
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
            # a customer whose open items net to nothing still shows them (gross columns, total 0)
            if all(abs(v) < 0.005 for v in vals.values()) or not self._search_ok(opts, names[pid]):
                continue
            for k, v in vals.items():
                grand[k] += v
            fc = '; '.join('%s %s' % (cur.name, '{:,.2f}'.format(amt)) for cur, amt in foreign[pid].items() if abs(amt) >= 0.005)
            rows.append({'id': 'p%d' % pid, 'parent': None, 'level': 0, 'kind': 'line', 'label': names[pid],
                         'values': {'partner': names[pid], **vals, 'fc': fc},
                         'drill': {'report': 'partner_ledger', 'partner_ids': [pid],
                                   'partner_type': kind, 'date_to': opts['date_to']} if pid else None})
        keys.append('credit')
        labels.append('Advances')  # with "and unmatched receipts / payments" under it
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'partner': 'Total', **{k: grand.get(k, 0.0) for k in keys + ['total']}}})
        columns = [{'key': 'partner', 'label': 'Customer' if kind == 'customer' else 'Vendor', 'type': 'text'}] + [
            {'key': k, 'label': lab, 'type': 'monetary', 'sub': 'days' if k not in ('current', 'credit') else (
                ('and unmatched receipts' if kind == 'customer' else 'and unmatched payments') if k == 'credit' else '')}
            for k, lab in zip(keys, labels)] + [{'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        if any(foreign.values()):
            columns.append({'key': 'fc', 'label': 'In foreign currency', 'type': 'text'})
        title = 'Aged Receivables' if kind == 'customer' else 'Aged Payables'
        return self._doc('aged', opts, columns, rows, 'As at %s  |  %s  |  aged by %s' % (
            dt.strftime('%d %B %Y'), format_ethiopian(dt), 'invoice date' if by_invoice else 'due date'), title)

    # ================================================================== Partner ledger
    def _report_partner_ledger(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        kind = opts.get('partner_type') or 'customer'
        extra = self._partner_acc_dom(kind)
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
                                  'partner_type': kind}})
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
            self._base_domain(opts, df, dt) + self._partner_acc_dom(lazy.get('partner_type') or ('customer' if lazy.get('account_type') == 'asset_receivable' else 'vendor')) + [
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
                                   'name': self._line_label(l, v), 'debit': l.debit, 'credit': l.credit,
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
        doc = (data or {}).get('doc')
        return {'doc': doc, 'fmt': self._fmt, 'body': self._body(doc) if doc else ''}

    NUMERIC = ('monetary', 'number', 'percent', 'number_int')

    @classmethod
    def _body(cls, doc):
        """The table rows as one HTML string with short class names: a full-year ledger has tens of thousands of cells,
        and rendering them one by one with inline styles made the PDF take minutes."""
        cols = doc['columns']
        out = []
        for row in doc['rows']:
            kind, level = row.get('kind'), row.get('level', 0)
            cls_ = ['b' if kind in ('section', 'total', 'group') else '']
            if row.get('warning'):
                cls_.append('w')
            if row.get('page_break'):
                cls_.append('pb')
            if kind == 'total':
                cls_.append('t2' if level == 0 and row.get('id') in ('total', 'profit', 'el_total', 'assets_total') else 't')
            cells = []
            for i, col in enumerate(cols):
                v = row['values'].get(col['key'])
                if i == 0 and (col['key'] == 'label' or not v):
                    v = row.get('label')
                text = escape(str(cls._fmt(v, col['type']))) if v not in (None, '') else ''
                if col['type'] in cls.NUMERIC:
                    cells.append('<td class="n">%s</td>' % text)
                elif i == 0 and level:
                    cells.append('<td style="padding-left:%dpx">%s</td>' % (4 + 12 * level, text))
                else:
                    cells.append('<td>%s</td>' % text)
            out.append('<tr class="%s">%s</tr>' % (' '.join(c for c in cls_ if c), ''.join(cells)))
        return Markup('\n'.join(out))

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
        if kind == 'date' and isinstance(value, str) and len(value) == 10 and value[4] == '-' and value[7] == '-':
            # the house format, as in the report's heading: 30 Oct 2026
            try:
                return datetime.strptime(value, '%Y-%m-%d').strftime('%d %b %Y')
            except ValueError:
                return value
        return value
