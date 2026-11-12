"""Item and partner history reports shared by Sales and Purchase."""
from collections import defaultdict

from odoo import fields, models


class SbReportShared(models.AbstractModel):
    _inherit = 'sb.report'

    def _items_by_partner(self, opts, types, sign_types=()):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        L = self.env['sb.voucher.line'].search([('voucher_id.company_id', '=', self.env.company.id), ('voucher_id.state', '=', 'posted'),
                                                ('voucher_id.voucher_type', 'in', types), ('voucher_id.date', '>=', df), ('voucher_id.date', '<=', dt),
                                                ('product_id', '!=', False)])
        agg = {}
        for l in L:
            sign = -1 if l.voucher_id.voucher_type in sign_types else 1
            k = (l.product_id, l.voucher_id.partner_id)
            a = agg.setdefault(k, [0.0, 0.0])
            a[0] += sign * l.quantity
            a[1] += sign * l.price_subtotal
        return df, dt, agg

    def _items_report(self, key, title, who_label, opts, types, sign_types):
        df, dt, agg = self._items_by_partner(opts, types, sign_types)
        rows, by_item = [], {}
        for (p, partner), (q, amt) in sorted(agg.items(), key=lambda kv: (kv[0][0].display_name, kv[0][1].name or '')):
            if not self._search_ok(opts, p.display_name, partner.name):
                continue
            g = by_item.setdefault(p, {'qty': 0.0, 'amount': 0.0})
            g['qty'] += q
            g['amount'] += amt
            rows.append({'id': 'l%d_%d' % (p.id, partner.id), 'parent': 'p%d' % p.id, 'level': 1, 'kind': 'line', 'label': partner.name or '',
                         'values': {'item': partner.name or '', 'qty': q, 'amount': amt, 'price': amt / q if q else 0.0}})
        out, grand = [], 0.0
        for p, g in by_item.items():
            grand += g['amount']
            out.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'group', 'label': p.display_name, 'expandable': True,
                        'values': {'item': p.display_name, 'qty': g['qty'], 'amount': g['amount'], 'price': g['amount'] / g['qty'] if g['qty'] else 0.0}})
            out += [r for r in rows if r['parent'] == 'p%d' % p.id]
        out.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'item': 'Total', 'amount': grand}})
        cols = [{'key': 'item', 'label': 'Item / %s' % who_label, 'type': 'text'}, {'key': 'qty', 'label': 'Quantity', 'type': 'number'},
                {'key': 'price', 'label': 'Average price', 'type': 'monetary'}, {'key': 'amount', 'label': 'Amount before tax', 'type': 'monetary'}]
        return self._doc(key, opts, cols, out, self._period_name(df, dt), title)

    def _tx_history(self, key, title, opts, types):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        V = self.env['sb.voucher'].search([('company_id', '=', self.env.company.id), ('voucher_type', 'in', types), ('date', '>=', df), ('date', '<=', dt),
                                           ('partner_id', '!=', False)], order='partner_id, date, id')
        labels = dict(V._fields['voucher_type']._description_selection(self.env))
        rows, groups = [], {}
        for v in V:
            if not self._search_ok(opts, v.name, v.partner_id.name, v.reference):
                continue
            pk = 'p%d' % v.partner_id.id
            if pk not in groups:
                groups[pk] = {'amount': 0.0}
                rows.append({'id': pk, 'parent': None, 'level': 0, 'kind': 'group', 'label': v.partner_id.name, 'expandable': True,
                             'values': {'ref': v.partner_id.name}})
            groups[pk]['amount'] += v.amount_total if v.state == 'posted' else 0.0
            rows.append({'id': 'v%d' % v.id, 'parent': pk, 'level': 1, 'kind': 'line', 'label': v.name or '',
                         'values': {'date': fields.Date.to_string(v.date), 'ref': v.name or '(draft)', 'type': labels.get(v.voucher_type),
                                    'reference': v.reference or '', 'state': dict(v._fields['state']._description_selection(self.env)).get(v.state),
                                    'amount': v.amount_total},
                             'open': {'model': 'sb.voucher', 'id': v.id}})
        for r in rows:
            if r['kind'] == 'group':
                r['values']['amount'] = groups[r['id']]['amount']
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Document', 'type': 'text'}, {'key': 'type', 'label': 'Type', 'type': 'text'},
                {'key': 'reference', 'label': 'Reference', 'type': 'text'}, {'key': 'state', 'label': 'Status', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc(key, opts, cols, rows, self._period_name(df, dt), title)


    # ------------------------------------------------------------------ equity statements
    EQUITY_COMPONENTS = {'SFP-EQ-01': 'capital', 'SFP-EQ-02': 'reserves', 'SFP-EQ-03': 'retained', 'SFP-EQ-04': 'oci'}

    def _equity_cols(self):
        """Equity accounts by component, on the statement of financial position's own IFRS lines (so the statement
        ends on the balance sheet's equity): {component: accounts}, and {component: label}."""
        lines = self._ifrs_lines('sfp').filtered(lambda l: l.section == 'equity')
        accs = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('sb_ifrs_line_id', 'in', lines.ids),
                                                   ('include_initial_balance', '=', True)], order='code')
        groups, names = {}, {}
        for line in lines:
            key = self.EQUITY_COMPONENTS.get(line.code, 'reserves')
            names.setdefault(key, line.name if key != 'reserves' else 'Reserves')
            groups.setdefault(key, self.env['account.account'])
            groups[key] |= accs.filtered(lambda a: a.sb_ifrs_line_id == line)
        groups.setdefault('retained', self.env['account.account'])
        names.setdefault('retained', 'Retained earnings')
        return groups, names

    def _bal_accounts(self, accounts, upto, frm=None, closing=None):
        dom = [('account_id', 'in', accounts.ids), ('parent_state', '=', 'posted'), ('date', '<=', upto), ('company_id', '=', self.env.company.id)]
        if frm:
            dom.append(('date', '>=', frm))
        if closing is not None:
            dom.append(('move_id.sb_closing', '=', closing))
        return -(self.env['account.move.line']._read_group(dom, [], ['balance:sum'])[0][0] or 0.0)

    def _profit(self, df, dt):
        pl = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('internal_group', 'in', ('income', 'expense'))])
        return self._bal_accounts(pl, dt, df, closing=False)

    def _equity_at(self, opts, day, comp_of):
        """Each component of equity as the statement of financial position shows it at a day: the equity accounts
        without the year's own closing, and in retained earnings the year's profit so far plus the results of earlier
        years not yet closed."""
        from datetime import timedelta
        fy_start = self._fy_start(day)
        out = defaultdict(float)
        bal = self._sums(self._base_domain(opts, None, day) + [('account_id', 'in', list(comp_of)),
                                                               '|', ('move_id.sb_closing', '=', False), ('date', '<', fy_start)])
        for aid, v in bal.items():
            out[comp_of[aid]] -= v['balance']
        cur = self._sums(self._base_domain(dict(opts, exclude_closing=True), fy_start, day)
                         + [('account_id.include_initial_balance', '=', False)], ())
        out['retained'] -= (cur.get((), {}).get('balance', 0.0) if cur else 0.0) + self._prior_results(opts, fy_start)
        return out

    def _equity_block(self, opts, df, dt, groups, order, prefix=''):
        """IAS 1.106 for one period: opening, balances brought forward from the old books, profit, share capital,
        dividends, direct entries to retained earnings, transfers, other movements, closing."""
        from datetime import timedelta
        comp_of = {a.id: k for k, accs in groups.items() for a in accs}
        before = df - timedelta(days=1)
        opening = self._equity_at(opts, before, comp_of)
        closing = self._equity_at(opts, dt, comp_of)
        pl = self._sums(self._base_domain(dict(opts, exclude_closing=True), df, dt)
                        + [('account_id.include_initial_balance', '=', False)], ())
        profit = -(pl.get((), {}).get('balance', 0.0)) if pl else 0.0
        # every entry on an equity account in the period (the year-end closing aside), classified entry by entry
        brought = self._opening_move_ids()
        lines = self._sums(self._base_domain(dict(opts, exclude_closing=True), df, dt) + [('account_id', 'in', list(comp_of))],
                           ('move_id', 'account_id'))
        per_move = defaultdict(lambda: defaultdict(float))
        accs_of = defaultdict(set)
        for (mid, aid), v in lines.items():
            per_move[mid][comp_of[aid]] -= v['balance']
            accs_of[mid].add(aid)
        # a dividend: equity goes down against a dividend account (payable, or paid) for most of the amount; a clearing
        # entry that touches a dividend account by a few cents is not one
        dividend_credit = {}
        if per_move:
            dividend_credit = {m.id: -(bal or 0.0) for m, bal in self.env['account.move.line']._read_group(
                [('move_id', 'in', list(per_move)), ('account_id.name', 'ilike', 'dividend'),
                 ('account_id', 'not in', list(comp_of))], ['move_id'], ['balance:sum'])}
        kinds = ('brought', 'capital', 'dividends', 'corrections', 'transfers', 'other')
        mv = {k: defaultdict(float) for k in kinds}
        mv_moves = {k: set() for k in kinds}
        mv_accs = {k: set() for k in kinds}
        for mid, comps in per_move.items():
            comps = {k: v for k, v in comps.items() if abs(v) > 0.005}
            if not comps:
                continue
            net = sum(comps.values())
            if mid in brought:
                kind = 'brought'
            elif abs(net) < 0.005:
                kind = 'transfers'
            elif net < -0.005 and dividend_credit.get(mid, 0.0) >= 0.5 * -net:
                kind = 'dividends'
            elif 'capital' in comps:
                kind = 'capital'
            elif set(comps) == {'retained'}:
                kind = 'corrections'
            else:
                kind = 'other'
            for k, v in comps.items():
                mv[kind][k] += v
            mv_moves[kind].add(mid)
            mv_accs[kind] |= accs_of[mid]
        rows = []
        d0, d1 = fields.Date.to_string(df), fields.Date.to_string(dt)

        def row(rid, label, vals, kind='line', drill=None, warning=False):
            v = {k: vals.get(k, 0.0) for k in order}
            v['total'] = sum(v.values())
            v['label'] = label
            rows.append({'id': prefix + rid, 'parent': None, 'level': 0, 'kind': kind, 'label': label, 'values': v,
                         'drill': drill, 'warning': warning})
            return v

        def entries(kind):
            return {'report': 'general_ledger', 'account_ids': sorted(mv_accs[kind]), 'move_ids': sorted(mv_moves[kind]),
                    'date_from': d0, 'date_to': d1}
        sfp = {'report': 'balance_sheet', 'date_from': fields.Date.to_string(self._fy_start(before)),
               'date_to': fields.Date.to_string(before)}
        has_brought = any(abs(v) > 0.005 for v in mv['brought'].values())
        o = row('open', 'Balance at %s' % before.strftime('%d %b %Y'), opening, 'line' if has_brought else 'total', drill=sfp)
        if has_brought:
            # the old books' balances posted in the period are the opening position, not shares issued or other movements
            row('brought', 'Balances brought forward from the previous books', mv['brought'], drill=entries('brought'))
            o = row('open_restated', 'Opening equity, with the balances brought forward',
                    {k: opening.get(k, 0.0) + mv['brought'].get(k, 0.0) for k in order}, 'total')
        row('profit', 'Profit / (loss) for the period', {'retained': profit},
            drill={'report': 'profit_loss', 'date_from': d0, 'date_to': d1})
        labels = {'capital': 'Share capital issued / (redeemed)', 'dividends': 'Dividends',
                  'corrections': 'Prior-period corrections and other entries made directly to retained earnings',
                  'transfers': 'Transfers between components of equity', 'other': 'Other movements'}
        for kind in ('capital', 'dividends', 'corrections', 'transfers', 'other'):
            if any(abs(v) > 0.005 for v in mv[kind].values()) or kind in ('capital', 'dividends'):
                row(kind, labels[kind], mv[kind], drill=entries(kind) if mv_moves[kind] else None)
        computed = {k: o[k] + (profit if k == 'retained' else 0.0) + sum(mv[x].get(k, 0.0) for x in labels) for k in order}
        rest = {k: closing.get(k, 0.0) - computed[k] for k in order}
        if any(abs(v) > 0.005 for v in rest.values()):
            # a year-end closing posted to another component than retained earnings moves equity between components
            row('closing_moves', 'Year-end closing entries between components', rest,
                warning=abs(sum(rest.values())) > 0.005)
        row('close', 'Balance at %s' % dt.strftime('%d %b %Y'), {k: closing.get(k, 0.0) for k in order}, 'total',
            drill={'report': 'balance_sheet', 'date_from': fields.Date.to_string(self._fy_start(dt)), 'date_to': d1})
        return rows

    def _report_equity_changes(self, opts):
        """IAS 1.106: for each component of equity, opening, profit, other movements, closing; with the comparative
        period(s) first, as IAS 1.38 asks, so each period's closing is the next one's opening."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        groups, names = self._equity_cols()
        order = [k for k in ('capital', 'reserves', 'oci', 'retained') if k in groups]
        periods = [c for c in self._column_periods(dict(opts, comparison=opts.get('comparison') if opts.get('comparison') in (
            'previous', 'last_year') else 'none')) if not c.get('is_total')]
        rows = []
        for c in reversed(periods):
            prefix = '' if c['key'] == 'p0' else '%s_' % c['key']
            if len(periods) > 1:
                label = self._period_name(c['date_from'], c['date_to'])
                rows.append({'id': prefix + 'head', 'parent': None, 'level': 0, 'kind': 'section', 'label': label,
                             'values': {'label': label}})
            rows += self._equity_block(opts, c['date_from'], c['date_to'], groups, order, prefix)
        cols = [{'key': 'label', 'label': '', 'type': 'text'}] + [{'key': k, 'label': names[k], 'type': 'monetary'} for k in order] + \
            [{'key': 'total', 'label': 'Total equity', 'type': 'monetary'}]
        sub = self._period_name(df, dt)
        if len(periods) > 1:
            sub += ' with %s' % ', '.join(self._period_name(c['date_from'], c['date_to']) for c in periods[1:])
        return self._doc('equity_changes', opts, cols, rows, sub, 'Statement of Changes in Equity')

    def _report_retained(self, opts):
        doc = self._report_equity_changes(opts)
        rows = []
        for r in doc['rows']:
            rows.append(dict(r, values={'label': r['values']['label'],
                                        'amount': None if r['kind'] == 'section' else r['values'].get('retained', 0.0)}))
        cols = [{'key': 'label', 'label': '', 'type': 'text'}, {'key': 'amount', 'label': 'Retained earnings', 'type': 'monetary'}]
        doc.update(key='retained', title='Retained Earnings', columns=cols, rows=rows)
        return doc

    def _report_working_tb(self, opts):
        """The trial balance with empty adjustment columns, for year-end work on paper or in Excel."""
        doc = self._report_trial_balance(opts)
        cols = [c for c in doc['columns'] if c['key'] in ('code', 'name', 'closing_dr', 'closing_cr')]
        cols += [{'key': 'adj_dr', 'label': 'Adjustment debit', 'type': 'monetary'}, {'key': 'adj_cr', 'label': 'Adjustment credit', 'type': 'monetary'},
                 {'key': 'final', 'label': 'Adjusted balance', 'type': 'monetary'}]
        doc.update(key='working_tb', title='Working Trial Balance', columns=cols)
        return doc
