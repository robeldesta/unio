"""Sales orders: salesperson submits (Confirm) -> finance check -> commercial approval -> confirmed (delivery).

Off unless the company switches it on. With it on, action_confirm on an order not yet approved submits it instead of
confirming it; the last approval confirms it (inside the approval, so a confirmation text asked on action_confirm is
not asked again of the approver). Send back returns it to quotation with the reason. Orders at or below the company's
amount skip the approval."""
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from odoo.addons.sonador_books.models.client_guard import refuse_client_write

STEPS = ('finance', 'commercial')
APPR = [('none', 'Not submitted'), ('finance', 'Finance check'), ('commercial', 'Commercial approval'),
        ('approved', 'Approved'), ('sent_back', 'Sent back')]
GROUP = {'finance': 'sonador_sale_approval.group_so_finance', 'commercial': 'sonador_sale_approval.group_so_commercial'}


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_so_approval = fields.Boolean('Sales orders need approval', default=False,
                                    help='Confirming a sales order sends it for the finance check and the commercial approval; '
                                         'it is confirmed for delivery after the last one.')
    sb_so_finance_step = fields.Boolean('Finance check', default=True)
    sb_so_commercial_step = fields.Boolean('Commercial approval', default=True)
    sb_so_approval_above = fields.Monetary('Only above', currency_field='currency_id',
                                           help='Orders up to this amount are confirmed without approval. 0: every order.')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sb_appr_state = fields.Selection(APPR, 'Approval', default='none', copy=False, tracking=True, index=True)
    sb_appr_reason = fields.Char('Reason', copy=False, help='Why it goes back (the salesperson sees it).')
    sb_sent_back_reason = fields.Char('Sent back because', copy=False, readonly=True)
    sb_submitted_at = fields.Datetime('Submitted for approval', copy=False, readonly=True)
    sb_finance_by = fields.Many2one('res.users', 'Finance check by', copy=False, readonly=True)
    sb_finance_at = fields.Datetime('Finance check on', copy=False, readonly=True)
    sb_commercial_by = fields.Many2one('res.users', 'Commercial approval by', copy=False, readonly=True)
    sb_commercial_at = fields.Datetime('Commercial approval on', copy=False, readonly=True)
    sb_appr_can = fields.Boolean(compute='_compute_sb_appr_can')
    sb_so_approval = fields.Boolean(related='company_id.sb_so_approval')
    sb_so_finance_step = fields.Boolean(related='company_id.sb_so_finance_step')
    sb_appr_user_id = fields.Many2one('res.users', 'Step decided by (instead of the role)', copy=False, readonly=True, index=True,
                                      help='When the salesperson holds the role of the step and self-approval is not allowed, '
                                           'the step goes to this person one level up.')

    # kept by Confirm / Approve / Send back: a client never writes them
    _SB_APPR_FIELDS = ('sb_appr_state', 'sb_submitted_at', 'sb_finance_by', 'sb_finance_at', 'sb_commercial_by',
                       'sb_commercial_at', 'sb_appr_user_id', 'sb_sent_back_reason')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            refuse_client_write(self.browse(), vals, self._SB_APPR_FIELDS, self.env._('Sales order'))
        return super().create(vals_list)

    def write(self, vals):
        refuse_client_write(self, vals, self._SB_APPR_FIELDS, self.env._('Sales order'))
        return super().write(vals)

    @api.depends('sb_appr_state')
    @api.depends_context('uid')
    def _compute_sb_appr_can(self):
        for o in self:
            o.sb_appr_can = o._sb_appr_can()

    def _sb_appr_seller(self):
        return self.user_id or self.create_uid

    def _sb_appr_can(self, user=None):
        user = user or self.env.user
        if self.sb_appr_state not in STEPS:
            return False
        if self.company_id._sb_no_self_approval() and user == self._sb_appr_seller() and self.sb_appr_state != 'finance':
            return False
        if self.sb_appr_user_id:
            return user == self.sb_appr_user_id
        return user.has_group(GROUP[self.sb_appr_state])

    def _sb_appr_enter(self, state):
        """Values when the order moves to this approval state: one level up when the salesperson holds the role."""
        user = self.env['res.users']
        seller = self._sb_appr_seller()
        if state == 'finance':
            pass  # the finance check stays with finance (whoever holds the role), even on their own orders: never up
        elif state in STEPS and self.company_id._sb_no_self_approval() and seller.has_group(GROUP[state]):
            user = seller._sb_one_up(self.company_id)
            if not user:
                raise UserError(self.env._('%s: the salesperson holds this approval role and nobody above them is set to '
                                           'decide it. Ask the administrator to set their manager.', self.name))
        return {'sb_appr_state': state, 'sb_appr_user_id': user.id or False}

    def _sb_appr_steps(self):
        c = self.company_id
        if not c.sb_so_approval or (c.sb_so_approval_above and self.amount_total <= c.sb_so_approval_above):
            return []
        return [s for s, on in (('finance', c.sb_so_finance_step), ('commercial', c.sb_so_commercial_step)) if on]

    def _sb_appr_next(self, after=None):
        steps = self._sb_appr_steps()
        later = [s for s in steps if after is None or STEPS.index(s) > STEPS.index(after)]
        return later[0] if later else 'approved'

    def _sb_log(self, body):
        # approvers (finance, commercial) need not have Sales rights: the note is written in their name all the same
        self.sudo().message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note',
                                 author_id=self.env.user.partner_id.id)

    def action_confirm(self):
        if self.env.context.get('sb_so_approved'):
            return super().action_confirm()
        todo = self.filtered(lambda o: o.state in ('draft', 'sent') and o.sb_appr_state != 'approved' and o._sb_appr_steps())
        for o in todo:
            if o.sb_appr_state in STEPS:
                raise UserError(self.env._('%(o)s waits for the %(s)s.', o=o.name, s=dict(APPR)[o.sb_appr_state].lower()))
            nxt = o._sb_appr_next()
            o.sudo().write(dict(o._sb_appr_enter(nxt), sb_submitted_at=fields.Datetime.now(), sb_appr_reason=False))
            o._sb_log(self.env._('Submitted for approval by %(u)s: now %(s)s.', u=self.env.user.name, s=dict(APPR)[nxt].lower()))
        rest = self - todo
        return super(SaleOrder, rest).action_confirm() if rest else True

    def action_sb_so_approve(self):
        now = fields.Datetime.now()
        for o in self:
            step = o.sb_appr_state
            if not o._sb_appr_can():
                raise UserError(self.env._('%s is not waiting for your approval.', o.name))
            nxt = o._sb_appr_next(step)
            o.sudo().write(dict(o._sb_appr_enter(nxt), **{'sb_%s_by' % step: self.env.user.id, 'sb_%s_at' % step: now,
                                                          'sb_appr_reason': False}))
            o._sb_log(self.env._('%(s)s: approved by %(u)s.', s=dict(APPR)[step], u=self.env.user.name))
            if nxt == 'approved':
                o.with_context(sb_so_approved=True, sb_confirm_inside=True).sudo().action_confirm()
                o._sb_log(self.env._('Confirmed for delivery.'))
        return True

    def action_sb_so_send_back(self, reason=None):
        for o in self:
            why = (reason or o.sb_appr_reason or '').strip()
            if not o._sb_appr_can():
                raise UserError(self.env._('%s is not waiting for your approval.', o.name))
            if not why:
                raise UserError(self.env._('Write the reason first: the salesperson will see it.'))
            step = o.sb_appr_state
            o.sudo().write({'sb_appr_state': 'sent_back', 'sb_sent_back_reason': why, 'sb_appr_reason': False,
                            'sb_finance_by': False, 'sb_finance_at': False, 'sb_appr_user_id': False})
            o._sb_log(self.env._('Sent back at %(s)s by %(u)s: %(n)s', s=dict(APPR)[step].lower(), u=self.env.user.name, n=why))
        return True

    @api.model
    def sb_so_waiting_for_user(self, user=None, limit=100):
        user = user or self.env.user
        steps = [s for s in STEPS if user.has_group(GROUP[s])]
        dom = Domain([('sb_appr_state', 'in', STEPS), ('sb_appr_user_id', '=', user.id)])
        if steps:
            dom |= Domain([('sb_appr_state', 'in', steps), ('sb_appr_user_id', '=', False)])
        found = self.sudo().search(Domain('company_id', 'in', user.company_ids.ids) & dom, order='sb_submitted_at, id', limit=limit)
        return found.filtered(lambda o: o._sb_appr_can(user))


