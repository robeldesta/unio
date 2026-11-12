"""A report template: the sections of a role's report (weekly, monthly, quarterly, half-yearly, yearly), in order, each with the question the person
answers in their own words and the blocks it starts with. Finance sets one up per role; people then fill it every
period."""
from odoo import api, fields, models


class SbRepTemplate(models.Model):
    _name = 'sb.rep.template'
    _description = 'Report template'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    period = fields.Selection([('week', 'Weekly'), ('month', 'Monthly')], default='week', required=True,
                              help='No longer used: every report is written weekly, monthly, quarterly, half-yearly and yearly.')
    department_ids = fields.Many2many('hr.department', string='Heads of',
                                      help='The managers of these departments write it (Store: the store and Finance).')
    employee_ids = fields.Many2many('hr.employee', string='Also written by', help='People named here write it too.')
    group_ids = fields.Many2many('res.groups', string='Or whoever holds',
                                 help='A role (e.g. Sales: commercial approval): whoever holds it writes it.')

    def _sb_writers_include(self, employee):
        """Whether `employee` writes this report: head of its department, named, or holding its role. Nothing set: nobody."""
        self.ensure_one()
        if not employee:
            return False
        user = employee.user_id
        return employee in self.department_ids.manager_id or employee in self.employee_ids \
            or bool(user and self.group_ids and (user.all_group_ids & self.group_ids))
    def _sb_writers(self):
        """Everyone who writes this report."""
        self.ensure_one()
        E = self.env['hr.employee'].sudo()
        users = self.group_ids.all_user_ids if 'all_user_ids' in self.group_ids._fields else self.group_ids.user_ids
        return (self.department_ids.manager_id | self.employee_ids | E.search([('user_id', 'in', users.ids)])).filtered('active')
    section_ids = fields.One2many('sb.rep.template.section', 'template_id', copy=True)
    config = fields.Json(help='Block settings, e.g. the accounts of the marketing expenses block and their groups.')


class SbRepTemplateSection(models.Model):
    _name = 'sb.rep.template.section'
    _description = 'Report template section'
    _order = 'sequence, id'

    template_id = fields.Many2one('sb.rep.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    title = fields.Char(required=True)
    prompt = fields.Char('What to write', help='The question the person answers under this section.')
    required = fields.Boolean('Words required', help='The report is not sent while this section has no words.')
    block_codes = fields.Char('Blocks', help='Comma separated block codes the section starts with.')
