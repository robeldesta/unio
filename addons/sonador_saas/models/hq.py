"""Unio HQ: what the control room knows.

- saas.snapshot: each customer company measured (users active, last login, records made, apps used, database and file
  size, Studio apps) with a health score; taken every night, and on demand.
- saas.server.metric / saas.server.event: what the server agent (/opt/unio/hq_agent.py, every 5 minutes) reports:
  CPU, memory, disk, load, backups, certificates, errors in the logs. The agent writes JSON files into the queue folder
  shared with Odoo; HQ reads them in.
- hq_data(): everything the HQ screen shows, in one call."""
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# tables that tell whether a company works in its system: (label, table)
WORK_TABLES = [('Vouchers', 'sb_voucher'), ('Sales orders', 'sale_order'), ('Purchase orders', 'purchase_order'),
               ('Stock moves', 'sb_stock_voucher'), ('Leads', 'crm_lead'), ('Tasks', 'project_task'), ('Employees', 'hr_employee'),
               ('Payroll', 'sb_payroll_run'), ('Messages', 'mail_message')]
LIVE = ('trial', 'active', 'overdue', 'suspended')


def _queue_dir(env):
    base = env['ir.config_parameter'].sudo().get_param('saas.cert_queue') or '/var/lib/unio-queue'
    return os.path.join(base, 'hq')


class SaasSnapshot(models.Model):
    _name = 'saas.snapshot'
    _description = 'Customer company measured'
    _order = 'date desc, id desc'

    tenant_id = fields.Many2one('saas.tenant', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, index=True, default=fields.Date.context_today)
    users_total = fields.Integer()
    users_active_7d = fields.Integer('Active users (7 days)')
    last_login = fields.Datetime()
    records_7d = fields.Integer('Records made (7 days)')
    records_total = fields.Integer()
    detail = fields.Text(help='Records per kind (JSON).')
    apps_on = fields.Integer()
    studio_apps = fields.Integer()
    db_mb = fields.Float('Database MB', digits=(12, 1))
    files_mb = fields.Float('Files MB', digits=(12, 1))
    health = fields.Integer(help='0 (silent) to 100 (busy every day).')

    _uniq_day = models.Constraint('unique(tenant_id, date)', 'One measure per company and day.')


class SaasServerMetric(models.Model):
    _name = 'saas.server.metric'
    _description = 'Server measure'
    _order = 'ts desc'

    ts = fields.Datetime(required=True, index=True)
    cpu = fields.Float('CPU %')
    mem = fields.Float('Memory %')
    disk = fields.Float('Disk %')
    load1 = fields.Float('Load (1 min)')
    detail = fields.Text()


class SaasServerEvent(models.Model):
    _name = 'saas.server.event'
    _description = 'Server event (error, backup, certificate...)'
    _order = 'ts desc'

    ts = fields.Datetime(required=True, index=True)
    kind = fields.Selection([('error', 'Error'), ('warning', 'Warning'), ('backup', 'Backup'), ('cert', 'Certificate'),
                             ('release', 'Release'), ('cron', 'Scheduled job')], required=True, index=True)
    database = fields.Char(index=True)
    message = fields.Text()
    key = fields.Char(index=True, help='To recognise the same event reported twice.')
    done = fields.Boolean('Dealt with')


