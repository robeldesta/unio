"""The checks. Each one is plain arithmetic on posted books (no AI, no guessing) and returns findings with the records
that support them. The engine keeps one finding per check and key from run to run: new ones open, ones no longer found
clear themselves, resolved ones that come back reopen, dismissed ones stay dismissed unless the amount grows."""
import logging
import re
import time
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of

_logger = logging.getLogger(__name__)

CHECKS = [
    {'code': 'margin', 'name': 'Cost of sales against sales', 'category': 'Profitability',
     'what': 'Gross margin of every month against the usual margin of the months before; sales below cost; sales with no cost.'},
    {'code': 'stock_gl', 'name': 'Stock against the ledger', 'category': 'Inventory',
     'what': 'Stock value from the stock records against the inventory accounts in the ledger.'},
    {'code': 'wip', 'name': 'Production cost moved into WIP', 'category': 'Inventory',
     'what': 'Each finished month with production receipts has its production cost moved into work in process.'},
    {'code': 'unposted', 'name': 'Saved but not posted', 'category': 'Data health',
     'what': 'Vouchers, receipts and issues saved but not posted: they are not in the books yet.'},
    {'code': 'overdue', 'name': 'Overdue customers', 'category': 'Receivables',
     'what': 'Customer balances past their due date, net of money received but not applied.'},
    {'code': 'slower', 'name': 'Customers paying slower', 'category': 'Receivables',
     'what': 'Customers who now take much longer to pay than over the year before.'},
    {'code': 'dup_bill', 'name': 'Possible duplicate bills', 'category': 'Payables',
     'what': 'Supplier bills with the same invoice number, or the same amount a few days apart.'},
]


def _month(d):
    return d.replace(day=1)


