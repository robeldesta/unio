"""Who gets told what, when, and where: one list of rules, no hidden settings.

Events come from the approval center (an item starts waiting, is approved or sent back, waits too long), from a daily
digest, from the controller's findings and from monthly plans not handed in. Channels are a registry too: Me's own
inbox and email ship here; a chat bot module adds its own channel with selection_add and a _send_<channel> method."""
import logging
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)

EVENTS = [('waiting', 'Document waiting for approval'), ('approved', 'Approved'), ('sent_back', 'Sent back'),
          ('overdue', 'Overdue (waiting too long)'), ('digest', 'Daily digest'), ('finding', 'Controller finding'),
          ('plan_due', 'Monthly plan due')]
RECIPIENTS = [('approver', 'The approver (who must act)'), ('submitter', 'The submitter (the person concerned)'),
              ('group', 'Everyone with a role'), ('users', 'Specific people')]
# events that happen at a moment; the others run once a day at the rule's time
INSTANT = ('waiting', 'approved', 'sent_back', 'finding')
# kinds where finance posts the document: "GRV/03188 was posted by Zufan Demissie." rather than "Approved"
POSTED_KINDS = ('store', 'autobill', 'voucher')


def _tz_list(self):
    return [(tz, tz) for tz in pytz.common_timezones]


