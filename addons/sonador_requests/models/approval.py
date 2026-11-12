"""Approval steps shared by every request: department head -> finance check -> general manager, as the company's rules say.

A model that inherits sb.request.mixin gets:
  action_submit()               draft -> the first step that applies
  action_approve()              the current step is approved by the person allowed to (next step, or approved)
  action_send_back(reason)      back to draft with the reason (the requester fixes it and submits again)
  action_cancel(reason)         cancelled with the reason
  sb_waiting_domain(user)       domain of what waits for this user's decision; sb_waiting_for_user(user) the records
  sb_waiting_for                who it waits for now, in words
Every step is written in the chatter with who and when. Steps and the amount above which the general manager approves
are set per company and kind of request (sb.request.policy). Hooks for the models: _sb_kind(), _sb_on_approved(),
_sb_check_submit(), _sb_check_approve(step)."""
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

STEPS = ('dept', 'finance', 'gm')
STATES = [('draft', 'Draft'), ('dept', 'Department approval'), ('finance', 'Finance check'),
          ('gm', 'General manager approval'), ('approved', 'Approved'), ('cancel', 'Cancelled')]
KINDS = [('payment', 'Payment request'), ('petty', 'Petty cash request'), ('perdiem', 'Driver per diem'),
         ('purchase', 'Purchase request')]
STEP_NAMES = {'dept': 'department approval', 'finance': 'finance check', 'gm': 'general manager approval'}


class SbRequestPolicy(models.Model):
    _name = 'sb.request.policy'
    _description = 'Request approval rule'
    _order = 'company_id, kind'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, ondelete='cascade')
    currency_id = fields.Many2one(related='company_id.currency_id')
    kind = fields.Selection(KINDS, required=True)
    dept_step = fields.Boolean('Department head approves', default=True,
                               help='Skipped when the requester is their own department head.')
    finance_step = fields.Boolean('Finance checks', default=True,
                                  help='Finance checks the request is valid and codes the accounts it is paid to.')
    gm_step = fields.Selection([('always', 'Always'), ('above', 'Above an amount'), ('never', 'Never')],
                               'General manager approves', default='always', required=True)
    gm_above = fields.Monetary('Above', currency_field='currency_id',
                               help='Requests above this amount need the general manager\'s final approval.')
    gm_delegate_id = fields.Many2one('res.users', 'Final approval up to a limit by',
                                     help='Small requests get their final approval from this person instead of the general '
                                          'manager (e.g. petty cash up to 2,000 by HR).')
    gm_delegate_limit = fields.Monetary('Up to', currency_field='currency_id')

    _kind_unique = models.Constraint('unique(company_id, kind)', 'One rule per kind of request and company.')

    @api.model
    def _for(self, kind, company=None):
        company = company or self.env.company
        rule = self.sudo().search([('company_id', '=', company.id), ('kind', '=', kind)], limit=1)
        if not rule:
            vals = {'company_id': company.id, 'kind': kind}
            if kind == 'petty':
                vals.update(dept_step=False, gm_step='never')
            if kind == 'perdiem':
                vals.update(dept_step=False)
            rule = self.sudo().create(vals)
        return rule

    def _needs(self, step, amount):
        self.ensure_one()
        if step == 'dept':
            return self.dept_step
        if step == 'finance':
            return self.finance_step
        return self.gm_step == 'always' or (self.gm_step == 'above' and amount > (self.gm_above or 0.0))


