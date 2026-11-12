"""Support screen service: report a problem from any screen, follow it, and (IT / the admin) answer and fix it."""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .ticket import KINDS, OPEN, PRIORITY, STATES, TARGETS


class SbSupportService(models.AbstractModel):
    _name = 'sb.support.service'
    _description = 'Support'

    def _agent(self):
        return self.env.user.has_group('sonador_support.group_agent')

    def _T(self):
        return self.env['sb.support.ticket'].sudo()

    def _agents(self, company):
        return self.env.ref('sonador_support.group_agent').sudo().all_user_ids.filtered(
            lambda u: company in u.company_ids and u.active and not u.share)

    def _dict(self, t, full=False):
        h_ans, h_fix, late_ans, late_fix = t._times()
        ans, fix = TARGETS.get(t.priority, TARGETS['normal'])
        when = lambda d: fields.Datetime.context_timestamp(t, d).strftime('%d %b %H:%M') if d else ''  # noqa: E731
        d = {'id': t.id, 'number': t.name, 'subject': t.subject, 'description': t.description or '', 'kind': t.kind,
             'kind_label': dict(KINDS).get(t.kind), 'priority': t.priority, 'priority_label': dict(PRIORITY).get(t.priority),
             'state': t.state, 'state_label': dict(STATES).get(t.state), 'reporter': t.reporter_id.name,
             'mine': t.reporter_id == self.env.user, 'assignee': t.assignee_id.name or '', 'assignee_me': t.assignee_id == self.env.user,
             'screen': t.screen or '', 'url': t.url or '', 'record': t.record_label or '', 'res_model': t.res_model or '',
             'res_id': t.res_id or 0, 'created': when(t.create_date), 'resolution': t.resolution or '',
             'answer_h': h_ans, 'fix_h': h_fix, 'answer_target': ans, 'fix_target': fix,
             'late': bool((late_ans and not t.first_response_at) or (late_fix and t.state in OPEN and t.state != 'resolved')),
             'answered': bool(t.first_response_at), 'reopened': t.reopened}
        if full:
            msgs = t.message_ids.filtered(lambda m: m.message_type == 'comment' and m.body).sorted('id')
            d['messages'] = [{'author': m.author_id.name, 'when': when(m.date), 'body': str(m.body),
                              'me': m.author_id == self.env.user.partner_id} for m in msgs]
        return d

    @api.model
    def home(self):
        T = self._T()
        c = self.env.company
        mine = T.search([('company_id', '=', c.id), ('reporter_id', '=', self.env.uid)], limit=50)
        res = {'agent': self._agent(), 'kinds': KINDS, 'priorities': PRIORITY,
               'mine': [self._dict(t) for t in mine]}
        if self._agent():
            queue = T.search([('company_id', '=', c.id), ('state', 'in', OPEN)])
            done = T.search([('company_id', '=', c.id), ('state', 'in', ('closed', 'cancelled'))], limit=30)
            rows = [self._dict(t) for t in queue]
            order = {'urgent': 0, 'high': 1, 'normal': 2, 'low': 3}
            rows.sort(key=lambda r: (not r['late'], r['state'] == 'resolved', order.get(r['priority'], 9), r['id']))
            res.update(queue=rows, done=[self._dict(t) for t in done],
                       counts={'new': len(queue.filtered(lambda t: t.state == 'new')), 'late': len([r for r in rows if r['late']]),
                               'waiting': len(queue.filtered(lambda t: t.state == 'waiting')), 'open': len(queue)},
                       agents=[{'id': u.id, 'name': u.name} for u in self._agents(c)])
        return res

    def _get(self, ticket_id):
        t = self._T().browse(int(ticket_id)).exists()
        if not t or (t.reporter_id != self.env.user and not self._agent()) or t.company_id not in self.env.user.company_ids:
            raise AccessError(self.env._('This problem is not yours to see.'))
        return t

    @api.model
    def ticket(self, ticket_id):
        return self._dict(self._get(ticket_id), full=True)

    @api.model
    def report(self, vals):
        """A problem reported from a screen: subject, what happened, kind, priority; the screen comes with it."""
        subject = (vals.get('subject') or '').strip()
        if not subject:
            raise UserError(self.env._('Say in a few words what the problem is.'))
        rec_label = ''
        if vals.get('res_model') and vals.get('res_id') and vals['res_model'] in self.env:
            r = self.env[vals['res_model']].sudo().browse(int(vals['res_id'])).exists()
            rec_label = r.display_name if r else ''
        t = self._T().create({
            'subject': subject[:200], 'description': (vals.get('description') or '').strip(), 'kind': vals.get('kind') or 'broken',
            'priority': vals.get('priority') or 'normal', 'company_id': self.env.company.id, 'reporter_id': self.env.uid,
            'url': (vals.get('url') or '')[:500], 'screen': (vals.get('screen') or '')[:200], 'res_model': vals.get('res_model') or False,
            'res_id': int(vals.get('res_id') or 0), 'record_label': rec_label, 'source': vals.get('source') or 'screen'})
        t.message_subscribe(partner_ids=self.env.user.partner_id.ids)
        agents = self._agents(t.company_id) - self.env.user
        t.message_post(body=Markup('<p><b>%s</b> reported a problem (%s): %s</p>%s%s') % (
            self.env.user.name, dict(PRIORITY)[t.priority].split(':')[0].lower(), t.subject,
            Markup('<p>%s</p>') % t.description if t.description else '',
            Markup('<p>Screen: %s%s</p>') % (t.screen or t.url or '', (' · ' + rec_label) if rec_label else '') if (t.screen or t.url) else ''),
            partner_ids=agents.partner_id.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        self.env['sb.support.ticket']._sb_notify(t, 'new')
        return self._dict(t, full=True)

    @api.model
    def act(self, ticket_id, action, note=''):
        """take, progress, waiting, resolve (note = what was done), close (reporter confirms), reopen (reporter), cancel,
        assign (note = user id), priority (note = new priority)."""
        t = self._get(ticket_id)
        agent, me = self._agent(), self.env.user
        now = fields.Datetime.now()
        note = (note or '').strip() if isinstance(note, str) else note
        if action in ('take', 'progress', 'waiting', 'resolve', 'assign', 'priority', 'cancel') and not agent:
            if not (action == 'cancel' and t.reporter_id == me and t.state == 'new'):
                raise AccessError(self.env._('Only IT / the admin can do this.'))
        if action in ('close', 'reopen') and t.reporter_id != me and not agent:
            raise AccessError(self.env._('Only the person who reported it confirms it.'))
        vals, say = {}, ''
        if action == 'take':
            vals = {'assignee_id': me.id, 'state': 'progress'}
            say = '%s is working on it.' % me.name
        elif action == 'assign':
            u = self.env['res.users'].sudo().browse(int(note or 0)).exists()
            if not u:
                raise UserError(self.env._('Choose who takes it.'))
            vals = {'assignee_id': u.id, 'state': 'progress' if t.state == 'new' else t.state}
            say = 'Given to %s.' % u.name
        elif action == 'progress':
            vals, say = {'state': 'progress'}, 'Being worked on again.'
        elif action == 'waiting':
            if not note:
                raise UserError(self.env._('Say what you need from them.'))
            vals, say = {'state': 'waiting'}, 'Waiting for %s: %s' % (t.reporter_id.name, note)
        elif action == 'resolve':
            if not note:
                raise UserError(self.env._('Say what was done, so they can check it.'))
            vals = {'state': 'resolved', 'resolution': note, 'resolved_at': now, 'assignee_id': t.assignee_id.id or me.id}
            say = 'Fixed: %s. %s, please check and confirm.' % (note, t.reporter_id.name)
        elif action == 'close':
            vals, say = {'state': 'closed', 'closed_at': now}, '%s confirmed it is fixed.' % me.name
        elif action == 'reopen':
            vals = {'state': 'progress' if t.assignee_id else 'new', 'resolved_at': False, 'reopened': t.reopened + 1}
            say = '%s says it is not fixed%s' % (me.name, (': ' + note) if note else '.')
        elif action == 'cancel':
            vals, say = {'state': 'cancelled', 'closed_at': now}, 'Cancelled%s' % ((': ' + note) if note else '.')
        elif action == 'priority':
            if note not in dict(PRIORITY):
                raise UserError(self.env._('Unknown priority.'))
            vals, say = {'priority': note}, 'Priority: %s.' % dict(PRIORITY)[note].split(':')[0]
        else:
            raise UserError(self.env._('Unknown action.'))
        if agent and t.reporter_id != me and not t.first_response_at and action in ('take', 'assign', 'waiting', 'resolve', 'progress'):
            vals['first_response_at'] = now
        t.write(vals)
        to = (t.reporter_id | t.assignee_id) - me
        t.message_post(body=Markup('<p>%s</p>') % say, partner_ids=to.partner_id.ids, message_type='comment',
                       subtype_xmlid='mail.mt_comment')
        self.env['sb.support.ticket']._sb_notify(t, action)
        return self._dict(t, full=True)

    @api.model
    def say(self, ticket_id, body):
        """A message in the problem's conversation (both sides)."""
        t = self._get(ticket_id)
        body = (body or '').strip()
        if not body:
            return self._dict(t, full=True)
        me = self.env.user
        if self._agent() and t.reporter_id != me and not t.first_response_at:
            t.first_response_at = fields.Datetime.now()
        if t.state == 'waiting' and t.reporter_id == me:
            t.state = 'progress' if t.assignee_id else 'new'
        to = ((t.reporter_id | t.assignee_id) - me) or (self._agents(t.company_id) - me)
        t.message_post(body=Markup('<p>%s</p>') % body, partner_ids=to.partner_id.ids, message_type='comment',
                       subtype_xmlid='mail.mt_comment')
        self.env['sb.support.ticket']._sb_notify(t, 'say')
        return self._dict(t, full=True)


class SbSupportTicket(models.Model):
    _inherit = 'sb.support.ticket'

    @api.model
    def _sb_notify(self, ticket, event):
        """Hook for other channels (e.g. Telegram); nothing by default."""
        return True
