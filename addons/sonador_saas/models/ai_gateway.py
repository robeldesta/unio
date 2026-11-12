"""Unio AI gateway: the only place the Anthropic key lives (platform database, setting unio.anthropic_key). Customer
systems on this server reach it directly (sonador_ai, saas.help, Studio); systems on their own server (a dedicated
customer stack) reach it over HTTPS with a token (saas.ai.client).

Spending is capped in US dollars, per week (Monday 00:00 East Africa time to the next Monday):
- for everyone together (saas.ai_week_usd), so the credit cannot run out in a bad week;
- per company, by its AI plan: Standard (included), Pro and Max (paid add-ons);
- per person, a share of the company's week (saas.ai_user_share %), so one person cannot use it all.
The plan also picks the model: help answers on Haiku; the assistant and Studio on Sonnet (Standard, Pro) or Opus (Max)."""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

API = 'https://api.anthropic.com/v1/messages'
# US dollars per million tokens (input, output)
PRICES = {'claude-opus-5-5': (4.0, 20.0), 'claude-sonnet-5-5': (2.0, 10.0), 'claude-haiku-5-5': (0.10, 0.50)}
PLANS = [('standard', 'Standard'), ('pro', 'Pro'), ('max', 'Max')]
CAPS = {'standard': 1.5, 'pro': 6.0, 'max': 20.0}  # US dollars a week per company
MODELS = {  # plan -> feature -> model
    'standard': {'help': 'claude-haiku-5-5', 'assistant': 'claude-sonnet-5-5', 'studio': 'claude-sonnet-5-5'},
    'pro': {'help': 'claude-haiku-5-5', 'assistant': 'claude-sonnet-5-5', 'studio': 'claude-sonnet-5-5'},
    'max': {'help': 'claude-haiku-5-5', 'assistant': 'claude-opus-5-5', 'studio': 'claude-opus-5-5'},
}
EAT = timezone(timedelta(hours=3))


def eat_today():
    return datetime.now(EAT).date()


def week_start():
    """Monday of this week, East Africa time."""
    today = eat_today()
    return today - timedelta(days=today.weekday())


class SaasTenant(models.Model):
    _inherit = 'saas.tenant'

    ai_plan = fields.Selection(PLANS, 'AI plan', default='standard', required=True, tracking=True)


