"""Calls of the CRM screens: contacts and companies, a contact's record (timeline, deals, what they buy), the deals
board, logging activities, tasks (My day), reorders due, the dashboard and targets. Months are Ethiopian months."""
import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

from .crm import CHANNELS, CTYPES, KINDS, LANGUAGES, LANGUAGES_BY_COUNTRY, LIFECYCLE, OUTCOMES, SOURCES, ethiopian_phone

SALE_TYPES = ('sale', 'cash_sale')
PAGE = 80


def _s(d):
    return fields.Date.to_string(d) if d else False


def _ec(d):
    return format_ethiopian(d) if d else ''


def eth_month(d):
    """(first day, last day, label) of the Ethiopian month of d (Pagume folds into Nehase)."""
    for _no, name, a, b in fiscal_periods(fiscal_year_of(d)):
        if a <= d <= b:
            return a, b, name
    return d, d, ''


class SbCrm(models.AbstractModel):
    _name = 'sb.crm'
    _description = 'CRM'

    # ------------------------------------------------------------------ access
    def _is_manager(self):
        return self.env.su or self.env.user.has_group('sonador_crm.group_crm_manager')

    def _require_sales(self):
        if not (self.env.su or self.env.user.has_group('sonador_crm.group_crm_user')):
            raise AccessError(self.env._('CRM is for the sales team.'))

    def _crm_users(self):
        users = self.env.ref('sonador_crm.group_crm_user').sudo().all_user_ids
        owners = self._P().search([('sb_owner_id', '!=', False)]).mapped('sb_owner_id')
        return (users | owners).filtered(lambda u: u.active and not u.share).sorted('name')

    def _P(self):
        return self.env['res.partner'].sudo().with_context(active_test=False)

    def _today(self):
        return fields.Date.context_today(self)

    def _local(self, dt):
        return fields.Datetime.context_timestamp(self, dt) if dt else False

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        self._require_sales()
        pipes = self.env['sb.crm.pipeline'].sudo().search([])
        reps = self._crm_users()
        sel = lambda items: [{'id': k, 'name': v} for k, v in items]  # noqa: E731
        from ..hooks import ensure_areas
        cc = self.env.company.country_id.code or 'ET'
        ensure_areas(self.env, cc)
        regions = sorted({r for r in self.env['sb.woreda'].sudo().search([('country_code', '=', cc if cc in ('ET', 'UG') else 'ET')]).mapped('region') if r})
        return {'me': self.env.user.id, 'is_manager': self._is_manager(), 'company': self.env.company.name,
                'currency': self.env.company.currency_id.name,
                'today': _s(self._today()), 'today_ec': _ec(self._today()),
                'users': [{'id': u.id, 'name': u.name} for u in reps],
                'pipelines': [{'id': p.id, 'name': p.name, 'stages': [{'id': s.id, 'name': s.name, 'kind': s.kind,
                                                                       'probability': s.probability, 'rot': s.rot_days}
                                                                      for s in p.stage_ids]} for p in pipes],
                'lifecycle': sel(LIFECYCLE), 'ctypes': sel(CTYPES), 'channels': sel(CHANNELS), 'sources': sel(SOURCES),
                'languages': sel([x for x in LANGUAGES if x[0] in LANGUAGES_BY_COUNTRY.get(cc, LANGUAGES_BY_COUNTRY['ET'])]), 'kinds': sel(KINDS), 'outcomes': sel(OUTCOMES), 'regions': regions,
                'area_label': 'District' if cc == 'UG' else 'Woreda',
                'tags': [{'id': t.id, 'name': t.name} for t in self.env['sb.crm.tag'].sudo().search([])],
                'lost_reasons': [{'id': r.id, 'name': r.name} for r in self.env['sb.crm.lost.reason'].sudo().search([])]}

    # ------------------------------------------------------------------ sales figures
    def _sales(self, partners, df=None, dt=None):
        """{commercial partner id: [(date, amount)]} from posted sales invoices and cash sales (returns subtract)."""
        dom = [('company_id', '=', self.env.company.id), ('state', '=', 'posted'),
               ('voucher_type', 'in', SALE_TYPES + ('sale_return',)), ('partner_id', 'in', partners.ids)]
        if df:
            dom.append(('date', '>=', df))
        if dt:
            dom.append(('date', '<=', dt))
        out = defaultdict(list)
        for v in self.env['sb.voucher'].sudo().search(dom, order='date'):
            sign = -1 if v.voucher_type == 'sale_return' else 1
            out[v.partner_id.commercial_partner_id.id].append((v.date, sign * v.amount_total))
        return out

    # ------------------------------------------------------------------ contacts
    def _row(self, p, last=None):
        return {'id': p.id, 'name': p.name, 'company': p.is_company, 'parent': p.parent_id.name or '', 'parent_id': p.parent_id.id,
                'phone': p.phone or '', 'email': p.email or '', 'lifecycle': p.sb_lifecycle or '',
                'ctype': p.sb_ctype or '', 'owner': p.sb_owner_id.name or '', 'owner_id': p.sb_owner_id.id,
                'region': p.sb_region or '', 'zone': p.sb_zone or '', 'woreda': p.sb_woreda_id.name or '',
                'last_touch': _ec(self._local(p.sb_last_touch).date()) if p.sb_last_touch else '',
                'customer': bool(p.customer_rank), 'ref': p.ref or '', 'vat': p.vat or ''}

    def _contact_domain(self, f):
        dom = [('type', '=', 'contact'), ('active', '=', True), ('employee_ids', '=', False),
               '|', ('company_id', '=', False), ('company_id', '=', self.env.company.id)]
        kind = f.get('kind') or 'all'
        if kind == 'companies':
            dom.append(('is_company', '=', True))
        elif kind == 'people':
            dom.append(('is_company', '=', False))
        if f.get('crm_only', True):
            dom += ['|', '|', ('customer_rank', '>', 0), ('sb_lifecycle', '!=', False), ('sb_owner_id', '!=', False)]
        for key, field in (('lifecycle', 'sb_lifecycle'), ('ctype', 'sb_ctype'), ('region', 'sb_region'), ('zone', 'sb_zone'),
                           ('source', 'sb_source')):
            if f.get(key):
                dom.append((field, '=', f[key]))
        if f.get('owner') == 'me':
            dom.append(('sb_owner_id', '=', self.env.uid))
        elif f.get('owner') == 'none':
            dom.append(('sb_owner_id', '=', False))
        elif f.get('owner'):
            dom.append(('sb_owner_id', '=', int(f['owner'])))
        if f.get('tag'):
            dom.append(('sb_tag_ids', 'in', [int(f['tag'])]))
        if f.get('quiet'):
            dom += ['|', ('sb_last_touch', '=', False), ('sb_last_touch', '<', fields.Datetime.now() - timedelta(days=int(f['quiet'])))]
        if f.get('search'):
            s = f['search'].strip()
            digits = ''.join(c for c in s if c.isdigit())
            ph = re.sub(r'^(251|0)', '', digits) if len(digits) >= 4 and len(digits) >= len(s.replace(' ', '')) - 1 else s
            dom += ['|', '|', '|', '|', ('name', 'ilike', s), ('phone', 'ilike', ph),
                    ('ref', 'ilike', s), ('vat', 'ilike', s), ('sb_woreda_id.name', 'ilike', s)]
        return dom

    @api.model
    def contacts(self, f=None):
        self._require_sales()
        f = f or {}
        dom = self._contact_domain(f)
        P = self._P()
        total = P.search_count(dom)
        order = {'name': 'name', 'recent': 'sb_last_touch desc nulls last, name', 'oldest': 'sb_last_touch asc nulls first, name',
                 'new': 'create_date desc'}.get(f.get('order'), 'name')
        rows = P.search(dom, order=order, limit=PAGE, offset=int(f.get('offset') or 0))
        return {'total': total, 'rows': [self._row(p) for p in rows], 'page': PAGE}

    @api.model
    def contact(self, partner_id):
        self._require_sales()
        p = self._P().browse(int(partner_id))
        com = p.commercial_partner_id
        today = self._today()
        sales = self._sales(com)[com.id]
        fy = self.env.company.compute_fiscalyear_dates(today)
        dates = sorted({d for d, a in sales if a > 0})
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 3]
        Deal = self.env['sb.deal'].sudo()
        deals = Deal.search([('partner_id', 'in', (p | com).ids)], order='state, id desc')
        acts = self.env['sb.crm.activity'].sudo().search(['|', ('partner_id', 'in', (p | com | com.child_ids).ids),
                                                          ('deal_id', 'in', deals.ids)], order='done, date desc', limit=200)
        orders = self.env['sale.order'].sudo().search([('partner_id', 'child_of', com.id)], order='date_order desc', limit=10)
        return {'id': p.id, 'name': p.name, 'company': p.is_company, 'parent': p.parent_id and {'id': p.parent_id.id, 'name': p.parent_id.name},
                'fields': {'name': p.name, 'phone': p.phone or '', 'email': p.email or '', 'vat': p.vat or '',
                           'ref': p.ref or '', 'function': p.function or '', 'lifecycle': p.sb_lifecycle or '', 'owner_id': p.sb_owner_id.id or '',
                           'ctype': p.sb_ctype or '', 'channel': p.sb_channel or '', 'source': p.sb_source or '',
                           'language': p.sb_language or '', 'woreda_id': p.sb_woreda_id.id or '',
                           'woreda': p.sb_woreda_id.display_name or '', 'region': p.sb_region or '', 'zone': p.sb_zone or '',
                           'kebele': p.sb_kebele or '', 'telegram': p.sb_telegram or '', 'hectares': p.sb_hectares or 0,
                           'crops': p.sb_crops or '', 'tags': p.sb_tag_ids.ids, 'street': p.street or '', 'city': p.city or '',
                           'latitude': p.sb_latitude, 'longitude': p.sb_longitude, 'is_company': p.is_company,
                           'parent_id': p.parent_id.id or ''},
                'people': [{'id': c.id, 'name': c.name, 'function': c.function or '', 'phone': c.phone or ''}
                           for c in (com.child_ids if p.is_company else self._P().browse()).filtered(lambda c: c.type == 'contact' and c.active)],
                'deals': [self._deal_row(d) for d in deals],
                'timeline': [self._act_row(a) for a in acts],
                'sales': {'total_fy': round(sum(a for d, a in sales if fy['date_from'] <= d <= fy['date_to']), 2),
                          'total': round(sum(a for d, a in sales), 2), 'count': len(dates),
                          'last': _ec(dates[-1]) if dates else '', 'days_since': (today - dates[-1]).days if dates else None,
                          'cycle': round(sum(gaps) / len(gaps)) if gaps else None,
                          'recent': [{'date': _ec(d), 'amount': a} for d, a in sorted(sales, reverse=True)[:8]]},
                'orders': [{'id': o.id, 'name': o.name, 'date': _ec(o.date_order.date()), 'amount': o.amount_total, 'state': o.state}
                           for o in orders],
                'customer': bool(com.customer_rank)}

    @api.model
    def save_contact(self, partner_id, vals):
        self._require_sales()
        P = self._P()
        w = {}
        simple = {'name': 'name', 'phone': 'phone', 'email': 'email', 'vat': 'vat', 'function': 'function',
                  'lifecycle': 'sb_lifecycle', 'ctype': 'sb_ctype', 'channel': 'sb_channel', 'source': 'sb_source',
                  'language': 'sb_language', 'kebele': 'sb_kebele', 'telegram': 'sb_telegram', 'crops': 'sb_crops',
                  'street': 'street', 'city': 'city', 'region': 'sb_region', 'zone': 'sb_zone'}
        for k, f in simple.items():
            if k in vals:
                w[f] = (vals[k] or '').strip() if isinstance(vals[k], str) else (vals[k] or False)
                if w[f] == '':
                    w[f] = False
        for k, f in (('owner_id', 'sb_owner_id'), ('woreda_id', 'sb_woreda_id'), ('parent_id', 'parent_id')):
            if k in vals:
                w[f] = int(vals[k]) if vals[k] else False
        for k, f in (('hectares', 'sb_hectares'), ('latitude', 'sb_latitude'), ('longitude', 'sb_longitude')):
            if k in vals:
                w[f] = float(vals[k] or 0)
        if 'tags' in vals:
            w['sb_tag_ids'] = [(6, 0, [int(t) for t in vals['tags']])]
        if 'telegram' in vals and w.get('sb_telegram'):
            w['sb_telegram'] = w['sb_telegram'].lstrip('@')
        if partner_id:
            p = P.browse(int(partner_id))
            p.write(w)
        else:
            if not w.get('name'):
                raise ValidationError(self.env._('Enter the name.'))
            ph = w.get('phone')
            if ph:
                dup = P.search([('phone', '=', ethiopian_phone(ph, self.env.company.country_id.code)), ('active', '=', True)], limit=1)
                if dup and not vals.get('force'):
                    raise UserError(self.env._('%(n)s already has the number %(p)s. Open that contact instead.', n=dup.name, p=ethiopian_phone(ph, self.env.company.country_id.code)))
            w.setdefault('is_company', bool(vals.get('is_company')))
            w.setdefault('sb_lifecycle', 'prospect')
            w.setdefault('sb_owner_id', self.env.uid)
            w['company_id'] = False
            p = P.create(w)
        return self.contact(p.id)

    @api.model
    def woredas(self, search):
        W = self.env['sb.woreda'].sudo()
        s = (search or '').strip()
        if not s:
            return []
        return [{'id': w.id, 'label': w.display_name, 'region': w.region, 'zone': w.zone}
                for w in W.search([('country_code', '=', 'UG' if self.env.company.country_id.code == 'UG' else 'ET'),
                                   '|', '|', ('name', 'ilike', s), ('zone', 'ilike', s), ('region', 'ilike', s)], limit=20)]

    @api.model
    def bulk(self, ids, vals):
        """Assign owner / lifecycle / tag to many contacts at once."""
        self._require_sales()
        P = self._P().browse([int(i) for i in ids])
        w = {}
        if vals.get('owner_id') is not None:
            w['sb_owner_id'] = int(vals['owner_id']) or False
        if vals.get('lifecycle'):
            w['sb_lifecycle'] = vals['lifecycle']
        if w:
            P.write(w)
        if vals.get('tag'):
            P.write({'sb_tag_ids': [(4, int(vals['tag']))]})
        return len(P)

    # ------------------------------------------------------------------ timeline
    def _act_row(self, a):
        loc = self._local(a.date)
        return {'id': a.id, 'kind': a.kind, 'subject': a.subject or '', 'body': a.body or '', 'user': a.user_id.name or '',
                'user_id': a.user_id.id, 'date': fields.Datetime.to_string(a.date), 'ec': _ec(loc.date()) if loc else '',
                'time': loc.strftime('%H:%M') if loc else '', 'done': a.done, 'outcome': a.outcome or '', 'minutes': a.minutes,
                'partner': a.partner_id.name or '', 'partner_id': a.partner_id.id, 'deal': a.deal_id.name or '', 'deal_id': a.deal_id.id,
                'map': 'https://maps.google.com/?q=%s,%s' % (a.latitude, a.longitude) if (a.latitude or a.longitude) else '',
                'overdue': bool(not a.done and a.date and a.date < fields.Datetime.now()),
                'phone': a.partner_id.phone or ''}

    @api.model
    def log(self, vals):
        """Record something done, or a task to do. vals: kind, subject, body, partner_id, deal_id, outcome, minutes, due
        (task: 'YYYY-MM-DDTHH:MM' local), user_id (task owner), lat / lon (visit)."""
        self._require_sales()
        kind = vals.get('kind') or 'note'
        if kind not in dict(KINDS):
            raise ValidationError(self.env._('Unknown activity.'))
        if not (vals.get('partner_id') or vals.get('deal_id')):
            raise ValidationError(self.env._('Pick the contact or the deal.'))
        if kind == 'task' and not (vals.get('subject') or '').strip():
            raise ValidationError(self.env._('Say what the task is.'))
        if kind != 'task' and not ((vals.get('subject') or '').strip() or (vals.get('body') or '').strip() or vals.get('outcome')):
            raise ValidationError(self.env._('Write what happened.'))
        deal = self.env['sb.deal'].sudo().browse(int(vals['deal_id'])) if vals.get('deal_id') else self.env['sb.deal']
        w = {'kind': kind, 'subject': (vals.get('subject') or '').strip() or False, 'body': (vals.get('body') or '').strip() or False,
             'partner_id': int(vals['partner_id']) if vals.get('partner_id') else deal.partner_id.id or False,
             'deal_id': deal.id or False, 'outcome': vals.get('outcome') or False, 'minutes': int(vals.get('minutes') or 0),
             'latitude': float(vals.get('lat') or 0), 'longitude': float(vals.get('lon') or 0), 'done': kind != 'task',
             'user_id': int(vals['user_id']) if vals.get('user_id') else self.env.uid}
        if kind == 'task':
            due = vals.get('due')
            if due:
                local = datetime.strptime(due[:16], '%Y-%m-%dT%H:%M')
                tz = pytz.timezone(self.env.user.tz or 'Africa/Addis_Ababa')
                w['date'] = tz.localize(local).astimezone(pytz.UTC).replace(tzinfo=None)
            else:
                w['date'] = datetime.combine(self._today() + timedelta(days=1), time(6, 0))
        a = self.env['sb.crm.activity'].sudo().create(w)
        p = a.partner_id.commercial_partner_id
        if kind in ('call', 'meeting', 'visit', 'telegram', 'sms', 'email') and p and p.sb_lifecycle in (False, 'prospect'):
            p.sb_lifecycle = 'contacted'
        if vals.get('followup_days') and kind != 'task':
            self.env['sb.crm.activity'].sudo().create({
                'kind': 'task', 'done': False, 'partner_id': a.partner_id.id, 'deal_id': a.deal_id.id or False,
                'subject': vals.get('followup') or self.env._('Follow up'), 'user_id': self.env.uid,
                'date': datetime.combine(self._today() + timedelta(days=int(vals['followup_days'])), time(6, 0))})
        return self._act_row(a)

    @api.model
    def finish(self, activity_id, outcome=False, note=''):
        a = self.env['sb.crm.activity'].sudo().browse(int(activity_id))
        if a.user_id != self.env.user and not self._is_manager():
            raise AccessError(self.env._('This is %s\'s task.', a.user_id.name))
        a.write({'done': True, 'done_date': fields.Datetime.now(), 'outcome': outcome or a.outcome,
                 'body': ((a.body or '') + ('\n' if a.body and note else '') + (note or '')) or False})
        return self._act_row(a)

    @api.model
    def reschedule(self, activity_id, days):
        a = self.env['sb.crm.activity'].sudo().browse(int(activity_id))
        a.date = max(a.date, fields.Datetime.now()) + timedelta(days=int(days))
        return self._act_row(a)

    @api.model
    def remove_activity(self, activity_id):
        a = self.env['sb.crm.activity'].sudo().browse(int(activity_id))
        if a.user_id != self.env.user and not self._is_manager():
            raise AccessError(self.env._('Only %s or a CRM manager can remove this.', a.user_id.name))
        a.unlink()
        return True

    @api.model
    def tasks(self, who='me'):
        self._require_sales()
        dom = [('kind', '=', 'task'), ('done', '=', False), ('company_id', '=', self.env.company.id)]
        if who == 'me' or not self._is_manager():
            dom.append(('user_id', '=', self.env.uid))
        elif who not in ('all', None, ''):
            dom.append(('user_id', '=', int(who)))
        acts = self.env['sb.crm.activity'].sudo().search(dom, order='date')
        groups = {'overdue': [], 'today': [], 'week': [], 'later': []}
        for a in acts:
            loc = self._local(a.date).date()
            today = self._today()
            k = 'overdue' if loc < today else 'today' if loc == today else 'week' if loc <= today + timedelta(days=7) else 'later'
            groups[k].append(self._act_row(a))
        done_today = self.env['sb.crm.activity'].sudo().search_count([
            ('user_id', '=', self.env.uid), ('done', '=', True), ('kind', '!=', 'task'),
            ('date', '>=', datetime.combine(self._today(), time(0)) - timedelta(hours=3))])
        return {'groups': groups, 'done_today': done_today}

    # ------------------------------------------------------------------ deals
    def _deal_row(self, d):
        days = (fields.Datetime.now() - d.stage_date).days if d.stage_date else 0
        nxt = self.env['sb.crm.activity'].sudo().search([('deal_id', '=', d.id), ('kind', '=', 'task'), ('done', '=', False)],
                                                         order='date', limit=1)
        return {'id': d.id, 'name': d.name, 'partner': d.partner_id.name or '', 'partner_id': d.partner_id.id,
                'contact': d.contact_id.name or '', 'contact_id': d.contact_id.id, 'owner': d.owner_id.name or '', 'owner_id': d.owner_id.id,
                'pipeline_id': d.pipeline_id.id, 'stage_id': d.stage_id.id, 'stage': d.stage_id.name, 'state': d.state,
                'amount': d.amount, 'quantity': d.quantity, 'probability': d.probability, 'close_date': _s(d.close_date),
                'close_ec': _ec(d.close_date), 'source': d.source or '', 'lost_reason': d.lost_reason_id.name or '',
                'lost_reason_id': d.lost_reason_id.id, 'lost_note': d.lost_note or '', 'note': d.note or '',
                'tags': d.tag_ids.ids, 'days_in_stage': days, 'stale': d.state == 'open' and days > (d.stage_id.rot_days or 9999),
                'overdue': bool(d.state == 'open' and d.close_date and d.close_date < self._today()),
                'next_task': nxt and {'subject': nxt.subject, 'ec': _ec(self._local(nxt.date).date())} or False,
                'orders': [{'id': o.id, 'name': o.name, 'state': o.state, 'amount': o.amount_total} for o in d.sale_order_ids],
                'won_ec': _ec(d.won_date), 'region': d.partner_id.sb_region or ''}

    @api.model
    def board(self, pipeline_id, f=None):
        self._require_sales()
        f = f or {}
        pipe = self.env['sb.crm.pipeline'].sudo().browse(int(pipeline_id))
        dom = [('pipeline_id', '=', pipe.id), ('company_id', '=', self.env.company.id)]
        if f.get('owner') == 'me':
            dom.append(('owner_id', '=', self.env.uid))
        elif f.get('owner'):
            dom.append(('owner_id', '=', int(f['owner'])))
        if f.get('search'):
            dom += ['|', ('name', 'ilike', f['search']), ('partner_id.name', 'ilike', f['search'])]
        if f.get('region'):
            dom.append(('partner_id.sb_region', '=', f['region']))
        recent = fields.Date.context_today(self) - timedelta(days=int(f.get('closed_days') or 60))
        Deal = self.env['sb.deal'].sudo()
        cols = []
        for st in pipe.stage_ids:
            sdom = dom + [('stage_id', '=', st.id)]
            if st.kind != 'open':
                sdom += [('stage_date', '>=', datetime.combine(recent, time(0)))]
            deals = Deal.search(sdom, order='close_date asc nulls last, id desc', limit=200)
            cols.append({'id': st.id, 'name': st.name, 'kind': st.kind, 'probability': st.probability,
                         'count': len(deals), 'amount': round(sum(deals.mapped('amount')), 2),
                         'deals': [self._deal_row(d) for d in deals]})
        open_deals = Deal.search(dom + [('state', '=', 'open')])
        return {'pipeline': {'id': pipe.id, 'name': pipe.name}, 'columns': cols,
                'open_amount': round(sum(open_deals.mapped('amount')), 2),
                'weighted': round(sum(d.amount * (d.probability or 0) / 100.0 for d in open_deals), 2)}

    @api.model
    def deal(self, deal_id):
        self._require_sales()
        d = self.env['sb.deal'].sudo().browse(int(deal_id))
        row = self._deal_row(d)
        acts = self.env['sb.crm.activity'].sudo().search([('deal_id', '=', d.id)], order='done, date desc')
        row['timeline'] = [self._act_row(a) for a in acts]
        row['contacts'] = [{'id': c.id, 'name': c.name} for c in (d.partner_id | d.partner_id.child_ids).filtered(lambda c: c.type == 'contact')]
        return row

    @api.model
    def save_deal(self, deal_id, vals):
        self._require_sales()
        Deal = self.env['sb.deal'].sudo()
        w = {}
        for k in ('name', 'source', 'note', 'lost_note'):
            if k in vals:
                w[k] = vals[k] or False
        for k in ('partner_id', 'contact_id', 'owner_id', 'pipeline_id', 'stage_id', 'lost_reason_id'):
            if k in vals:
                w[k] = int(vals[k]) if vals[k] else False
        for k in ('amount', 'quantity', 'probability'):
            if k in vals:
                w[k] = float(vals[k] or 0)
        if 'close_date' in vals:
            w['close_date'] = vals['close_date'] or False
        if 'tags' in vals:
            w['tag_ids'] = [(6, 0, [int(t) for t in vals['tags']])]
        if deal_id:
            d = Deal.browse(int(deal_id))
            if w.get('pipeline_id') and w['pipeline_id'] != d.pipeline_id.id and not w.get('stage_id'):
                w['stage_id'] = self.env['sb.crm.pipeline'].sudo().browse(w['pipeline_id']).stage_ids[:1].id
            d.write(w)
        else:
            if not w.get('pipeline_id'):
                w['pipeline_id'] = self.env['sb.crm.pipeline'].sudo().search([], limit=1).id
            pipe = self.env['sb.crm.pipeline'].sudo().browse(w['pipeline_id'])
            w.setdefault('stage_id', pipe.stage_ids.filtered(lambda s: s.kind == 'open')[:1].id)
            w.setdefault('probability', self.env['sb.crm.stage'].sudo().browse(w['stage_id']).probability)
            w.setdefault('owner_id', self.env.uid)
            if not w.get('name'):
                partner = self._P().browse(w.get('partner_id') or 0)
                if not partner:
                    raise ValidationError(self.env._('Name the deal or pick the customer.'))
                w['name'] = self.env._('%(p)s – %(m)s', p=partner.name, m=eth_month(self._today())[2])
            d = Deal.create(w)
            p = d.partner_id.commercial_partner_id
            if p and p.sb_lifecycle in (False, 'prospect', 'contacted'):
                p.sb_lifecycle = 'qualified'
            if p and not p.sb_owner_id:
                p.sb_owner_id = d.owner_id
        return self.deal(d.id)

    @api.model
    def move_deal(self, deal_id, stage_id, lost_reason_id=False, lost_note=''):
        self._require_sales()
        d = self.env['sb.deal'].sudo().browse(int(deal_id))
        st = self.env['sb.crm.stage'].sudo().browse(int(stage_id))
        if st.kind == 'lost' and not lost_reason_id:
            raise ValidationError(self.env._('Pick why the deal was lost.'))
        old = d.stage_id
        d.write({'stage_id': st.id, 'lost_reason_id': int(lost_reason_id) if lost_reason_id else False,
                 'lost_note': lost_note or False})
        self.env['sb.crm.activity'].sudo().create({'kind': 'note', 'deal_id': d.id, 'partner_id': d.partner_id.id, 'done': True,
                                                    'subject': self.env._('Stage: %(a)s → %(b)s', a=old.name, b=st.name)
                                                    + (' (%s)' % d.lost_reason_id.name if st.kind == 'lost' else '')})
        if st.kind == 'won' and d.partner_id:
            p = d.partner_id.commercial_partner_id
            p.sb_lifecycle = 'repeat' if p.sb_lifecycle in ('customer', 'repeat') else 'customer'
        return self._deal_row(d)

    @api.model
    def quote(self, deal_id):
        """A quotation for the deal's customer, linked to the deal."""
        self._require_sales()
        d = self.env['sb.deal'].sudo().browse(int(deal_id))
        if not d.partner_id:
            raise UserError(self.env._('Pick the customer of the deal first.'))
        p = d.partner_id.commercial_partner_id
        if not p.customer_rank:
            p.customer_rank = 1
        so = self.env['sale.order'].sudo().create({'partner_id': p.id, 'sb_deal_id': d.id, 'user_id': d.owner_id.id or self.env.uid,
                                                   'origin': d.name})
        self.env['sb.crm.activity'].sudo().create({'kind': 'note', 'deal_id': d.id, 'partner_id': p.id, 'done': True,
                                                    'subject': self.env._('Quotation %s created', so.name)})
        if not self.env.user.has_group('sonador_sales.group_sb_sales_user'):
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'type': 'success', 'sticky': True, 'message': self.env._('Quotation %s is ready for the sales office to price and send.', so.name)}}
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order', 'res_id': so.id, 'views': [[False, 'form']], 'target': 'current'}

    # ------------------------------------------------------------------ reorders due
    @api.model
    def reorders(self, f=None):
        """Customers past their usual buying cycle: the average gap between their purchases (at least 2), or 90 days
        after a single purchase in the last year."""
        self._require_sales()
        f = f or {}
        today = self._today()
        since = today - timedelta(days=550)
        V = self.env['sb.voucher'].sudo()
        by = defaultdict(list)
        for partner, day, amount in V._read_group(
                [('company_id', '=', self.env.company.id), ('state', '=', 'posted'), ('voucher_type', 'in', SALE_TYPES),
                 ('date', '>=', since), ('partner_id', '!=', False)], ['partner_id', 'date:day'], ['amount_total:sum']):
            by[partner.commercial_partner_id.id].append((fields.Date.to_date(day), amount))
        P = self._P()
        out = []
        partners = P.browse(list(by)).exists()
        tasks = set(self.env['sb.crm.activity'].sudo().search([('kind', '=', 'task'), ('done', '=', False),
                                                               ('partner_id', 'in', partners.ids)]).mapped('partner_id').ids)
        for p in partners:
            if f.get('owner') == 'me' and p.sb_owner_id.id != self.env.uid:
                continue
            if f.get('region') and p.sb_region != f['region']:
                continue
            visits = sorted(by[p.id])
            dates = [d for d, _a in visits]
            last = dates[-1]
            gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 3]
            cycle = round(sum(gaps) / len(gaps)) if gaps else 90
            since_last = (today - last).days
            if since_last < cycle * 1.1 or since_last > 540:
                continue
            out.append({'id': p.id, 'name': p.name, 'phone': p.phone or '', 'owner': p.sb_owner_id.name or '',
                        'region': p.sb_region or '', 'zone': p.sb_zone or '', 'last': _ec(last), 'days': since_last, 'cycle': cycle,
                        'buys': len(dates), 'last_amount': round(visits[-1][1], 2),
                        'year_amount': round(sum(a for d, a in visits if d >= today - timedelta(days=365)), 2),
                        'overdue_by': since_last - cycle, 'has_task': p.id in tasks})
        out.sort(key=lambda r: (-r['year_amount']))
        return out

    @api.model
    def reorder_tasks(self, partner_ids):
        """A call task for each (for its owner, or me), saying how long they usually go between purchases."""
        self._require_sales()
        A = self.env['sb.crm.activity'].sudo()
        due = {r['id']: r for r in self.reorders()}
        n = 0
        for p in self._P().browse([int(i) for i in partner_ids]):
            if A.search_count([('partner_id', '=', p.id), ('kind', '=', 'task'), ('done', '=', False)]):
                continue
            r = due.get(p.id) or {}
            A.create({'kind': 'task', 'done': False, 'partner_id': p.id, 'user_id': (p.sb_owner_id or self.env.user).id,
                      'subject': self.env._('Reorder call: last bought %(l)s, usually every %(c)s days', l=r.get('last', '?'),
                                            c=r.get('cycle', '?')),
                      'date': datetime.combine(self._today(), time(6, 0))})
            n += 1
        return n

    # ------------------------------------------------------------------ dashboard and targets
    @api.model
    def dashboard(self, month=False, owner=False):
        self._require_sales()
        today = self._today()
        d0 = fields.Date.to_date(month) if month else today
        m0, m1, label = eth_month(d0)
        mine = owner == 'me' or not self._is_manager()
        uid = self.env.uid if mine else (int(owner) if owner else False)
        Deal = self.env['sb.deal'].sudo()
        ddom = [('company_id', '=', self.env.company.id)] + ([('owner_id', '=', uid)] if uid else [])
        open_deals = Deal.search(ddom + [('state', '=', 'open')])
        won = Deal.search(ddom + [('state', '=', 'won'), ('won_date', '>=', m0), ('won_date', '<=', m1)])
        lost = Deal.search(ddom + [('state', '=', 'lost'), ('stage_date', '>=', datetime.combine(m0, time(0))),
                                   ('stage_date', '<=', datetime.combine(m1, time(23, 59)))])
        by_stage = defaultdict(float)
        for d in open_deals:
            by_stage[d.stage_id.name] += d.amount
        # forecast: weighted open deals by the Ethiopian month they should close in
        fc = defaultdict(float)
        for d in open_deals:
            if d.close_date:
                fc[eth_month(max(d.close_date, today))[2]] += d.amount * (d.probability or 0) / 100.0
        months = []
        cur = m0
        for _i in range(4):
            a, b, name = eth_month(cur)
            months.append(name)
            cur = b + timedelta(days=1)
        A = self.env['sb.crm.activity'].sudo()
        adom = [('company_id', '=', self.env.company.id), ('done', '=', True), ('kind', 'not in', ('task', 'note')),
                ('date', '>=', datetime.combine(m0, time(0)) - timedelta(hours=3)), ('date', '<', datetime.combine(m1 + timedelta(days=1), time(0)) - timedelta(hours=3))]
        if uid:
            adom.append(('user_id', '=', uid))
        per_rep = defaultdict(Counter)
        for a in A.search(adom):
            per_rep[a.user_id.name][a.kind] += 1
        # sales actually invoiced in the month, by owner and region
        sales = self.env['sb.voucher'].sudo().search([('company_id', '=', self.env.company.id), ('state', '=', 'posted'),
                                                      ('voucher_type', 'in', SALE_TYPES + ('sale_return',)),
                                                      ('date', '>=', m0), ('date', '<=', m1)])
        by_owner, by_region = defaultdict(float), defaultdict(float)
        total_sales = 0.0
        for v in sales:
            amt = -v.amount_total if v.voucher_type == 'sale_return' else v.amount_total
            p = v.partner_id.commercial_partner_id
            if uid and p.sb_owner_id.id != uid:
                continue
            total_sales += amt
            by_owner[p.sb_owner_id.name or self.env._('No owner')] += amt
            by_region[p.sb_region or self.env._('Region not set')] += amt
        targets = self.env['sb.crm.target'].sudo().search([('company_id', '=', self.env.company.id), ('month', '=', m0)])
        tgt = {t.user_id.name: t.amount for t in targets if not uid or t.user_id.id == uid}
        reps = sorted(set(by_owner) | set(tgt) | set(per_rep))
        overdue = A.search_count([('kind', '=', 'task'), ('done', '=', False), ('date', '<', fields.Datetime.now())]
                                 + ([('user_id', '=', uid)] if uid else []))
        P = self._P()
        new_contacts = P.search_count([('create_date', '>=', datetime.combine(m0, time(0))),
                                       ('sb_lifecycle', 'in', ('prospect', 'contacted', 'qualified'))]
                                      + ([('sb_owner_id', '=', uid)] if uid else []))
        return {'month': _s(m0), 'label': label, 'range': '%s – %s' % (_ec(m0), _ec(m1)), 'mine': mine,
                'kpi': {'open': round(sum(open_deals.mapped('amount')), 2), 'open_count': len(open_deals),
                        'weighted': round(sum(d.amount * (d.probability or 0) / 100.0 for d in open_deals), 2),
                        'won': round(sum(won.mapped('amount')), 2), 'won_count': len(won), 'lost_count': len(lost),
                        'win_rate': round(100.0 * len(won) / (len(won) + len(lost)), 0) if (won or lost) else None,
                        'sales': round(total_sales, 2), 'target': round(sum(tgt.values()), 2), 'overdue': overdue,
                        'new_contacts': new_contacts, 'reorders': len(self.reorders({'owner': 'me'} if mine else {}))},
                'stages': [{'name': s, 'amount': round(a, 2)} for s, a in by_stage.items()],
                'forecast': [{'month': m, 'amount': round(fc.get(m, 0.0), 2)} for m in months],
                'reps': [{'name': r, 'sales': round(by_owner.get(r, 0.0), 2), 'target': tgt.get(r, 0.0),
                          'calls': per_rep[r]['call'], 'meetings': per_rep[r]['meeting'], 'visits': per_rep[r]['visit'],
                          'messages': per_rep[r]['telegram'] + per_rep[r]['sms'] + per_rep[r]['email']} for r in reps],
                'regions': sorted([{'name': k, 'amount': round(v, 2)} for k, v in by_region.items()], key=lambda x: -x['amount'])[:12],
                'lost_reasons': [{'name': k, 'count': v} for k, v in Counter(lost.mapped('lost_reason_id.name')).most_common()]}

    @api.model
    def targets(self, month):
        self._require_sales()
        m0, _m1, label = eth_month(fields.Date.to_date(month))
        T = self.env['sb.crm.target'].sudo()
        have = {t.user_id.id: t.amount for t in T.search([('company_id', '=', self.env.company.id), ('month', '=', m0)])}
        return {'month': _s(m0), 'label': label, 'rows': [{'user_id': u.id, 'name': u.name, 'amount': have.get(u.id, 0.0)}
                                                         for u in self._crm_users()]}

    @api.model
    def save_targets(self, month, rows):
        if not self._is_manager():
            raise AccessError(self.env._('Targets are set by a CRM manager.'))
        m0 = eth_month(fields.Date.to_date(month))[0]
        T = self.env['sb.crm.target'].sudo()
        for r in rows:
            t = T.search([('company_id', '=', self.env.company.id), ('month', '=', m0), ('user_id', '=', int(r['user_id']))])
            amt = float(r.get('amount') or 0)
            if t and not amt:
                t.unlink()
            elif t:
                t.amount = amt
            elif amt:
                T.create({'user_id': int(r['user_id']), 'month': m0, 'amount': amt})
        return self.targets(month)

    # ------------------------------------------------------------------ settings
    @api.model
    def save_pipeline(self, vals):
        if not self._is_manager():
            raise AccessError(self.env._('Pipelines are set by a CRM manager.'))
        Pipe = self.env['sb.crm.pipeline'].sudo()
        St = self.env['sb.crm.stage'].sudo()
        p = Pipe.browse(int(vals['id'])) if vals.get('id') else Pipe.create({'name': vals['name']})
        p.name = vals['name']
        keep = []
        for i, s in enumerate(vals.get('stages') or []):
            w = {'pipeline_id': p.id, 'name': s['name'], 'sequence': i, 'probability': float(s.get('probability') or 0),
                 'kind': s.get('kind') or 'open', 'rot_days': int(s.get('rot') or 14)}
            st = St.browse(int(s['id'])) if s.get('id') else St.create(w)
            st.write(w)
            keep.append(st.id)
        gone = p.stage_ids.filtered(lambda s: s.id not in keep)
        if self.env['sb.deal'].sudo().with_context(active_test=False).search_count([('stage_id', 'in', gone.ids)]):
            raise UserError(self.env._('Move the deals out of a stage before removing it.'))
        gone.unlink()
        if not p.stage_ids.filtered(lambda s: s.kind == 'won') or not p.stage_ids.filtered(lambda s: s.kind == 'lost'):
            raise UserError(self.env._('A pipeline needs a Won and a Lost stage.'))
        return self.ctx()

    @api.model
    def team(self):
        """Logins and their CRM access (manager only)."""
        if not self._is_manager():
            raise AccessError(self.env._('Only a CRM manager sets access.'))
        u_grp, m_grp = self.env.ref('sonador_crm.group_crm_user'), self.env.ref('sonador_crm.group_crm_manager')
        users = self.env['res.users'].sudo().search([('share', '=', False), ('active', '=', True)], order='name')
        owners = Counter(self._P().search([('sb_owner_id', '!=', False)]).mapped('sb_owner_id.id'))
        return [{'id': u.id, 'name': u.name, 'login': u.login, 'department': u.employee_ids[:1].department_id.name or '',
                 'crm': u_grp in u.all_group_ids, 'manager': m_grp in u.all_group_ids, 'contacts': owners.get(u.id, 0),
                 'via_sales': self.env.ref('sonador_sales.group_sb_sales_user') in u.all_group_ids}
                for u in users if u.employee_ids or owners.get(u.id)]

    @api.model
    def set_access(self, user_id, level):
        """level: none | user | manager."""
        if not self._is_manager():
            raise AccessError(self.env._('Only a CRM manager sets access.'))
        u = self.env['res.users'].sudo().browse(int(user_id))
        u_grp, m_grp = self.env.ref('sonador_crm.group_crm_user'), self.env.ref('sonador_crm.group_crm_manager')
        if level == 'manager':
            u.group_ids = [(4, m_grp.id), (4, u_grp.id)]
        elif level == 'user':
            u.group_ids = [(3, m_grp.id), (4, u_grp.id)]
        else:
            u.group_ids = [(3, m_grp.id), (3, u_grp.id)]
        return self.team()

    @api.model
    def save_tag(self, name):
        self._require_sales()
        name = (name or '').strip()
        T = self.env['sb.crm.tag'].sudo()
        t = T.search([('name', '=ilike', name)], limit=1) or T.create({'name': name})
        return {'id': t.id, 'name': t.name}
