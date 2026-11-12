"""Presentation layer for the report engine and Navigation Centers:
- a plain-language subtitle, KPI cards and an optional chart for every report document;
- live data for the Business Status dashboard and each Navigation Center.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models

from .locale import locale_of
from .ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_description, fy_label

BLURBS = {
    'trial_balance': 'Every account with its opening balance, movements and closing balance. Debits must equal credits.',
    'general_ledger': 'Every posting per account, with a running balance. Expand an account to see its lines.',
    'account_transactions': 'Every entry on this account, with the running balance. Double-click a line to open its voucher.',
    'cash_book': 'Money in and out of each bank and cash account, with a running balance.',
    'profit_loss': 'What the business earned and spent in the period, on IFRS line items.',
    'income_ytd': 'This period next to the year to date, with each line as a % of revenue.',
    'balance_sheet': 'What the business owns, owes and the owners\' equity, at a date.',
    'aged': 'Who owes us money (or whom we owe), sorted by how long it has been due.',
    'partner_ledger': 'Every invoice, payment and credit per customer or vendor, with running balance.',
    'partner_statement': 'A statement you can send: opening balance, transactions, amount due and ageing.',
    'journal': 'Entries in journal format: each voucher with its debit and credit lines.',
    'document_register': 'One line per document with tax, total, amount paid and status.',
    'partner_sales': 'Totals per customer or vendor for the period, cash and on account.',
    'partner_list': 'Master file: TIN, contact details and current balance.',
    'open_credits': 'Money received or paid in advance that is not yet applied to an invoice or bill.',
    'wht_register': 'Withholding tax per document with TIN and receipt number, ready for the monthly declaration.',
    'cash_requirements': 'Unpaid bills by due date: what has to be paid, and when.',
    'cheque_register': 'Cheques received and issued, and where each one stands.',
    'deposit_report': 'What went into each bank, with deposit slip numbers.',
    'transactions': 'Search every ledger line by number, reference, partner, account or amount.',
    'chart': 'All accounts with their type, IFRS line item and balance.',
    'cash_flow': 'Where cash came from and where it went: operating, investing and financing (IAS 7).',
    'vat_return': 'Output VAT on sales less input VAT on purchases: what is payable this period.',
    'vat_register': 'Every taxable document with the other party\'s TIN, for e-filing.',
    'audit_trail': 'Who created, posted, edited or voided which voucher, and when.',
    'find_vouchers': 'Find any voucher by number, reference, partner, amount or date.',
    'daily_register': 'Everything posted on each day, journal by journal.',
    'accounting_review': 'Things an accountant should look at before closing the period.',
}


def _card(label, value, kind='money', tone=None, hint=None, delta=None):
    return {'label': label, 'value': value, 'kind': kind, 'tone': tone, 'hint': hint, 'delta': delta}


class SbReportPresent(models.AbstractModel):
    _inherit = 'sb.report'

    @api.model
    def get_report(self, key, options=None, expanded=None):
        doc = super().get_report(key, options, expanded)
        try:
            self._present(doc)
        except Exception:  # presentation must never break a report
            doc.setdefault('summary', [])
        return doc

    def _row(self, doc, rid):
        return next((r for r in doc['rows'] if r['id'] == rid), None)

    def _present(self, doc):
        key = doc['key']
        doc['blurb'] = BLURBS.get(key, '')
        doc['summary'] = []
        doc['chart'] = None
        handler = getattr(self, '_present_%s' % key, None)
        if handler:
            handler(doc)
        elif doc['rows']:
            total = self._row(doc, 'total')
            n = len([r for r in doc['rows'] if r['kind'] in ('line', 'group')])
            doc['summary'].append(_card('Lines', n, 'int'))
            if total:
                for col in doc['columns']:
                    if col['type'] == 'monetary' and total['values'].get(col['key']) not in (None, ''):
                        doc['summary'].append(_card(col['label'], total['values'][col['key']]))
                        if len(doc['summary']) >= 4:
                            break

    # ------------------------------------------------------------------ per report
    def _present_trial_balance(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        dr, cr = t.get('debit', 0.0), t.get('credit', 0.0)
        ok = abs(dr - cr) < 0.01 and abs(t.get('closing', 0.0)) < 0.01
        n = len([r for r in doc['rows'] if r['kind'] == 'line'])
        doc['summary'] = [
            _card('Debits in period', dr), _card('Credits in period', cr),
            _card('Status', 'Balanced' if ok else 'Out of balance', 'badge', 'good' if ok else 'bad',
                  None if ok else 'Difference %s' % '{:,.2f}'.format(dr - cr)),
            _card('Accounts with activity', n, 'int'),
        ]

    def _present_profit_loss(self, doc):
        keys = [c['key'] for c in doc['columns'] if c['type'] == 'monetary' and c['key'] not in ('var',)]
        main = 'total' if 'total' in keys else (keys[0] if keys else 'p0')

        def val(rid, k=main):
            r = self._row(doc, rid)
            return (r or {}).get('values', {}).get(k, 0.0) or 0.0

        rev, gross, op, profit = val('revenue'), val('gross'), val('operating'), val('profit')
        prev = 'p1' if 'p1' in keys else None

        def delta(rid):
            if not prev:
                return None
            a, b = val(rid, 'p0'), val(rid, prev)
            return ((a - b) / abs(b) * 100.0) if abs(b) > 0.005 else None

        doc['summary'] = [
            _card('Revenue', rev, delta=delta('revenue')),
            _card('Gross profit', gross, hint=self._pct(gross, rev) + ' margin', delta=delta('gross')),
            _card('Operating profit', op, delta=delta('operating')),
            _card('Profit for the period', profit, tone='good' if profit >= 0 else 'bad',
                  hint=self._pct(profit, rev) + ' of revenue', delta=delta('profit')),
        ]
        month_cols = [c for c in doc['columns'] if c['key'].startswith('m')]
        if len(month_cols) > 1:
            doc['chart'] = {
                'type': 'line',
                'labels': [c['label'].split()[0] for c in month_cols],
                'series': [
                    {'label': 'Revenue', 'data': [val('revenue', c['key']) for c in month_cols]},
                    {'label': 'Gross profit', 'data': [val('gross', c['key']) for c in month_cols]},
                    {'label': 'Profit', 'data': [val('profit', c['key']) for c in month_cols]},
                ],
            }
        else:
            mix = [(r['label'], r['values'].get(main, 0.0)) for r in doc['rows']
                   if r['kind'] == 'group' and r.get('parent') == 'operating_expense' and r['values'].get(main)]
            if mix:
                doc['chart'] = {'type': 'doughnut', 'title': 'Operating expenses',
                                'labels': [m[0] for m in mix], 'series': [{'label': 'Expenses', 'data': [abs(m[1]) for m in mix]}]}

    def _present_income_ytd(self, doc):
        def val(rid, k):
            r = self._row(doc, rid)
            return (r or {}).get('values', {}).get(k, 0.0) or 0.0
        doc['summary'] = [
            _card('Revenue this period', val('revenue', 'cur')),
            _card('Profit this period', val('profit', 'cur'), tone='good' if val('profit', 'cur') >= 0 else 'bad',
                  hint=self._pct(val('profit', 'cur'), val('revenue', 'cur')) + ' of revenue'),
            _card('Revenue year to date', val('revenue', 'ytd')),
            _card('Profit year to date', val('profit', 'ytd'), tone='good' if val('profit', 'ytd') >= 0 else 'bad',
                  hint=self._pct(val('profit', 'ytd'), val('revenue', 'ytd')) + ' of revenue'),
        ]

    def _present_balance_sheet(self, doc):
        def val(rid):
            r = self._row(doc, rid)
            return (r or {}).get('values', {}).get('p0', 0.0) or 0.0
        ca, cl = val('sec_current_asset'), val('sec_current_liability')
        balanced = not self._row(doc, 'diff')
        doc['summary'] = [
            _card('Total assets', val('assets')),
            _card('Total liabilities', val('liabilities')),
            _card('Equity', val('equity')),
            _card('Current ratio', (ca / cl) if abs(cl) > 0.005 else None, 'ratio',
                  hint='current assets / current liabilities'),
            _card('Status', 'Balanced' if balanced else 'Out of balance', 'badge', 'good' if balanced else 'bad'),
        ]
        mix = [(r['label'], r['values'].get('p0', 0.0)) for r in doc['rows']
               if r['kind'] == 'group' and r.get('parent') in ('sec_current_asset', 'sec_non_current_asset')
               and r['values'].get('p0', 0.0) > 0.005]
        if mix:
            doc['chart'] = {'type': 'doughnut', 'title': 'What the assets are',
                            'labels': [m[0] for m in mix], 'series': [{'label': 'Assets', 'data': [m[1] for m in mix]}]}

    def _present_aged(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        bucket_cols = [c for c in doc['columns'] if c['type'] == 'monetary' and c['key'] not in ('total', 'credit')]
        total = t.get('total', 0.0)
        credit = t.get('credit', 0.0)
        gross = total - credit  # owed before advances and unmatched payments
        current = t.get('current', 0.0)
        overdue = gross - current
        older = sum(t.get(c['key'], 0.0) for c in bucket_cols[3:])
        doc['summary'] = [
            _card('Total due', total, hint='after %s of advances and unmatched payments' % '{:,.2f}'.format(-credit)
                  if credit else ''),
            _card('Not yet due', current, tone='good'),
            _card('Overdue', overdue, tone='bad' if overdue > 0.005 else None,
                  hint=self._pct(overdue, gross) + ' of the invoices open'),
            _card('Over %s' % (bucket_cols[2]['label'].split('-')[-1] if len(bucket_cols) > 2 else '60'), older,
                  tone='bad' if older > 0.005 else None, hint='before advances'),
            _card('Customers' if 'Receivable' in doc['title'] else 'Vendors',
                  len([r for r in doc['rows'] if r['kind'] == 'line']), 'int'),
        ]
        # one stacked bar: the total outstanding split by age
        doc['chart'] = {'type': 'bar_stacked', 'labels': ['Outstanding'],
                        'series': [{'label': c['label'], 'data': [t.get(c['key'], 0.0)]} for c in bucket_cols]}
        if 'Receivable' in doc['title']:
            doc['blurb'] = 'Who owes us money, sorted by how long it has been due.'
        else:
            doc['blurb'] = 'Whom we owe, sorted by how long it has been due.'

    def _present_cash_book(self, doc):
        groups = [r for r in doc['rows'] if r['kind'] == 'group']
        opening = sum(r['values'].get('opening', 0.0) for r in groups)
        dr = sum(r['values'].get('debit', 0.0) for r in groups)
        cr = sum(r['values'].get('credit', 0.0) for r in groups)
        doc['summary'] = [_card('Opening', opening), _card('Money in', dr, tone='good'),
                          _card('Money out', cr, tone='bad' if cr else None), _card('Closing', opening + dr - cr)]
        if groups:
            doc['chart'] = {'type': 'bar', 'labels': [r['label'] for r in groups],
                            'series': [{'label': 'In', 'data': [r['values'].get('debit', 0.0) for r in groups]},
                                       {'label': 'Out', 'data': [r['values'].get('credit', 0.0) for r in groups]}]}

    def _present_general_ledger(self, doc):
        groups = [r for r in doc['rows'] if r['kind'] == 'group']
        t = (self._row(doc, 'total') or {}).get('values', {})
        doc['summary'] = [_card('Accounts', len(groups), 'int'), _card('Total debits', t.get('debit', 0.0)),
                          _card('Total credits', t.get('credit', 0.0))]

    def _present_account_transactions(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        n = len([r for r in doc['rows'] if r['kind'] == 'line'])
        doc['summary'] = [_card('Brought forward', t.get('opening', 0.0)), _card('Debits', t.get('debit', 0.0), hint='%d entries' % n),
                          _card('Credits', t.get('credit', 0.0)), _card('Balance', t.get('balance', 0.0))]

    def _present_partner_ledger(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        doc['summary'] = [_card('Opening', t.get('opening', 0.0)), _card('Invoiced / debits', t.get('debit', 0.0)),
                          _card('Paid / credits', t.get('credit', 0.0)), _card('Balance', t.get('balance', 0.0))]

    _present_partner_statement = _present_partner_ledger

    def _present_vat_return(self, doc):
        out_ = (self._row(doc, 'sale') or {}).get('values', {}).get('tax', 0.0)
        in_ = (self._row(doc, 'purchase') or {}).get('values', {}).get('tax', 0.0)
        pay = out_ - in_
        doc['summary'] = [_card('Output VAT (sales)', out_), _card('Input VAT (purchases)', in_),
                          _card('Payable' if pay >= 0 else 'Credit to carry forward', abs(pay), tone='bad' if pay > 0 else 'good')]

    def _present_wht_register(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        n = len([r for r in doc['rows'] if r['kind'] == 'line'])
        missing = len([r for r in doc['rows'] if r['kind'] == 'line' and not r['values'].get('cert')])
        doc['summary'] = [_card('Documents', n, 'int'), _card('Taxable amount', t.get('base', 0.0)),
                          _card('Tax withheld', t.get('amount', 0.0)),
                          _card('Missing receipt no.', missing, 'int', 'bad' if missing else 'good')]

    def _present_vat_register(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        lines = [r for r in doc['rows'] if r['kind'] == 'line']
        no_tin = len([r for r in lines if not r['values'].get('vat')])
        doc['summary'] = [_card('Documents', len(lines), 'int'), _card('Taxable (%g%%)' % locale_of(self.env.company).get('vat', 15.0), t.get('base_std', 0.0)),
                          _card('VAT', t.get('tax', 0.0)), _card('Without TIN', no_tin, 'int', 'bad' if no_tin else 'good')]

    def _present_cash_flow(self, doc):
        def val(rid):
            return (self._row(doc, rid) or {}).get('values', {}).get('amount', 0.0) or 0.0
        if self._row(doc, 'operating'):
            doc['summary'] = [_card('Operating', val('operating')), _card('Investing', val('investing')),
                              _card('Financing', val('financing')), _card('Net change in cash', val('net'),
                                                                         tone='good' if val('net') >= 0 else 'bad')]
            doc['chart'] = {'type': 'bar', 'labels': ['Operating', 'Investing', 'Financing', 'Net'],
                            'series': [{'label': 'Cash flow', 'data': [val('operating'), val('investing'), val('financing'), val('net')]}]}
        else:
            doc['summary'] = [_card('Cash in', val('in'), tone='good'), _card('Cash out', val('out'), tone='bad'),
                              _card('Net', val('net'))]

    def _present_document_register(self, doc):
        t = (self._row(doc, 'total') or {}).get('values', {})
        lines = [r for r in doc['rows'] if r['kind'] == 'line']
        doc['summary'] = [_card('Documents', len(lines), 'int'), _card('Before tax', t.get('untaxed', 0.0)),
                          _card('VAT / tax', t.get('tax', 0.0)), _card('Total', t.get('total', 0.0))]
        if 'due' in t:
            doc['summary'].append(_card('Still to collect' if 'Invoice' in doc['title'] else 'Still to pay',
                                        t.get('due', 0.0), tone='bad' if t.get('due', 0.0) > 0.005 else 'good'))

    def _present_accounting_review(self, doc):
        lines = [r for r in doc['rows'] if r['kind'] == 'line']
        fix = len([r for r in lines if r['values'].get('status') == 'Fix'])
        review = len([r for r in lines if r['values'].get('status') == 'Review'])
        ok = len(lines) - fix - review
        doc['summary'] = [_card('Checks passed', ok, 'int', 'good'), _card('To review', review, 'int', 'warn' if review else 'good'),
                          _card('To fix', fix, 'int', 'bad' if fix else 'good')]

    def _present_bank_rec(self, doc):
        def val(rid):
            return (self._row(doc, rid) or {}).get('values', {}).get('amount', 0.0) or 0.0
        diff = val('diff')
        # the reconciliation's own state: "Reconciled" only once it is finished and proves to the books
        if abs(diff) >= 0.005:
            status, tone = 'Difference %s' % '{:,.2f}'.format(diff), 'bad'
        elif doc.get('rec_state') == 'draft':
            unticked = doc.get('rec_difference') or 0.0
            status = 'In progress' + (', %s to tick' % '{:,.2f}'.format(unticked) if abs(unticked) >= 0.005 else '')
            tone = 'warn'
        else:
            status, tone = 'Reconciled', 'good'
        doc['blurb'] = 'Bank statement balance, plus deposits in transit, less outstanding cheques, against the books.'
        doc['summary'] = [_card('Statement balance', val('stmt')), _card('Deposits in transit', val('dep')),
                          _card('Outstanding cheques', -val('out')), _card('Balance per books', val('gl')),
                          _card('Status', status, 'badge', tone)]

    def _present_cheque_register(self, doc):
        lines = [r for r in doc['rows'] if r['kind'] == 'line']
        t = (self._row(doc, 'total') or {}).get('values', {})
        post = len([r for r in lines if r['values'].get('state') == 'Post-dated'])
        doc['summary'] = [_card('Cheques', len(lines), 'int'), _card('Amount', t.get('amount', 0.0)),
                          _card('Post-dated', post, 'int')]

    def _pct(self, a, b):
        return ('%.1f%%' % (a / b * 100.0)) if abs(b or 0.0) > 0.005 else '–'

    # ================================================================== Navigation centers / Business Status
    @api.model
    def get_center_data(self, center, as_of=None):
        """as_of: the day the figures are taken at (balances, open items, ageing, the year's trend up to it); default today."""
        if as_of:
            return self.with_context(sb_asof=as_of).get_center_data(center)
        company = self.env.company
        today = self._asof()
        fy = fiscal_year_of(today)
        periods = fiscal_periods(fy)
        period = next((p for p in periods if p[2] <= today <= p[3]), None)
        data = {
            'company': company.name,
            'currency': company.currency_id.symbol or company.currency_id.name,
            'today': fields.Date.to_string(today),
            'as_of': fields.Date.to_string(today),
            'is_today': today == fields.Date.context_today(self),
            'today_ec': format_ethiopian(today),
            'fiscal_year': fy, 'fy_label': fy_label(fy),
            'period': '%d - %s' % (period[0], period[1]) if period else '',
            'period_range': '%s - %s' % (period[2].strftime('%d %b'), period[3].strftime('%d %b %Y')) if period else '',
            'lock_date_ec': format_ethiopian(company.fiscalyear_lock_date),
        }
        builder = getattr(self, '_center_%s' % center, None)
        if builder:
            data.update(builder(company, today, fy, periods))
        return data

    def _asof(self):
        """The day the dashboard is looked at: today, or the day chosen at the top (balance at a date)."""
        d = self.env.context.get('sb_asof')
        return fields.Date.to_date(d) if d else fields.Date.context_today(self)

    def _aml(self, domain, groupby, aggs):
        return self.env['account.move.line']._read_group(
            [('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'), ('date', '<=', self._asof())] + domain,
            groupby, aggs)

    def _open_items(self, atype, limit=12):
        """Open balances per customer / vendor taken from the aged report itself, as at today: the dashboard total is the
        Aged Receivables / Aged Payables total (advances and unmatched payments reduce it), to the cent."""
        kind = 'customer' if atype == 'asset_receivable' else 'vendor'
        today = self._asof()
        doc = super().get_report('aged', {'partner_type': kind, 'date_to': fields.Date.to_string(today)})
        rows, total = [], 0.0
        for r in doc['rows']:
            vals = r.get('values') or {}
            if r.get('id') == 'total':
                total = vals.get('total', 0.0)
                continue
            amt = vals.get('total', 0.0)
            if amt < 0.005 or not str(r.get('id', '')).startswith('p'):
                continue  # owes nothing on balance (an advance): in the total, not in the list
            overdue = any(vals.get(k) for k in vals if k.startswith('b') or k == 'older')
            rows.append({'partner_id': int(r['id'][1:]) or False, 'name': r.get('label') or 'No partner', 'amount': amt,
                         'due': '', 'overdue': bool(overdue)})
        sign = 1 if kind == 'customer' else -1
        dues = {p.id: d for p, d in self._aml(self._partner_acc_dom(kind) + [
            ('reconciled', '=', False), ('partner_id', 'in', [r['partner_id'] for r in rows if r['partner_id']]),
            ('balance', '>' if sign > 0 else '<', 0)], ['partner_id'], ['date_maturity:min'])}
        for r in rows:
            d = dues.get(r['partner_id'])
            r['due'] = fields.Date.to_string(d) if d else ''
            r['overdue'] = bool(d and d < today)
        rows.sort(key=lambda r: (r['due'] or '9999', -r['amount']))
        return rows[:limit], total, len(rows)

    def _aging(self, kind):
        doc = super().get_report('aged', {'partner_type': kind, 'date_to': fields.Date.to_string(self._asof())})
        total = next((r for r in doc['rows'] if r['id'] == 'total'), {'values': {}})['values']
        cols = [c for c in doc['columns'] if c['type'] == 'monetary' and c['key'] != 'total']
        return {'labels': [c['label'] for c in cols], 'values': [total.get(c['key'], 0.0) for c in cols],
                'total': total.get('total', 0.0)}

    def _trend(self, fy, periods):
        """Monthly revenue, cost of sales, expenses and profit for the fiscal year so far."""
        today = self._asof()
        months = [p for p in periods if p[2] <= today]
        start = months[0][2] if months else periods[0][2]
        per = defaultdict(lambda: defaultdict(float))
        # one total per account and day, not every line of the year read into memory
        res = self.env['account.move.line']._read_group(
            [('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'), ('date', '>=', start),
             ('date', '<=', today), ('account_id.include_initial_balance', '=', False), ('move_id.sb_closing', '=', False)],
            ['account_id', 'date:day'], ['balance:sum'])
        sections = {}
        for account, day, balance in res:
            day = day.date() if hasattr(day, 'date') else day
            idx = next((i for i, p in enumerate(months) if p[2] <= day <= p[3]), None)
            if idx is None:
                continue
            if account not in sections:
                sections[account] = account.sb_ifrs_line_id.section
            sec = sections[account]
            if sec == 'revenue':
                per[idx]['revenue'] += -balance
            elif sec == 'cost_of_sales':
                per[idx]['cogs'] += balance
            else:
                per[idx]['other'] += -balance
        labels = [p[1].split()[0] for p in months]
        revenue = [per[i]['revenue'] for i in range(len(months))]
        cogs = [per[i]['cogs'] for i in range(len(months))]
        gross = [revenue[i] - cogs[i] for i in range(len(months))]
        net = [gross[i] + per[i]['other'] for i in range(len(months))]
        expenses = [gross[i] - net[i] for i in range(len(months))]
        return {'labels': labels, 'revenue': revenue, 'cogs': cogs, 'gross': gross, 'expenses': expenses, 'net': net,
                'totals': {'revenue': sum(revenue), 'cogs': sum(cogs), 'gross': sum(gross),
                           'expenses': sum(expenses), 'net': sum(net)}}

    def _cash_balances(self):
        res = self._aml([('account_id.account_type', '=', 'asset_cash')], ['account_id'], ['balance:sum'])
        rows = [{'id': a.id, 'code': a.code, 'name': a.name, 'balance': b or 0.0} for a, b in res]
        Account = self.env['account.account'].with_company(self.env.company)
        known = {r['id'] for r in rows}
        for a in Account.search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', self.env.company.id),
                                 ('sb_hidden_template', '=', False)]):  # not the standard chart's idle Bank / Cash on hand
            if a.id not in known:
                rows.append({'id': a.id, 'code': a.code, 'name': a.name, 'balance': 0.0})
        rows.sort(key=lambda r: r['code'] or '')
        return rows

    def _statutory(self):
        """Taxes, statutory and payroll liabilities by account (VAT, PAYE, pension, withholding, profit tax, salaries)."""
        res = self._aml([('account_id.sb_liability_group', 'in', ('tax', 'employee'))], ['account_id'], ['balance:sum'])
        rows = [{'id': a.id, 'code': a.code, 'name': a.name, 'group': a.sb_liability_group, 'balance': -(b or 0.0)}
                for a, b in res if abs(b or 0.0) > 0.005]
        rows.sort(key=lambda r: (r['group'] != 'tax', r['code'] or ''))
        return rows, sum(r['balance'] for r in rows)

    def _center_business(self, company, today, fy, periods):
        owe, owe_total, owe_n = self._open_items('asset_receivable', 8)
        pay, pay_total, pay_n = self._open_items('liability_payable', 8)
        tax, tax_total = self._statutory()
        return {'balances': self._cash_balances(), 'owe': owe, 'owe_total': owe_total, 'owe_count': owe_n,
                'pay': pay, 'pay_total': pay_total, 'pay_count': pay_n, 'tax': tax, 'tax_total': tax_total,
                'aged_ar': self._aging('customer'), 'aged_ap': self._aging('vendor'), 'trend': self._trend(fy, periods)}

    def _partner_list(self, kind, limit=40):
        atype = 'asset_receivable' if kind == 'customer' else 'liability_payable'
        sign = 1 if kind == 'customer' else -1
        bal = {p.id: sign * (b or 0.0) for p, b in self._aml([('account_id.account_type', '=', atype)], ['partner_id'], ['balance:sum'])}
        field = 'customer_rank' if kind == 'customer' else 'supplier_rank'
        partners = self.env['res.partner'].search(['|', (field, '>', 0), ('id', 'in', [k for k in bal if k]),
                                                   ('parent_id', '=', False)], limit=200)
        rows = [{'id': p.id, 'name': p.name, 'vat': p.vat or '', 'phone': p.phone or '', 'balance': bal.get(p.id, 0.0)}
                for p in partners]
        rows.sort(key=lambda r: (-abs(r['balance']), r['name'] or ''))
        return rows[:limit], len(rows)

    def _center_customers(self, company, today, fy, periods):
        rows, n = self._partner_list('customer')
        owe, owe_total, owe_n = self._open_items('asset_receivable', 6)
        return {'partners': rows, 'partner_count': n, 'aged': self._aging('customer'), 'owe_total': owe_total,
                'trend': self._trend(fy, periods)}

    def _center_vendors(self, company, today, fy, periods):
        rows, n = self._partner_list('vendor')
        pay, pay_total, pay_n = self._open_items('liability_payable', 10)
        return {'partners': rows, 'partner_count': n, 'aged': self._aging('vendor'), 'pay': pay, 'pay_total': pay_total}

    def _center_banking(self, company, today, fy, periods):
        pay, pay_total, pay_n = self._open_items('liability_payable', 10)
        cheques = self.env['sb.voucher.cash'].search([('cheque_state', '=', 'open'), ('company_id', '=', company.id)])
        return {'balances': self._cash_balances(), 'pay': pay, 'pay_total': pay_total,
                'cheques_in': sum(cheques.filtered(lambda c: c.direction == 'in').mapped('amount')),
                'cheques_out': sum(cheques.filtered(lambda c: c.direction == 'out').mapped('amount')),
                'cheques_count': len(cheques)}

    def _center_inventory(self, company, today, fy, periods):
        Product = self.env['product.product']
        items = []
        if 'qty_available' in Product._fields:
            for p in Product.search([('type', '=', 'consu'), ('is_storable', '=', True)] if 'is_storable' in Product._fields
                                    else [('type', '=', 'product')], limit=40):
                items.append({'id': p.id, 'code': p.default_code or '', 'name': p.name, 'qty': p.qty_available,
                              'cost': p.standard_price, 'method': p.categ_id.property_cost_method or ''})
        return {'items': items, 'trend': self._trend(fy, periods)}

    def _center_payroll(self, company, today, fy, periods):
        if not self._report_allowed('payroll_register'):
            # pay is for the HR role (or payroll managers); the accounting side sees the posted journal entries only
            return {'employees': [], 'runs': [], 'restricted': True}
        emps = self.env['sb.employee'].search([('company_id', '=', company.id)], order='name')
        runs = self.env['sb.payroll.run'].search([('company_id', '=', company.id)], order='date desc', limit=6)
        return {'employees': [{'id': e.id, 'name': e.name, 'job': e.job or '', 'tin': e.tin or '',
                               'basic': e.basic_salary + e.taxable_allowance + e.nontaxable_allowance} for e in emps],
                'runs': [{'id': r.id, 'name': r.name, 'state': dict(r._fields['state']._description_selection(self.env)).get(r.state),
                          'net': r.total_net, 'tax': r.total_tax, 'pension': r.total_pension_ee + r.total_pension_er} for r in runs]}

    def _center_company(self, company, today, fy, periods):
        lock = company.fiscalyear_lock_date
        return {'trend': self._trend(fy, periods), 'info': [
            ('Company', company.name), ('TIN', company.vat or 'not set'), ('Currency', company.currency_id.name),
            ('Fiscal year', fy_description(fy)), ('Posting method', 'Real-time'),
            ('Accounting method', 'Accrual'), ('Audit trail', 'On (every voucher keeps its history)'),
            ('Books closed through', format_ethiopian(lock) if lock else 'No period closed yet'),
        ]}


