"""Purchase order approval limits: amount bands, each approved by a group (configurable).

Confirming an order whose total (company currency, VAT included) falls in a band the user's groups do not cover sends it
to "To Approve"; only a member of that band's group (or of a higher band's group) can approve it. Each approval is logged.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class SbPoApprovalRule(models.Model):
    _name = 'sb.po.approval.rule'
    _description = 'Purchase Order Approval Limit'
    _order = 'amount_from, id'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount_from = fields.Monetary('From (more than)', default=0.0, help='The band starts above this amount.')
    amount_to = fields.Monetary('Up to (and including)', help='Empty: no upper limit.')
    group_id = fields.Many2one('res.groups', 'Approved by', required=True,
                               help='Members of this group approve orders in the band. Higher bands can approve lower ones.')
    active = fields.Boolean(default=True)

    @api.constrains('amount_from', 'amount_to')
    def _check_band(self):
        for r in self:
            if r.amount_to and r.amount_to <= r.amount_from:
                raise ValidationError(self.env._('The upper limit must be above the lower one.'))

    @api.depends('amount_from', 'amount_to')
    def _compute_display_name(self):
        for r in self:
            fmt = lambda x: '{:,.0f}'.format(x)  # noqa: E731
            r.display_name = ('Up to %s' % fmt(r.amount_to)) if not r.amount_from else (
                ('%s to %s' % (fmt(r.amount_from), fmt(r.amount_to))) if r.amount_to else ('Over %s' % fmt(r.amount_from)))

    @api.model
    def _rules(self, company):
        return self.search([('company_id', '=', company.id)], order='amount_from')

    @api.model
    def _band(self, company, amount):
        for r in self._rules(company):
            if amount > r.amount_from - 0.005 and (not r.amount_to or amount <= r.amount_to + 0.005):
                if r.amount_from and amount <= r.amount_from:
                    continue
                return r
        return self.browse()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sb_approval_rule_id = fields.Many2one('sb.po.approval.rule', 'Approval band', compute='_compute_sb_approval', store=False)
    sb_approver_group_id = fields.Many2one('res.groups', 'To be approved by', compute='_compute_sb_approval')
    sb_approved_by = fields.Many2one('res.users', 'Approved by', copy=False, readonly=True)
    sb_can_approve = fields.Boolean(compute='_compute_sb_can_approve', help='Waiting for approval, and this user may approve it.')
    sb_can_submit = fields.Boolean(compute='_compute_sb_can_approve', help='The requester, or a purchase clerk.')
    sb_can_cancel = fields.Boolean(compute='_compute_sb_can_approve')

    @api.depends('state', 'amount_total', 'user_id')
    @api.depends_context('uid')
    def _compute_sb_can_approve(self):
        user = self.env.user
        # a purchase clerk in their own right (approvers get the clerk role only through their approval group)
        clerk = self.env.ref('sonador_purchase.group_sb_purchase_user') in user.sudo().group_ids
        for o in self:
            mine = o._sb_can_approve() if o.state == 'to approve' and o.id else False
            o.sb_can_approve = bool(mine) or (mine is None and user.has_group('purchase.group_purchase_manager'))
            o.sb_can_submit = clerk or user in (o.user_id | o.create_uid)
            o.sb_can_cancel = o.sb_can_approve or (user in (o.user_id | o.create_uid)) or (clerk and o.state != 'to approve')

    def _sb_amount_company(self):
        self.ensure_one()
        return self.currency_id._convert(self.amount_total, self.company_id.currency_id, self.company_id,
                                         self.date_order or fields.Date.today())

    @api.depends('amount_total', 'currency_id', 'company_id')
    def _compute_sb_approval(self):
        for o in self:
            rule = self.env['sb.po.approval.rule'].sudo()._band(o.company_id, o._sb_amount_company()) if o.company_id else False
            o.sb_approval_rule_id = rule
            o.sb_approver_group_id = rule.group_id if rule else False

    def _sb_can_approve(self, user=None):
        """The user's groups cover the order's band (a higher band's group covers the lower bands)."""
        self.ensure_one()
        user = user or self.env.user
        Rule = self.env['sb.po.approval.rule'].sudo()
        rules = Rule._rules(self.company_id)
        c = self.company_id
        strict = 'sb_self_approval' in c._fields and c.sb_self_approval == 'up'
        requester = self.user_id or self.create_uid
        if strict and user == requester and requester:
            return False  # no self-approval: someone else in the band (or above) approves it
        if not rules:
            return None  # no limits configured: Odoo's own rule applies
        band = Rule._band(self.company_id, self._sb_amount_company())
        if not band:
            return False
        allowed = rules.filtered(lambda r: r.amount_from >= band.amount_from)
        mine = user.sudo().all_group_ids
        if any(r.group_id in mine for r in allowed):
            return True
        # the general manager's own order (nobody else holds the band): the company's named person approves it
        if strict and c.sb_gm_own_approver_id and user == c.sb_gm_own_approver_id and requester:
            return any(r.group_id in requester.sudo().all_group_ids for r in allowed)
        return False

    def _approval_allowed(self):
        mine = self._sb_can_approve()
        return super()._approval_allowed() if mine is None else mine

    def button_approve(self, force=False):
        for o in self.filtered(lambda x: x.state == 'to approve'):
            if o._sb_can_approve() is False:
                raise UserError(self.env._('%(po)s (%(amt)s) needs approval by %(group)s.', po=o.name,
                                           amt='{:,.2f}'.format(o._sb_amount_company()),
                                           group=o.sb_approver_group_id.display_name or self.env._('a higher approver')))
        res = super().button_approve(force=force)
        for o in self.filtered(lambda x: x.state == 'purchase' and not x.sb_approved_by):
            o.sb_approved_by = self.env.user
            o.message_post(body=self.env._('Approved by %(user)s (band: %(band)s).', user=self.env.user.name,
                                           band=o.sb_approval_rule_id.display_name or '-'))
        return res
