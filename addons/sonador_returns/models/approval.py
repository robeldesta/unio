"""Returns approved before finance (a company setting; Eco Green: on, the commercial manager).

A sales return (RET) or van sales return is prepared by the store or sales and sent for approval. The commercial
approver (the person named on the company, else whoever holds the sales commercial approval role) approves it or sends
it back with the reason; when self-approval is not allowed, a return its approver prepared goes one level up. Approved,
it goes to finance, who post it (Me > Approvals: kind 'return' for the approver, then 'store' for finance)."""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

APPROVAL_TYPES = ('customer_return', 'van_return')
APPR = [('none', 'Not sent'), ('waiting', 'Waiting for approval'), ('approved', 'Approved'), ('sent_back', 'Sent back')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_return_approval = fields.Boolean('Returns approved before finance',
                                        help='Sales returns and van sales returns are approved (commercial approval) before '
                                             'finance posts them.')
    sb_return_approver_id = fields.Many2one('res.users', 'Returns approved by', domain=[('share', '=', False)],
                                            help='Empty: whoever holds the sales commercial approval role.')
    sb_returns_always_damaged = fields.Boolean(
        'Returns always go to damaged stock',
        help='Every returned item goes to the damaged stock, even in perfect condition (it never goes back to the finished '
             'stock directly: damaged first, then recycled).')


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_appr_state = fields.Selection(APPR, 'Approval', default='none', copy=False, tracking=True, index=True)
    sb_appr_user_id = fields.Many2one('res.users', 'Approver (one level up)', copy=False, readonly=True, index='btree_not_null',
                                      help='The return goes to this person instead of the approver: its approver prepared it.')
    sb_appr_submitted_by = fields.Many2one('res.users', 'Sent for approval by', copy=False, readonly=True)
    sb_appr_submitted_at = fields.Datetime('Sent for approval on', copy=False, readonly=True)
    sb_appr_by = fields.Many2one('res.users', 'Approved by', copy=False, readonly=True)
    sb_appr_at = fields.Datetime('Approved on', copy=False, readonly=True)
    sb_needs_return_approval = fields.Boolean(compute='_compute_sb_needs_return_approval')
    sb_appr_can = fields.Boolean(compute='_compute_sb_needs_return_approval')
    sb_always_damaged = fields.Boolean(related='company_id.sb_returns_always_damaged')
    sb_appr_note = fields.Char('Reason to send back', copy=False, help='Why the return goes back (the person who sent it sees it).')

    @api.depends_context('uid')
    @api.depends('voucher_type', 'company_id', 'sb_appr_state', 'sb_appr_user_id')
    def _compute_sb_needs_return_approval(self):
        for v in self:
            v.sb_needs_return_approval = v.voucher_type in APPROVAL_TYPES and bool((v.company_id or self.env.company).sb_return_approval)
            v.sb_appr_can = v.sb_appr_state == 'waiting' and v._sb_return_approver_ok(self.env.user)

    # ------------------------------------------------------------------ who approves
    def _sb_return_role(self):
        return self.env.ref('sonador_sale_approval.group_so_commercial', raise_if_not_found=False)

    def _sb_return_is_approver(self, user):
        named = self.company_id.sb_return_approver_id
        if named:
            return user == named
        role = self._sb_return_role()
        return bool(role) and user.has_group('sonador_sale_approval.group_so_commercial')

    def _sb_return_approver_ok(self, user):
        if self.sb_appr_user_id:
            return user == self.sb_appr_user_id
        if self._sb_no_self(user):
            return False
        return self._sb_return_is_approver(user)

    def _sb_no_self(self, user):
        company = self.company_id
        no_self = hasattr(company, '_sb_no_self_approval') and company._sb_no_self_approval()
        return no_self and user == (self.sb_appr_submitted_by or self.create_uid)

    def _sb_return_approvers(self):
        """The people the return waits for (for the message and Me)."""
        if self.sb_appr_user_id:
            return self.sb_appr_user_id
        if self.company_id.sb_return_approver_id:
            return self.company_id.sb_return_approver_id
        role = self._sb_return_role()
        return role.sudo().user_ids.filtered(lambda u: u.active and not u.share) if role else self.env['res.users']

    # ------------------------------------------------------------------ the flow
    def action_sb_send_return_approval(self):
        for v in self:
            if v.state != 'draft' or v.voucher_type not in APPROVAL_TYPES:
                raise UserError(self.env._('Only a return not posted yet is sent for approval.'))
            if not v.sb_needs_return_approval:
                raise UserError(self.env._('Returns are not approved before finance in this company: send it to finance.'))
            if v.sb_appr_state == 'waiting':
                raise UserError(self.env._('%s already waits for approval.', v.display_name))
            v._check()
            user = self.env.user
            vals = {'sb_appr_state': 'waiting', 'sb_appr_submitted_by': user.id, 'sb_appr_submitted_at': fields.Datetime.now(),
                    'sb_appr_user_id': False, 'sb_sent_back_reason': False}
            company = v.company_id
            no_self = hasattr(company, '_sb_no_self_approval') and company._sb_no_self_approval()
            if no_self and v._sb_return_is_approver(user):
                up = user._sb_one_up(company) if hasattr(user, '_sb_one_up') else self.env['res.users']
                if not up:
                    raise UserError(self.env._('You approve returns yourself and nobody above you is set to approve yours. Ask the '
                                               'administrator to set your manager.'))
                vals['sb_appr_user_id'] = up.id
            v.sudo().write(vals)
            v.message_post(body=self.env._('Sent for approval by %(u)s (to %(who)s).', u=user.name,
                                           who=', '.join(v._sb_return_approvers().mapped('name')) or self.env._('the approver')))
        return True

    def action_sb_return_approve(self):
        for v in self:
            if v.sb_appr_state != 'waiting':
                raise UserError(self.env._('%s does not wait for approval.', v.display_name))
            if not self.env.su and not v._sb_return_approver_ok(self.env.user):
                raise UserError(self.env._('%(doc)s waits for %(who)s.', doc=v.display_name,
                                           who=', '.join(v._sb_return_approvers().mapped('name')) or self.env._('the approver')))
            # approved: it goes to finance, who post it
            v.sudo().write({'sb_appr_state': 'approved', 'sb_appr_by': self.env.uid, 'sb_appr_at': fields.Datetime.now(),
                            'sb_sent_finance_at': fields.Datetime.now(), 'sb_sent_finance_by': (v.sb_appr_submitted_by or self.env.user).id})
            v.sudo().message_post(body=self.env._('Approved by %s. Now with finance, who post it.', self.env.user.name),
                                  author_id=self.env.user.partner_id.id)
        return True

    def action_sb_return_send_back(self, reason):
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._('Say why it goes back: the person who sent it will see your reason.'))
        for v in self:
            if v.sb_appr_state != 'waiting':
                raise UserError(self.env._('%s does not wait for approval.', v.display_name))
            if not self.env.su and not v._sb_return_approver_ok(self.env.user):
                raise UserError(self.env._('%s does not wait for you.', v.display_name))
            v.sudo().write({'sb_appr_state': 'sent_back', 'sb_sent_back_reason': reason, 'sb_appr_user_id': False})
            v.sudo().message_post(body=self.env._('Sent back by %(u)s: %(r)s', u=self.env.user.name, r=reason),
                                  author_id=self.env.user.partner_id.id)
        return True

    def action_sb_return_send_back_form(self):
        self.action_sb_return_send_back(self.sb_appr_note)
        self.sudo().sb_appr_note = False
        return True

    def action_sb_send_finance(self):
        waiting = self.filtered(lambda v: v.sb_needs_return_approval and v.sb_appr_state != 'approved')
        if waiting:
            raise UserError(self.env._('%s is approved before finance: send it for approval.', ', '.join(waiting.mapped('display_name'))))
        return super().action_sb_send_finance()

    def action_sb_send_back(self, reason):
        # finance sends an approved return back: it is approved again after the correction
        res = super().action_sb_send_back(reason)
        self.filtered(lambda v: v.sb_needs_return_approval).sudo().write({'sb_appr_state': 'sent_back'})
        return res

    def _sb_require(self):
        if self.sb_needs_return_approval and self.sb_appr_state != 'approved' and not self.env.su:
            raise UserError(self.env._('%s is approved before finance posts it: send it for approval.', self.display_name))
        return super()._sb_require()

    def _check(self):
        super()._check()
        if self.voucher_type in APPROVAL_TYPES and self.company_id.sb_returns_always_damaged and \
                self.line_ids.filtered(lambda l: l.product_id and l.sb_condition == 'good'):
            raise UserError(self.env._('Returned goods always go to the damaged stock in this company (damaged first, then recycled): '
                                       'set every line to Damaged.'))


