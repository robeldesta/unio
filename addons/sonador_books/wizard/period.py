from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_label


class SbPeriodWizard(models.TransientModel):
    _name = 'sb.period.wizard'
    _description = 'Accounting Periods'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    fiscal_year = fields.Integer('Fiscal year', required=True,
                                 default=lambda s: fiscal_year_of(fields.Date.context_today(s)))
    fiscal_year_label = fields.Char('Fiscal Year (E.C.)', compute='_compute_fiscal_year_label')
    lock_date = fields.Date(related='company_id.fiscalyear_lock_date', string='Books closed through')
    lock_date_ec = fields.Char(compute='_compute_lock_date_ec', string='Closed through (E.C.)')
    line_ids = fields.One2many('sb.period.wizard.line', 'wizard_id', compute='_compute_lines', store=True,
                               readonly=False)

    closer_names = fields.Char(compute='_compute_closer_names')

    @api.depends('company_id')
    def _compute_closer_names(self):
        group = self.env.ref('sonador_books.group_sb_manager', raise_if_not_found=False)
        for w in self:
            users = group.sudo().user_ids.filtered(lambda u: u.active and not u.share and w.company_id in u.company_ids
                                                   and u.id != 1) if group else []
            names = sorted(users.mapped('name')) if users else []
            w.closer_names = (', '.join(names[:3]) + (' or another manager' if len(names) > 3 else '')) if names else \
                self.env._('a manager')

    @api.depends('fiscal_year')
    def _compute_fiscal_year_label(self):
        for w in self:
            w.fiscal_year_label = fy_label(w.fiscal_year)

    @api.depends('fiscal_year')
    def _compute_display_name(self):
        for w in self:
            w.display_name = self.env._('Accounting Periods')

    @api.depends('lock_date')
    def _compute_lock_date_ec(self):
        for w in self:
            w.lock_date_ec = format_ethiopian(w.lock_date)

    @api.depends('fiscal_year', 'company_id.fiscalyear_lock_date')
    def _compute_lines(self):
        today = fields.Date.context_today(self)
        for w in self:
            lock = w.company_id.fiscalyear_lock_date
            commands = [Command.clear()]
            for fy in (w.fiscal_year - 1, w.fiscal_year):
                for no, name, start, end in fiscal_periods(fy):
                    commands.append(Command.create({
                        'fiscal_year': fy,
                        'number': no,
                        'name': name,
                        'date_from': start,
                        'date_to': end,
                        'closed': bool(lock and end <= lock),
                        'current': start <= today <= end,
                    }))
            w.line_ids = commands

    def action_prev_year(self):
        self.fiscal_year -= 1
        return self._reopen()

    def action_next_year(self):
        self.fiscal_year += 1
        return self._reopen()

    def _reopen(self):
        self._compute_lines()  # a period just closed or reopened shows so at once
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'name': self.env._('Accounting Periods'),
        }


class SbPeriodWizardLine(models.TransientModel):
    _name = 'sb.period.wizard.line'
    _description = 'Accounting Period'
    _order = 'date_from'

    wizard_id = fields.Many2one('sb.period.wizard', required=True, ondelete='cascade')
    fiscal_year = fields.Integer('Fiscal year')
    fy_label = fields.Char('FY', compute='_compute_fy_label')
    number = fields.Integer('Period')
    name = fields.Char('Month (E.C.)')
    date_from = fields.Date('From')
    date_to = fields.Date('To')
    closed = fields.Boolean()
    current = fields.Boolean()

    @api.depends('fiscal_year')
    def _compute_fy_label(self):
        for line in self:
            line.fy_label = str(line.fiscal_year)

    def action_close_through(self):
        """Close the books up to and including this period (blocks posting for everyone)."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can close periods.'))
        company = self.wizard_id.company_id
        company.sudo().fiscalyear_lock_date = self.date_to
        return self.wizard_id._reopen()

    def action_reopen_from(self):
        """Ask why on the same screen; the period is reopened from there (see period_close)."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can reopen periods.'))
        self._sb_check_reopen()
        self.wizard_id.write({'reopen_from': self.date_from, 'reopen_name': self.name, 'reopen_reason': False,
                              'close_to': False, 'close_name': False})
        return self.wizard_id._reopen()

    def _sb_check_reopen(self):
        company = self.wizard_id.company_id
        if company.hard_lock_date and company.hard_lock_date >= self.date_from:
            raise UserError(self.env._('This period is under the hard lock date and cannot be reopened.'))
        closed = company._sb_closed_through()
        if closed and self.date_from <= closed:
            raise UserError(self.env._(
                '%(name)s is in a closed year: the year ending %(ec)s has a year-end closing entry. Reopen that year in the '
                'Year-End Closing first (it reverses the closing entry), then reopen the period.', name=self.name,
                ec=format_ethiopian(closed)))