class SbSetupGuide(models.AbstractModel):
    _inherit = 'sb.report'

    @api.model
    def get_setup_status(self):
        company = self.env.company
        Journal = self.env['account.journal']
        Partner = self.env['res.partner']
        V = self.env['sb.voucher']
        banks = Journal.search([('type', 'in', ('bank', 'cash')), ('company_id', '=', company.id)])
        own_banks = banks.filtered(lambda j: j.code not in ('BNK1', 'CSH1', 'PC'))
        used = set(self.env['account.move.line'].search([('company_id', '=', company.id)], limit=5000).mapped('account_id').ids)
        unmapped = self.env['account.account'].search([('id', 'in', list(used)), ('sb_ifrs_line_id', '=', False)])
        suspect = self.env['account.account'].search([('id', 'in', list(used)), ('sb_ifrs_suggested_id', '!=', False)])
        steps = [
            ('company', 'Company details and TIN', 'Name, TIN, VAT registration number and address on every document.',
             bool(company.vat), {'type': 'ir.actions.act_window', 'res_model': 'res.company', 'res_id': company.id,
                                 'views': [[False, 'form']]}, company.vat and 'TIN %s' % company.vat or 'TIN missing'),
            ('logo', 'Company logo', 'Printed on reports, statements and vouchers.', bool(company.logo),
             {'type': 'ir.actions.act_window', 'res_model': 'res.company', 'res_id': company.id, 'views': [[False, 'form']]}, ''),
            ('banks', 'Your banks, cash and mobile money', locale_of(company).get('banks_hint', 'One account per bank (CBE, Awash, Dashen ...), USD accounts, telebirr, petty cash.'),
             bool(own_banks), 'sonador_books.action_sb_bank_accounts', '%d accounts' % len(banks)),
            ('taxes', 'VAT and withholding', locale_of(company).get('tax_hint', 'Check the withholding rate and thresholds; VAT 15% is ready (turnover tax was repealed from 8 July 2024).'),
             bool(company.sb_wht_rate), 'sonador_books.action_sb_company_settings', 'Withholding %.1f%%' % company.sb_wht_rate),
            ('ifrs', 'Chart of accounts and IFRS mapping', 'Every account used must sit on an IFRS line item.',
             not unmapped and not suspect, 'sonador_books.action_sb_ifrs_check',
             '%d to map' % len(unmapped) if unmapped else '%d look wrong' % len(suspect) if suspect else 'All mapped'),
            ('partners', 'Customers and vendors', 'Add them with their TIN so VAT registers and statements are complete.',
             bool(Partner.search_count(['|', ('customer_rank', '>', 0), ('supplier_rank', '>', 0)])),
             'sonador_books.action_sb_customers', '%d customers, %d vendors' % (
                 Partner.search_count([('customer_rank', '>', 0)]), Partner.search_count([('supplier_rank', '>', 0)]))),
            ('items', 'Items and costing (if you sell stock)', 'Inventory items with FIFO or average costing.',
             bool(self.env['product.template'].search_count([])), 'sonador_books.action_sb_products', ''),
            ('opening', 'Opening balances', 'Bank balances, open customer and vendor invoices and GL balances at the start date.',
             bool(V.search_count([('voucher_type', '=', 'opening'), ('state', '=', 'posted')])), 'sonador_books.action_sb_beginning', ''),
            ('periods', 'Fiscal years and periods', 'Hamle to Sene, two open years; close periods as you finish them.',
             bool(self.env['account.fiscal.year'].search_count([]) if 'account.fiscal.year' in self.env else True),
             'sonador_books.action_sb_periods_open', ''),
            ('users', 'Users', 'One login per person with a role (Clerk, Accountant, Manager, Auditor), so the audit trail shows who did what.',
             self.env['res.users'].search_count([('share', '=', False)]) > 1, 'base.action_res_users',
             '%d users' % self.env['res.users'].search_count([('share', '=', False)])),
            ('first', 'Your first voucher', 'Post a cash sale, receipt or payment and open its Journal Entry to see the accounts it posted to.',
             bool(V.search_count([('state', '=', 'posted')])), 'sonador_books.action_sb_nav_center', ''),
        ]
        out = [{'key': k, 'title': t, 'help': h, 'done': bool(d), 'action': a, 'note': n} for k, t, h, d, a, n in steps]
        return {'steps': out, 'done': sum(1 for s in out if s['done']), 'total': len(out)}