class SbMe(models.AbstractModel):
    _inherit = 'sb.me'

    def _approval_kinds(self):
        return super()._approval_kinds() + ['return']

    def _return_item(self, v):
        cur = v.currency_id
        value = sum(l.quantity * l.sb_price for l in v.line_ids)
        reason = dict(v._fields['sb_return_reason']._description_selection(self.env)).get(v.sb_return_reason) or ''
        return {'key': 'return:%d' % v.id, 'app': 'Inventory', 'icon': 'fa-undo',
                'title': '%s · %s' % (v._type_label(), v.partner_id.name or (v.sb_van_trip_id.name if 'sb_van_trip_id' in v._fields else '') or ''),
                'doc': v.display_name, 'amount': value, 'currency': cur.symbol or cur.name,
                'details': ['%s × %s @ %s' % ('%g' % l.quantity, l.product_id.display_name, '{:,.2f}'.format(l.sb_price))
                            for l in v.line_ids.filtered('product_id')[:6]]
                           + [self.env._('Reason: %s', reason)] + ([self.env._('Against %s', v.sb_origin_voucher_id.name)] if v.sb_origin_voucher_id else []),
                'since': v.sb_appr_submitted_at, 'model': 'sb.stock.voucher', 'res_id': v.id,
                'submitter_uid': v.sb_appr_submitted_by.id}

    def _ap_return_pending(self):
        SV = self.env['sb.stock.voucher'].sudo()
        user = self.env.user
        return [self._return_item(v) for v in SV.search([('state', '=', 'draft'), ('sb_appr_state', '=', 'waiting'),
                                                         ('company_id', 'in', self.env.companies.ids)], order='sb_appr_submitted_at', limit=80)
                if v._sb_return_approver_ok(user)]

    def _ap_return_decide(self, rid, approve, note):
        # the approver need not have Inventory rights: their right is checked here, the document is written as them
        v = self.env['sb.stock.voucher'].sudo().browse(int(rid))
        if not v._sb_return_approver_ok(self.env.user):
            raise UserError(self.env._('%s does not wait for you.', v.display_name))
        if approve:
            v.action_sb_return_approve()
        else:
            v.action_sb_return_send_back(note)

    def _ap_return_next(self, rid):
        return 'finance'

    def _ap_return_mine(self):
        rows = []
        for v in self.env['sb.stock.voucher'].sudo().search([('sb_appr_state', '=', 'waiting'), ('sb_appr_submitted_by', '=', self.env.uid),
                                                             ('state', '=', 'draft')], limit=30):
            it = self._return_item(v)
            it['waiting_for'] = ', '.join(v._sb_return_approvers().mapped('name')) or 'the commercial approver'
            rows.append(it)
        return rows

    def _ap_return_open(self, rid):
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.stock.voucher', 'res_id': int(rid), 'target': 'current',
                'views': [[self.env.ref('sonador_returns.sb_returns_form').id, 'form']]}
