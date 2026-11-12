"""Dashboard: one window into the business for the person logged in.

Top to bottom: a short briefing in plain sentences, what waits for them (decided right there), what looks wrong and
why (found by the system, nobody has to report it), a question box (Unio AI, with their own rights), then the numbers.

Everything is read with the person's own rights, so a role sees only what it may open anyway: a store keeper never
sees money, the general manager sees the whole company. A block whose data the role cannot read is left out.

Other modules add to it by inheriting sb.dashboard and appending to _dash_tiles / _dash_signals / _dash_charts /
_dash_questions (each returns a list; a failing one is skipped, the rest still shows)."""
import hashlib
import json
import logging
import re
import time
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

BAD, WARN, INFO = 'bad', 'warn', 'info'

# Each person's dashboard is kept a short while in this worker, so opening it again (or coming back to it) is instant.
# It is built again after a decision taken from the dashboard or from Me, and on Refresh.
CACHE_SECONDS = 120
_CACHE = {}  # (database, user, company) -> (time built, data)


class SbDashboardBrief(models.Model):
    _name = 'sb.dashboard.brief'
    _description = 'Dashboard briefing (written once a day per person)'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='cascade', default=lambda s: s.env.user)
    day = fields.Date(required=True, index=True)
    facts_hash = fields.Char()
    text = fields.Text()
    source = fields.Selection([('ai', 'Unio AI'), ('plain', 'Plain')], default='plain')


