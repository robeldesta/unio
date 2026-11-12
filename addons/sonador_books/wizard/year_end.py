from datetime import timedelta

from markupsafe import escape

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_label
from ..models.period import YEAR_END


class AccountMove(models.Model):
    _inherit = 'account.move'

    sb_closing = fields.Boolean('Year-end closing entry', copy=False, index=True,
                                help='Moves the year\'s profit or loss to retained earnings. '
                                     'Income statements leave it out so the closed year still reads correctly.')


class SbYearEndWizard(models.TransientModel):
    _name = 'sb.year.end.wizard'
    _description = 'Year-End Closing'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    year = fields.Selection('_year_choices', string='Fiscal year to close', required=True,
                            default=lambda s: str(fiscal_year_of(fields.Date.context_today(s)) - 1))
    fiscal_year = fields.Integer(compute='_compute_fiscal_year')

    @api.model
    def _year_choices(self):
        # a year is closed once it has ended: the current one is offered only to reopen it, if it was closed
        current = fiscal_year_of(fields.Date.context_today(self))
        end = fiscal_periods(current)[-1][3]
        closed = self.env['account.move'].sudo().search_count([('sb_closing', '=', True), ('state', '=', 'posted'),
                                                               ('date', '=', end)], limit=1)
        return [(str(y), fy_label(y)) for y in range(current - 5, current + (1 if closed else 0))]

    @api.depends('year')
    def _compute_fiscal_year(self):
        for w in self:
            w.fiscal_year = int(w.year or 0)

    @api.depends('year')
    def _compute_display_name(self):
        for w in self:
            w.display_name = self.env._('Year-End Closing')
    fy_label = fields.Char(compute='_compute_info', string='Fiscal year')
    date_from = fields.Date(compute='_compute_info')
    date_to = fields.Date(compute='_compute_info')
    period_label = fields.Char(compute='_compute_info', string='Dates')
    retained_account_id = fields.Many2one(
        'account.account', string='Retained earnings account', required=True,
        domain=[('account_type', 'in', ('equity', 'equity_unaffected'))],
        default=lambda s: s.env.company.retained_earnings_account_id
        if 'retained_earnings_account_id' in s.env.company._fields else False)
    profit = fields.Monetary(compute='_compute_info', string='Profit / (loss) to close')
    currency_id = fields.Many2one(related='company_id.currency_id')
    status = fields.Selection([('open', 'Open'), ('closed', 'Closed')], compute='_compute_info')
    closing_voucher_id = fields.Many2one('sb.voucher', compute='_compute_info', string='Closing voucher')
    checklist = fields.Html(compute='_compute_info', sanitize=False)
    hard_lock = fields.Boolean('Lock permanently (cannot be reopened)',
                               help='Use only after the auditors have signed off.')
    reopen_reason = fields.Char('Reason for reopening', help='Kept in the period close log.')

    def _range(self):
        periods = fiscal_periods(self.fiscal_year)
        return periods[0][2], periods[-1][3]

    def _closing_voucher(self):
        start, end = self._range()
        return self.env['sb.voucher'].search([
            ('company_id', '=', self.company_id.id), ('voucher_type', '=', 'journal'), ('state', '=', 'posted'),
            ('move_id.sb_closing', '=', True), ('date', '=', end)], limit=1)

    def _pl_balances(self):
        start, end = self._range()
        res = self.env['account.move.line']._read_group([
            ('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'),
            ('date', '>=', start), ('date', '<=', end),
            ('account_id.include_initial_balance', '=', False), ('move_id.sb_closing', '=', False)],
            ['account_id'], ['balance:sum'])
        return [(acc, bal) for acc, bal in res if abs(bal or 0.0) > 0.005]

    @api.depends('year', 'company_id')
    def _compute_info(self):
        for w in self:
            start, end = w._range()
            w.date_from, w.date_to = start, end
            w.fy_label = fy_label(w.fiscal_year)
            w.period_label = '%s to %s  (%s to %s)' % (format_ethiopian(start), format_ethiopian(end),
                                                       start.strftime('%d %b %Y'), end.strftime('%d %b %Y'))
            w.profit = -sum(b for _a, b in w._pl_balances())
            v = w._closing_voucher()
            w.closing_voucher_id = v
            w.status = 'closed' if v else 'open'
            w.checklist = w._checklist_html(start, end)

    def _checklist_html(self, start, end):
        review = self.env['sb.report'].get_report('accounting_review', {
            'date_from': fields.Date.to_string(start), 'date_to': fields.Date.to_string(end)})
        rows = []
        for r in review['rows']:
            v = r['values']
            icon = {'OK': ('fa-check-circle', '#1E8E5A'), 'Review': ('fa-exclamation-circle', '#B7791F'),
                    'Fix': ('fa-times-circle', '#C0392B')}[v['status']]
            rows.append('<tr><td style="padding:.3rem .6rem"><i class="fa %s" style="color:%s"></i></td>'
                        '<td style="padding:.3rem .6rem">%s</td><td style="padding:.3rem .6rem;text-align:right">%s</td>'
                        '<td style="padding:.3rem .6rem;color:#6B7A85">%s</td></tr>'
                        % (icon[0], icon[1], v['check'], v['count'] or '', '' if v['status'] == 'OK' else v['advice']))
        lock = self.company_id.fiscalyear_lock_date
        rows.append('<tr><td style="padding:.3rem .6rem"><i class="fa %s" style="color:%s"></i></td>'
                    '<td style="padding:.3rem .6rem">Periods of the year closed</td><td></td>'
                    '<td style="padding:.3rem .6rem;color:#6B7A85">%s</td></tr>'
                    % (('fa-check-circle', '#1E8E5A', 'Closed through %s' % format_ethiopian(lock)) if lock and lock >= end
                       else ('fa-info-circle', '#5D8DA8', 'The wizard closes them for you')))
        blocking = self._sb_blocking(start, end)
        rows.append('<tr><td style="padding:.3rem .6rem"><i class="fa %s" style="color:%s"></i></td>'
                    '<td style="padding:.3rem .6rem">Unposted entries, vouchers and stock documents, documents in correction '
                    'or being edited</td><td style="padding:.3rem .6rem;text-align:right">%s</td>'
                    '<td style="padding:.3rem .6rem;color:#6B7A85">%s</td></tr>'
                    % ((('fa-times-circle', '#C0392B') if blocking else ('fa-check-circle', '#1E8E5A'))
                       + (len(blocking) or '', escape('Post, finish or delete them first: %s' % '; '.join(blocking[:8]))
                          if blocking else 'None')))
        rows.append(self._suspense_check_html(end))
        return '<table style="width:100%%">%s</table>' % ''.join(rows)

    def _sb_blocking(self, start, end):
        """What dated in the year is not in the books as it will be: drafts (vouchers with lines, journal entries, stock
        documents), documents reset to draft for a correction, vouchers reopened with Edit. The year is not closed over them."""
        company = self.company_id
        span = [('company_id', '=', company.id), ('date', '>=', start), ('date', '<=', end)]
        out = []
        V = self.env['sb.voucher'].sudo()
        drafts = V.search(span + [('state', '=', 'draft'), ('line_ids', '!=', False)])
        if drafts:
            out.append('%s draft vouchers' % len(drafts))
        editing = V.search(span + [('sb_editing', '=', True)])
        if editing:
            out.append('being edited: %s' % ', '.join(editing.mapped('name')))
        moves = self.env['account.move'].sudo().search(span + [('state', '=', 'draft')])
        if moves:
            out.append('%s draft journal entries (%s)' % (len(moves), ', '.join(moves[:5].mapped(
                lambda m: m.name if m.name and m.name != '/' else m.ref or str(m.id)))))
        if 'sb.stock.voucher' in self.env:
            S = self.env['sb.stock.voucher'].sudo()
            correcting = S.search(span + [('state', '=', 'draft'), ('sb_correction_before', '!=', False)])
            if correcting:
                out.append('in correction: %s' % ', '.join(correcting.mapped('name')))
            stock = S.search(span + [('state', '=', 'draft'), ('sb_correction_before', '=', False), ('line_ids', '!=', False)])
            if stock:
                out.append('%s draft stock documents' % len(stock))
        return out

    def _suspense_check_html(self, end):
        """Suspense and clearing accounts should be empty at year end: what is parked there is not yet in its real account."""
        company = self.company_id
        accounts = self.env['account.account'].with_company(company).search([
            ('company_ids', 'in', company.id), '|', ('name', 'ilike', 'suspense'), ('name', 'ilike', 'clearing')])
        accounts |= company.account_journal_suspense_account_id
        res = self.env['account.move.line']._read_group([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('date', '<=', end),
            ('account_id', 'in', accounts.ids)], ['account_id'], ['balance:sum'])
        open_ = [(acc, bal) for acc, bal in res if not company.currency_id.is_zero(bal or 0.0)]
        if not open_:
            icon, note = ('fa-check-circle', '#1E8E5A'), 'All at zero'
        else:
            icon = ('fa-exclamation-circle', '#B7791F')
            note = 'Clear them before closing: %s' % '; '.join(
                '%s %s' % (acc.display_name, '{:,.2f}'.format(bal)) for acc, bal in open_[:6]) + (' …' if len(open_) > 6 else '')
        return ('<tr><td style="padding:.3rem .6rem"><i class="fa %s" style="color:%s"></i></td>'
                '<td style="padding:.3rem .6rem">Suspense and clearing accounts at year end</td>'
                '<td style="padding:.3rem .6rem;text-align:right">%s</td><td style="padding:.3rem .6rem;color:#6B7A85">%s</td></tr>'
                % (icon[0], icon[1], len(open_) or '', escape(note)))

    def _sb_is_retained(self, account):
        """Retained earnings: the company's retained earnings account, an account of Odoo's current year earnings type,
        or one on the retained earnings line of the statement of financial position (SFP-EQ-03)."""
        company = self.company_id or self.env.company
        if not account:
            return False
        if account.account_type == 'equity_unaffected':
            return True
        if 'retained_earnings_account_id' in company._fields and account == company.retained_earnings_account_id:
            return True
        line = account.sb_ifrs_line_id if 'sb_ifrs_line_id' in account._fields else False
        return bool(account.account_type == 'equity' and line and line.code == 'SFP-EQ-03')

    def action_close_year(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can close a year.'))
        if self._closing_voucher():
            raise UserError(self.env._('%s is already closed.', self.fy_label))
        start, end = self._range()
        if end >= fields.Date.context_today(self):
            raise UserError(self.env._('%(fy)s ends on %(end)s (%(ec)s): a year is closed after it has ended.',
                                       fy=self.fy_label, end=end, ec=format_ethiopian(end)))
        company = self.company_id
        lock = company.fiscalyear_lock_date
        if lock and lock >= end:
            raise UserError(self.env._('The periods of %s are already locked. Reopen them in Accounting Periods first, '
                                       'then run the wizard.', self.fy_label))
        if not self._sb_is_retained(self.retained_account_id):
            raise UserError(self.env._('%s is not a retained earnings account: the year\'s result is closed to retained '
                                       'earnings (the company\'s retained earnings account, or an account of the current '
                                       'year earnings type), never to capital or reserves.',
                                       self.retained_account_id.display_name))
        blocking = self._sb_blocking(start, end)
        if blocking:
            raise UserError(self.env._('%(fy)s cannot be closed yet: %(what)s. Post, finish or delete them first.',
                                       fy=self.fy_label, what='; '.join(blocking)))
        lines = []
        total = 0.0
        for acc, bal in self._pl_balances():
            total += bal
            lines.append(Command.create({'account_id': acc.id, 'name': self.env._('Close %s', acc.display_name),
                                         'debit': -bal if bal < 0 else 0.0, 'credit': bal if bal > 0 else 0.0}))
        if not lines:
            raise UserError(self.env._('There is no income or expense to close in %s.', self.fy_label))
        lines.append(Command.create({'account_id': self.retained_account_id.id,
                                     'name': self.env._('Result of %s', self.fy_label),
                                     'debit': total if total > 0 else 0.0, 'credit': -total if total < 0 else 0.0}))
        voucher = self.env['sb.voucher'].create({
            'voucher_type': 'journal', 'company_id': company.id, 'date': end,
            'memo': self.env._('Year-end closing %s', self.fy_label), 'reference': 'YEAR-END %d' % self.fiscal_year,
            'line_ids': lines,
        })
        from ..models.voucher_controls import CLOSING_POST
        voucher.with_context(sb_closing_post=CLOSING_POST).action_post()  # closing income in a filed VAT month is no sale
        voucher.move_id.with_context(sb_year_end=YEAR_END).sb_closing = True
        locking = company.sudo().with_context(sb_year_end=YEAR_END, sb_lock_note='Year-end close %s' % self.fy_label)
        locking.fiscalyear_lock_date = end
        if self.hard_lock:
            locking.hard_lock_date = end
        self.env['sb.lock.log']._log(company, 'close_year', 'hard_lock_date' if self.hard_lock else 'fiscalyear_lock_date',
                                     lock, end, '%s closed, closing entry %s' % (self.fy_label, voucher.name))
        FY = self.env['account.fiscal.year'] if 'account.fiscal.year' in self.env else None
        if FY is not None:
            fy = FY.search([('company_id', '=', company.id), ('date_from', '=', start)], limit=1)
            if fy:
                fy.write({'state': 'closed', 'closing_move_id': voucher.move_id.id})
            nstart, nend = fiscal_periods(self.fiscal_year + 1)[0][2], fiscal_periods(self.fiscal_year + 1)[-1][3]
            if not FY.search_count([('company_id', '=', company.id), ('date_from', '<=', nend), ('date_to', '>=', nstart)]):
                FY.create({'name': fy_label(self.fiscal_year + 1), 'date_from': nstart, 'date_to': nend,
                           'company_id': company.id})
        voucher.message_post(body=self.env._('Year closed by %s: profit of %s moved to %s; books locked through %s.',
                                             self.env.user.name, '{:,.2f}'.format(-total),
                                             self.retained_account_id.display_name, format_ethiopian(end)))
        return self._reopen_form()

    def action_reopen_year(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can reopen a year.'))
        reason = (self.reopen_reason or '').strip()
        if not reason:
            raise UserError(self.env._('Enter the reason for reopening %s: it is kept in the period close log.', self.fy_label))
        voucher = self._closing_voucher()
        if not voucher:
            raise UserError(self.env._('%s is not closed.', self.fy_label))
        start, end = self._range()
        company = self.company_id
        if company.hard_lock_date and company.hard_lock_date >= end:
            raise UserError(self.env._('%s is locked permanently and cannot be reopened.', self.fy_label))
        later = company._sb_closed_through()
        if later and later > end:
            raise UserError(self.env._('A later year (ending %(end)s, %(ec)s) is closed too. Reopen that year first.',
                                       end=later, ec=format_ethiopian(later)))
        old = company.fiscalyear_lock_date
        company.sudo().with_context(sb_year_end=YEAR_END, sb_lock_note='Year-end reopen %s: %s' % (self.fy_label, reason)) \
            .fiscalyear_lock_date = start - timedelta(days=1)
        voucher = voucher.with_context(sb_year_end=YEAR_END, sb_void_reason=self.env._('Year %(fy)s reopened: %(why)s',
                                                                                    fy=self.fy_label, why=reason))
        voucher.action_void()
        # the reversal is part of the closing pair: income statements ignore both
        voucher.move_id.reversal_move_ids.write({'sb_closing': True})
        self.env['sb.lock.log']._log(company, 'reopen_year', 'fiscalyear_lock_date', old, start - timedelta(days=1),
                                     '%s reopened, closing entry %s reversed. Reason: %s' % (self.fy_label, voucher.name, reason))
        if 'account.fiscal.year' in self.env:
            fy = self.env['account.fiscal.year'].search([('company_id', '=', company.id), ('date_from', '=', start)], limit=1)
            fy.write({'state': 'open', 'closing_move_id': False})
        return self._reopen_form()

    def action_open_review(self):
        start, end = self._range()
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.report', 'name': 'Internal Accounting Review',
                'context': {'report': 'accounting_review', 'catalog_key': 'acc_review', 'ui': {'mode': 'custom'},
                            'options': {'date_from': fields.Date.to_string(start), 'date_to': fields.Date.to_string(end),
                                        '_title': 'Internal Accounting Review'}}}

    def _reopen_form(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Year-End Closing')}


# ---------------------------------------------------------------------- one way to close
# The fiscal year app (om_fiscal_year) brings its own year-end closing, period closing and lock dates screens. Years are
# closed with Year-End Closing and periods in Accounting Periods only: the other screens are hidden and refuse to run.
OTHER_CLOSING_MENUS = ('om_fiscal_year.menu_fiscal_year_closing', 'om_fiscal_year.menu_account_period_closing',
                       'om_fiscal_year.menu_action_change_lock_date')


def _use_the_wizard(env):
    return UserError(env._('Close and reopen years with Year-End Closing, and periods in Accounting Periods '
                           '(Accounting > Setup).'))


class AccountFiscalYear(models.Model):
    _inherit = 'account.fiscal.year'

    def _close(self, *args, **kwargs):
        raise _use_the_wizard(self.env)

    def action_open_closing_wizard(self):
        raise _use_the_wizard(self.env)

    def action_reopen(self):
        raise _use_the_wizard(self.env)

    @api.model
    def _sb_hide_other_closing(self):
        for xmlid in OTHER_CLOSING_MENUS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu and menu.active:
                menu.sudo().active = False


class AccountFiscalYearClosing(models.TransientModel):
    _inherit = 'account.fiscal.year.closing'

    def action_close(self):
        raise _use_the_wizard(self.env)


class AccountPeriodClosing(models.Model):
    _inherit = 'account.period.closing'

    def action_close(self):
        raise _use_the_wizard(self.env)

    def action_reopen(self):
        raise _use_the_wizard(self.env)


class ChangeLockDate(models.TransientModel):
    _inherit = 'change.lock.date'

    def update_lock_date(self):
        raise _use_the_wizard(self.env)
