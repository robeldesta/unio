"""Onboarding screens and the hooks that start a checklist when People hires someone or records a leaving."""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .journey import ROLES


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbPeopleJourneyHooks(models.AbstractModel):
    _inherit = 'sb.people'

    @api.model
    def hire(self, vals):
        eid = super().hire(vals)
        self.env['sb.onboarding'].sudo().start(eid, 'onboarding')
        return eid

    @api.model
    def depart(self, employee_id, vals):
        res = super().depart(employee_id, vals)
        self.env['sb.onboarding'].sudo().start(int(employee_id), 'offboarding', vals.get('date'))
        return res


class SbOnboarding(models.AbstractModel):
    _name = 'sb.onboarding'
    _description = 'Onboarding and offboarding'

    def _P(self):
        return self.env['sb.people']

    def _is_hr(self):
        return self._P()._is_hr()

    def _me(self):
        return self._P()._me()

    def _E(self):
        return self.env['hr.employee'].sudo().with_context(active_test=False)

    # ------------------------------------------------------------------ start
    def _template(self, e, kind):
        T = self.env['sb.journey.template'].sudo().search([('kind', '=', kind), ('company_id', 'in', (e.company_id.id, False))])
        scored = []
        for t in T:
            if t.department_ids and e.department_id not in t.department_ids:
                continue
            if t.job_ids and e.job_id not in t.job_ids:
                continue
            scored.append((bool(t.job_ids) * 2 + bool(t.department_ids), -t.sequence, t))
        return max(scored, key=lambda x: (x[0], x[1]))[2] if scored else T[:1]

    def _owner(self, e, role):
        c = e.company_id
        return {'manager': e.parent_id, 'employee': e, 'it': c.sb_owner_it_id, 'finance': c.sb_owner_finance_id,
                'store': c.sb_owner_store_id, 'hr': c.sb_owner_hr_id}.get(role) or self._E().browse()

    @api.model
    def start(self, employee_id, kind, day=None):
        e = self._E().browse(int(employee_id))
        J = self.env['sb.journey'].sudo()
        if J.search_count([('employee_id', '=', e.id), ('kind', '=', kind), ('state', '=', 'open')]):
            return False
        if day:
            day = fields.Date.to_date(day)
        else:
            day = (e.current_version_id.contract_date_start if kind == 'onboarding' else e.departure_date) or fields.Date.context_today(self)
        t = self._template(e, kind)
        tasks = [(0, 0, {'name': x.name, 'description': x.description, 'role': x.role, 'owner_id': self._owner(e, x.role).id or False,
                         'due': day + timedelta(days=x.offset), 'action': x.action, 'legal_ref': x.legal_ref, 'sequence': x.sequence})
                 for x in t.task_ids]
        if kind == 'offboarding':
            for a in e.sb_asset_ids.filtered(lambda a: not a.returned_on):
                tasks.append((0, 0, {'name': 'Return %s %s' % (a.name, a.serial or ''), 'role': 'store', 'owner_id': self._owner(e, 'store').id or False,
                                     'due': day, 'action': 'assets', 'asset_id': a.id}))
            for lo in e.sb_loan_ids.filtered(lambda l: l.state == 'open' and l.remaining > 0):
                tasks.append((0, 0, {'name': 'Clear %s: %s outstanding' % ('loan' if lo.kind == 'loan' else 'salary advance', '{:,.2f}'.format(lo.remaining)),
                                     'role': 'finance', 'owner_id': self._owner(e, 'finance').id or False, 'due': day, 'action': 'loans', 'loan_id': lo.id}))
        j = J.create({'employee_id': e.id, 'kind': kind, 'template_id': t.id or False, 'date': day, 'task_ids': tasks})
        e.message_post(body=self.env._('%(k)s started: %(n)s tasks.', k=dict(J._fields['kind'].selection)[kind], n=len(tasks)))
        return j.id

    # ------------------------------------------------------------------ access
    def _can_do(self, task):
        if self._is_hr():
            return True
        me = self._me()
        if not me:
            return False
        if task.owner_id == me:
            return True
        return task.role == 'manager' and task.journey_id.employee_id.parent_id == me

    def _task_dict(self, t):
        today = fields.Date.context_today(self)
        return {'id': t.id, 'name': t.name, 'description': t.description or '', 'role': t.role, 'role_label': dict(ROLES).get(t.role),
                'owner': t.owner_id.name or '', 'due': _s(t.due), 'due_ec': format_ethiopian(t.due) if t.due else '',
                'overdue': bool(t.due and t.due < today and not t.done), 'done': t.done, 'done_on': _s(t.done_on),
                'done_by': t.done_by.name or '', 'note': t.note or '', 'action': t.action or '', 'legal_ref': t.legal_ref or '',
                'can_do': self._can_do(t), 'employee': self._P()._card(t.employee_id), 'journey_id': t.journey_id.id}

    def _journey_dict(self, j, full=False):
        today = fields.Date.context_today(self)
        d = {'id': j.id, 'kind': j.kind, 'employee': self._P()._card(j.employee_id), 'date': _s(j.date), 'date_ec': format_ethiopian(j.date),
             'state': j.state, 'progress': j.progress, 'template': j.template_id.name or '', 'tasks_total': len(j.task_ids),
             'tasks_done': len(j.task_ids.filtered('done')), 'overdue': len(j.task_ids.filtered(lambda t: not t.done and t.due and t.due < today))}
        if full:
            d['tasks'] = [self._task_dict(t) for t in j.task_ids]
            d['interview'] = {'exit_reason': j.exit_reason or '', 'exit_rating': j.exit_rating or '', 'exit_feedback': j.exit_feedback or '',
                              'rehire': j.rehire or ''}
            d['can_hr'] = self._is_hr()
            d['policies'] = self._policy_status(j.employee_id)
        return d

    # ------------------------------------------------------------------ screens
    @api.model
    def ctx(self):
        me = self._me()
        c = self.env.company
        return {'is_hr': self._is_hr(), 'me': me.id or False, 'roles': [{'id': k, 'name': v} for k, v in ROLES],
                'owners': {'it': c.sb_owner_it_id.id, 'finance': c.sb_owner_finance_id.id, 'store': c.sb_owner_store_id.id, 'hr': c.sb_owner_hr_id.id},
                'people': [{'id': e.id, 'name': e.name} for e in self._E().search([('company_id', '=', c.id), ('active', '=', True)], order='name')]}

    @api.model
    def overview(self, kind=None, state='open'):
        J = self.env['sb.journey'].sudo()
        dom = [('company_id', '=', self.env.company.id)]
        if state:
            dom.append(('state', '=', state))
        if kind:
            dom.append(('kind', '=', kind))
        if not self._is_hr():
            me = self._me()
            dom.append(('task_ids.owner_id', '=', me.id or -1))
        return [self._journey_dict(j) for j in J.search(dom, limit=200)]

    @api.model
    def journey(self, journey_id):
        j = self.env['sb.journey'].sudo().browse(int(journey_id))
        me = self._me()
        if not (self._is_hr() or j.employee_id == me or me in j.task_ids.owner_id or j.employee_id.parent_id == me):
            raise AccessError(self.env._('Not allowed.'))
        return self._journey_dict(j, True)

    @api.model
    def my_tasks(self):
        me = self._me()
        T = self.env['sb.journey.task'].sudo()
        dom = [('done', '=', False), ('journey_id.state', '=', 'open')]
        if me:
            dom += ['|', ('owner_id', '=', me.id), '&', ('role', '=', 'manager'), ('employee_id.parent_id', '=', me.id)]
        elif not self._is_hr():
            return []
        else:
            dom.append(('role', '=', 'hr'))
        return [self._task_dict(t) for t in T.search(dom, order='due', limit=200)]

    @api.model
    def task_done(self, task_id, done=True, note=''):
        t = self.env['sb.journey.task'].sudo().browse(int(task_id))
        if not self._can_do(t):
            raise AccessError(self.env._('This task is for %s.', t.owner_id.name or dict(ROLES).get(t.role)))
        if done:
            self._run_action(t)
        t.write({'done': bool(done), 'done_on': fields.Date.context_today(self) if done else False,
                 'done_by': self.env.user.id if done else False, 'note': note or t.note})
        j = t.journey_id
        if all(j.task_ids.mapped('done')):
            j.state = 'done'
        elif j.state == 'done':
            j.state = 'open'
        return self._journey_dict(j, True)

    def _run_action(self, t):
        e = t.employee_id
        kind = t.journey_id.kind
        if t.action == 'probation':
            self._P().confirm_probation(e.id)
        elif t.action == 'login':
            if kind == 'onboarding' and not e.user_id:
                self._P().with_company(e.company_id).create_logins()
            elif kind == 'offboarding' and e.user_id and e.user_id.active and e.user_id != self.env.ref('base.user_admin'):
                e.user_id.sudo().active = False
        elif t.action == 'assets' and t.asset_id and kind == 'offboarding' and not t.asset_id.returned_on:
            t.asset_id.returned_on = fields.Date.context_today(self)
        elif t.action == 'loans' and t.loan_id and t.loan_id.remaining > 0.005:
            raise UserError(self.env._('%(s)s still outstanding: deduct it in the final settlement or record the repayment first.',
                                       s='{:,.2f}'.format(t.loan_id.remaining)))
        elif t.action == 'policies':
            missing = [p for p in self._policy_status(e) if not p['acked']]
            if missing:
                raise UserError(self.env._('Not yet acknowledged: %s', ', '.join(p['name'] for p in missing)))

    @api.model
    def interview_save(self, journey_id, vals):
        if not self._is_hr():
            raise AccessError(self.env._('Exit interviews are recorded by HR.'))
        j = self.env['sb.journey'].sudo().browse(int(journey_id))
        j.write({k: vals.get(k) or False for k in ('exit_reason', 'exit_rating', 'exit_feedback', 'rehire')})
        return self._journey_dict(j, True)

    @api.model
    def start_manual(self, employee_id, kind, day=None):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        jid = self.start(int(employee_id), kind, day)
        if not jid:
            raise UserError(self.env._('There is already an open %s for this person.', kind))
        return jid

    # ------------------------------------------------------------------ policies
    def _policy_status(self, e):
        out = []
        for p in self.env['sb.policy'].sudo().search([('company_id', 'in', (e.company_id.id, False))]):
            ack = p.ack_ids.filtered(lambda a: a.employee_id == e and a.version == p.version)[:1]
            out.append({'id': p.id, 'name': p.name, 'version': p.version, 'acked': bool(ack),
                        'date': fields.Datetime.to_string(ack.date) if ack else False})
        return out

    @api.model
    def policies(self):
        me = self._me()
        rows = []
        for p in self.env['sb.policy'].sudo().search([('company_id', 'in', (self.env.company.id, False))]):
            ack = p.ack_ids.filtered(lambda a: a.employee_id == me and a.version == p.version)[:1] if me else False
            rows.append({'id': p.id, 'name': p.name, 'version': p.version, 'body': p.body or '',
                         'file': {'id': p.attachment_id.id, 'name': p.attachment_id.name} if p.attachment_id else False,
                         'acked': bool(ack), 'acked_on': fields.Datetime.to_string(ack.date) if ack else False,
                         'count': len(p.ack_ids.filtered(lambda a: a.version == p.version))})
        active = self._E().search_count([('company_id', '=', self.env.company.id), ('active', '=', True)])
        return {'policies': rows, 'headcount': active, 'is_hr': self._is_hr(), 'me': me.id or False}

    @api.model
    def acknowledge(self, policy_id):
        me = self._me()
        if not me:
            raise UserError(self.env._('Your login is not linked to an employee.'))
        p = self.env['sb.policy'].sudo().browse(int(policy_id))
        if not p.ack_ids.filtered(lambda a: a.employee_id == me and a.version == p.version):
            self.env['sb.policy.ack'].sudo().create({'policy_id': p.id, 'employee_id': me.id, 'version': p.version})
        return self.policies()

    @api.model
    def policy_save(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('Policies are published by HR.'))
        P = self.env['sb.policy'].sudo()
        data = {'name': vals['name'], 'version': vals.get('version') or '1', 'body': vals.get('body') or False}
        p = P.browse(int(vals['id'])) if vals.get('id') else P
        if p:
            p.write(data)
        else:
            p = P.create(data)
        if vals.get('file_data'):
            p.attachment_id = self.env['ir.attachment'].sudo().create({'name': vals.get('file_name') or p.name, 'datas': vals['file_data'],
                                                                       'res_model': 'sb.policy', 'res_id': p.id})
        return self.policies()

    # ------------------------------------------------------------------ templates and owners
    @api.model
    def templates(self):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        return [{'id': t.id, 'name': t.name, 'kind': t.kind, 'departments': ', '.join(t.department_ids.mapped('name')),
                 'tasks': [{'id': x.id, 'name': x.name, 'role': x.role, 'offset': x.offset, 'action': x.action or '', 'legal_ref': x.legal_ref or ''}
                           for x in t.task_ids]} for t in self.env['sb.journey.template'].sudo().search([])]

    @api.model
    def template_save(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        T = self.env['sb.journey.template'].sudo()
        t = T.browse(int(vals['id'])) if vals.get('id') else T.create({'name': vals['name'], 'kind': vals.get('kind') or 'onboarding'})
        t.name = vals['name']
        keep = self.env['sb.journey.template.task']
        for x in vals.get('tasks') or []:
            if not x.get('name'):
                continue
            xv = {'name': x['name'], 'role': x.get('role') or 'hr', 'offset': int(x.get('offset') or 0), 'action': x.get('action') or '',
                  'legal_ref': x.get('legal_ref') or False}
            rec = t.task_ids.filtered(lambda r: r.id == x.get('id'))
            if rec:
                rec.write(xv)
            else:
                rec = self.env['sb.journey.template.task'].sudo().create(dict(xv, template_id=t.id))
            keep |= rec
        (t.task_ids - keep).unlink()
        return self.templates()

    @api.model
    def owners_save(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('HR only.'))
        c = self.env.company.sudo()
        c.write({'sb_owner_%s_id' % k: int(vals[k]) if vals.get(k) else False for k in ('it', 'finance', 'store', 'hr')})
        return self.ctx()
