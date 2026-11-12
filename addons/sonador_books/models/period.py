"""Closed periods and closed years.

Every change of a lock date (closing or reopening periods, the year-end close and reopen, Odoo's own lock date settings)
is written to the period log: who, when, the old and the new date. So is every lock date exception (granted, revoked,
expired) and every change made by hand to a document series counter. The log cannot be edited or deleted.

A year closed with the Year-End Wizard stays locked: the books cannot be reopened (nor a lock exception granted) earlier
than the end of a closed year until that year is reopened in the wizard, which reverses its closing entry.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ethiopian_calendar import format_ethiopian

LOCK_FIELDS = [
    ('fiscalyear_lock_date', 'Books (everyone)'),
    ('tax_lock_date', 'Tax returns'),
    ('sale_lock_date', 'Sales'),
    ('purchase_lock_date', 'Purchases'),
    ('hard_lock_date', 'Books, permanently'),
]
# a context value no client request can carry: only the Year-End Wizard may unlock a closed year
YEAR_END = object()
# a lock date exception is a short, explained opening: at most this many days
EXCEPTION_MAX_DAYS = 7


class SbLockLog(models.Model):
    _name = 'sb.lock.log'
    _description = 'Period Close Log'
    _order = 'logged_at desc, id desc'

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True, ondelete='cascade')
    logged_at = fields.Datetime('When', required=True, readonly=True, default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', 'By', required=True, readonly=True, default=lambda s: s.env.user)
    event = fields.Selection([('lock', 'Locked'), ('unlock', 'Reopened'), ('close_year', 'Year closed'),
                              ('reopen_year', 'Year reopened'), ('exception', 'Exception granted'),
                              ('exception_end', 'Exception ended'), ('series', 'Numbering changed'),
                              ('partner_merge', 'Contacts merged')], required=True, readonly=True)
    lock_field = fields.Selection(LOCK_FIELDS, 'Lock', readonly=True)
    old_date = fields.Date('Was', readonly=True)
    new_date = fields.Date('Now', readonly=True)
    note = fields.Char(readonly=True)

    def write(self, vals):
        raise UserError(self.env._('The period close log cannot be changed.'))

    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._('The period close log cannot be deleted.'))
        return super().unlink()

    @api.model
    def _log(self, company, event, lock_field=False, old=False, new=False, note=False):
        return self.sudo().create({'company_id': company.id, 'user_id': self.env.user.id, 'event': event,
                                   'lock_field': lock_field, 'old_date': old, 'new_date': new, 'note': note})


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Odoo's restrictive audit trail: posted entries and their history cannot be deleted. Always on.
    restrictive_audit_trail = fields.Boolean(default=True)

    def _compute_force_restrictive_audit_trail(self):
        for company in self:
            company.force_restrictive_audit_trail = True

    def _sb_closed_through(self):
        """End of the last year closed with the Year-End Wizard (a posted closing entry that is not reversed), or False."""
        self.ensure_one()
        closings = self.env['account.move'].sudo().search([
            ('company_id', '=', self.id), ('sb_closing', '=', True), ('state', '=', 'posted'),
            ('reversed_entry_id', '=', False)], order='date desc')
        # only the wizard's own reopen undoes a closing entry: its reversal is itself marked as a closing entry
        closing = closings.filtered(lambda m: not m.reversal_move_ids.filtered(lambda r: r.sb_closing and r.state == 'posted'))[:1]
        return closing.date or False

    def _sb_out_of_books(self, through):
        """Documents dated on or before `through` whose entry is out of the books for now: a voucher reopened with Edit, a
        stock document reset to draft for a correction. A period closed over them would lose them."""
        self.ensure_one()
        span = [('company_id', '=', self.id), ('date', '<=', through)]
        names = [self.env._('%s (being edited)', n) for n in
                 self.env['sb.voucher'].sudo().search(span + [('sb_editing', '=', True)]).mapped('name')]
        if 'sb.stock.voucher' in self.env:
            names += [self.env._('%s (in correction)', n) for n in self.env['sb.stock.voucher'].sudo().search(
                span + [('state', '=', 'draft'), ('sb_correction_before', '!=', False)]).mapped('name')]
        return names

    def write(self, vals):
        changed = [f for f, _label in LOCK_FIELDS if f in vals]
        if not changed:
            return super().write(vals)
        before = {c.id: {f: c[f] for f in changed} for c in self}
        if not self.env.su:
            today = fields.Date.context_today(self)
            for f in ('fiscalyear_lock_date', 'hard_lock_date'):
                day = fields.Date.to_date(vals.get(f))
                if day and day >= today:
                    raise UserError(self.env._(
                        'The books can be closed only through a day that has passed: %(date)s (%(ec)s) is today or later.',
                        date=day, ec=format_ethiopian(day)))
        if 'fiscalyear_lock_date' in vals and self.env.context.get('sb_year_end') is not YEAR_END:
            new = fields.Date.to_date(vals['fiscalyear_lock_date'])
            for company in self:
                closed = company._sb_closed_through()
                if closed and (not new or new < closed):
                    raise UserError(self.env._(
                        'The year ending %(end)s (%(ec)s) is closed and has a year-end closing entry. Reopen that year in the '
                        'Year-End Closing first: it reverses the closing entry and unlocks the year.',
                        end=closed, ec=format_ethiopian(closed)))
        for f in ('fiscalyear_lock_date', 'hard_lock_date'):
            new = fields.Date.to_date(vals.get(f)) if f in vals else None
            for company in self.filtered(lambda c: new and (not c[f] or new > c[f])):
                if open_ := company._sb_out_of_books(new):
                    raise UserError(self.env._(
                        'The books cannot be closed through %(date)s (%(ec)s): %(docs)s dated in it %(verb)s out of the books '
                        'until posted again. Post or discard them first.', date=new, ec=format_ethiopian(new),
                        docs=', '.join(open_), verb=self.env._('is') if len(open_) == 1 else self.env._('are')))
        res = super().write(vals)
        Log = self.env['sb.lock.log']
        note = self.env.context.get('sb_lock_note') or False
        for company in self:
            for f in changed:
                old, new = before[company.id][f], company[f]
                if old != new:
                    Log._log(company, 'lock' if new and (not old or new > old) else 'unlock', f, old, new, note)
        return res

    @api.model
    def _sb_enforce_controls(self):
        """Run on every module update: the restrictive audit trail is on, and a closed year whose lock was removed
        is locked again (logged)."""
        for company in self.sudo().search([]):
            if not company.restrictive_audit_trail:
                company.restrictive_audit_trail = True
            closed = company._sb_closed_through()
            if closed and (not company.fiscalyear_lock_date or company.fiscalyear_lock_date < closed):
                company.with_context(sb_lock_note='Closed year locked again (it had a closing entry)').fiscalyear_lock_date = closed


class AccountLockException(models.Model):
    _inherit = 'account.lock_exception'

    sb_end_logged = fields.Boolean(copy=False, readonly=True)

    def _sb_describe(self):
        self.ensure_one()
        return self.env._('Exception %(id)s granted by %(by)s for %(user)s%(until)s%(reason)s', id=self.id,
                          by=self.create_uid.name or self.env.user.name,
                          user=self.user_id.name or self.env._('everyone'),
                          until=self.env._(', until %s', fields.Datetime.to_string(self.end_datetime)) if self.end_datetime else '',
                          reason=': %s' % self.reason if self.reason else '')

    def _sb_log_end(self, how):
        for exc in self.filtered(lambda e: not e.sb_end_logged):
            self.env['sb.lock.log']._log(exc.company_id, 'exception_end', exc.lock_date_field, exc.company_lock_date,
                                         exc.lock_date, '%s (%s)' % (exc._sb_describe(), how))
            exc.sudo().with_context(sb_lock_exception_log=True).sb_end_logged = True

    def _sb_check_terms(self, vals, start=None):
        """An exception says why, ends within EXCEPTION_MAX_DAYS, and is granted by an accounting manager."""
        if self.env.su:
            return
        if not self.env.user.has_group('sonador_books.group_sb_manager'):
            raise UserError(self.env._('Only accounting managers can grant lock date exceptions.'))
        if 'reason' in vals and not (vals['reason'] or '').strip():
            raise UserError(self.env._('Enter the reason for the lock date exception: it is kept in the period close log.'))
        if 'end_datetime' in vals:
            end = fields.Datetime.to_datetime(vals['end_datetime'])
            limit = (start or fields.Datetime.now()) + timedelta(days=EXCEPTION_MAX_DAYS)
            if not end or end > limit:
                raise UserError(self.env._('A lock date exception ends within %(n)s days: give an end date no later than '
                                           '%(limit)s.', n=EXCEPTION_MAX_DAYS, limit=fields.Datetime.to_string(limit)))

    def write(self, vals):
        if self.env.context.get('sb_lock_exception_log'):
            return super().write(vals)
        if set(vals) & {'reason', 'end_datetime', 'user_id', 'lock_date', 'lock_date_field', 'company_id'}:
            if set(vals) & {'user_id', 'lock_date', 'lock_date_field', 'company_id'} and not self.env.su:
                raise UserError(self.env._('A lock date exception cannot be changed: revoke it and grant a new one.'))
            for exc in self:
                exc._sb_check_terms(vals, start=exc.create_date)
        active = self.filtered(lambda e: e.state == 'active')
        res = super().write(vals)
        if 'active' in vals or 'end_datetime' in vals:
            active.filtered(lambda e: e.state != 'active')._sb_log_end(self.env._('revoked'))
        return res

    @api.model
    def _sb_log_expired(self):
        """Daily: an exception that ran out is written to the period log as ended."""
        self.sudo().with_context(active_test=False).search([('sb_end_logged', '=', False), ('active', '=', True),
                                                            ('end_datetime', '<', fields.Datetime.now())])._sb_log_end(self.env._('expired'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sb_check_terms({'reason': vals.get('reason'), 'end_datetime': vals.get('end_datetime'), **vals})
            # Odoo takes either lock_date_field + lock_date, or the lock date field itself: {'fiscalyear_lock_date': ...}
            if vals.get('lock_date_field', 'fiscalyear_lock_date' if 'fiscalyear_lock_date' in vals else False) != 'fiscalyear_lock_date':
                continue
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            closed = company._sb_closed_through()
            lock = fields.Date.to_date(vals['lock_date'] if 'lock_date' in vals else vals.get('fiscalyear_lock_date'))
            if closed and (not lock or lock < closed):
                raise UserError(self.env._(
                    'The year ending %(end)s (%(ec)s) is closed. Reopen it in Year-End Closing instead of granting an exception.',
                    end=closed, ec=format_ethiopian(closed)))
        exceptions = super().create(vals_list)
        for exc in exceptions:
            self.env['sb.lock.log']._log(exc.company_id, 'exception', exc.lock_date_field, exc.company_lock_date, exc.lock_date,
                                         exc._sb_describe())
        return exceptions


class AccountAccount(models.Model):
    _inherit = 'account.account'

    def write(self, vals):
        """An account with entries in a closed year keeps its type: any change (another statement, another section of the
        balance sheet, receivable to payable, current to non-current ...) would rewrite the closed year's reports."""
        if vals.get('account_type'):
            for account in self.filtered(lambda a: a.account_type != vals['account_type']):
                for company in account.company_ids:
                    closed = company._sb_closed_through()
                    if closed and self.env['account.move.line'].sudo().search_count([
                            ('account_id', '=', account.id), ('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                            ('date', '<=', closed)], limit=1):
                        raise UserError(self.env._(
                            '%(acc)s has entries in a closed year (through %(ec)s): its type cannot be changed. Create a new '
                            'account of the right type and move the balance to it with a journal voucher.',
                            acc=account.display_name, ec=format_ethiopian(closed)))
        return super().write(vals)