class SaasHq(models.AbstractModel):
    _name = 'saas.hq'
    _description = 'Unio HQ'

    # ------------------------------------------------------------------ measuring the companies
    @api.model
    def measure(self, tenants=None):
        from .provision import _tenant_env
        tenants = tenants or self.env['saas.tenant'].sudo().search([('state', 'in', LIVE)])
        today = fields.Date.context_today(self)
        week = fields.Datetime.now() - timedelta(days=7)
        cr0 = self.env.cr
        for t in tenants:
            try:
                cr, env = _tenant_env(t.subdomain)
            except Exception as e:  # noqa: BLE001
                _logger.warning('HQ cannot open %s: %s', t.subdomain, e)
                continue
            try:
                users = env['res.users'].sudo().search([('share', '=', False), ('active', '=', True), ('id', '!=', 1)])
                logins = [u.login_date for u in users if u.login_date]
                detail, rec7, rec_all = {}, 0, 0
                for label, table in WORK_TABLES:
                    cr.execute("SELECT to_regclass(%s)", (table,))
                    if not cr.fetchone()[0]:
                        continue
                    cr.execute('SELECT count(*), count(*) FILTER (WHERE create_date > %%s) FROM "%s"' % table, (week,))
                    total, recent = cr.fetchone()
                    if table == 'mail_message':
                        # messages are counted, but they are not "work" on their own
                        detail[label] = [total, recent]
                        continue
                    detail[label] = [total, recent]
                    rec7 += recent
                    rec_all += total
                apps_on = env['ir.ui.menu'].sudo().search_count([('parent_id', '=', False)])
                studio = env['sb.studio.app'].sudo().search_count([]) if 'sb.studio.app' in env else 0
            finally:
                cr.close()
            cr0.execute('SELECT pg_database_size(%s)', (t.subdomain,))
            db_mb = cr0.fetchone()[0] / 1048576.0
            files_mb = self._dir_mb(os.path.join(odoo_data_dir(), 'filestore', t.subdomain))
            active7 = sum(1 for d in logins if d and d > week)
            last = max(logins) if logins else False
            health = self._health(len(users), active7, rec7, last)
            vals = {'tenant_id': t.id, 'date': today, 'users_total': len(users), 'users_active_7d': active7, 'last_login': last,
                    'records_7d': rec7, 'records_total': rec_all, 'detail': json.dumps(detail), 'apps_on': apps_on, 'studio_apps': studio,
                    'db_mb': round(db_mb, 1), 'files_mb': round(files_mb, 1), 'health': health}
            snap = self.env['saas.snapshot'].sudo().search([('tenant_id', '=', t.id), ('date', '=', today)], limit=1)
            snap.write(vals) if snap else snap.create(vals)
        return True

    def _dir_mb(self, path):
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total / 1048576.0

    def _health(self, users, active7, rec7, last):
        """0-100: people logging in, and work being recorded, recently."""
        if not users:
            return 0
        login_part = min(active7 / max(users, 1), 1.0) * 45
        work_part = min(rec7 / 50.0, 1.0) * 40
        recency = 0
        if last:
            days = (fields.Datetime.now() - last).days
            recency = 15 if days <= 1 else 10 if days <= 3 else 5 if days <= 7 else 0
        return int(round(login_part + work_part + recency))

    # ------------------------------------------------------------------ reading what the server agent wrote
    @api.model
    def ingest(self):
        folder = _queue_dir(self.env)
        if not os.path.isdir(folder):
            return False
        M, E = self.env['saas.server.metric'].sudo(), self.env['saas.server.event'].sudo()
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not name.endswith('.jsonl') or not os.path.isfile(path):
                continue
            work = path + '.reading'
            try:
                os.rename(path, work)
            except OSError:
                continue
            with open(work) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    ts = datetime.utcfromtimestamp(d.get('ts') or 0) if isinstance(d.get('ts'), (int, float)) else fields.Datetime.now()
                    if d.get('type') == 'metric':
                        M.create({'ts': ts, 'cpu': d.get('cpu'), 'mem': d.get('mem'), 'disk': d.get('disk'), 'load1': d.get('load1'),
                                  'detail': json.dumps(d.get('detail') or {})})
                    elif d.get('type') == 'event':
                        key = d.get('key') or ('%s:%s' % (d.get('kind'), d.get('message', '')[:120]))
                        if E.search_count([('key', '=', key), ('ts', '>', ts - timedelta(hours=1))]):
                            continue
                        E.create({'ts': ts, 'kind': d.get('kind') if d.get('kind') in dict(E._fields['kind'].selection) else 'warning',
                                  'database': d.get('database'), 'message': (d.get('message') or '')[:4000], 'key': key})
            os.remove(work)
        # keep 30 days of measures and 90 days of events
        M.search([('ts', '<', fields.Datetime.now() - timedelta(days=30))]).unlink()
        E.search([('ts', '<', fields.Datetime.now() - timedelta(days=90))]).unlink()
        return True

    @api.model
    def _cron_hq(self):
        self.ingest()

    # ------------------------------------------------------------------ the HQ screen
    def _to_etb(self, amount, currency):
        if not amount:
            return 0.0
        if currency == 'ETB':
            return amount
        cur = self.env['res.currency'].with_context(active_test=False).search([('name', '=', currency)], limit=1)
        etb = self.env['res.currency'].with_context(active_test=False).search([('name', '=', 'ETB')], limit=1)
        if not cur or not etb:
            return amount
        return cur._convert(amount, etb, self.env.company, fields.Date.context_today(self))

    @api.model
    def hq_data(self):
        T = self.env['saas.tenant'].sudo()
        S = self.env['saas.signup'].sudo()
        Pay = self.env['saas.payment'].sudo()
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        tenants = T.search([])
        live = tenants.filtered(lambda t: t.state in LIVE)
        paying = tenants.filtered(lambda t: t.state in ('active', 'overdue'))
        trials = tenants.filtered(lambda t: t.state == 'trial')
        def monthly(t):
            users = max(t.subscribed_users or 0, t.paid_users or 0)
            return self._to_etb(t.plan_id.price_for(users, t.currency)['total'], t.currency) if users and t.plan_id else 0.0
        mrr = sum(monthly(t) for t in paying)
        latest = {}
        for s in self.env['saas.snapshot'].sudo().search([('tenant_id', 'in', live.ids)], order='date desc'):
            latest.setdefault(s.tenant_id.id, s)
        # ---------------------------------------------------------- money: payments per month (12), MRR now, ARR
        months = [(today.replace(day=1) - relativedelta(months=i)) for i in range(11, -1, -1)]
        paid = Pay.search([('state', '=', 'confirmed')])
        by_month = defaultdict(float)
        by_method = defaultdict(float)
        for p in paid:
            m = p.create_date.date().replace(day=1)
            amt = self._to_etb(p.amount, p.currency)
            by_month[m] += amt
            by_method[dict(p._fields['method'].selection).get(p.method)] += amt
        revenue = [{'label': m.strftime('%b %y'), 'value': round(by_month.get(m, 0.0), 2)} for m in months]
        invoices = self.env['sb.voucher'].sudo().search([('saas_tenant_id', '!=', False), ('state', '=', 'posted')])
        open_inv = invoices.filtered(lambda v: 'amount_residual' in v._fields and v.amount_residual > 0.005)
        # ---------------------------------------------------------- funnel: 12 weeks
        weeks = [(today - timedelta(days=today.weekday()) - timedelta(weeks=i)) for i in range(11, -1, -1)]
        signups = S.search([('create_date', '>=', datetime.combine(weeks[0], datetime.min.time()))])
        new_t = T.search([('create_date', '>=', datetime.combine(weeks[0], datetime.min.time()))])
        funnel_weeks = []
        for w in weeks:
            a, b = datetime.combine(w, datetime.min.time()), datetime.combine(w + timedelta(days=7), datetime.min.time())
            funnel_weeks.append({'label': w.strftime('%d %b'),
                                 'forms': len(signups.filtered(lambda s: a <= s.create_date < b)),
                                 'systems': len(new_t.filtered(lambda t: a <= t.create_date < b)),
                                 'paying': len(new_t.filtered(lambda t: a <= t.create_date < b and t.paid_months > 0))})
        # companies made before sign-ups were confirmed by email have no form: they count as a form and a system
        before = len(tenants - S.search([('tenant_id', '!=', False)]).mapped('tenant_id'))
        all_forms = S.search_count([]) + before
        made = len(tenants)
        ever_paid = len(tenants.filtered(lambda t: t.paid_months > 0 or t.state in ('active', 'overdue')))
        funnel = [{'step': 'Sign-up forms sent', 'n': all_forms}, {'step': 'Systems made (email confirmed)', 'n': made},
                  {'step': 'Paying at least once', 'n': ever_paid}, {'step': 'Paying now', 'n': len(paying)}]
        # ---------------------------------------------------------- leads: what needs a person
        abandoned = S.search([('state', 'in', ('pending', 'expired')), ('create_date', '>', now - timedelta(days=30))], limit=50)
        ending = trials.filtered(lambda t: t.trial_end and 0 <= (t.trial_end - today).days <= 7)
        waiting_pay = Pay.search([('state', '=', 'waiting')])
        leads = {
            'abandoned': [{'company': s.company, 'email': s.email, 'phone': s.phone or '', 'when': fields.Datetime.to_string(s.create_date)[:16],
                           'industry': s.industry or '', 'country': s.country} for s in abandoned],
            'trials_ending': [{'company': t.name, 'email': t.email, 'phone': t.phone or '', 'days': (t.trial_end - today).days,
                               'health': latest[t.id].health if t.id in latest else None, 'id': t.id} for t in ending],
            'payments': [{'company': p.tenant_id.name, 'amount': p.amount, 'currency': p.currency, 'method': p.method, 'reference': p.reference,
                          'check': p.checked or '', 'id': p.id} for p in waiting_pay],
        }
        # ---------------------------------------------------------- customers
        customers = []
        for t in live.sorted(lambda x: (x.state != 'active', x.name)):
            s = latest.get(t.id)
            customers.append({'id': t.id, 'name': t.name, 'url': t.url, 'state': t.state, 'country': t.country, 'industry': t.industry,
                              'plan': t.plan_id.name, 'users': s.users_total if s else t.paid_users, 'active7': s.users_active_7d if s else None,
                              'last_login': fields.Datetime.to_string(s.last_login)[:16] if s and s.last_login else '',
                              'records7': s.records_7d if s else None, 'health': s.health if s else None,
                              'mrr': round(monthly(t), 2) if t.state in ('active', 'overdue') else 0,
                              'trial_left': (t.trial_end - today).days if t.state == 'trial' and t.trial_end else None,
                              'db_mb': s.db_mb if s else None, 'files_mb': s.files_mb if s else None, 'studio': s.studio_apps if s else 0,
                              'referrals': len(t.referral_ids), 'since': fields.Date.to_string(t.create_date.date()),
                              'billing': t.billing or '', 'paid_until': fields.Date.to_string(t.paid_until) if t.paid_until else ''})
        at_risk = [c for c in customers if c['health'] is not None and c['health'] < 25]
        # ---------------------------------------------------------- tech
        M = self.env['saas.server.metric'].sudo()
        E = self.env['saas.server.event'].sudo()
        metrics = M.search([('ts', '>', now - timedelta(hours=24))], order='ts asc')
        last_m = metrics[-1:] if metrics else M
        events = E.search([], limit=60)
        backups = E.search([('kind', '=', 'backup')], limit=1)
        certs = E.search([('kind', '=', 'cert')], limit=1)
        self.env.cr.execute("SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT datistemplate ORDER BY 2 DESC")
        dbs = [{'name': n, 'mb': round(sz / 1048576.0, 1)} for n, sz in self.env.cr.fetchall()]
        releases = E.search([('kind', '=', 'release')], limit=10)
        tech = {
            'now': {'cpu': last_m.cpu, 'mem': last_m.mem, 'disk': last_m.disk, 'load1': last_m.load1,
                    'at': fields.Datetime.to_string(last_m.ts)[:16] if last_m else '', 'detail': json.loads(last_m.detail or '{}') if last_m else {}},
            'series': [{'t': fields.Datetime.to_string(m.ts)[11:16], 'cpu': m.cpu, 'mem': m.mem} for m in metrics],
            'errors_24h': E.search_count([('kind', '=', 'error'), ('done', '=', False), ('ts', '>', now - timedelta(hours=24))]),
            'events': [{'id': e.id, 'ts': fields.Datetime.to_string(e.ts)[:16], 'kind': e.kind, 'db': e.database or '', 'message': (e.message or '')[:600],
                        'done': e.done} for e in events],
            'backup': backups.message or '', 'backup_at': fields.Datetime.to_string(backups.ts)[:16] if backups else '',
            'certs': certs.message or '', 'dbs': dbs,
            'releases': [{'ts': fields.Datetime.to_string(r.ts)[:16], 'message': r.message} for r in releases],
            'agent': bool(metrics),
        }
        # ---------------------------------------------------------- support (in-app questions, see support.py)
        support = self.env['saas.ticket'].sudo().hq_summary() if 'saas.ticket' in self.env else {}
        support['ai'] = self.env['saas.ai'].sudo().hq_summary() if 'saas.ai' in self.env else {}
        support['tours'] = self.env['saas.tour.event'].sudo().hq_summary() if 'saas.tour.event' in self.env else []
        kpis = {
            'mrr': round(mrr, 2), 'arr': round(mrr * 12, 2), 'paying': len(paying), 'trials': len(trials),
            'live': len(live), 'users': sum(c['users'] or 0 for c in customers),
            'signups_30d': S.search_count([('create_date', '>', now - timedelta(days=30))]),
            'conversion': round(100.0 * ever_paid / len(tenants), 1) if tenants else 0.0,
            'cash_month': round(by_month.get(today.replace(day=1), 0.0), 2),
            'open_invoices': round(sum(self._to_etb(v.amount_residual, v.amount_currency_id.name if 'amount_currency_id' in v._fields else 'ETB')
                                       for v in open_inv), 2),
            'at_risk': len(at_risk), 'payments_waiting': len(waiting_pay), 'errors_24h': tech['errors_24h'],
            'tickets_open': support.get('open', 0),
            'yearly': len(paying.filtered(lambda t: t.billing == 'yearly')),
            'renewals_30d': len(paying.filtered(lambda t: t.paid_until and 0 <= (t.paid_until - today).days <= 30)),
            'overdue': len(tenants.filtered(lambda t: t.state == 'overdue')),
            'paid_seats': sum(max(t.subscribed_users or 0, t.paid_users or 0) for t in paying),
        }
        countries = defaultdict(int)
        industries = defaultdict(int)
        for t in live:
            countries[t.country] += 1
            industries[t.industry or 'other'] += 1
        plan = self._plan(kpis, today)
        return {'kpis': kpis, 'plan': plan, 'revenue': revenue, 'by_method': [{'label': k, 'value': round(v, 2)} for k, v in by_method.items()],
                'funnel': funnel, 'funnel_weeks': funnel_weeks, 'leads': leads, 'customers': customers, 'at_risk': at_risk,
                'tech': tech, 'support': support, 'countries': dict(countries), 'industries': dict(industries),
                'currency': 'ETB', 'generated': fields.Datetime.to_string(now)[:16]}

    # the plan agreed on 20 Oct 2026: month -> (paying companies, seats, MRR in birr); setting saas.plan overrides it
    PLAN = {'2026-11': (15, 90, 75000), '2026-12': (25, 160, 135000), '2027-01': (40, 250, 210000), '2027-02': (52, 320, 272000),
            '2027-03': (64, 390, 331000), '2027-04': (76, 460, 391000), '2027-05': (88, 530, 450000), '2027-06': (100, 600, 510000),
            '2027-07': (112, 670, 570000), '2027-08': (125, 745, 633000), '2027-09': (137, 820, 697000), '2027-10': (150, 900, 765000)}

    def _plan(self, kpis, today):
        import json
        raw = self.env['ir.config_parameter'].sudo().get_param('saas.plan')
        try:
            plan = {k: tuple(v) for k, v in json.loads(raw).items()} if raw else self.PLAN
        except ValueError:
            plan = self.PLAN
        months = sorted(plan)
        key = today.strftime('%Y-%m')
        cur = key if key in plan else next((m for m in months if m >= key), months[-1])
        c, u, m = plan[cur]
        actual = {'companies': kpis['paying'], 'seats': kpis.get('paid_seats', 0), 'mrr': kpis['mrr']}
        pct = lambda a, b: round(100.0 * a / b) if b else 0
        return {'month': cur, 'year_end': months[-1], 'year_target': {'companies': plan[months[-1]][0], 'seats': plan[months[-1]][1],
                                                                       'mrr': plan[months[-1]][2], 'arr': plan[months[-1]][2] * 12},
                'rows': [{'label': 'Paying companies', 'actual': actual['companies'], 'target': c, 'pct': pct(actual['companies'], c)},
                         {'label': 'Paid seats', 'actual': actual['seats'], 'target': u, 'pct': pct(actual['seats'], u)},
                         {'label': 'MRR (birr)', 'actual': round(actual['mrr']), 'target': m, 'pct': pct(actual['mrr'], m)}],
                'months': [{'month': k, 'companies': plan[k][0], 'seats': plan[k][1], 'mrr': plan[k][2]} for k in months]}

    @api.model
    def event_done(self, event_id):
        self.env['saas.server.event'].sudo().browse(event_id).done = True
        return True


def odoo_data_dir():
    from odoo.tools import config
    return config['data_dir']