class CtlEngine(models.AbstractModel):
    _name = 'ctl.engine'
    _description = 'Controller checks'

    @api.model
    def _checks(self):
        return CHECKS

    # ------------------------------------------------------------------ running
    @api.model
    def _cron_run(self):
        for company in self.env['res.company'].search([]):
            try:
                self.with_company(company).with_context(allowed_company_ids=[company.id]).run()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception('Controller run failed for %s', company.name)

    @api.model
    def run(self):
        """Run every check for the current company and file the findings."""
        start = time.time()
        company = self.env.company
        Finding = self.env['ctl.finding'].sudo()
        today = fields.Date.context_today(self)
        seen, per_check, new = set(), {}, 0
        for check in CHECKS:
            method = getattr(self, '_check_%s' % check['code'])
            try:
                with self.env.cr.savepoint():
                    found = method()
                    if found is None:
                        per_check[check['code']] = {'skipped': True}
                        continue
                    for f in found:
                        new += self._file(check, f, today)
            except Exception as e:  # one broken check must not stop the others
                _logger.exception('Controller check %s failed', check['code'])
                self.env.invalidate_all()
                per_check[check['code']] = {'error': str(e)[:300]}
                continue
            per_check[check['code']] = {'count': len(found), 'amount': sum(f.get('amount', 0.0) for f in found)}
            seen.update((check['code'], f['key']) for f in found)
        cleared = 0
        for f in Finding.search([('company_id', '=', company.id), ('state', 'in', ('open', 'review'))]):
            if (f.rule, f.key) not in seen and not per_check.get(f.rule, {}).get('error') \
                    and not per_check.get(f.rule, {}).get('skipped'):
                f.write({'state': 'cleared'})
                f.message_post(body=self.env._('No longer found on %s: cleared.', today))
                cleared += 1
        open_count = Finding.search_count([('company_id', '=', company.id), ('state', 'in', ('open', 'review'))])
        run = self.env['ctl.run'].sudo().create({'company_id': company.id, 'seconds': round(time.time() - start, 1),
                                                  'new': new, 'open': open_count, 'cleared': cleared, 'checks': per_check})
        return run.id

    def _file(self, check, f, today):
        """Create or refresh one finding. Returns 1 when it is new (or back)."""
        Finding = self.env['ctl.finding'].sudo()
        company = self.env.company
        rec = Finding.search([('company_id', '=', company.id), ('rule', '=', check['code']), ('key', '=', f['key'])], limit=1)
        vals = {k: f[k] for k in ('name', 'summary', 'advice', 'period', 'amount', 'severity', 'confidence', 'confidence_note',
                                  'metric_label', 'metric_current', 'metric_baseline') if k in f}
        vals.update(last_seen=today, category=check['category'],
                    evidence_ids=[(5, 0, 0)] + [(0, 0, dict(e, sequence=i)) for i, e in enumerate(f.get('evidence', []))])
        if not rec:
            Finding.create(dict(vals, company_id=company.id, rule=check['code'], key=f['key'], first_seen=today))
            return 1
        back = False
        if rec.state in ('resolved', 'cleared'):
            back = True
        elif rec.state == 'dismissed' and abs(f.get('amount', 0.0)) > abs(rec.amount) + company.ctl_materiality:
            back = True
        if back:
            vals['state'] = 'open'
        rec.write(vals)
        if back:
            rec.message_post(body=self.env._('Found again on %s: reopened.', today))
        return 1 if back else 0

    # ------------------------------------------------------------------ helpers
    def _fmt(self, amount):
        return self.env.company.currency_id.format(amount)

    def _ev_move(self, move, role, amount=None):
        voucher = self.env['sb.voucher'].search([('move_id', '=', move.id)], limit=1)
        rec = voucher or move
        return {'role': role, 'label': rec.display_name or move.name, 'date': move.date,
                'partner': move.partner_id.display_name or '', 'amount': amount if amount is not None else move.amount_total,
                'res_model': rec._name, 'res_id': rec.id}

    def _ev_report(self, label, drill, role='Report'):
        return {'role': role, 'label': label, 'drill': drill}

    # ------------------------------------------------------------------ 1. cost of sales against sales
    def _check_margin(self):
        company = self.env.company
        mat = company.ctl_materiality
        points = company.ctl_margin_points
        today = fields.Date.context_today(self)
        start = _month(today) - relativedelta(months=12)
        if company.ctl_start_date:
            start = max(start, company.ctl_start_date)
        self.env.cr.execute("""
            SELECT date_trunc('month', l.date)::date,
                   -SUM(l.balance) FILTER (WHERE a.account_type = 'income'),
                   SUM(l.balance) FILTER (WHERE a.account_type = 'expense_direct_cost')
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              JOIN account_move m ON m.id = l.move_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date >= %s AND l.date <= %s
               AND NOT COALESCE(m.sb_closing, FALSE)
               AND a.account_type IN ('income', 'expense_direct_cost')
             GROUP BY 1 ORDER BY 1""", (company.id, start, today))
        months = {r[0]: (r[1] or 0.0, r[2] or 0.0) for r in self.env.cr.fetchall()}
        if not any(c for _r, c in months.values()):
            return []  # a business with no cost of sales at all (services): nothing to compare
        out = []
        margin_of = {p: 100.0 * (r - c) / r for p, (r, c) in months.items() if r >= mat}
        for i in range(6, -1, -1):
            m = _month(today) - relativedelta(months=i)
            rev, cost = months.get(m, (0.0, 0.0))
            if rev < mat or (company.ctl_start_date and m < _month(company.ctl_start_date)):
                continue
            # the usual margin: the median of the six months before, so one odd month does not move it
            prior = sorted(margin_of[p] for p in (m - relativedelta(months=k) for k in range(1, 7)) if p in margin_of)
            if len(prior) < 2:
                continue
            mid = len(prior) // 2
            b_margin = prior[mid] if len(prior) % 2 else (prior[mid - 1] + prior[mid]) / 2.0
            b_cost = sum(months[p][1] for p in (m - relativedelta(months=k) for k in range(1, 7)) if p in months)
            margin = 100.0 * (rev - cost) / rev
            diff = margin - b_margin
            no_cost = cost <= 0.0 and b_cost > 0.0
            below_cost = margin < 0
            if not no_cost and not below_cost and abs(diff) < points:
                continue
            end = m + relativedelta(months=1, days=-1)
            label = m.strftime('%B %Y')
            unfinished = end >= today
            evidence = [self._ev_report('Income statement, %s' % label, {'report': 'profit_loss', 'date_from': str(m),
                                                                           'date_to': str(min(end, today))})]
            evidence += self._margin_evidence(m, min(end, today))
            usual = min(max(b_margin, 0.0), 95.0)
            if no_cost:
                name = 'Sales with %s cost of sales in %s' % ('negative' if cost < 0 else 'no', label)
                summary = ('Sales of %s were posted in %s with %s of cost of sales. %s, so at least that much cost is '
                           'missing, reversed or posted somewhere else.'
                           % (self._fmt(rev), label, self._fmt(cost),
                              ('At the usual %.1f%% margin the cost would be about %s' % (usual, self._fmt(rev * (1 - usual / 100.0))))
                              if usual > 0 else 'Even sold at no margin the cost would be %s' % self._fmt(rev)))
                amount = rev * (1 - usual / 100.0) - cost
                severity = 'high'
            elif below_cost:
                name = 'Cost of sales above sales in %s' % label
                summary = ('Cost of sales of %s against sales of %s in %s: a %.1f%% margin, selling below cost. The usual '
                           'margin is %.1f%%. Either cost is overstated (wrong account, double issue, cost not reversed '
                           'on returns) or sales are missing.' % (self._fmt(cost), self._fmt(rev), label, margin, b_margin))
                amount = cost - rev * (1 - usual / 100.0)
                severity = 'high'
            else:
                name = 'Gross margin %s to %.1f%% in %s' % ('fell' if diff < 0 else 'rose', margin, label)
                summary = ('Gross margin was %.1f%% in %s against a usual %.1f%% (median of the months before, %+.1f points). '
                           'On sales of %s that is %s of cost %s than the usual rate.'
                           % (margin, label, b_margin, diff, self._fmt(rev), self._fmt(abs(diff) * rev / 100.0),
                              'more' if diff < 0 else 'less'))
                amount = abs(diff) * rev / 100.0
                severity = 'high' if abs(diff) >= 2 * points or margin > 90 else 'medium'
            notes = []
            if unfinished:
                notes.append('the month is not over')
            if 'sb.stock.voucher' in self.env and self.env['sb.stock.voucher'].search_count(
                    [('company_id', '=', company.id), ('state', '=', 'draft'), ('date', '>=', m), ('date', '<=', end)]):
                notes.append('stock documents of the month are not posted')
            out.append({
                'key': m.strftime('%Y-%m'), 'name': name, 'summary': summary, 'period': label, 'amount': amount,
                'severity': severity, 'confidence': 'low' if len(notes) > 1 else 'medium' if notes else 'high',
                'confidence_note': ('; '.join(notes)).capitalize() if notes else 'Posted sales and cost of sales, closing entries left out',
                'metric_label': 'Gross margin %', 'metric_current': round(margin, 1), 'metric_baseline': round(b_margin, 1),
                'advice': 'Open the income statement for the month, then the cost entries below: missing or late stock '
                          'issues, cost posted to another account, price changes or a manual journal on cost of sales.',
                'evidence': evidence})
        return out

    def _margin_evidence(self, df, dt):
        """The entries that move the month most: manual journals on cost of sales first, then the largest cost and sales entries."""
        company = self.env.company
        self.env.cr.execute("""
            SELECT l.move_id, a.account_type, SUM(l.balance), j.type
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              JOIN account_move m ON m.id = l.move_id
              JOIN account_journal j ON j.id = m.journal_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date BETWEEN %s AND %s
               AND NOT COALESCE(m.sb_closing, FALSE) AND a.account_type IN ('income', 'expense_direct_cost')
             GROUP BY 1, 2, 4""", (company.id, df, dt))
        rows = self.env.cr.fetchall()
        Move = self.env['account.move']
        manual = sorted([r for r in rows if r[1] == 'expense_direct_cost' and r[3] == 'general'], key=lambda r: -abs(r[2]))[:5]
        cost = sorted([r for r in rows if r[1] == 'expense_direct_cost' and r not in manual], key=lambda r: -abs(r[2]))[:5]
        sales = sorted([r for r in rows if r[1] == 'income'], key=lambda r: r[2])[:5]
        ev = [self._ev_move(Move.browse(r[0]), 'Journal on cost of sales', r[2]) for r in manual]
        ev += [self._ev_move(Move.browse(r[0]), 'Cost of sales', r[2]) for r in cost]
        ev += [self._ev_move(Move.browse(r[0]), 'Sale', -r[2]) for r in sales]
        return ev

    # ------------------------------------------------------------------ 2. stock against the ledger
    def _check_stock_gl(self):
        Report = self.env['sb.report']
        if not hasattr(Report, '_report_inv_gl_rec'):
            return None  # no inventory app
        company = self.env.company
        today = fields.Date.context_today(self)
        doc = Report.get_report('inv_gl_rec', {'date_to': str(today)})
        wip = self.env['account.account']
        if 'fprr' in dict(self.env['sb.stock.voucher']._fields['voucher_type'].selection):
            wip = self.env['sb.stock.voucher'].search([('company_id', '=', company.id), ('voucher_type', '=', 'fprr'),
                                                       ('account_id', '!=', False)], limit=200).mapped('account_id')
        out = []
        for row in doc['rows']:
            if row.get('kind') != 'line' or (row['id'][1:].isdigit() and int(row['id'][1:]) in wip.ids):
                continue  # work in process is not kept in the stock records: the WIP check follows it
            v = row['values']
            diff = v.get('diff') or 0.0
            if abs(diff) < company.ctl_materiality:
                continue
            acc = self.env['account.account'].browse(int(row['id'][1:]))
            evidence = [self._ev_report('Inventory to ledger reconciliation', {'report': 'inv_gl_rec', 'date_to': str(today)}),
                        self._ev_report('General ledger, %s' % acc.display_name,
                                        {'report': 'general_ledger', 'account_ids': [acc.id], 'date_from': None, 'date_to': str(today)})]
            # entries on the stock account that did not come from a stock document: the usual cause
            self.env.cr.execute("""
                SELECT l.move_id, SUM(l.balance) FROM account_move_line l JOIN account_move m ON m.id = l.move_id
                 JOIN account_journal j ON j.id = m.journal_id
                 WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.account_id = %s AND j.type = 'general'
                   AND l.date >= %s AND NOT EXISTS (SELECT 1 FROM sb_stock_voucher s WHERE s.move_id = m.id)
                 GROUP BY 1 ORDER BY abs(SUM(l.balance)) DESC LIMIT 10""", (company.id, acc.id, today - timedelta(days=365)))
            evidence += [self._ev_move(self.env['account.move'].browse(mid), 'Journal on the stock account', bal)
                         for mid, bal in self.env.cr.fetchall()]
            out.append({
                'key': acc.code or str(acc.id), 'period': today.strftime('At %d %b %Y'),
                'name': 'Stock and ledger differ by %s on %s' % (self._fmt(abs(diff)), acc.display_name),
                'summary': 'The stock records value %s at %s; the ledger shows %s. The %s by %s.'
                           % (acc.display_name, self._fmt(v.get('stock') or 0.0), self._fmt(v.get('gl') or 0.0),
                              'stock records are higher' if diff > 0 else 'ledger is higher', self._fmt(abs(diff))),
                'amount': abs(diff), 'severity': 'high' if abs(diff) >= 5 * company.ctl_materiality else 'medium',
                'confidence': 'high', 'confidence_note': 'Both sides read from posted records on the same date',
                'metric_label': 'Stock records', 'metric_current': v.get('stock') or 0.0, 'metric_baseline': v.get('gl') or 0.0,
                'advice': 'Look for journals posted straight to the stock account, stock documents posted without an entry, '
                          'and opening balances entered on one side only.',
                'evidence': evidence})
        return out

    # ------------------------------------------------------------------ 2a. production cost into WIP
    def _check_wip(self):
        """Companies that book material issues to cost of sales and move the month's production cost into work in process
        with a month-end journal (Eco Green's TCA/WIP): a finished month whose production receipts took value out of WIP
        but no journal put the month's cost in. Until it is posted, that month's cost of sales counts the materials twice."""
        if 'sb.stock.voucher' not in self.env or 'fprr' not in dict(self.env['sb.stock.voucher']._fields['voucher_type'].selection):
            return None
        company = self.env.company
        today = fields.Date.context_today(self)
        S = self.env['sb.stock.voucher']
        wip = S.search([('company_id', '=', company.id), ('voucher_type', '=', 'fprr'), ('state', '=', 'posted'),
                        ('account_id', '!=', False)], limit=200).mapped('account_id')
        if not wip:
            return None
        start = company.ctl_start_date or (today - relativedelta(months=12))
        periods = []
        for fy in sorted({fiscal_year_of(start), fiscal_year_of(today)}):
            periods += [p for p in fiscal_periods(fy) if p[3] < today and p[3] >= start]
        out = []
        for _no, label, df, dt in periods:
            fprr = S.search([('company_id', '=', company.id), ('voucher_type', '=', 'fprr'), ('state', '=', 'posted'),
                             ('date', '>=', df), ('date', '<=', dt)])
            received = sum(fprr.mapped('amount_total'))
            if received < company.ctl_materiality:
                continue
            stock_moves = S.search([('company_id', '=', company.id), ('move_id', '!=', False), ('date', '>=', df),
                                    ('date', '<=', dt)]).mapped('move_id')
            self.env.cr.execute("""
                SELECT COALESCE(SUM(l.debit), 0) FROM account_move_line l JOIN account_move m ON m.id = l.move_id
                  JOIN account_journal j ON j.id = m.journal_id
                 WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.account_id IN %s AND l.date BETWEEN %s AND %s
                   AND j.type = 'general' AND NOT (m.id = ANY(%s))""", (company.id, tuple(wip.ids), df, dt, stock_moves.ids or [0]))
            moved = self.env.cr.fetchone()[0] or 0.0
            if moved >= company.ctl_materiality:
                continue
            issued = S.search([('company_id', '=', company.id), ('voucher_type', '=', 'giv'), ('state', '=', 'posted'),
                               ('date', '>=', df), ('date', '<=', dt), ('reference', 'not like', 'SALE|%')])
            materials = sum(issued.mapped('amount_total'))
            out.append({
                'key': str(df), 'period': label,
                'name': 'Production cost of %s not moved into WIP' % label,
                'summary': '%s ended on %s. Production receipts took %s out of work in process, but no journal moved the '
                           'month\'s production cost in (materials issued: %s). Until it is posted, the month\'s cost of sales '
                           'carries the materials twice and work in process is understated.'
                           % (label, dt, self._fmt(received), self._fmt(materials)),
                'amount': max(materials, received), 'severity': 'high', 'confidence': 'high',
                'confidence_note': 'No journal on the WIP account in the month',
                'metric_label': 'Moved into WIP', 'metric_current': moved, 'metric_baseline': received,
                'advice': 'Post the month-end production cost journal (TCA/WIP) for %s.' % label,
                'evidence': [self._ev_report('Work in process ledger, %s' % label, {
                    'report': 'general_ledger', 'account_ids': wip.ids, 'date_from': str(df), 'date_to': str(dt)})]
                            + [{'role': 'Production receipt', 'label': v.display_name, 'date': v.date, 'amount': v.amount_total,
                                'res_model': v._name, 'res_id': v.id} for v in fprr[:20]]})
        return out

    # ------------------------------------------------------------------ 2b. saved but not posted
    def _check_unposted(self):
        company = self.env.company
        cutoff = fields.Date.context_today(self) - timedelta(days=company.ctl_draft_days)
        out = []
        V = self.env['sb.voucher']
        vouchers = V.search([('company_id', '=', company.id), ('state', '=', 'draft'), ('date', '<=', cutoff),
                             '|', ('line_ids', '!=', False), ('apply_ids.amount_applied', '!=', 0)], order='date')
        if vouchers:
            amount = sum(vouchers.mapped('amount_total'))
            out.append({
                'key': 'vouchers', 'name': '%d voucher%s saved but not posted' % (len(vouchers), '' if len(vouchers) == 1 else 's'),
                'summary': '%d voucher%s, the oldest of %s, total %s. They are not in the ledger, so the reports leave them out.'
                           % (len(vouchers), '' if len(vouchers) == 1 else 's', vouchers[0].date, self._fmt(amount)),
                'amount': amount, 'severity': 'medium' if amount >= company.ctl_materiality else 'low', 'confidence': 'high',
                'confidence_note': 'Read from the vouchers', 'advice': 'Post them or delete them.',
                'evidence': [{'role': dict(v._fields['voucher_type'].selection).get(v.voucher_type), 'label': v.display_name,
                              'date': v.date, 'partner': v.partner_id.display_name or '', 'amount': v.amount_total,
                              'res_model': v._name, 'res_id': v.id} for v in vouchers[:50]]})
        if 'sb.stock.voucher' in self.env:
            S = self.env['sb.stock.voucher']
            docs = S.search([('company_id', '=', company.id), ('state', '=', 'draft'), ('date', '<=', cutoff),
                             ('line_ids', '!=', False)], order='date')
            if docs:
                amount = sum(docs.mapped('amount_total'))
                types = dict(S._fields['voucher_type'].selection)
                out.append({
                    'key': 'stock', 'name': '%d stock document%s saved but not posted' % (len(docs), '' if len(docs) == 1 else 's'),
                    'summary': '%d receipt%s, issue%s and transfer%s, the oldest of %s, value %s. Stock and cost of sales leave '
                               'them out until they are posted.' % (len(docs), *(('' if len(docs) == 1 else 's'),) * 3, docs[0].date,
                                                                   self._fmt(amount)),
                    'amount': amount, 'severity': 'high' if amount >= 5 * company.ctl_materiality else 'medium',
                    'confidence': 'high', 'confidence_note': 'Read from the stock documents', 'advice': 'Post them or void them.',
                    'evidence': [{'role': types.get(d.voucher_type), 'label': d.display_name, 'date': d.date,
                                  'amount': d.amount_total, 'res_model': d._name, 'res_id': d.id} for d in docs[:50]]})
        return out

    # ------------------------------------------------------------------ 3. receivables
    def _check_overdue(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        due_before = today - timedelta(days=company.ctl_overdue_days)
        AML = self.env['account.move.line']
        base = [('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                ('account_id.account_type', '=', 'asset_receivable'), ('partner_id', '!=', False)]
        lines = AML.search(base + [('amount_residual', '!=', 0)])
        per = defaultdict(lambda: AML)
        for l in lines:
            per[l.partner_id.commercial_partner_id] |= l
        # a contact shared by several debtor accounts (one account per salesperson or route, as in Peachtree books):
        # each account is followed on its own
        for partner in list(per):
            accounts = per[partner].account_id
            if len(accounts) > 1:
                pl = per.pop(partner)
                for a in accounts:
                    per[(partner, a)] = pl.filtered(lambda l, a=a: l.account_id == a)
        out, stale = [], []
        year_ago = today - timedelta(days=365)
        self.env.cr.execute("""
            SELECT DISTINCT COALESCE(p.commercial_partner_id, p.id) FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id JOIN res_partner p ON p.id = l.partner_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND a.account_type = 'asset_receivable'
               AND l.date >= %s AND l.balance < 0""", (company.id, year_ago))
        paid_lately = {r[0] for r in self.env.cr.fetchall()}
        for who, pl in per.items():
            partner, account = who if isinstance(who, tuple) else (who, None)
            title = '%s · %s' % (partner.name, account.display_name) if account else partner.name
            key = '%d:%d' % (partner.id, account.id) if account else str(partner.id)
            overdue = pl.filtered(lambda l: l.amount_residual > 0 and (l.date_maturity or l.date) < due_before)
            credits = pl.filtered(lambda l: l.amount_residual < 0)
            od, net = sum(overdue.mapped('amount_residual')), sum(pl.mapped('amount_residual'))
            amount = min(od, net)
            if amount < company.ctl_materiality:
                continue
            oldest = min(overdue.mapped(lambda l: l.date_maturity or l.date))
            days = (today - oldest).days
            newest = max(overdue.mapped(lambda l: l.date_maturity or l.date))
            if newest < year_ago and partner.id not in paid_lately and not account:
                stale.append((partner, amount, oldest))  # nothing new and nothing paid for a year: one finding for all of them
                continue
            note = ('Money received but not applied: %s' % self._fmt(-sum(credits.mapped('amount_residual')))) if credits else \
                'Open items after every receipt applied'
            out.append({
                'key': key, 'period': today.strftime('At %d %b %Y'),
                'name': '%s: %s overdue, oldest %d days' % (title, self._fmt(amount), days),
                'summary': '%s: %s past due for more than %d days (%d invoice%s); the oldest was due %s.'
                           % (title, self._fmt(amount), company.ctl_overdue_days, len(overdue),
                              '' if len(overdue) == 1 else 's', oldest),
                'amount': amount, 'severity': 'high' if days > 90 and amount >= 5 * company.ctl_materiality else 'medium',
                'confidence': 'medium' if credits else 'high', 'confidence_note': note,
                'metric_label': 'Days overdue', 'metric_current': days, 'metric_baseline': company.ctl_overdue_days,
                'advice': 'Confirm the balance with the customer; apply receipts and credit notes still open on the account.',
                'evidence': [self._ev_report('Account ledger, %s' % account.display_name, {
                                 'report': 'general_ledger', 'account_ids': [account.id], 'date_from': None, 'date_to': str(today)})
                             if account else
                             self._ev_report('Customer ledger', {'report': 'partner_ledger', 'partner_ids': [partner.id],
                                                                'partner_type': 'customer', 'date_from': None, 'date_to': str(today)})]
                            + [dict(self._ev_move(l.move_id, 'Overdue since %s' % (l.date_maturity or l.date), l.amount_residual))
                               for l in overdue.sorted(lambda l: l.date_maturity or l.date)[:15]]
                            + [dict(self._ev_move(l.move_id, 'Not applied', l.amount_residual)) for l in credits[:5]]})
        if stale:
            total = sum(a for _p, a, _d in stale)
            stale.sort(key=lambda x: -x[1])
            out.append({
                'key': 'stale', 'period': today.strftime('At %d %b %Y'),
                'name': '%d customers owe %s with no payment for over a year' % (len(stale), self._fmt(total)),
                'summary': '%d customer balances, total %s, are more than a year old and nothing was received from these '
                           'customers in the last year. They may not be collectable.' % (len(stale), self._fmt(total)),
                'amount': total, 'severity': 'high' if total >= 5 * company.ctl_materiality else 'medium', 'confidence': 'high',
                'confidence_note': 'Open items older than a year, no receipt since',
                'advice': 'Confirm each balance in writing. Chase the ones that are real; propose an allowance or write-off '
                          'for the rest to the manager.',
                'evidence': [{'role': 'Due since %s' % d, 'label': p.name, 'amount': a, 'res_model': 'res.partner', 'res_id': p.id,
                              'drill': {'report': 'partner_ledger', 'partner_ids': [p.id], 'partner_type': 'customer',
                                        'date_from': None, 'date_to': str(today)}} for p, a, d in stale]})
        return out

    def _check_slower(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        self.env.cr.execute("""
            SELECT COALESCE(p.commercial_partner_id, p.id), d.id, d.date, MAX(c.date)
              FROM account_move_line d
              JOIN account_account a ON a.id = d.account_id
              JOIN res_partner p ON p.id = d.partner_id
              JOIN account_partial_reconcile r ON r.debit_move_id = d.id
              JOIN account_move_line c ON c.id = r.credit_move_id
             WHERE d.company_id = %s AND d.parent_state = 'posted' AND d.reconciled AND d.balance > 0
               AND a.account_type = 'asset_receivable'
             GROUP BY 1, 2, 3 HAVING MAX(c.date) >= %s""", (company.id, today - timedelta(days=455)))
        recent, before = defaultdict(list), defaultdict(list)
        for pid, _lid, d, paid in self.env.cr.fetchall():
            (recent if paid >= today - timedelta(days=90) else before)[pid].append((paid - d).days)
        out = []
        for pid, days in recent.items():
            prior = before.get(pid, [])
            if len(days) < 3 or len(prior) < 3:
                continue
            now, then = sum(days) / len(days), sum(prior) / len(prior)
            if now - then < company.ctl_slower_days:
                continue
            partner = self.env['res.partner'].browse(pid)
            open_bal = sum(self.env['account.move.line'].search([
                ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                ('account_id.account_type', '=', 'asset_receivable'), ('partner_id', 'child_of', pid)]).mapped('amount_residual'))
            if open_bal < company.ctl_materiality:
                continue
            out.append({
                'key': str(pid), 'period': 'Last 90 days',
                'name': '%s now pays in %d days (was %d)' % (partner.name, now, then),
                'summary': 'Over the last 90 days %s paid %d invoices in %d days on average, against %d days over the year '
                           'before. %s is open on the account now.' % (partner.name, len(days), now, then, self._fmt(open_bal)),
                'amount': open_bal, 'severity': 'medium' if now - then >= 2 * company.ctl_slower_days else 'low',
                'confidence': 'high' if len(days) >= 5 else 'medium',
                'confidence_note': '%d invoices paid recently, %d in the year before' % (len(days), len(prior)),
                'metric_label': 'Days to pay', 'metric_current': round(now), 'metric_baseline': round(then),
                'advice': 'Ask why; review the credit limit and terms before the next sale on credit.',
                'evidence': [self._ev_report('Customer ledger', {'report': 'partner_ledger', 'partner_ids': [pid],
                                                                  'partner_type': 'customer', 'date_from': None, 'date_to': str(today)})]})
        return out

    # ------------------------------------------------------------------ 4. duplicate supplier bills
    def _check_dup_bill(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        since = today - timedelta(days=400)
        bills = []  # (record, partner, ref, date, amount)
        V = self.env['sb.voucher']
        for v in V.search([('company_id', '=', company.id), ('state', '=', 'posted'), ('date', '>=', since),
                           ('voucher_type', 'in', ('bill', 'cash_purchase')), ('partner_id', '!=', False)]):
            bills.append((v, v.partner_id.commercial_partner_id, v.reference or (v.move_id.ref or ''), v.date, v.amount_total))
        with_voucher = set(V.search([('company_id', '=', company.id), ('move_id', '!=', False)]).mapped('move_id').ids)
        for m in self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted'), ('date', '>=', since),
                                                  ('move_type', '=', 'in_invoice'), ('partner_id', '!=', False)]):
            if m.id not in with_voucher:
                bills.append((m, m.partner_id.commercial_partner_id, m.ref or '', m.date, m.amount_total))
        norm = lambda r: re.sub(r'[^0-9A-Z]', '', (r or '').upper()).lstrip('0')  # noqa: E731
        groups, used = [], set()
        by_ref = defaultdict(list)
        for b in bills:
            n = norm(b[2])
            if len(n) >= 3:
                by_ref[(b[1].id, n)].append(b)
        for (_pid, n), bs in by_ref.items():
            if len(bs) > 1:
                groups.append(('ref', bs))
                used.update((b[0]._name, b[0].id) for b in bs)
        by_amount = defaultdict(list)
        for b in bills:
            if (b[0]._name, b[0].id) not in used and b[4] > 0:
                by_amount[(b[1].id, round(b[4], 2))].append(b)
        for bs in by_amount.values():
            bs.sort(key=lambda b: b[3])
            cluster = [bs[0]]
            for b in bs[1:]:
                if (b[3] - cluster[-1][3]).days <= company.ctl_duplicate_days:
                    cluster.append(b)
                else:
                    if len(cluster) > 1:
                        groups.append(('amount', cluster))
                    cluster = [b]
            if len(cluster) > 1:
                groups.append(('amount', cluster))
        out = []
        for kind, bs in groups:
            bs.sort(key=lambda b: (b[3], b[0].id))
            extra = sum(b[4] for b in bs[1:])
            partner = bs[0][1]
            if kind == 'amount' and extra < company.ctl_materiality / 10:
                continue
            same_ref = kind == 'ref'
            out.append({
                'key': ','.join('%s:%d' % (b[0]._name, b[0].id) for b in bs), 'period': '%s to %s' % (bs[0][3], bs[-1][3]),
                'name': '%s: %d bills %s' % (partner.name, len(bs), 'with invoice no. %s' % bs[0][2] if same_ref
                                             else 'of %s within %d days' % (self._fmt(bs[0][4]), company.ctl_duplicate_days)),
                'summary': '%d bills from %s %s, total %s. If one was entered twice, %s was booked (and maybe paid) too much.'
                           % (len(bs), partner.name, 'carry the same supplier invoice number' if same_ref else
                              'for the same amount a few days apart', self._fmt(sum(b[4] for b in bs)), self._fmt(extra)),
                'amount': extra, 'severity': 'high' if same_ref and extra >= company.ctl_materiality else 'medium',
                'confidence': 'high' if same_ref else 'low',
                'confidence_note': 'Same supplier invoice number' if same_ref else 'Same amount only: may be a regular delivery',
                'advice': 'Compare the supplier invoices on paper. If one is a duplicate, reverse it and check whether it was paid.',
                'evidence': [{'role': 'Bill', 'label': b[0].display_name, 'date': b[3], 'partner': b[2] or '', 'amount': b[4],
                              'res_model': b[0]._name, 'res_id': b[0].id} for b in bs]})
        return out

    # ------------------------------------------------------------------ the overview screen
    @api.model
    def overview(self):
        company = self.env.company
        F = self.env['ctl.finding']
        live = F.search([('company_id', '=', company.id), ('state', 'in', ('open', 'review'))])
        run = self.env['ctl.run'].search([('company_id', '=', company.id)], limit=1)
        checks = []
        for c in CHECKS:
            mine = live.filtered(lambda f: f.rule == c['code'])
            info = (run.checks or {}).get(c['code'], {}) if run else {}
            checks.append({'code': c['code'], 'name': c['name'], 'category': c['category'], 'what': c['what'],
                           'count': len(mine), 'amount': sum(mine.mapped('amount')), 'high': len(mine.filtered(lambda f: f.severity == 'high')),
                           'status': 'error' if info.get('error') else 'skipped' if info.get('skipped') else
                           ('never' if not run else 'ok' if not mine else 'found')})
        ran = [c for c in checks if c['status'] in ('ok', 'found')]
        top = live.sorted(lambda f: -f.priority)[:12]
        return {
            'company': company.name, 'currency': company.currency_id.symbol or company.currency_id.name,
            'open': len(live), 'high': len(live.filtered(lambda f: f.severity == 'high')),
            'review': len(live.filtered(lambda f: f.state == 'review')), 'amount': sum(live.mapped('amount')),
            'passed': len([c for c in ran if c['status'] == 'ok']), 'applicable': len(ran),
            'last_run': fields.Datetime.to_string(run.date) if run else False, 'run_seconds': run.seconds if run else 0,
            'checks': checks,
            'top': [{'id': f.id, 'name': f.name, 'summary': f.summary, 'severity': f.severity, 'confidence': f.confidence,
                     'category': f.category, 'amount': f.amount, 'state': f.state, 'period': f.period or '',
                     'user': f.user_id.name or '', 'evidence': f.evidence_count} for f in top],
            'can_run': self.env.user.has_group('sonador_books.group_sb_accountant'),
        }

    @api.model
    def run_now(self):
        self.run()
        return self.overview()