class SbDashboard(models.AbstractModel):
    _name = 'sb.dashboard'
    _description = 'Dashboard'

    # ------------------------------------------------------------------ helpers
    def _safe(self, fn, default=None):
        try:
            with self.env.cr.savepoint():
                return fn()
        except Exception:  # noqa: BLE001 - one block failing must not close the dashboard
            _logger.info('dashboard: block skipped', exc_info=True)
            return default

    def _approvals(self):
        """What waits for this person's decision (from Me), read once per request."""
        key = 'sb_dashboard_approvals_%d' % self.env.uid
        if key not in self.env.cr.cache:
            self.env.cr.cache[key] = self._safe(lambda: self.env['sb.me'].approvals(), []) or []
        return self.env.cr.cache[key]

    def _today(self):
        return fields.Date.context_today(self)

    def _can(self, model, group=None):
        if model not in self.env:
            return False
        if group and not self.env.user.has_group(group):
            return False
        return self.env[model].has_access('read')

    def _sees_money(self):
        """Ledger figures (bank, debtors, sales totals): accounting roles and management, not the store or the floor."""
        return self._can('account.move.line', 'account.group_account_readonly')

    def _sees_stock(self):
        return self._can('stock.quant', 'stock.group_stock_user')

    def _cur(self):
        c = self.env.company.currency_id
        return c.symbol or c.name

    def _short(self, v):
        """63,200,212 -> 63.2M; 866,400 -> 866k; for tiles and sentences (the exact figure is one click away)."""
        a = abs(v or 0)
        sign = '-' if (v or 0) < 0 else ''
        if a >= 1e6:
            return '%s%.1fM' % (sign, a / 1e6)
        if a >= 1e4:
            return '%s%.0fk' % (sign, a / 1e3)
        return '%s%s' % (sign, '{:,.0f}'.format(a))

    def _money(self, v):
        return '%s %s' % (self._short(v), self._cur())

    def _full(self, v):
        return '{:,.0f}'.format(v or 0)

    def _ml(self, domain, model='account.move.line'):
        return {'model': model, 'domain': domain}

    def _ec(self, d):
        try:
            from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian, gregorian
            return '' if gregorian() else format_ethiopian(d)
        except Exception:  # noqa: BLE001
            return ''

    def _balance(self, domain):
        r = self.env['account.move.line']._read_group(domain, aggregates=['balance:sum'])
        return (r[0][0] if r else 0.0) or 0.0

    def _cash_domain(self, upto=None):
        d = [('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'asset_cash'), ('company_id', '=', self.env.company.id)]
        if upto:
            d.append(('date', '<=', upto))
        return d

    def _income_by_month(self, months=6):
        """Revenue posted per calendar month (income accounts, credit positive), oldest first, this month last."""
        today = self._today()
        start = (today.replace(day=1) - relativedelta(months=months - 1))
        rows = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'income'), ('date', '>=', start),
             ('date', '<=', today), ('company_id', '=', self.env.company.id)],
            groupby=['date:month'], aggregates=['balance:sum'])
        got = {d.strftime('%Y-%m'): -(b or 0.0) for d, b in rows}
        out = []
        for i in range(months):
            m = start + relativedelta(months=i)
            out.append((m.strftime('%b'), got.get(m.strftime('%Y-%m'), 0.0)))
        return out

    # ------------------------------------------------------------------ the page
    def _cache_key(self):
        # the latest decision anywhere is part of the key: a decision taken in another worker (Me, Telegram, this page)
        # makes every kept dashboard stale at once
        last = self.env['sb.approval.log'].sudo().search([], order='id desc', limit=1).id if 'sb.approval.log' in self.env else 0
        return (self.env.cr.dbname, self.env.uid, self.env.company.id, last)

    @api.model
    def forget(self, uid=None):
        """Drop the kept dashboard of this person (after a decision), or of everyone in this database."""
        db = self.env.cr.dbname
        for k in [k for k in _CACHE if k[0] == db and (uid is None or k[1] == uid)]:
            _CACHE.pop(k, None)
        return True

    @api.model
    def data(self, fresh=False):
        """The dashboard as kept for up to two minutes, or built now (fresh=True: the Refresh button)."""
        key = self._cache_key()
        hit = _CACHE.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_SECONDS:
            out = dict(hit[1])
        else:
            out = self._build()
            out['built_at'] = fields.Datetime.to_string(fields.Datetime.now())
            if len(_CACHE) > 2000:
                _CACHE.clear()
            _CACHE[key] = (time.time(), out)
            out = dict(out)
        # today's AI briefing may have been written since the dashboard was kept
        if out.get('brief', {}).get('pending'):
            b = self._safe(lambda: self._brief(out, cached_only=True), None)
            if b:
                out['brief'] = b
        return out

    def _build(self):
        P = self.env['sb.people'] if 'sb.people' in self.env else None
        me = self._safe(lambda: P._me(), None) if P is not None else None
        today = self._today()
        out = {
            'user': {'name': self.env.user.name, 'first': (me.name if me else self.env.user.name).split(' ')[0]},
            'role': (me.job_title or (me.job_id.name if me.job_id else '')) if me else '',
            'today': fields.Date.to_string(today), 'today_label': today.strftime('%d %b %Y').lstrip('0'),
            'today_ec': self._ec(today), 'company': self.env.company.name,
            'money': bool(self._sees_money()), 'stock': bool(self._sees_stock()),
            'sign': bool(getattr(self.env.company, 'sb_confirm_on', False)),
            'avatar': '/web/image/res.users/%d/avatar_128' % self.env.uid,
        }
        out['needs'] = self._safe(self._needs, {'approvals': [], 'queues': []})
        out['tiles'] = self._collect('_dash_tiles')
        out['signals'] = sorted(self._collect('_dash_signals'), key=lambda s: {BAD: 0, WARN: 1, INFO: 2}.get(s.get('level'), 3))
        out['charts'] = self._collect('_dash_charts')
        out['questions'] = (self._collect('_dash_questions') or [])[:4]
        out['ai'] = bool(self._safe(lambda: 'sb.ai' in self.env and self.env['sb.ai'].enabled(), False))
        out['hidden'] = self._hidden_note(out)
        # the page opens at once: today's written briefing if there is one, else plain sentences while Unio AI writes it
        out['brief'] = self._safe(lambda: self._brief(out, cached_only=True), None) or {
            'paragraphs': self._plain_brief(out), 'source': 'plain', 'pending': out['ai']}
        return out

    def _collect(self, method):
        """Each provider returns a list; one that fails is skipped."""
        items = self._safe(lambda: getattr(self, method)(), []) or []
        return [i for i in items if i]

    def _hidden_note(self, d):
        hidden = []
        if not d['money']:
            hidden.append('bank and cash, sales and debtors')
        if not d['stock']:
            hidden.append('stock')
        return ('Not shown to your role: %s.' % ', '.join(hidden)) if hidden else 'Your role sees the whole company.'

    # ------------------------------------------------------------------ needs you
    def _needs(self):
        approvals = self._approvals()
        queues = []
        if 'sb.app.home' in self.env:
            Home = self.env['sb.app.home']
            # a department's work queues go to the people of that department (and management), not to everyone who can read them
            for app, label, group in (('sales', 'Sales', 'sales_team.group_sale_salesman'), ('purchase', 'Purchase', 'purchase.group_purchase_user'),
                                      ('store', 'Store', 'stock.group_stock_user')):
                if not hasattr(Home, '_home_%s' % app) or not self.env.user.has_group(group):
                    continue
                home = self._safe(lambda app=app: Home.get_home(app), None)
                for q in (home or {}).get('queues') or []:
                    if q.get('count') and q.get('tone') in ('warn', 'bad') and q['key'] not in ('today',):
                        queues.append({'app': label, 'key': '%s:%s' % (app, q['key']), 'title': q['title'], 'count': q['count'],
                                       'tone': q['tone'], 'model': q['model'], 'domain': q['domain'],
                                       'rows': [r for r in (q.get('rows') or []) if re.match(r'\s*\w', str(r.get('label') or ''))][:3]})
        seen, uniq = set(), []
        for q in queues:  # the same queue can come from two app homes (store and sales both list deliveries)
            sig = (q['model'], json.dumps(q['domain'], default=str))
            if sig not in seen:
                seen.add(sig)
                uniq.append(q)
        uniq.sort(key=lambda q: (q['tone'] != 'bad', -q['count']))
        return {'approvals': approvals[:6], 'approvals_total': len(approvals), 'queues': uniq[:4]}

    # ------------------------------------------------------------------ numbers
    def _dash_tiles(self):
        tiles = []
        today = self._today()
        if self._sees_money():
            now = self._balance(self._cash_domain())
            week = self._balance(self._cash_domain(today - timedelta(days=7)))
            tiles.append({'key': 'cash', 'icon': 'fa-university', 'label': 'Bank and cash', 'value': self._short(now), 'unit': self._cur(),
                          'sub': ('%s%s this week' % ('+' if now > week else '−', self._short(abs(now - week)))) if abs(now - week) >= 1 else 'no change this week',
                          'trend': False if abs(now - week) < 1 else 'up' if now > week else 'down', 'action': self._ml(self._cash_domain())})
            ar = self.env['account.move.line']._read_group(
                [('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
                 ('company_id', '=', self.env.company.id)], aggregates=['amount_residual:sum'])
            owed = (ar[0][0] if ar else 0.0) or 0.0
            late_dom = [('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
                        ('company_id', '=', self.env.company.id), ('date_maturity', '<', today - timedelta(days=30))]
            late = self.env['account.move.line']._read_group(late_dom, aggregates=['amount_residual:sum'])
            late = (late[0][0] if late else 0.0) or 0.0
            tiles.append({'key': 'ar', 'icon': 'fa-users', 'label': 'Owed by customers', 'value': self._short(owed), 'unit': self._cur(),
                          'sub': '%s over 30 days' % self._short(late), 'action': self._ml(late_dom)})
            months = self._income_by_month(2)
            last, this = months[0][1], months[1][1]
            tiles.append({'key': 'sales', 'icon': 'fa-shopping-cart', 'label': 'Sales, %s' % today.strftime('%B'), 'value': self._short(this), 'unit': self._cur(),
                          'sub': '%s in %s' % (self._short(last), months[0][0]),
                          'action': self._ml([('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'income'),
                                              ('date', '>=', today.replace(day=1))])})
            out_dom = self._cash_domain() + [('date', '>', today - timedelta(days=7)), ('credit', '>', 0)]
            paid = self.env['account.move.line']._read_group(out_dom, aggregates=['credit:sum', '__count'])
            tiles.append({'key': 'out', 'icon': 'fa-arrow-circle-up', 'label': 'Paid out, 7 days', 'value': self._short(paid[0][0] if paid else 0), 'unit': self._cur(),
                          'sub': '%d payments' % (paid[0][1] if paid else 0), 'action': self._ml(out_dom)})
        if self._sees_stock():
            neg = self._negative_stock()
            if 'sb.stock.voucher' in self.env and self.env['sb.stock.voucher'].has_access('read'):
                dom = [('state', '=', 'draft'), ('company_id', '=', self.env.company.id)]
                SV = self.env['sb.stock.voucher']
                n = SV.search_count(dom)
                oldest = SV.search(dom, order='date', limit=1)
                tiles.append({'key': 'store_drafts', 'icon': 'fa-file-text-o', 'label': 'Store documents not posted', 'value': '{:,}'.format(n),
                              'sub': ('oldest %s' % oldest.date.strftime('%d %b').lstrip('0')) if oldest else 'none',
                              'action': {'model': 'sb.stock.voucher', 'domain': dom}})
            tiles.append({'key': 'neg', 'icon': 'fa-cubes', 'label': 'Items below zero', 'value': '{:,}'.format(len(neg)), 'sub': 'in the stores',
                          'action': {'model': 'stock.quant', 'domain': [('location_id.usage', '=', 'internal'), ('quantity', '<', 0)]}})
        # what waits for a decision is counted in "Needs you", not repeated here
        return tiles

    def _negative_stock(self):
        rows = self.env['stock.quant']._read_group(
            [('location_id.usage', '=', 'internal'), ('company_id', 'in', [self.env.company.id, False])],
            groupby=['product_id', 'location_id'], aggregates=['quantity:sum'])
        return sorted([(p, l, q) for p, l, q in rows if (q or 0) < -0.0001], key=lambda r: r[2])

    def _dash_charts(self):
        charts = []
        if self._sees_money():
            months = self._income_by_month(6)
            charts.append({'key': 'sales', 'title': 'Sales by month', 'unit': 'million %s' % self._cur(),
                           'bars': [[m, round(v / 1e6, 2)] for m, v in months], 'partial_last': True})
        elif self._sees_stock():
            rows = self.env['stock.quant']._read_group(
                [('location_id.usage', '=', 'internal'), ('product_id.sale_ok', '=', True), ('quantity', '>', 0)],
                groupby=['product_id'], aggregates=['quantity:sum'], order='quantity:sum desc', limit=6)
            if rows:
                charts.append({'key': 'onhand', 'title': 'Top items on hand', 'unit': 'thousand units',
                               'bars': [[(p.name or '')[:10], round(q / 1e3, 1)] for p, q in rows], 'partial_last': False})
        return charts

    def _dash_questions(self):
        q = []
        if self._sees_money():
            q += ['Who owes us the most, and for how long?', 'How much did we pay out this week, and to whom?', 'Why did sales change this month?']
        if self._sees_stock():
            q += ['Which items are below zero, and where?', 'What did the store receive this week?']
        q.append('What is waiting for me?')
        return q

    # ------------------------------------------------------------------ what's off
    def _dash_signals(self):
        out = []
        for fn in (self._sig_sales_trend, self._sig_overdue, self._sig_cash_below_zero, self._sig_duplicate_journals,
                   self._sig_future_entries, self._sig_no_lock, self._sig_negative_stock, self._sig_old_drafts,
                   self._sig_old_approvals):
            s = self._safe(fn, None)
            if s:
                out.append(s)
        return out

    def _sig_sales_trend(self):
        if not self._sees_money():
            return None
        m = self._income_by_month(4)[:3]  # the last three full months
        a, b, c = (v for _, v in m)
        if a > 0 and a > b > c and c < a * 0.8:
            return {'key': 'sales_trend', 'icon': 'fa-line-chart', 'level': BAD if c < a * 0.6 else WARN, 'title': 'Sales down three months running',
                    'figure': '%s, %s, %s' % tuple('%s %s' % (n, self._short(v)) for n, v in m),
                    'why': 'Revenue posted fell from %s in %s to %s in %s (%d%%).' % (self._money(a), m[0][0], self._money(c), m[2][0],
                                                                                  round((c - a) / a * 100)),
                    'todo': 'Compare sales by customer and by salesperson for %s against %s.' % (m[2][0], m[0][0])}
        return None

    def _sig_overdue(self):
        if not self._sees_money():
            return None
        today = self._today()
        dom = [('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
               ('amount_residual', '>', 0), ('date_maturity', '<', today - timedelta(days=30)), ('partner_id', '!=', False),
               ('company_id', '=', self.env.company.id)]
        rows = self.env['account.move.line']._read_group(dom, groupby=['partner_id'], aggregates=['amount_residual:sum', 'date_maturity:min'],
                                                         order='amount_residual:sum desc', limit=3)
        if not rows:
            return None
        top = rows[0]
        others = '; '.join('%s %s, %d days' % (p.name, self._money(v), (today - d).days) for p, v, d in rows[1:])
        return {'key': 'overdue', 'icon': 'fa-hourglass-half', 'level': WARN, 'title': 'Customers well past due',
                'figure': '%s · %s · %d days' % (top[0].name, self._money(top[1]), (today - top[2]).days),
                'why': 'Unpaid more than 30 days after the due date.%s' % ((' Also: %s.' % others) if others else ''),
                'todo': 'Call them this week; hold new credit sales until they pay.', 'action': self._ml(dom)}

    def _sig_cash_below_zero(self):
        if not self._sees_money():
            return None
        rows = self.env['account.move.line']._read_group(self._cash_domain(), groupby=['account_id'], aggregates=['balance:sum'])
        neg = [(a, b) for a, b in rows if (b or 0) < -1]
        if not neg:
            return None
        a, b = min(neg, key=lambda r: r[1])
        return {'key': 'cash_neg', 'icon': 'fa-university', 'level': WARN, 'title': 'Cash or bank below zero',
                'figure': '%s · %s' % (a.name, self._money(b)) + (' (+%d more)' % (len(neg) - 1) if len(neg) > 1 else ''),
                'why': 'A cash box or bank account cannot hold less than nothing. Usually a deposit or replenishment that was not '
                       'entered, or a payment posted to the wrong account.',
                'todo': 'Open the account and find the missing entry.',
                'action': self._ml(self._cash_domain() + [('account_id', 'in', [x.id for x, _ in neg])])}

    def _sig_duplicate_journals(self):
        if not self._sees_money():
            return None
        rows = self.env['account.journal']._read_group(
            [('type', 'in', ('bank', 'cash')), ('default_account_id', '!=', False), ('company_id', '=', self.env.company.id)],
            groupby=['default_account_id'], aggregates=['__count', 'id:array_agg'])
        dup = [(acc, ids) for acc, n, ids in rows if n > 1]
        if not dup:
            return None
        J = self.env['account.journal']
        parts = ['%s (%s)' % (' and '.join(J.browse(ids).mapped('name')), acc.code) for acc, ids in dup]
        extra = sum(self._balance(self._cash_domain() + [('account_id', '=', acc.id)]) * (len(ids) - 1) for acc, ids in dup)
        return {'key': 'dup_journals', 'icon': 'fa-clone', 'level': BAD, 'title': 'Bank counted twice',
                'figure': '%d accounts with two journals' % len(dup),
                'why': '%s. Any report that adds up journals shows bank %s too high.' % ('; '.join(parts), self._money(extra)),
                'todo': 'Archive the duplicate journal of each pair.',
                'action': {'model': 'account.journal', 'domain': [('id', 'in', [i for _, ids in dup for i in ids])]}}

    def _sig_future_entries(self):
        if not self._sees_money():
            return None
        today = self._today()
        dom = [('state', '=', 'posted'), ('date', '>', today), ('company_id', '=', self.env.company.id)]
        n = self.env['account.move'].search_count(dom)
        if not n:
            return None
        last = self.env['account.move'].search(dom, order='date desc', limit=1)
        return {'key': 'future', 'icon': 'fa-calendar-times-o', 'level': INFO, 'title': 'Entries dated in the future',
                'figure': '%d entries, up to %s' % (n, last.date.strftime('%d %b').lstrip('0')),
                'why': 'Posted with a date after today: usually a typing error in the date.', 'todo': 'Check the dates.',
                'action': {'model': 'account.move', 'domain': dom}}

    def _sig_no_lock(self):
        if not self._can('account.move.line', 'account.group_account_user'):
            return None
        today = self._today()
        lock = self.env.company.fiscalyear_lock_date
        if lock and lock >= today.replace(day=1) - relativedelta(months=2):
            return None
        return {'key': 'no_lock', 'icon': 'fa-unlock-alt', 'level': INFO, 'title': 'Months still open',
                'figure': ('Locked up to %s' % lock.strftime('%d %b %Y').lstrip('0')) if lock else 'No month has been locked',
                'why': 'Anyone with posting rights can still post into the months already reported.',
                'todo': 'Lock each month once its banks are reconciled.'}

    def _sig_negative_stock(self):
        if not self._sees_stock():
            return None
        neg = self._negative_stock()
        if not neg:
            return None
        top = '; '.join('%s %s in %s' % (p.display_name, '{:,.0f}'.format(q), l.name) for p, l, q in neg[:3])
        return {'key': 'neg_stock', 'icon': 'fa-cubes', 'level': BAD if len(neg) >= 10 else WARN, 'title': 'Stock below zero',
                'figure': '%d items in the stores' % len(neg),
                'why': 'Issued before it was received, so stock value and cost are wrong. Worst: %s.' % top,
                'todo': 'Find the missing receipt or the wrong issue for each item.',
                'action': {'model': 'stock.quant', 'domain': [('location_id.usage', '=', 'internal'), ('quantity', '<', 0)]}}

    def _sig_old_drafts(self):
        if 'sb.stock.voucher' not in self.env or not self.env['sb.stock.voucher'].has_access('read'):
            return None
        dom = [('state', '=', 'draft'), ('date', '<', self._today() - timedelta(days=7)), ('company_id', '=', self.env.company.id)]
        n = self.env['sb.stock.voucher'].search_count(dom)
        if not n:
            return None
        return {'key': 'old_drafts', 'icon': 'fa-file-text-o', 'level': WARN, 'title': 'Store documents waiting over a week',
                'figure': '%d not posted' % n,
                'why': 'Until they are posted, stock in the books trails the store and suppliers cannot be paid against them.',
                'todo': 'Post them, or void the ones that never happened.', 'action': {'model': 'sb.stock.voucher', 'domain': dom}}

    def _sig_old_approvals(self):
        items = self._approvals()
        old = [i for i in items if (i.get('days') or 0) > 7]
        if not old:
            return None
        oldest = max(old, key=lambda i: i.get('days') or 0)
        return {'key': 'old_approvals', 'icon': 'fa-inbox', 'level': WARN, 'title': 'Decisions waiting on you',
                'figure': '%d over a week · oldest %d days' % (len(old), oldest.get('days') or 0),
                'why': 'People are waiting on these. The oldest: %s.' % (oldest.get('title') or ''),
                'todo': 'Decide them or send them back with a reason.', 'me': True}

    # ------------------------------------------------------------------ the briefing
    def _facts(self, d):
        """The figures the briefing is written from: only what this person's dashboard already shows."""
        f = ['Person: %s%s. Today: %s%s.' % (d['user']['name'], (', ' + d['role']) if d['role'] else '', d['today_label'],
                                              (' (%s)' % d['today_ec']) if d['today_ec'] else '')]
        for t in d['tiles']:
            f.append('Tile: %s = %s %s (%s)' % (t['label'], t['value'], t.get('unit', ''), t.get('sub', '')))
        n = d['needs']
        if n.get('approvals_total'):
            f.append('Waiting for their decision: %d. Newest: %s' % (n['approvals_total'], '; '.join(
                '%s (%s days)' % (a.get('title'), a.get('days')) for a in n['approvals'][:3])))
        for q in n.get('queues') or []:
            f.append('Work queue: %s: %d' % (q['title'], q['count']))
        for s in d['signals']:
            f.append('Problem (%s): %s: %s. %s' % (s['level'], s['title'], s['figure'], s.get('why', '')))
        return f

    def _plain_brief(self, d):
        """Without AI: three sentences from the same figures."""
        t = {x['key']: x for x in d['tiles']}
        p = []
        if 'cash' in t:
            p.append('Bank and cash stand at %s %s, %s. %s were paid out in the last 7 days.' % (
                t['cash']['value'], t['cash']['unit'], t['cash']['sub'], '%s %s' % (t['out']['value'], t['out']['unit']) if 'out' in t else 'Nothing'))
        n = d['needs']
        waits = []
        if n.get('approvals_total'):
            waits.append('%d decision%s' % (n['approvals_total'], 's' if n['approvals_total'] != 1 else ''))
        for q in (n.get('queues') or [])[:2]:
            waits.append('%d %s' % (q['count'], q['title'][0].lower() + q['title'][1:]))
        if waits:
            p.append('Waiting on you: %s.' % ', '.join(waits))
        bad = [s for s in d['signals'] if s['level'] == BAD][:2]
        if bad:
            p.append('Needs attention: %s.' % '; '.join('%s (%s)' % (s['title'].lower(), s['figure']) for s in bad))
        if not p:
            p.append('Nothing is waiting on you and nothing looks wrong in your area today.')
        return p

    def _brief(self, d, force=False, cached_only=False):
        """Written by Unio AI once a day per person (and again when the figures change a lot); plain sentences without AI."""
        if not d.get('ai'):
            return None
        facts = self._facts(d)
        h = hashlib.sha1('\n'.join(facts).encode()).hexdigest()
        B = self.env['sb.dashboard.brief']
        today = self._today()
        old = B.search([('user_id', '=', self.env.uid), ('day', '=', today)], limit=1)
        if old and not force:
            return {'paragraphs': json.loads(old.text or '[]'), 'source': old.source, 'stale': old.facts_hash != h}
        if cached_only:
            return None
        system = ('You write the morning briefing at the top of a business dashboard for one person. Use ONLY the facts given; '
                  'never invent a figure or a name. Write 2 or 3 short paragraphs, at most 90 words in all, plain English, no '
                  'headings, no lists, no greeting. Lead with what matters most to this person\'s role: money and trend for '
                  'managers and finance, documents and stock for the store. Then what waits for them, then the worst problem. '
                  'Keep numbers as given (e.g. 63.2M Br). Return JSON: {"paragraphs": ["...", "..."]}.')
        data = self.env['unio.ai'].ask('assistant', {'system': system, 'max_tokens': 500,
                                                     'messages': [{'role': 'user', 'content': '\n'.join(facts)}]})
        text = ''.join(b.get('text', '') for b in (data.get('content') or []) if b.get('type') == 'text').strip()
        start, end = text.find('{'), text.rfind('}')
        paragraphs = json.loads(text[start:end + 1]).get('paragraphs') if start >= 0 else None
        if not paragraphs:
            return None
        vals = {'user_id': self.env.uid, 'day': today, 'facts_hash': h, 'text': json.dumps(paragraphs), 'source': 'ai'}
        old.write(vals) if old else B.create(vals)
        return {'paragraphs': paragraphs, 'source': 'ai'}

    @api.model
    def rewrite_brief(self, force=True):
        """Today's briefing by Unio AI: written now when there is none yet (force=False), or written again (force=True)."""
        d = self.data()
        b = self._safe(lambda: self._brief(d, force=force), None)
        hit = _CACHE.get(self._cache_key())
        if b and hit:
            hit[1]['brief'] = b
        return b or {'paragraphs': self._plain_brief(d), 'source': 'plain'}

    # ------------------------------------------------------------------ acting from the dashboard
    @api.model
    def open_list(self, model, domain, title):
        """A tile, signal or queue opens the records behind it (as the person: rules still apply)."""
        if model not in self.env or not self.env[model].has_access('read'):
            return False
        return {'type': 'ir.actions.act_window', 'name': title, 'res_model': model, 'domain': domain,
                'views': [[False, 'list'], [False, 'form']], 'target': 'current'}


class SbMeDashboard(models.AbstractModel):
    _inherit = 'sb.me'

    @api.model
    def decide(self, key, approve, note='', channel='me'):
        """A decision changes what waits for people: their kept dashboards are built again on the next open."""
        res = super().decide(key, approve, note=note, channel=channel)
        self.env['sb.dashboard'].forget()
        return res