class SaasAiClient(models.Model):
    """A customer system on its own server that uses Unio AI through this gateway."""
    _name = 'saas.ai.client'
    _description = 'AI for a system on its own server'

    name = fields.Char('Company', required=True)
    database = fields.Char('Key', required=True, index=True, help='How its use is counted (unique).')
    token_hash = fields.Char(readonly=True)
    plan = fields.Selection(PLANS, 'AI plan', default='standard', required=True)
    active = fields.Boolean(default=True)

    _db_uniq = models.Constraint('unique(database)', 'One entry per system.')

    def new_token(self):
        """A new token for the system (shown once; only its hash is kept)."""
        self.ensure_one()
        token = 'unio_' + secrets.token_urlsafe(32)
        self.token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token

    @api.model
    def by_token(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([('token_hash', '=', hashlib.sha256(token.encode()).hexdigest()), ('active', '=', True)], limit=1)


class SaasAiUsage(models.Model):
    _name = 'saas.ai.usage'
    _description = 'AI used by a company'
    _order = 'date desc, id desc'

    tenant_id = fields.Many2one('saas.tenant', ondelete='cascade', index=True)
    database = fields.Char(index=True)
    user = fields.Char(index=True, help='Login of the person who asked.')
    date = fields.Date(default=lambda s: eat_today(), index=True)
    feature = fields.Char(help='assistant, help, studio...')
    model = fields.Char()
    calls = fields.Integer()
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    cost_usd = fields.Float(digits=(12, 4))


class SaasAiReply(models.Model):
    """The answer to a question asked from a system on its own server, kept two days by the question's id: when the
    answer is lost on the way back (a dropped connection), the system asks again with the same id and gets the same answer,
    charged once."""
    _name = 'saas.ai.reply'
    _description = 'AI answer kept for a retry'

    database = fields.Char(required=True, index=True)
    request_id = fields.Char(required=True, index=True)
    response = fields.Text()

    _unique = models.Constraint('unique(database, request_id)', 'One answer per question.')

    @api.autovacuum
    def _gc_old(self):
        self.search([('create_date', '<', fields.Datetime.now() - timedelta(days=2))]).unlink()


class SaasAi(models.AbstractModel):
    _name = 'saas.ai'
    _description = 'Unio AI gateway'

    def _P(self):
        return self.env['ir.config_parameter'].sudo()

    @api.model
    def enabled(self):
        return bool(self._P().get_param('unio.anthropic_key'))

    # ------------------------------------------------------------------ plans and caps
    def _settings(self):
        P = self._P()
        caps = dict(CAPS)
        try:
            caps.update(json.loads(P.get_param('saas.ai_plan_caps') or '{}'))
        except ValueError:
            pass
        return {'week': float(P.get_param('saas.ai_week_usd') or 10.0), 'caps': caps,
                'user_share': float(P.get_param('saas.ai_user_share') or 40.0),
                'files': P.get_param('saas.ai_files', '1') == '1'}

    def _plan(self, database):
        t = self.env['saas.tenant'].sudo().search([('subdomain', '=', database)], limit=1)
        if t:
            return t.ai_plan or 'standard'
        c = self.env['saas.ai.client'].sudo().search([('database', '=', database)], limit=1)
        return c.plan if c else 'standard'

    def _spent(self, domain):
        rows = self.env['saas.ai.usage'].sudo()._read_group(domain + [('date', '>=', week_start())], aggregates=['cost_usd:sum'])
        return round(rows[0][0] or 0.0, 4) if rows else 0.0

    @api.model
    def status(self, database, user=False):
        """Where a company (and a person in it) stands this week."""
        s = self._settings()
        plan = self._plan(database)
        cap = float(s['caps'].get(plan, CAPS['standard']))
        company = self._spent([('database', '=', database)])
        mine = self._spent([('database', '=', database), ('user', '=', user)]) if user else 0.0
        total = self._spent([])
        user_cap = round(cap * s['user_share'] / 100.0, 4)
        return {'enabled': self.enabled(), 'plan': plan, 'plan_name': dict(PLANS)[plan], 'cap': cap, 'used': company,
                'pct': min(100, round(100 * company / cap)) if cap else 100, 'user_cap': user_cap, 'user_used': mine,
                'user_pct': min(100, round(100 * mine / user_cap)) if user_cap else 100,
                'paused': total >= s['week'], 'resets': fields.Date.to_string(week_start() + timedelta(days=7)),
                'upgrade': plan != 'max', 'files': s['files']}

    def _check(self, database, user):
        st = self.status(database, user)
        if st['paused']:
            raise UserError(self.env._('Unio AI is paused for everyone until Monday: this week\'s AI budget is used up. '
                                       'Everything else works as usual.'))
        if st['used'] >= st['cap']:
            more = self.env._(' For more, ask about the Pro or Max AI plan.') if st['upgrade'] else ''
            raise UserError(self.env._('Your company\'s AI for this week is used up (%(plan)s plan). It renews on Monday.%(more)s',
                                       plan=st['plan_name'], more=more))
        if user and st['user_used'] >= st['user_cap']:
            raise UserError(self.env._('You have used your share of the company\'s AI for this week. It renews on Monday.'))
        return st

    # ------------------------------------------------------------------ the call
    @api.model
    def call(self, database, feature, payload, user=False):
        """One Messages API call on behalf of a company (and a person in it), within the caps. The model follows the
        company's plan and the feature."""
        key = self._P().get_param('unio.anthropic_key')
        if not key:
            raise UserError(self.env._('Unio AI is not switched on yet.'))
        st = self._check(database, user)
        if not st['files'] and any(isinstance(m.get('content'), list) and any(b.get('type') in ('image', 'document') for b in m['content'])
                                   for m in payload.get('messages') or []):
            raise UserError(self.env._('Files are switched off for Unio AI. Type the details instead.'))
        body = dict(payload)
        body['model'] = MODELS[st['plan']].get(feature, 'claude-sonnet-5-5')
        body.setdefault('max_tokens', 2000)
        body.setdefault('cache_control', {'type': 'ephemeral'})  # repeated instructions and tools at a tenth of the price
        try:
            r = requests.post(API, timeout=180, json=body, headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        except requests.RequestException as e:
            _logger.warning('Anthropic unreachable: %s', e)
            raise UserError(self.env._('The AI could not be reached just now. Try again in a minute.'))
        data = r.json()
        if r.status_code >= 400:
            _logger.warning('Anthropic %s: %s', r.status_code, data)
            raise UserError(self.env._('The AI could not answer just now (%s). Try again in a minute.', (data.get('error') or {}).get('type', r.status_code)))
        self._count(database, feature, body['model'], data.get('usage') or {}, user)
        return data

    def _count(self, database, feature, model, usage, user=False):
        U = self.env['saas.ai.usage'].sudo()
        tenant = self.env['saas.tenant'].sudo().search([('subdomain', '=', database)], limit=1)
        inp, out = int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0)
        cache_read = int(usage.get('cache_read_input_tokens') or 0)
        cache_write = int(usage.get('cache_creation_input_tokens') or 0)
        pin, pout = PRICES.get(model, PRICES['claude-sonnet-5-5'])
        cost = (inp * pin + out * pout + cache_read * pin * 0.1 + cache_write * pin * 1.25) / 1e6
        today = eat_today()
        row = U.search([('database', '=', database), ('date', '=', today), ('feature', '=', feature), ('model', '=', model),
                        ('user', '=', user or False)], limit=1)
        vals = {'calls': row.calls + 1, 'input_tokens': row.input_tokens + inp + cache_read + cache_write,
                'output_tokens': row.output_tokens + out, 'cost_usd': row.cost_usd + cost}
        if row:
            row.write(vals)
        else:
            U.create(dict(vals, database=database, tenant_id=tenant.id, feature=feature, model=model, user=user or False, date=today))

    # ------------------------------------------------------------------ for HQ
    def _hq_only(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only Unio HQ manages the AI settings.'))

    @api.model
    def hq_summary(self):
        s = self._settings()
        rows = self.env['saas.ai.usage'].sudo().search([('date', '>=', week_start())])
        per = {}
        for r in rows:
            d = per.setdefault(r.database, {'database': r.database, 'company': r.tenant_id.name or r.database, 'calls': 0, 'cost': 0.0})
            d['calls'] += r.calls
            d['cost'] += r.cost_usd
        companies = []
        for t in self.env['saas.tenant'].sudo().search([]):
            d = per.pop(t.subdomain, {'database': t.subdomain, 'calls': 0, 'cost': 0.0})
            companies.append(dict(d, company=t.name, plan=t.ai_plan, kind='tenant', id=t.id))
        for c in self.env['saas.ai.client'].sudo().search([]):
            d = per.pop(c.database, {'database': c.database, 'calls': 0, 'cost': 0.0})
            companies.append(dict(d, company=c.name, plan=c.plan, kind='client', id=c.id))
        companies += [dict(d, plan='standard', kind='other', id=False) for d in per.values()]
        for d in companies:
            d['cap'] = float(s['caps'].get(d['plan'], CAPS['standard']))
            d['cost'] = round(d['cost'], 2)
        total = round(sum(rows.mapped('cost_usd')), 2)
        month = round(sum(self.env['saas.ai.usage'].sudo().search([('date', '>=', eat_today().replace(day=1))]).mapped('cost_usd')), 2)
        return {'enabled': self.enabled(), 'calls': sum(rows.mapped('calls')), 'cost': total, 'month': month, 'week_cap': s['week'],
                'paused': total >= s['week'], 'caps': s['caps'], 'user_share': s['user_share'], 'week_start': fields.Date.to_string(week_start()),
                'files': s['files'],
                'plans': [{'id': k, 'name': v} for k, v in PLANS],
                'companies': sorted(companies, key=lambda x: (-x['cost'], x['company'] or ''))}

    @api.model
    def hq_set_key(self, key):
        """Paste the Anthropic key (never shown again; empty switches AI off)."""
        self._hq_only()
        self._P().set_param('unio.anthropic_key', (key or '').strip())
        return self.hq_summary()

    @api.model
    def hq_test(self):
        """One tiny call to check the key works."""
        self._hq_only()
        key = self._P().get_param('unio.anthropic_key')
        if not key:
            raise UserError(self.env._('Add the key first.'))
        r = requests.post(API, timeout=60, json={'model': 'claude-haiku-5-5', 'max_tokens': 16, 'messages': [{'role': 'user', 'content': 'Say OK.'}]},
                          headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        if r.status_code >= 400:
            raise UserError(self.env._('The key did not work: %s', (r.json().get('error') or {}).get('message', r.status_code)))
        self._count('hq', 'test', 'claude-haiku-5-5', r.json().get('usage') or {}, self.env.user.login)
        return True

    @api.model
    def hq_set_caps(self, week, caps, user_share):
        self._hq_only()
        P = self._P()
        P.set_param('saas.ai_week_usd', str(float(week)))
        P.set_param('saas.ai_plan_caps', json.dumps({k: float(caps.get(k, CAPS[k])) for k in CAPS}))
        P.set_param('saas.ai_user_share', str(min(100.0, max(1.0, float(user_share)))))
        return self.hq_summary()

    @api.model
    def hq_set_files(self, on):
        """Files and photos in Unio AI (they cost more than text): on or off for everyone."""
        self._hq_only()
        self._P().set_param('saas.ai_files', '1' if on else '0')
        return self.hq_summary()

    @api.model
    def hq_set_plan(self, kind, rec_id, plan):
        self._hq_only()
        if plan not in dict(PLANS):
            raise UserError(self.env._('Unknown plan.'))
        if kind == 'tenant':
            self.env['saas.tenant'].sudo().browse(int(rec_id)).ai_plan = plan
        else:
            self.env['saas.ai.client'].sudo().browse(int(rec_id)).plan = plan
        return self.hq_summary()

    @api.model
    def hq_add_client(self, name, database):
        """A system on its own server: returns its token, to paste into that system once."""
        self._hq_only()
        database = (database or '').strip().lower()
        if not name or not database:
            raise UserError(self.env._('Give the company and a short key for the system.'))
        C = self.env['saas.ai.client'].sudo()
        c = C.search([('database', '=', database)], limit=1) or C.create({'name': name, 'database': database})
        return {'token': c.new_token(), 'summary': self.hq_summary()}
