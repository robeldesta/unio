"""Who approves when the approver would be the person asking: a company rule every app with approval steps follows.

Self-approval "not allowed": nobody approves at any step what they submitted themselves; the step goes one level up
(the person's manager, else the manager of their department's parent department). The general manager's own requests
go to the person named on the company (e.g. the CEO or the board's delegate)."""
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_self_approval = fields.Selection([('allowed', 'Allowed'), ('up', 'Not allowed (goes one level up)')],
                                        'Self-approval', default='allowed', required=True,
                                        help='Not allowed: when the approver of a step would be the person who submitted '
                                             'it, the step goes to their own approver one level up.')
    sb_gm_own_approver_id = fields.Many2one('res.users', "Approves the general manager's own requests",
                                            domain=[('share', '=', False)],
                                            help="Decides the final-approval step of what the general manager (or anyone "
                                                 "with the final-approval role) submits, e.g. the CEO.")

    def _sb_no_self_approval(self):
        return (self or self.env.company).sb_self_approval == 'up'


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _sb_role_peer(self, group, company=None):
        """Another active person holding this role (the finance check is never passed up to management: it goes to
        another finance person). Nobody else: an empty recordset."""
        self.ensure_one()
        company = company or self.env.company
        holders = self.env.ref(group).sudo().all_user_ids.filtered(
            lambda u: u != self and u.active and not u.share and company in u.company_ids)
        return holders.sorted('id')[:1]

    def _sb_one_up(self, company=None):
        """The person one level above this user: their manager, else the manager of the parent department, else the
        person who approves the general manager's own requests. Never the user themselves."""
        self.ensure_one()
        company = company or self.env.company
        emps = self.sudo().employee_ids
        emp = emps.filtered(lambda e: e.company_id == company)[:1] or emps[:1]
        for cand in (emp.parent_id.user_id, emp.department_id.manager_id.user_id, emp.department_id.parent_id.manager_id.user_id,
                     company.sb_gm_own_approver_id):
            if cand and cand != self and cand.active:
                return cand
        return self.browse()