class SbRequestMixin(models.AbstractModel):
    _name = 'sb.request.mixin'
    _description = 'Request with approval steps'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Number', default='/', copy=False, readonly=True, index=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection(STATES, default='draft', required=True, tracking=True, index=True, copy=False)
    requester_id = fields.Many2one('res.users', 'Requested by', required=True, default=lambda s: s.env.user, tracking=True,
                                   index=True, domain=[('share', '=', False)])
    employee_id = fields.Many2one('hr.employee', 'Employee', compute='_compute_employee', store=True, readonly=False)
    department_id = fields.Many2one('hr.department', 'Department', compute='_compute_employee', store=True, readonly=False,
                                    tracking=True)
    dept_approver_id = fields.Many2one('res.users', 'Department head', compute='_compute_dept_approver', store=True,
                                       readonly=False, tracking=True, domain=[('share', '=', False)],
                                       help='Approves the request first: the department\'s manager, else the requester\'s '
                                            'manager, else whoever the requester sent the last request to.')
    submitted_at = fields.Datetime('Submitted', readonly=True, copy=False)
    dept_by = fields.Many2one('res.users', 'Department approval by', readonly=True, copy=False)
    dept_at = fields.Datetime('Department approval on', readonly=True, copy=False)
    finance_by = fields.Many2one('res.users', 'Finance check by', readonly=True, copy=False)
    finance_at = fields.Datetime('Finance check on', readonly=True, copy=False)
    gm_by = fields.Many2one('res.users', 'Final approval by', readonly=True, copy=False)
    gm_at = fields.Datetime('Final approval on', readonly=True, copy=False)
    reason = fields.Char('Reason', copy=False,
                         help='Say why, before sending the request back or cancelling it: the requester sees it.')
    sent_back_reason = fields.Char('Sent back because', readonly=True, copy=False)
    sent_back_by = fields.Many2one('res.users', 'Sent back by', readonly=True, copy=False)
    cancel_reason = fields.Char('Cancelled because', readonly=True, copy=False)
    sb_step_user_id = fields.Many2one('res.users', 'Decided by (instead of the role)', readonly=True, copy=False, index=True,
                                      help='When the requester holds the role of the step and self-approval is not allowed, '
                                           'the step goes to this person one level up.')
    gm_needed = fields.Boolean('Needs the general manager', compute='_compute_route')
    route = fields.Char('Approval path', compute='_compute_route')
    sb_waiting_for = fields.Char('Waiting for', compute='_compute_waiting')
    sb_waiting_me = fields.Boolean('Waiting for me', compute='_compute_waiting', search='_search_waiting_me')
    sb_can_decide = fields.Boolean(compute='_compute_waiting')
    sb_is_owner = fields.Boolean(compute='_compute_waiting', help='The requester (or who entered it, or a requests administrator).')
    sb_can_cancel = fields.Boolean(compute='_compute_waiting')
    sb_migrated = fields.Boolean('Brought over', copy=False, readonly=True, index=True,
                                 help='Brought over from the previous system with its approval state.')
    sb_legacy_ref = fields.Char('Previous system ref.', copy=False, readonly=True, index=True)

    # ------------------------------------------------------------------ hooks for the models
    def _sb_kind(self):
        raise NotImplementedError

    def _sb_amount(self):
        return self.amount

    def _sb_check_submit(self):
        if self.currency_id.compare_amounts(self._sb_amount(), 0.0) <= 0:
            raise UserError(self.env._('%s: enter the amount.', self.display_name))

    def _sb_check_approve(self, step):
        pass

    def _sb_on_approved(self):
        pass

    def _sb_sequence_code(self):
        return self._name

    # ------------------------------------------------------------------ defaults
    @api.depends('requester_id', 'company_id')
    def _compute_employee(self):
        for r in self:
            emp = r.requester_id.employee_ids.filtered(lambda e: e.company_id == r.company_id)[:1] or r.requester_id.employee_ids[:1]
            r.employee_id = emp
            r.department_id = emp.department_id

    @api.depends('department_id', 'employee_id', 'requester_id')
    def _compute_dept_approver(self):
        for r in self:
            if r.state != 'draft' and r.dept_approver_id:
                r.dept_approver_id = r.dept_approver_id
                continue
            user = r.department_id.manager_id.user_id
            if not user or user == r.requester_id:
                user = r.employee_id.parent_id.user_id or user
            if not user and r.requester_id:
                last = self.sudo().search([('requester_id', '=', r.requester_id.id), ('dept_approver_id', '!=', False),
                                           ('id', '!=', r._origin.id or 0)], order='id desc', limit=1)
                user = last.dept_approver_id
            r.dept_approver_id = user

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                code = self.new(vals)._sb_sequence_code()
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code(code) or '/'
        return super().create(vals_list)

    # ------------------------------------------------------------------ the route
    def _sb_policy(self):
        return self.env['sb.request.policy']._for(self._sb_kind(), self.company_id)

    def _sb_steps(self):
        """The steps this request goes through, in order."""
        self.ensure_one()
        pol = self._sb_policy()
        amount = self._sb_amount()
        steps = []
        for s in STEPS:
            if not pol._needs(s, amount):
                continue
            if s == 'dept' and self.dept_approver_id and self.dept_approver_id == self.requester_id \
                    and not self.company_id._sb_no_self_approval():
                continue
            steps.append(s)
        return steps

    def _sb_next(self, after=None):
        steps = self._sb_steps()
        if after is None:
            return steps[0] if steps else 'approved'
        later = [s for s in steps if STEPS.index(s) > STEPS.index(after)]
        return later[0] if later else 'approved'

    @api.depends('state', 'amount', 'dept_approver_id', 'requester_id', 'company_id')
    def _compute_route(self):
        labels = dict(STATES)
        for r in self:
            try:
                steps = r._sb_steps() if r.company_id else []
            except NotImplementedError:
                steps = []
            r.gm_needed = 'gm' in steps
            r.route = ' > '.join(labels[s] for s in steps) or self.env._('No approval needed')

    def _sb_can(self, user=None):
        """May this user decide the step the request is at?"""
        self.ensure_one()
        user = user or self.env.user
        if self.state not in STEPS:
            return False
        if self.company_id._sb_no_self_approval() and user == self.requester_id and self.state != 'finance':
            return False
        if self.state == 'dept':
            return user == self.dept_approver_id or user.has_group('sonador_requests.group_request_admin')
        if self.sb_step_user_id:
            return user == self.sb_step_user_id
        if self.state == 'finance':
            return user.has_group('sonador_requests.group_request_finance')
        return user.has_group('sonador_requests.group_request_gm')

    def _sb_step_user(self, step):
        """Who decides this step instead of the role: set when the requester holds the role themselves and
        self-approval is not allowed (one level up; the general manager's own requests: the company's named person)."""
        self.ensure_one()
        c = self.company_id
        req = self.requester_id
        if step == 'gm':
            policy = self._sb_policy()
            if policy.gm_delegate_id and policy.gm_delegate_id != req and \
                    self.currency_id.compare_amounts(self._sb_amount(), policy.gm_delegate_limit or 0.0) <= 0:
                return policy.gm_delegate_id
        if step not in ('finance', 'gm') or not c._sb_no_self_approval():
            return self.env['res.users']
        group = {'finance': 'sonador_requests.group_request_finance', 'gm': 'sonador_requests.group_request_gm'}[step]
        if not req.has_group(group):
            return self.env['res.users']
        if step == 'gm' and c.sb_gm_own_approver_id and c.sb_gm_own_approver_id != req:
            return c.sb_gm_own_approver_id
        if step == 'finance':
            return self.env['res.users']  # the finance check stays with finance, even on their own requests: never up
        up = req._sb_one_up(c)
        if not up:
            raise UserError(self.env._('%(d)s: you hold the %(s)s role yourself and nobody above you is set to decide it. '
                                       'Ask the administrator to set your manager.', d=self.display_name, s=STEP_NAMES[step]))
        return up

    def _sb_enter(self, state):
        """Values to write when the request moves to this state."""
        return {'state': state, 'sb_step_user_id': self._sb_step_user(state).id or False}

    @api.depends('state', 'dept_approver_id')
    @api.depends_context('uid')
    def _compute_waiting(self):
        for r in self:
            r.sb_can_decide = r._sb_can() if r.id else False
            r.sb_is_owner = not r.id or self.env.su or self.env.user in (r.requester_id | r.create_uid) or \
                self.env.user.has_group('sonador_requests.group_request_admin')
            r.sb_can_cancel = bool(r.id) and r._sb_can_cancel()
            r.sb_waiting_me = r.sb_can_decide
            if r.state == 'dept':
                r.sb_waiting_for = self.env._('%s (department head)', r.dept_approver_id.name or '?')
            elif r.state == 'gm' and r.sb_step_user_id and r.sb_step_user_id == r._sb_policy().gm_delegate_id:
                r.sb_waiting_for = self.env._('%(u)s (final approval up to %(l)s)', u=r.sb_step_user_id.name,
                                              l='{:,.0f}'.format(r._sb_policy().gm_delegate_limit))
            elif r.state in ('finance', 'gm') and r.sb_step_user_id:
                r.sb_waiting_for = self.env._('%(u)s (%(s)s, one level up)', u=r.sb_step_user_id.name, s=STEP_NAMES[r.state])
            elif r.state == 'finance':
                r.sb_waiting_for = self.env._('Finance')
            elif r.state == 'gm':
                r.sb_waiting_for = self.env._('The general manager')
            else:
                r.sb_waiting_for = False

    @api.model
    def sb_waiting_domain(self, user=None):
        user = user or self.env.user
        parts = [Domain([('state', '=', 'dept'), ('dept_approver_id', '=', user.id)]),
                 Domain([('state', 'in', ('finance', 'gm')), ('sb_step_user_id', '=', user.id)])]
        if user.has_group('sonador_requests.group_request_finance'):
            parts.append(Domain([('state', '=', 'finance'), ('sb_step_user_id', '=', False)]))
        if user.has_group('sonador_requests.group_request_gm'):
            parts.append(Domain([('state', '=', 'gm'), ('sb_step_user_id', '=', False)]))
        dom = Domain('company_id', 'in', user.company_ids.ids) & Domain.OR(parts)
        strict = self.env['res.company'].sudo().search([('id', 'in', user.company_ids.ids), ('sb_self_approval', '=', 'up')])
        if strict:   # never one's own
            dom &= Domain.OR([Domain('requester_id', '!=', user.id), Domain('company_id', 'not in', strict.ids)])
        return list(dom)

    @api.model
    def sb_waiting_for_user(self, user=None, limit=200):
        user = user or self.env.user
        return self.sudo().search(self.sb_waiting_domain(user), order='submitted_at, id', limit=limit)

    def _search_waiting_me(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        ids = self.sb_waiting_for_user().ids
        positive = (operator == '=') == value
        return [('id', 'in' if positive else 'not in', ids)]

    # ------------------------------------------------------------------ guarding edits
    _SB_LOCKED = ('amount', 'requester_id', 'company_id')

    def write(self, vals):
        if not self.env.su and set(vals) & set(self._SB_LOCKED):
            for r in self:
                if r.state != 'draft':
                    raise UserError(self.env._('%s is past draft: send it back to change the amount or the requester.',
                                               r.display_name))
        return super().write(vals)

    def unlink(self):
        for r in self:
            if r.state not in ('draft', 'cancel') or r.sb_migrated and not self.env.su:
                raise UserError(self.env._('%s cannot be deleted: cancel it with a reason instead.', r.display_name))
        return super().unlink()

    # ------------------------------------------------------------------ the actions
    def _sb_log(self, body):
        self.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def action_submit(self):
        for r in self:
            if r.state != 'draft':
                raise UserError(self.env._('%s was submitted already.', r.display_name))
            if r.requester_id != self.env.user and not (self.env.su or self.env.user.has_group('sonador_requests.group_request_admin')
                                                        or r.create_uid == self.env.user):
                raise UserError(self.env._('Only the requester submits %s.', r.display_name))
            r._sb_check_submit()
            moved_up = False
            if r.company_id._sb_no_self_approval() and r.dept_approver_id and r.dept_approver_id == r.requester_id \
                    and r._sb_policy().dept_step:
                up = r.requester_id._sb_one_up(r.company_id)
                if not up:
                    raise UserError(self.env._('%s: you are your own department head and nobody above you is set to approve. '
                                               'Ask the administrator to set your manager.', r.display_name))
                r.sudo().dept_approver_id = up
                moved_up = True
            nxt = r._sb_next()
            if nxt == 'dept' and not r.dept_approver_id:
                raise UserError(self.env._('Choose the department head who approves %s.', r.display_name))
            r.sudo().write(dict(r._sb_enter(nxt), submitted_at=fields.Datetime.now(), reason=False))
            note = self.env._('Submitted for %(s)s. Approval path: %(p)s.',
                              s=dict(STATES)[nxt].lower() if nxt != 'approved' else self.env._('payment (no approval needed)'),
                              p=r.route)
            if 'dept' not in r._sb_steps() and r._sb_policy().dept_step and r.dept_approver_id == r.requester_id:
                note += ' ' + self.env._('Department approval skipped: the requester is the department head.')
            if moved_up:
                note += ' ' + self.env._('The requester is their own department head: the department approval goes one level '
                                         'up, to %s.', r.dept_approver_id.name)
            r._sb_log(note)
            if nxt == 'approved':
                r._sb_on_approved()
        return True

    def action_approve(self):
        now = fields.Datetime.now()
        for r in self:
            step = r.state
            if step not in STEPS:
                raise UserError(self.env._('%s is not waiting for an approval.', r.display_name))
            if not r._sb_can():
                raise UserError(self.env._('%(d)s waits for %(w)s, not for you.', d=r.display_name, w=r.sb_waiting_for))
            r._sb_check_approve(step)
            nxt = r._sb_next(step)
            vals = dict(r._sb_enter(nxt), **{'%s_by' % step: self.env.user.id, '%s_at' % step: now, 'reason': False})
            r.sudo().write(vals)
            on_behalf = step == 'dept' and self.env.user != r.dept_approver_id
            r._sb_log(self.env._('%(step)s: approved by %(u)s%(b)s. %(next)s', step=STEP_NAMES[step].capitalize(),
                                 u=self.env.user.name, b=self.env._(' on behalf of %s', r.dept_approver_id.name) if on_behalf else '',
                                 next=(self.env._('Now waiting for %s.', r.sb_waiting_for) if nxt != 'approved'
                                       else self.env._('Fully approved.'))))
            if nxt == 'approved':
                r._sb_on_approved()
        return True

    def action_send_back(self, reason=None):
        for r in self:
            why = (reason or r.reason or '').strip()
            if r.state not in STEPS:
                raise UserError(self.env._('%s is not waiting for an approval.', r.display_name))
            if not r._sb_can():
                raise UserError(self.env._('%(d)s waits for %(w)s, not for you.', d=r.display_name, w=r.sb_waiting_for))
            if not why:
                raise UserError(self.env._('Write the reason first: the requester will see it.'))
            step = r.state
            r.sudo().write({'state': 'draft', 'sent_back_reason': why, 'sent_back_by': self.env.user.id, 'reason': False,
                            'sb_step_user_id': False,
                            'dept_by': False, 'dept_at': False, 'finance_by': False, 'finance_at': False,
                            'gm_by': False, 'gm_at': False})
            r._sb_log(self.env._('Sent back at %(s)s by %(u)s: %(n)s', s=STEP_NAMES[step], u=self.env.user.name, n=why))
        return True

    def _sb_can_cancel(self):
        user = self.env.user
        if self.env.su or user.has_group('sonador_requests.group_request_admin'):
            return True
        if self.state == 'draft' and user in (self.requester_id | self.create_uid):
            return True
        if self.state in STEPS and user == self.requester_id:
            return True
        return self.state in STEPS and self._sb_can()

    def action_cancel(self, reason=None):
        for r in self:
            why = (reason or r.reason or '').strip()
            if r.state in ('cancel',):
                continue
            if not r._sb_can_cancel():
                raise UserError(self.env._('You cannot cancel %s.', r.display_name))
            r._sb_check_cancel()
            if not why and r.state != 'draft':
                raise UserError(self.env._('Write the reason for cancelling first.'))
            r.sudo().write({'state': 'cancel', 'cancel_reason': why or False, 'reason': False, 'sb_step_user_id': False})
            r._sb_log(self.env._('Cancelled by %(u)s%(n)s', u=self.env.user.name, n=(': ' + why) if why else '.'))
        return True

    def _sb_check_cancel(self):
        pass

    def action_draft(self):
        for r in self:
            if r.state != 'cancel':
                raise UserError(self.env._('Only a cancelled request goes back to draft.'))
            if not (self.env.su or self.env.user in (r.requester_id | r.create_uid)
                    or self.env.user.has_group('sonador_requests.group_request_admin')):
                raise UserError(self.env._('Only the requester reopens %s.', r.display_name))
            r.sudo().write({'state': 'draft', 'cancel_reason': False})
            r._sb_log(self.env._('Reopened as draft by %s.', self.env.user.name))
        return True