class SbMe(models.AbstractModel):
    _inherit = 'sb.me'

    def _approval_kinds(self):
        return super()._approval_kinds() + ['so']

    def _so_item(self, o):
        cur = o.currency_id
        return {'key': 'so:%d' % o.id, 'app': 'Sales', 'icon': 'fa-handshake-o', 'title': '%s · %s' % (o.name, o.partner_id.name),
                'doc': o.name, 'amount': o.amount_total, 'currency': cur.symbol or cur.name,
                'details': ['%s × %s' % ('%g' % l.product_uom_qty, l.product_id.display_name) for l in o.order_line.filtered('product_id')[:5]]
                           + ['Salesperson: %s' % (o.user_id.name or '-'), 'Step: %s' % dict(APPR)[o.sb_appr_state]],
                'since': o.sb_submitted_at or o.write_date, 'model': 'sale.order', 'res_id': o.id,
                'approve_label': 'Check' if o.sb_appr_state == 'finance' else 'Confirm',
                'submitter_uid': (o.user_id or o.create_uid).id}

    def _ap_so_pending(self):
        return [self._so_item(o) for o in self.env['sale.order'].sb_so_waiting_for_user(self.env.user)]

    def _ap_so_decide(self, rid, approve, note):
        o = self.env['sale.order'].browse(int(rid))
        if approve:
            o.action_sb_so_approve()
        else:
            o.action_sb_so_send_back(note)

    def _ap_so_next(self, rid):
        o = self.env['sale.order'].sudo().browse(int(rid))
        if o.sb_appr_state not in STEPS:
            return False
        return o.sb_appr_user_id.name or ('Finance' if o.sb_appr_state == 'finance' else 'the commercial manager')

    def _ap_so_mine(self):
        rows = []
        for o in self.env['sale.order'].sudo().search(['|', ('user_id', '=', self.env.uid), ('create_uid', '=', self.env.uid),
                                                        ('sb_appr_state', 'in', STEPS)], limit=50):
            it = self._so_item(o)
            it['waiting_for'] = (o.sb_appr_user_id.name if o.sb_appr_user_id else
                                 'finance' if o.sb_appr_state == 'finance' else 'the commercial manager')
            rows.append(it)
        return rows

    def _ap_so_open(self, rid):
        return self._form('sale.order', rid)