class SbNotifyRule(models.Model):
    _name = 'sb.notify.rule'
    _description = 'Notification rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    event = fields.Selection(EVENTS, required=True, default='waiting')
    kinds = fields.Char('Only for', help='Comma-separated sources, empty for all: leave, overtime, plan, po (purchase), fuel, '
                                         'perdiem, hours, gate, and any other app that adds approvals to Me.')
    recipients = fields.Selection(RECIPIENTS, required=True, default='approver')
    group_id = fields.Many2one('res.groups', 'Role')
    user_ids = fields.Many2many('res.users', string='People', domain=[('share', '=', False)])
    channel = fields.Selection([('inbox', 'Me inbox'), ('email', 'Email')], required=True, default='inbox')
    timing = fields.Selection([('instant', 'At the moment it happens'), ('daily', 'Once a day at a set time')],
                              compute='_compute_timing', store=True, readonly=False, required=True, precompute=True)
    skip_brought_over = fields.Boolean('Leave out brought-over items', default=True,
                                       help='Overdue reminders leave out items brought over from the earlier system '
                                            '(their backlog is cleared separately).')
    hour = fields.Float('Time of day', default=8.0, help='For daily rules (digest, overdue, plan due): local time, e.g. 8.5 = 08:30.')
    tz = fields.Selection(_tz_list, 'Time zone', default=lambda s: s.env.user.tz or 'Africa/Addis_Ababa')
    days = fields.Integer('Days', default=2, help='Overdue: waiting longer than this many days. Plan due: from this day of the month on.')
    last_run = fields.Datetime(readonly=True)
    sent_count = fields.Integer(compute='_compute_sent_count', string='Sent')

    @api.depends('event')
    def _compute_timing(self):
        for r in self:
            r.timing = 'instant' if r.event in INSTANT else 'daily'

    def _compute_sent_count(self):
        data = dict(self.env['sb.notify.sent'].sudo()._read_group([('rule_id', 'in', self.ids)], ['rule_id'], ['__count']))
        for r in self:
            r.sent_count = data.get(r, 0)

    def _kinds(self):
        return [k.strip() for k in (self.kinds or '').split(',') if k.strip()]

    def _match(self, kind):
        ks = self._kinds()
        return not ks or kind in ks

    # ------------------------------------------------------------------ who
    def _fixed_users(self):
        if self.recipients == 'group':
            return self.group_id.sudo().user_ids.filtered(lambda u: u.active and not u.share) if self.group_id else self.env['res.users']
        if self.recipients == 'users':
            return self.user_ids.filtered('active')
        return self.env['res.users']

    def _approver_pool(self):
        """People who may have something waiting: internal, active users of the rule's company."""
        return self.env['res.users'].sudo().search([('share', '=', False), ('active', '=', True),
                                                    ('company_ids', 'in', self.company_id.id)])

    # ------------------------------------------------------------------ sending
    def _send(self, users, title, body, key=False, item=None, dedupe=None):
        """Send one message to each user once (dedupe: a string that makes the message unique per user)."""
        Sent = self.env['sb.notify.sent'].sudo()
        sender = getattr(self, '_send_%s' % self.channel, None)
        if not sender:
            return 0
        n = 0
        for u in users:
            if dedupe and Sent.search_count([('rule_id', '=', self.id), ('user_id', '=', u.id), ('dedupe', '=', dedupe)]):
                continue
            try:
                with self.env.cr.savepoint():
                    sender(u, title, body, key=key, item=item)
                    Sent.create({'rule_id': self.id, 'user_id': u.id, 'dedupe': dedupe or False, 'key': key or False})
                    n += 1
            except Exception:
                _logger.exception('Notification "%s" to user %s failed', self.name, u.id)
        return n

    def _send_inbox(self, user, title, body, key=False, item=None):
        item = item or {}
        self.env['sb.me.notice'].sudo().create({'user_id': user.id, 'title': title, 'body': body, 'key': key or False,
                                                'model': item.get('model') or False, 'res_id': item.get('res_id') or 0,
                                                'event': self.event})

    def _send_email(self, user, title, body, key=False, item=None):
        if not user.email:
            return
        html = '<p>%s</p>' % '<br/>'.join(html_escape(l) for l in (body or '').split('\n'))
        self.env['mail.mail'].sudo().create({'subject': title, 'body_html': html, 'email_to': user.email,
                                             'email_from': self.company_id.email or self.env.company.email or False,
                                             'auto_delete': True}).send()

    # ------------------------------------------------------------------ events
    @api.model
    def _event_decided(self, log):
        """An item was approved or sent back (from Me or the chat bot)."""
        event = log.decision
        # once decided, the same document waiting again (sent back, then sent in again) is news again
        self.env['sb.notify.sent'].sudo().search([('dedupe', '=', 'wait:%s' % log.key)]).unlink()
        for r in self.sudo().search([('event', '=', event), ('company_id', '=', log.company_id.id or self.env.company.id)]):
            if not r._match(log.kind):
                continue
            users = log.submitter_id if r.recipients in ('submitter', 'approver') else r._fixed_users()
            users = users.filtered(lambda u: u.active)
            if r.recipients == 'submitter' and log.submitter_id == log.user_id:
                continue
            if r.recipients in ('group', 'users') and event == 'approved' and log.next_step:
                continue   # a role or named people hear of the final approval, not of every step
            verb = 'Approved' if event == 'approved' else 'Sent back'
            body = '%s by %s.%s' % (verb, log.user_id.name, ('\nReason: %s' % log.note) if log.note else '')
            head = verb
            if event == 'approved' and log.next_step:
                # one step of several: say so, and who has it now (not "Approved", which reads as done)
                head = 'Step approved'
                body = 'Approved by %s. Now waiting for %s.' % (log.user_id.name, log.next_step)
            elif event == 'approved':
                body = 'Approved by %s. Fully approved.' % log.user_id.name
                if r.recipients in ('group', 'users') and log.submitter_id:
                    body += '\nRequested by %s.%s' % (log.submitter_id.name, ('\nAmount: {:,.2f} {}'.format(log.amount, (log.company_id or self.env.company).currency_id.symbol or '')) if log.amount else '')
            title = log.title or ''
            if event == 'approved' and log.kind in POSTED_KINDS:
                doc = self.env[log.model].sudo().browse(log.res_id).exists().display_name if log.model in self.env else ''
                doc = doc or title
                head, title, body = 'Posted', doc, '%s was posted by %s.' % (doc, log.user_id.name)
            r._send(users, '%s: %s' % (head, title), body, key=log.key,
                    item={'model': log.model, 'res_id': log.res_id, 'key': log.key}, dedupe='log:%d' % log.id)

    def _items_for(self, user):
        me = self.env['sb.me'].with_user(user).with_company(self.company_id)
        try:
            with self.env.cr.savepoint():
                return [i for i in me.approvals() if self._match(i.get('kind'))]
        except Exception:
            _logger.exception('Approvals of user %s could not be read', user.id)
            return []

    def _run_waiting(self):
        for u in self._approver_pool():
            for it in self._items_for(u):
                body = '\n'.join([x for x in [it.get('title')] + (it.get('details') or [])[:4] if x])
                if it.get('amount'):
                    body += '\nAmount: {:,.2f} {}'.format(it['amount'], it.get('currency') or '')
                self._send(u, '%s waiting for you' % (it.get('app') or 'Approval'), body, key=it['key'], item=it,
                           dedupe='wait:%s' % it['key'])

    def _brought_over(self, item):
        """A document copied from the earlier system (its backlog is not chased)."""
        Model = self.env.get(item.get('model') or '')
        if Model is None or not item.get('res_id'):
            return False
        rec = Model.sudo().browse(item['res_id']).exists()
        return bool(rec) and any(f in Model._fields and rec[f] for f in ('sb_migrated', 'sb_import_ref', 'sb_legacy_ref'))

    def _run_overdue(self, today):
        """Each late item to a person who can decide it, one message per item with its Approve / Send back (signed where
        the step asks): a list of things nobody on it can act on is noise. A recipient list (group / users) gets only the
        late items they can decide themselves."""
        users = self._fixed_users() if self.recipients in ('group', 'users') else self._approver_pool()
        for u in users:
            late = [i for i in self._items_for(u) if (i.get('days') or 0) > self.days
                    and not (self.skip_brought_over and self._brought_over(i))]
            for i in late[:20]:
                body = '\n'.join([x for x in [i.get('title')] + (i.get('details') or [])[:3] if x])
                if i.get('amount'):
                    body += '\nAmount: {:,.2f} {}'.format(i['amount'], i.get('currency') or '')
                self._send(u, '%s waiting %s days' % (i.get('app') or 'Approval', i['days']), body, key=i['key'], item=i,
                           dedupe='overdue:%s:w%s' % (i['key'], today.isocalendar()[1]))  # at most once a week per item

    def _run_digest(self, today):
        users = self._fixed_users() if self.recipients in ('group', 'users') else self._approver_pool()
        for u in users:
            items = self._items_for(u)
            mine = []
            if self.recipients in ('submitter', 'group', 'users'):
                try:
                    with self.env.cr.savepoint():
                        mine = self.env['sb.me'].with_user(u).with_company(self.company_id).submitted()
                except Exception:
                    mine = []
            if not items and not mine:
                continue
            first = (u.name or '').split(' ')[0]
            lines = ['Good morning %s.' % first]
            if items:
                oldest = max((i.get('days') or 0) for i in items)
                lines.append('%s waiting for your approval%s.' % (
                    'One thing is' if len(items) == 1 else '%d things are' % len(items),
                    ', the oldest for %d days' % oldest if oldest > 1 else ''))
                lines += ['· %s (%s%s)' % (self._item_label(i), i.get('app'), ', %s days' % i['days'] if i.get('days') else '') for i in items[:15]]
                if len(items) > 15:
                    lines.append('and %d more in Me.' % (len(items) - 15))
            if mine:
                lines.append('')
                lines.append('Still with others, from you (%d):' % len(mine))
                lines += ['· %s: with %s' % (i['title'], i.get('waiting_for') or '-') for i in mine[:10]]
            title = ('%d waiting for you today' % len(items)) if items else 'Your requests still waiting'
            self._send(u, title, '\n'.join(lines), item={'digest': items, 'mine': mine}, dedupe='digest:%s' % today)

    @api.model
    def _item_label(self, i):
        """An item as a person names it: its number first (a brought-over store document by its earlier-system number)."""
        title = i.get('title') or ''
        ref = next((d[5:].strip() for d in (i.get('details') or []) if d.startswith('Ref. ')), '')
        if ref:
            ref = ref.split('|')[-1]
            doc = i.get('doc') or ''
            if doc and title.startswith(doc):
                return ref + title[len(doc):]
        return title

    def _run_findings(self):
        if 'ctl.finding' not in self.env:
            return
        F = self.env['ctl.finding'].sudo()
        since = self.last_run or (fields.Datetime.now() - timedelta(days=1))
        for f in F.search([('company_id', '=', self.company_id.id), ('state', '=', 'open'), ('create_date', '>', since)], limit=50):
            users = (f.user_id if self.recipients == 'approver' and f.user_id else self._fixed_users())
            body = '%s\n%s' % (f.summary or '', f.advice or '')
            self._send(users, 'Controller finding: %s' % f.name, body.strip(), item={'model': 'ctl.finding', 'res_id': f.id},
                       dedupe='finding:%d' % f.id)

    def _run_plan_due(self, today):
        if 'sb.plan' not in self.env or today.day < (self.days or 1):
            return
        month = today.replace(day=1)
        done = self.env['sb.plan'].sudo().search([('month', '=', month), ('state', 'in', ('submitted', 'approved'))]).employee_id
        emps = self.env['hr.employee'].sudo().search([('company_id', '=', self.company_id.id), ('user_id', '!=', False),
                                                      ('id', 'not in', done.ids)])
        users = emps.user_id if self.recipients in ('submitter', 'approver') else self._fixed_users()
        if self.recipients in ('group', 'users'):
            body = '\n'.join('· %s' % e.name for e in emps[:60])
            self._send(users, '%d people have not handed in this month\'s plan' % len(emps), body, dedupe='plan:%s' % today)
        else:
            self._send(users, 'Your plan for this month', 'Your plan for this month is not handed in yet. Open Planning to send it.',
                       dedupe='plan:%s' % month)

    # ------------------------------------------------------------------ the clock
    def _local_now(self):
        return datetime.now(pytz.timezone(self.tz or 'UTC'))

    def _due_today(self):
        """A daily rule runs once a day, at or after its time."""
        now = self._local_now()
        if now.hour + now.minute / 60.0 < (self.hour or 0):
            return False
        if self.last_run:
            last = pytz.utc.localize(self.last_run).astimezone(now.tzinfo)
            if last.date() == now.date() and last.hour + last.minute / 60.0 >= (self.hour or 0):
                return False
        return now.date()

    @api.model
    def _cron_run(self):
        for r in self.sudo().search([]):
            try:
                with self.env.cr.savepoint():
                    if r.event == 'waiting':
                        r._run_waiting()
                    elif r.event == 'finding':
                        r._run_findings()
                    elif r.event in ('overdue', 'digest', 'plan_due'):
                        today = r._due_today()
                        if not today:
                            continue
                        getattr(r, '_run_%s' % r.event)(today)
                    else:
                        continue
                    r.last_run = fields.Datetime.now()
            except Exception:
                _logger.exception('Notification rule %s failed', r.id)

    def action_run_now(self):
        for r in self:
            if r.event in ('overdue', 'digest', 'plan_due'):
                getattr(r, '_run_%s' % r.event)(r._local_now().date())
            elif r.event == 'waiting':
                r._run_waiting()
            elif r.event == 'finding':
                r._run_findings()
            r.last_run = fields.Datetime.now()
        return True


class SbNotifySent(models.Model):
    _name = 'sb.notify.sent'
    _description = 'Notification sent'
    _order = 'id desc'

    rule_id = fields.Many2one('sb.notify.rule', required=True, index=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='cascade')
    dedupe = fields.Char(index=True)
    key = fields.Char()
    date = fields.Datetime(default=fields.Datetime.now)

    @api.autovacuum
    def _gc_old(self):
        self.search([('date', '<', fields.Datetime.now() - timedelta(days=120))]).unlink()
