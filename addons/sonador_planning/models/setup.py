"""Planning setup brought over from the old planning: roles with their must-do items (the role's template lines), indicator
types (how a result is checked), and change requests on approved plans (a small proposal the manager accepts in one click,
instead of sending the whole plan back)."""
from odoo import fields, models

CHANGE_KINDS = [('edit', 'Change a task'), ('add', 'Add a task'), ('remove', 'Drop a task')]
CHANGE_STATES = [('pending', 'Waiting'), ('accepted', 'Accepted'), ('refused', 'Refused')]


class SbPlanRole(models.Model):
    _name = 'sb.plan.role'
    _description = 'Planning role'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    item_ids = fields.One2many('sb.plan.role.item', 'role_id', 'Must-do items')
    active = fields.Boolean(default=True)
    legacy_ref = fields.Char(index=True)


class SbPlanRoleItem(models.Model):
    _name = 'sb.plan.role.item'
    _description = 'Must-do item of a role'
    _order = 'role_id, sequence, id'

    role_id = fields.Many2one('sb.plan.role', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char('What', required=True)
    done_when = fields.Char('Done when')
    target_qty = fields.Float('Target')
    unit = fields.Char()
    output = fields.Char('Evidence')
    indicator_id = fields.Many2one('sb.plan.indicator', 'Checked by', ondelete='set null')
    mandatory = fields.Boolean('Must be in every plan', default=True,
                               help='Off: offered as a starting line only.')
    legacy_ref = fields.Char(index=True)


class SbPlanIndicator(models.Model):
    _name = 'sb.plan.indicator'
    _description = 'How a result is checked'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    code = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    requires_document = fields.Boolean('Expects a document', help='When done, the check-in asks where the document is.')
    uses_progress = fields.Boolean('Uses progress %', default=True)
    description = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    active = fields.Boolean(default=True)
    legacy_ref = fields.Char(index=True)


class SbPlanChange(models.Model):
    _name = 'sb.plan.change'
    _description = 'Change to an approved plan'
    _order = 'create_date desc, id desc'

    plan_id = fields.Many2one('sb.plan', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='plan_id.employee_id', store=True, index=True)
    task_id = fields.Many2one('sb.plan.task', ondelete='cascade', help='Empty when the change adds a task.')
    kind = fields.Selection(CHANGE_KINDS, required=True)
    vals = fields.Json('Proposed values')
    summary = fields.Char()
    reason = fields.Char()
    state = fields.Selection(CHANGE_STATES, default='pending', required=True, index=True)
    decided_by = fields.Many2one('res.users')
    decided_at = fields.Datetime()
    decision_note = fields.Char()


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_pl_role_id = fields.Many2one('sb.plan.role', 'Planning role', groups='hr.group_hr_user', ondelete='set null')


class SbPerfGoal(models.Model):
    _inherit = 'sb.perf.goal'

    sb_pl_legacy_ref = fields.Char(index=True, copy=False)
