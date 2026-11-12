"""What Unio AI keeps between conversations: the company's facts and house rules (admins keep the list; the assistant
may propose one, which an admin adds with a click), and a few lines each person writes about their own work. Both go
with every question; the company part is cached, so it costs next to nothing."""
from odoo import api, fields, models

# what a person may change on their own user
SELF_FIELDS = ['sb_ai_about']


class SbAiMemory(models.Model):
    _name = 'sb.ai.memory'
    _description = 'Unio AI company memory'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    text = fields.Char('Fact or house rule', required=True, help='One line the assistant should always know, e.g. "We are VAT registered."')
    active = fields.Boolean(default=True)
    source = fields.Selection([('admin', 'Written by an admin'), ('assistant', 'Proposed by the assistant')], default='admin', required=True)

    @api.model
    def for_prompt(self):
        return [m.text for m in self.sudo().search([('company_id', '=', self.env.company.id)], limit=60)]


class ResUsers(models.Model):
    _inherit = 'res.users'

    sb_ai_about = fields.Text('About me, for Unio AI', help='Your role and what you usually do: the assistant reads it with every question.')

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + SELF_FIELDS

    def _sb_ai_profile(self):
        """A few lines about the person: job and department from People, the apps they use, what they wrote themselves."""
        self.ensure_one()
        u = self.sudo()
        lines = []
        emp = u.employee_id if 'employee_id' in u._fields else False
        if emp:
            job = emp.job_title or (emp.job_id.name if 'job_id' in emp._fields and emp.job_id else '')
            dept = emp.department_id.name if emp.department_id else ''
            if job or dept:
                lines.append('Job: %s' % ', '.join(x for x in (job, dept) if x))
        try:
            menus = self.env['ir.ui.menu'].with_user(self).load_menus(False)
            apps = [menus[i]['name'] for i in menus['root']['children']]
            lines.append('Apps they use: %s' % ', '.join(a for a in apps if a not in ('Me', 'Discuss', 'Apps', 'Settings')))
        except Exception:  # noqa: BLE001 - the profile never stops a question
            pass
        if u.has_group('base.group_system'):
            lines.append('They are an administrator of the system.')
        if u.sb_ai_about:
            lines.append('In their words: %s' % u.sb_ai_about.strip()[:600])
        return lines
