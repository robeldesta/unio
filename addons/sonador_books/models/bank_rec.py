"""Bank reconciliation, Peachtree style: tick what the bank has cleared until the difference is zero.
Statements (CSV / Excel from CBE, Awash, Dashen ...) can be imported and matched automatically."""
import base64
import csv
import io
import re
from datetime import datetime, timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from .ethiopian_calendar import format_ethiopian


def controlled(records):
    from .move_guard import controlled as flow  # move_guard loads last: imported when used
    return flow(records)

DATE_FORMATS = ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d-%b-%Y', '%d %b %Y', '%d %b %y', '%m/%d/%Y', '%d.%m.%Y', '%Y/%m/%d', '%d/%m/%y',
                '%d %B %Y', '%y-%m-%d %H:%M', '%y-%m-%d')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    sb_cleared = fields.Boolean('Cleared by the bank', copy=False, index=True)
    sb_rec_id = fields.Many2one('sb.bank.rec', string='Bank reconciliation', copy=False, index=True)

    def _sb_check_not_bank_reconciled(self):
        """A bank line cleared in a finished reconciliation is what the bank showed: it is not changed or deleted."""
        rec = self.filtered(lambda l: l.sb_cleared and l.sb_rec_id.state == 'done').sb_rec_id[:1]
        if rec:
            raise UserError(self.env._('%(entry)s is cleared in the bank reconciliation %(rec)s. Reopen the reconciliation first.',
                                       entry=self.filtered(lambda l: l.sb_rec_id == rec)[:1].move_id.name, rec=rec.name))

    def _sb_check_rec_flags(self, vals):
        """Cleared / reconciliation flags are set by the bank reconciliation alone (Finish, Reopen, Clear carried
        difference), never written directly, so a line cannot be taken out of a finished reconciliation."""
        from .move_guard import in_flow
        if {'sb_cleared', 'sb_rec_id'} & set(vals) and not in_flow(self.env):
            raise UserError(self.env._('"Cleared by the bank" is set by the bank reconciliation only (Finish / Reopen).'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sb_check_rec_flags(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._sb_check_rec_flags(vals)
        if {'account_id', 'debit', 'credit', 'balance', 'amount_currency'} & set(vals):
            self._sb_check_not_bank_reconciled()
        return super().write(vals)

    def unlink(self):
        self._sb_check_not_bank_reconciled()
        return super().unlink()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def button_draft(self):
        self.line_ids._sb_check_not_bank_reconciled()
        return super().button_draft()

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._sb_warn_reconciled_period()
        return posted

    def _sb_warn_reconciled_period(self):
        """An entry on a bank account dated inside a finished bank reconciliation is not in it: say so on the entry, on the
        reconciliation and to the user. The reconciliation shows it under "Posted after reconciling" and the next one
        loads it as outstanding."""
        Rec = self.env['sb.bank.rec']
        for move in self:
            for line in move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_cash' and not l.sb_cleared):
                rec = Rec.search([('account_id', '=', line.account_id.id), ('company_id', '=', move.company_id.id),
                                  ('state', '=', 'done'), ('date', '>=', line.date)], order='date, id', limit=1)
                if not rec:
                    continue
                msg = self.env._('%(entry)s (%(amount)s on %(account)s, dated %(date)s) falls inside the bank reconciliation of '
                                 '%(rec_date)s, which is finished. It is not in that reconciliation: the next one loads it '
                                 'as outstanding. If the statement of %(rec_date)s already shows it, reopen that '
                                 'reconciliation and tick it.', entry=move.name, amount='{:,.2f}'.format(line.balance),
                                 account=line.account_id.display_name, date=line.date, rec_date=rec.date)
                doc = self.env['sb.voucher'].search([('move_id', '=', move.id)], limit=1) or move
                doc.message_post(body=msg)
                rec.message_post(body=msg)
                self.env.user._bus_send('simple_notification', {
                    'type': 'warning', 'sticky': True, 'title': self.env._('Posted into a reconciled period'), 'message': msg})


class SbBankRec(models.Model):
    _name = 'sb.bank.rec'
    _description = 'Bank Reconciliation'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    account_id = fields.Many2one('account.account', string='Bank account', required=True, check_company=True,
                                 domain="[('account_type', '=', 'asset_cash')]")
    date = fields.Date('Statement date', required=True, default=fields.Date.context_today)
    date_ec = fields.Char(compute='_compute_ec', string='E.C.')
    statement_balance = fields.Monetary('Statement ending balance', tracking=True)
    opening_balance = fields.Monetary('Statement opening balance', compute='_compute_opening', store=True,
                                      help='Worked out, never typed: the ending balance of the previous reconciliation of this '
                                           'account. For its first reconciliation: the opening balance on the imported '
                                           'statement, else the balance per books before the first statement line.')
    stmt_opening = fields.Monetary('Opening balance on the statement', readonly=True, copy=False,
                                   help="First reconciliation of the account: the bank's own opening balance, read from the "
                                        "imported statement.")
    finished_at = fields.Datetime('Finished on', readonly=True, copy=False)
    state = fields.Selection([('draft', 'In progress'), ('done', 'Reconciled')], default='draft', tracking=True)
    line_ids = fields.One2many('sb.bank.rec.line', 'rec_id', string='Ledger items')
    stmt_ids = fields.One2many('sb.bank.rec.stmt', 'rec_id', string='Statement lines')
    statement_file = fields.Binary('Bank statement (CSV, Excel or PDF)', attachment=False)
    statement_filename = fields.Char()
    charges = fields.Monetary('Bank charges on the statement')
    charges_account_id = fields.Many2one('account.account', string='Charges account',
                                         default=lambda s: s.env.company.sb_bank_charges_account_id)
    interest = fields.Monetary('Interest earned on the statement')
    interest_account_id = fields.Many2one('account.account', string='Interest account',
                                          default=lambda s: s.env['account.account']._sb_find('120100')[:1])
    adjust_voucher_ids = fields.Many2many('sb.voucher', string='Charge / interest vouchers', readonly=True)

    gl_balance = fields.Monetary('Balance per books', compute='_compute_totals',
                                 help='The bank account per the ledger at the statement date, less cheques drawn on it that '
                                      'are still in the outstanding cheques account.')
    cleared_in = fields.Monetary('Cleared deposits', compute='_compute_totals')
    cleared_out = fields.Monetary('Cleared withdrawals', compute='_compute_totals')
    outstanding_in = fields.Monetary('Deposits in transit', compute='_compute_totals')
    outstanding_out = fields.Monetary('Outstanding cheques / withdrawals', compute='_compute_totals')
    cleared_balance = fields.Monetary('Cleared balance', compute='_compute_totals',
                                      help='Statement opening balance + cleared deposits - cleared withdrawals.')
    difference = fields.Monetary('Unreconciled difference', compute='_compute_totals',
                                 help='Statement ending balance - cleared balance. Zero when everything is ticked correctly.')
    unmatched_count = fields.Integer(compute='_compute_totals', string='Statement lines not matched')
    # the proof to the ledger: balance per books = statement + deposits in transit - outstanding items, plus entries posted
    # into the period after it was reconciled; anything else is not explained
    reconciled_balance = fields.Monetary('Reconciled balance', compute='_compute_proof',
                                         help='Statement ending balance + deposits in transit - outstanding cheques / withdrawals.')
    late_line_ids = fields.Many2many('account.move.line', compute='_compute_proof', string='Posted after reconciling',
                                     help='Entries on this bank account dated inside this reconciliation that are not in it: '
                                          'posted (or back-dated) after it was reconciled. They are carried into the next '
                                          'reconciliation as outstanding.')
    late_amount = fields.Monetary('Posted into this period after reconciling', compute='_compute_proof')
    unexplained = fields.Monetary('Not explained', compute='_compute_proof',
                                  help='Balance per books - reconciled balance - entries posted after reconciling. Zero when '
                                       'the reconciliation proves to the ledger.')
    tick_until = fields.Date('Tick everything up to', help='Ticks every item dated on or before this day at once.')
    # an amount the previous reconciliation could not explain comes into every later one with its ending balance: it is
    # shown on its own line and cleared once, by an adjusting entry
    carry_from_id = fields.Many2one('sb.bank.rec', compute='_compute_carry', string='Carried from')
    carried_diff = fields.Monetary('Difference carried from the previous reconciliation', compute='_compute_carry',
                                   help='The amount the previous reconciliation of this account does not explain. It comes '
                                        'into this one with that reconciliation\'s ending balance; "Clear carried difference" '
                                        'books it to the bank charges account and settles it.')
    carried_open = fields.Monetary('Carried difference still to clear', compute='_compute_carry')
    carry_voucher_ids = fields.Many2many('sb.voucher', 'sb_bank_rec_carry_voucher_rel', 'rec_id', 'voucher_id',
                                         string='Carried difference cleared by', readonly=True, copy=False)

    # what a finished reconciliation is: changed only after Reopen
    _SB_LOCKED = {'company_id', 'account_id', 'date', 'statement_balance', 'opening_balance', 'line_ids', 'stmt_ids',
                  'statement_file', 'statement_filename', 'charges', 'charges_account_id', 'interest', 'interest_account_id',
                  'adjust_voucher_ids', 'carry_voucher_ids'}

    def _sb_check_open(self):
        if self.filtered(lambda r: r.state == 'done'):
            raise UserError(self.env._('This bank reconciliation is finished. Reopen it to change it.'))

    @api.model_create_multi
    def create(self, vals_list):
        reused, new_vals = self.browse(), []
        for vals in vals_list:
            # worked out from the previous reconciliation (or the statement), never typed
            vals.pop('opening_balance', None)
            vals.pop('stmt_opening', None)
            vals.pop('finished_at', None)
            if vals.get('state', 'draft') != 'draft':
                raise UserError(self.env._('A new bank reconciliation starts in progress: Finish it when the difference is zero.'))
            # one reconciliation in progress per account: saving (or Load items) again on a new one for the same account
            # and date carries on with the one already started instead of leaving another draft behind
            company = vals.get('company_id') or self.env.company.id
            draft = self.search([('account_id', '=', vals.get('account_id') or 0), ('company_id', '=', company),
                                 ('state', '=', 'draft')], limit=1) if vals.get('account_id') else self.browse()
            if draft and str(draft.date) == str(vals.get('date') or fields.Date.context_today(self)):
                draft.write({k: v for k, v in vals.items() if k not in ('account_id', 'company_id', 'date')})
                reused |= draft
                continue
            if draft:
                raise UserError(self.env._('The reconciliation of %(account)s dated %(date)s is still in progress. Open it and '
                                           'finish it (or delete it) before starting another.',
                                           account=draft.account_id.display_name, date=draft.date))
            new_vals.append(vals)
        return reused | super().create(new_vals)

    def write(self, vals):
        if self._SB_LOCKED & set(vals):
            self._sb_check_open()
        if {'state', 'finished_at'} & set(vals):
            from .move_guard import in_flow
            if not in_flow(self.env):
                raise UserError(self.env._('A bank reconciliation is finished and reopened with its buttons only.'))
        if {'opening_balance', 'stmt_opening'} & set(vals) and not self.env.context.get('sb_rec_opening'):
            raise UserError(self.env._('The opening balance is not typed: it is the ending balance of the previous '
                                       'reconciliation of this account (for the first one, the opening balance on the bank '
                                       'statement).'))
        return super().write(vals)

    def init(self):
        # reconciliations made before the opening balance was worked out: the first one of each account keeps the opening
        # balance it was finished with as the statement's own
        self.env.cr.execute("""
            UPDATE sb_bank_rec r SET stmt_opening = r.opening_balance
             WHERE r.stmt_opening IS NULL AND r.opening_balance IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM sb_bank_rec p WHERE p.account_id = r.account_id AND p.date < r.date)
        """)

    def unlink(self):
        self._sb_check_open()
        if self.carry_voucher_ids:
            raise UserError(self.env._('This reconciliation cleared the carried difference with %s. Void that voucher before '
                                       'deleting the reconciliation.', ', '.join(self.carry_voucher_ids.mapped('name'))))
        return super().unlink()

    @api.depends('account_id', 'date')
    def _compute_name(self):
        for r in self:
            r.name = '%s - %s' % (r.account_id.name or 'Bank', r.date and r.date.strftime('%d %b %Y') or '')

    @api.depends('date')
    def _compute_ec(self):
        for r in self:
            r.date_ec = format_ethiopian(r.date)

    @api.depends('account_id', 'date', 'stmt_opening', 'stmt_ids.date')
    def _compute_opening(self):
        for r in self:
            r.opening_balance = r._sb_expected_opening()[0]

    def _sb_previous(self):
        self.ensure_one()
        return self.search([('account_id', '=', self.account_id.id), ('state', '=', 'done'),
                            ('date', '<', self.date or fields.Date.today()), ('id', '!=', self._origin.id or 0)],
                           order='date desc, id desc', limit=1)

    def _sb_expected_opening(self):
        """(opening balance, where it comes from): the ending balance of the previous finished reconciliation of the account;
        for the first one, the bank's opening balance read from the imported statement, else the balance per books before
        the first statement line (nothing before it when there are no statement lines: every item is loaded)."""
        self.ensure_one()
        prev = self._sb_previous()
        if prev:
            return prev.statement_balance, self.env._('the ending balance of the reconciliation of %s', prev.date)
        if self.stmt_opening:
            return self.stmt_opening, self.env._('the opening balance on the bank statement')
        if self.stmt_ids and self.account_id:
            first = min(self.stmt_ids.mapped('date'))
            res = self.env['account.move.line']._read_group([
                ('account_id', '=', self.account_id.id), ('parent_state', '=', 'posted'), ('date', '<', first)],
                [], ['balance:sum'])
            return res[0][0] or 0.0, self.env._('the balance per books before %s, the first statement line', first)
        return 0.0, self.env._('nil: the first reconciliation of the account, every item loaded')

    def _sb_refresh_opening(self):
        for r in self.filtered(lambda r: r.state == 'draft'):
            expected = r._sb_expected_opening()[0]
            if r.currency_id.compare_amounts(r.opening_balance, expected):
                r.with_context(sb_rec_opening=True).opening_balance = expected

    @api.depends('line_ids.cleared', 'line_ids.amount', 'statement_balance', 'opening_balance', 'stmt_ids.matched_line_id',
                 'carry_voucher_ids')
    def _compute_totals(self):
        for r in self:
            cur = r.currency_id or self.env.company.currency_id
            gl = 0.0
            if r.account_id and r.date:
                res = self.env['account.move.line']._read_group([
                    ('account_id', '=', r.account_id.id), ('parent_state', '=', 'posted'), ('date', '<=', r.date)],
                    [], ['balance:sum'])
                # cheques drawn on this bank wait in the outstanding cheques account until presented: for the bank they
                # are paid out already (the reconciliation lists them as outstanding)
                gl = (res[0][0] or 0.0) - sum(r._sb_issued_cheques().mapped('amount'))
                # the entry clearing the carried difference, dated on the first open day when the statement's is closed
                gl += sum(r._sb_carry_lines().filtered(lambda l: l.date > r.date).mapped('balance'))
            cin = sum(l.amount for l in r.line_ids if l.cleared and l.amount > 0)
            cout = -sum(l.amount for l in r.line_ids if l.cleared and l.amount < 0)
            oin = sum(l.amount for l in r.line_ids if not l.cleared and l.amount > 0)
            oout = -sum(l.amount for l in r.line_ids if not l.cleared and l.amount < 0)
            r.gl_balance = cur.round(gl)
            r.cleared_in, r.cleared_out = cur.round(cin), cur.round(cout)
            r.outstanding_in, r.outstanding_out = cur.round(oin), cur.round(oout)
            r.cleared_balance = cur.round(r.opening_balance + cin - cout)
            r.difference = cur.round(r.statement_balance - r.cleared_balance)
            r.unmatched_count = len(r.stmt_ids.filtered(lambda s: not s.matched_line_id))

    @api.depends('account_id', 'date', 'carry_voucher_ids')
    def _compute_carry(self):
        for r in self:
            prev = r._sb_previous() if r.account_id else r.browse()
            cur = r.currency_id or self.env.company.currency_id
            # the previous reconciliation's own proof (not its carried difference: that one it cleared, or still shows)
            carried = cur.round(prev.unexplained) if prev else 0.0
            r.carry_from_id = prev if carried else False
            r.carried_diff = carried
            r.carried_open = cur.round(carried + sum(r._sb_carry_lines().mapped('balance'))) if carried else 0.0

    @api.depends('line_ids.cleared', 'line_ids.amount', 'statement_balance', 'state', 'carry_voucher_ids')
    def _compute_proof(self):
        Line = self.env['account.move.line']
        for r in self:
            r.reconciled_balance = r.statement_balance + r.outstanding_in - r.outstanding_out
            late = Line
            prev = r._sb_previous() if r.account_id else r.browse()
            cur = r.currency_id or self.env.company.currency_id
            if r.account_id and r.date and r._origin.id:
                dom = [('account_id', '=', r.account_id.id), ('parent_state', '=', 'posted'), ('date', '<=', r.date),
                       ('company_id', '=', r.company_id.id), ('id', 'not in', r.line_ids.move_line_id.ids),
                       '|', ('sb_rec_id', '=', False), ('sb_rec_id.date', '>', r.date)]
                if not prev:
                    # the account's first reconciliation: the history before its statement period is inside the opening
                    # balance; without statement lines, everything in the books when it was finished was
                    if r.stmt_ids:
                        dom.append(('date', '>=', r._first_cutoff()))
                    elif r.stmt_opening:
                        dom.append(('create_date', '>', r.finished_at or r.create_date))
                late = Line.search(dom, order='date, id')
            r.late_line_ids = late
            r.late_amount = cur.round(sum(late.mapped('balance')))
            r.unexplained = cur.round(r.gl_balance - r.reconciled_balance - r.late_amount)

    def _sb_carry_lines(self):
        """The bank lines of the entries that cleared the carried difference: in the books, never on a statement."""
        self.ensure_one()
        return self.carry_voucher_ids.filtered(lambda v: v.state == 'posted').move_id.line_ids.filtered(
            lambda l: l.account_id == self.account_id)

    def _sb_issued_cheques(self):
        """Cheques drawn on this bank account, issued by the statement date and not yet presented (or presented / cancelled
        after it): they sit in the outstanding cheques account, not on the bank account."""
        self.ensure_one()
        rows = self.env['sb.voucher.cash'].search([
            ('account_id', '=', self.account_id.id), ('method', '=', 'cheque'), ('direction', '=', 'out'),
            ('voucher_state', '=', 'posted'), ('voucher_date', '<=', self.date), ('company_id', '=', self.company_id.id),
            ('cheque_state', 'in', ('open', 'cleared', 'bounced'))])
        return rows.filtered(lambda c: c.cheque_state == 'open' or c.settle_voucher_id.date > self.date)

    def _sb_cheque_move_line(self, row):
        """The issued cheque's line on the outstanding cheques account."""
        account = row.voucher_id.company_id.sb_cheque_outstanding_account_id
        lines = row.voucher_id.move_id.line_ids.filtered(lambda l: l.account_id == account and l.credit)
        return lines.filtered(lambda l: row.currency_id.is_zero(l.credit - row.amount))[:1] or lines[:1]

    # ------------------------------------------------------------------ actions
    def action_load(self):
        """Every posted line on the bank account up to the statement date that the bank has not cleared yet."""
        self._sb_check_open()
        self._sb_refresh_opening()
        for r in self:
            if not r.account_id:
                raise UserError(self.env._('Choose the bank account.'))
            first = self.search([('account_id', '=', r.account_id.id), ('state', '=', 'done')], order='date, id', limit=1)
            if first:
                first._sb_clear_history()
            since = []
            if r.stmt_ids and r._first_rec():
                # first reconciliation of the account: the statement period, plus 60 days for cheques presented late
                since = [('date', '>=', min(r.stmt_ids.mapped('date')) - timedelta(days=60))]
            lines = self.env['account.move.line'].search(since + [
                ('account_id', '=', r.account_id.id), ('parent_state', '=', 'posted'),
                '|', ('date', '<=', r.date), ('move_id', 'in', r.adjust_voucher_ids.move_id.ids),
                ('company_id', '=', r.company_id.id), '|', ('sb_cleared', '=', False), ('sb_rec_id', '=', r.id),
                ('move_id', 'not in', r.carry_voucher_ids.move_id.ids)], order='date, id')
            have = set(r.line_ids.mapped('move_line_id').ids)
            self.env['sb.bank.rec.line'].create([{'rec_id': r.id, 'move_line_id': l.id, 'cleared': l.sb_rec_id == r and l.sb_cleared}
                                                 for l in lines if l.id not in have])
            # issued cheques not yet presented: outstanding, ticked when the statement shows them (cleared on Finish)
            cheques = [{'rec_id': r.id, 'move_line_id': r._sb_cheque_move_line(c).id, 'cheque_row_id': c.id}
                       for c in r._sb_issued_cheques().filtered(lambda c: c.cheque_state == 'open')]
            self.env['sb.bank.rec.line'].create([c for c in cheques if c['move_line_id'] and c['move_line_id'] not in have])
        return True

    def _sb_clear_history(self):
        """The first reconciliation of an account starts from the bank's opening balance: every book item before its
        statement period (the opening balance brought over, earlier receipts and payments) is inside that balance, so it
        counts as cleared by this reconciliation and never comes back as outstanding in the next one."""
        self.ensure_one()
        if self.state != 'done' or not self.stmt_ids or not self._first_rec():
            return
        cutoff = self._first_cutoff()
        # entries back-dated before the statement period after it was finished are not in the bank's opening balance:
        # they stay outstanding for the next reconciliation
        posted_by = [('create_date', '<=', self.finished_at)] if self.finished_at else []
        old = self.env['account.move.line'].search([
            ('account_id', '=', self.account_id.id), ('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'),
            ('date', '<', cutoff), ('sb_cleared', '=', False)] + posted_by)
        if old:
            controlled(old).write({'sb_cleared': True, 'sb_rec_id': self.id})

    def action_tick_until(self):
        self.ensure_one()
        if not self.tick_until:
            raise UserError(self.env._('Enter the day to tick up to.'))
        self.line_ids.filtered(lambda l: l.date <= self.tick_until and not l.cleared).write({'cleared': True})
        return True

    def action_clear_all(self):
        self.line_ids.write({'cleared': True})
        return True

    def action_clear_none(self):
        self.line_ids.write({'cleared': False})
        return True

    def action_import(self):
        self.ensure_one()
        self._sb_check_open()
        if not self.statement_file:
            raise UserError(self.env._('Attach the bank statement file first.'))
        rows = self._parse_statement(base64.b64decode(self.statement_file), self.statement_filename or '')
        if not rows:
            raise UserError(self.env._('No transactions found in the file. Expected columns such as Date, Description, '
                                       'Reference, Debit / Credit or Amount.'))
        last_balance = next((r['balance'] for r in reversed(rows) if r.get('balance') is not None), None)
        self.stmt_ids = [Command.clear()] + [Command.create({k: v for k, v in r.items() if k != 'balance'}) for r in rows]
        first = not self._sb_previous()
        if first:
            # the account's first reconciliation: the bank's own opening balance (printed on top, or worked back from
            # the first running balance)
            # the running balance is more reliable than a printed opening (Abyssinia prints it after the first item)
            opening = rows[0]['balance'] - rows[0]['amount'] if rows[0].get('balance') is not None else \
                self._statement_opening(base64.b64decode(self.statement_file))
            if opening is not None:
                self.with_context(sb_rec_opening=True).stmt_opening = opening
        if not self.statement_balance:
            if last_balance is not None:
                self.statement_balance = last_balance
            elif first:
                self.statement_balance = self.opening_balance + sum(r['amount'] for r in rows)
        self.action_load()
        self.action_match()
        return True

    def _first_cutoff(self):
        """Where the first reconciliation starts in the books: the day after the books last showed the bank's opening
        balance (up to a month before the first statement line: a statement printed from the 11th may open on the 7th),
        else the first statement day."""
        first = min(self.stmt_ids.mapped('date'))
        Line = self.env['account.move.line']
        for back in range(1, 32):
            day = first - timedelta(days=back)
            res = Line._read_group([('account_id', '=', self.account_id.id), ('parent_state', '=', 'posted'), ('date', '<=', day)],
                                   [], ['balance:sum'])
            if abs((res[0][0] or 0.0) - self.opening_balance) < 0.01:
                return day + timedelta(days=1)
        return first

    def _first_rec(self):
        return not self.search_count([('account_id', '=', self.account_id.id), ('state', '=', 'done'), ('id', '!=', self.id)])

    def _statement_opening(self, data):
        """'Opening Balance: ETB 33,003.86 cr' printed above the transactions (Dashen and others)."""
        text = data.decode('utf-8-sig', errors='replace')[:3000]
        m = re.search(r'opening balance[^0-9\-(]*([\-(]?[\d,]+\.?\d*\)?)\s*(cr|dr)?', text, re.I)
        if not m:
            return None
        v = self._to_float(m.group(1))
        return -v if (m.group(2) or '').lower() == 'dr' else v

    def action_match(self):
        """Match statement lines to ledger items, in passes:
        1. same amount within 7 days (the reference decides between candidates);
        2. same amount within 60 days: cheques the payee presents weeks later;
        3. the bank's amount differs from the books by a transfer fee (up to 300 and 0.5%) within 7 days;
        4. one bank line = several lines of one voucher; 5. several same-day bank lines = one voucher.
        Before all: an entry and its reversal (or reclass) on this account clear each other."""
        self._sb_check_open()
        for r in self:
            free = r.line_ids.filtered(lambda l: not l.stmt_ids)
            if r.stmt_ids and r._first_rec():
                # first reconciliation: only cheques written before the statement period can still reach the bank;
                # receipts of that time were banked then
                cutoff = r._first_cutoff()
                free = free.filtered(lambda l: l.date >= cutoff or l.amount < 0)
            # first: an entry and its reversal / reclass on this account cancel out; neither ever reaches the bank
            for exact in (True, False):  # true reversals first, then entries that name the one they correct
                for l in free:
                    if l.cleared:
                        continue
                    twin = free.filtered(lambda o: o != l and not o.cleared and abs(o.amount + l.amount) < 0.005 and (
                        o.move_line_id.move_id.reversed_entry_id == l.move_line_id.move_id if exact
                        else l.move_line_id.move_id.name in (o.move_line_id.move_id.ref or '')))[:1]
                    if twin:
                        (l | twin).write({'cleared': True})
            # ... also a whole voucher moved to another bank by an entry that names it (lines and the move net to nil)
            for mv in free.filtered(lambda l: not l.cleared).move_line_id.move_id:
                own = free.filtered(lambda l: not l.cleared and l.move_line_id.move_id == mv)
                fixes = free.filtered(lambda o: not o.cleared and o.move_line_id.move_id != mv and mv.name
                                      and mv.name in (o.move_line_id.move_id.ref or ''))
                if own and fixes and abs(sum((own | fixes).mapped('amount'))) < 0.005:
                    (own | fixes).write({'cleared': True})
            free = free.filtered(lambda l: not l.cleared)
            for tol, days in ((0.005, 7), ('groups', 7), (0.005, 60), (300.0, 7)):
                if tol == 'groups':
                    free = r._match_groups(free)
                    continue
                for s in r.stmt_ids.filtered(lambda s: not s.matched_line_id):
                    limit = min(tol, max(0.005, 0.005 * abs(s.amount)))
                    cands = free.filtered(lambda l: (l.amount > 0) == (s.amount > 0) and abs(l.amount - s.amount) <= limit
                                          and abs((l.date - s.date).days) <= days)
                    if s.reference:
                        by_ref = cands.filtered(lambda l: s.reference.lower() in ((l.ref or '') + ' ' + (l.name or '')).lower())
                        cands = by_ref or cands
                    if cands:
                        best = cands.sorted(lambda l: (abs(l.amount - s.amount), abs((l.date - s.date).days)))[0]
                        s.matched_line_id = best
                        best.cleared = True
                        free -= best
            # 6. an entry corrected by others that name it (a receipt partly moved to another bank): the net amount
            #    paid in one or more bank lines of those days
            from itertools import combinations
            open_stmt = r.stmt_ids.filtered(lambda s: not s.matched_line_id)
            for l in free.filtered(lambda l: not l.cleared):
                fixes = free.filtered(lambda o: o != l and not o.cleared and l.move_line_id.move_id.name
                                      and l.move_line_id.move_id.name in (o.move_line_id.move_id.ref or ''))
                if not fixes:
                    continue
                net = round(l.amount + sum(fixes.mapped('amount')), 2)
                pool = [s for s in open_stmt if not s.matched_line_id and (s.amount > 0) == (net > 0) and abs((s.date - l.date).days) <= 7]
                hit = next((c for n in (1, 2, 3) for c in combinations(pool, n) if abs(sum(x.amount for x in c) - net) < 0.005), None)
                if hit:
                    for x in hit:
                        x.matched_line_id = l
                    (l | fixes).write({'cleared': True})
                    free -= (l | fixes)
            # 7. one bank line = two or three book lines of the same days (two van settlements banked together)
            for s in r.stmt_ids.filtered(lambda s: not s.matched_line_id):
                pool = free.filtered(lambda l: not l.cleared and (l.amount > 0) == (s.amount > 0) and abs((l.date - s.date).days) <= 3)
                if len(pool) > 25:
                    continue
                hit = next((c for n in (2, 3) for c in combinations(pool, n) if abs(sum(x.amount for x in c) - s.amount) < 0.005), None)
                if hit:
                    s.matched_line_id = hit[0]
                    for x in hit:
                        x.cleared = True
                    free -= self.env['sb.bank.rec.line'].browse([x.id for x in hit])
            # charges and interest booked from this reconciliation are inside lines the bank already shows
            adj = r.adjust_voucher_ids.filtered(lambda v: v.line_ids.account_id in (r.charges_account_id | r.interest_account_id)).move_id
            free.filtered(lambda l: not l.cleared and l.move_line_id.move_id in adj).write({'cleared': True})
            if r.stmt_ids and r._first_rec():
                # first reconciliation: items before the statement period that the bank did not show belong to
                # earlier, unreconciled history
                r.line_ids.filtered(lambda l: l.date < r._first_cutoff() and not l.stmt_ids).unlink()
        return True

    def _match_groups(self, free):
        """4. one bank line = several lines of one voucher; 5. several same-day bank lines = one voucher."""
        r = self
        # 4. one bank line = several lines of one voucher (a receipt of 22,050 + 13,200 deposited as 35,250)
        for s in r.stmt_ids.filtered(lambda s: not s.matched_line_id):
            near = free.filtered(lambda l: (l.amount > 0) == (s.amount > 0) and abs((l.date - s.date).days) <= 7)
            for move in near.move_line_id.move_id:
                group = near.filtered(lambda l: l.move_line_id.move_id == move)
                if len(group) > 1 and abs(sum(group.mapped('amount')) - s.amount) < 0.005:
                    s.matched_line_id = group[0]
                    group.write({'cleared': True})
                    free -= group
                    break
        # 5. several bank lines of one day = one voucher (1,000,000 received as two transfers of 500,000)
        days = {}
        for s in r.stmt_ids.filtered(lambda s: not s.matched_line_id):
            days.setdefault((s.date, s.amount > 0), []).append(s)
        for (day, inflow), group in days.items():
            if len(group) < 2:
                continue
            total = sum(x.amount for x in group)
            cands = free.filtered(lambda l: abs(l.amount - total) < 0.005 and abs((l.date - day).days) <= 7)
            if cands:
                best = cands.sorted(lambda l: abs((l.date - day).days))[0]
                for x in group:
                    x.matched_line_id = best
                best.cleared = True
                free -= best
        return free

    def action_post_adjustments(self):
        """Book the charges and interest shown on the statement; the new lines come in already cleared."""
        self.ensure_one()
        self._sb_check_open()
        V = self.env['sb.voucher']
        made = V
        # a statement of a closed period (December, after the year-end close): the charges go on the first open day
        day = self._sb_first_open_day()
        if self.charges:
            v = V.create({'voucher_type': 'payment', 'company_id': self.company_id.id, 'date': day, 'sb_series_id': self._br_series('payment').id,
                          'memo': self.env._('Bank charges per statement %s', self.name),
                          'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': 'transfer'})],
                          'line_ids': [Command.create({'account_id': self.charges_account_id.id, 'name': self.env._('Bank charges'),
                                                       'amount': self.charges})]})
            v.action_post()
            made |= v
        if self.interest:
            v = V.create({'voucher_type': 'receipt', 'company_id': self.company_id.id, 'date': day, 'sb_series_id': self._br_series('receipt').id,
                          'memo': self.env._('Interest per statement %s', self.name),
                          'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': 'transfer'})],
                          'line_ids': [Command.create({'account_id': self.interest_account_id.id, 'name': self.env._('Interest'),
                                                       'amount': self.interest})]})
            v.action_post()
            made |= v
        if not made:
            raise UserError(self.env._('Enter the charges or interest shown on the statement.'))
        self.adjust_voucher_ids |= made
        self.charges = self.interest = 0.0
        self.action_load()
        bank_lines = made.move_id.line_ids.filtered(lambda l: l.account_id == self.account_id)
        self.line_ids.filtered(lambda l: l.move_line_id in bank_lines).write({'cleared': True})
        return True

    def action_create_from_statement(self):
        """Unmatched statement lines become vouchers: money in -> receipt, money out -> payment (to suspense for review)."""
        self.ensure_one()
        self._sb_check_open()
        suspense = self.env['account.account']._sb_find('211002')[:1]
        V = self.env['sb.voucher']
        for s in self.stmt_ids.filtered(lambda s: not s.matched_line_id):
            account = s.account_id or suspense
            if not account:
                continue
            v = V.create({
                'voucher_type': 'receipt' if s.amount > 0 else 'payment', 'company_id': self.company_id.id, 'date': s.date,
                'sb_series_id': self._br_series('receipt' if s.amount > 0 else 'payment').id,
                'reference': s.reference, 'memo': s.description,
                'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': 'transfer', 'reference': s.reference})],
                'line_ids': [Command.create({'account_id': account.id, 'name': s.description or s.reference or '/',
                                             'amount': abs(s.amount)})]})
            v.action_post()
            self.adjust_voucher_ids |= v
        self.action_load()
        self.action_match()
        return True

    def _br_series(self, doc_type):
        """Vouchers the reconciliation books itself (charges, interest, lines the books lacked) take an internal series,
        BR-R/0001 and BR-P/0001: the CRV / CPV numbers belong to the company's printed vouchers."""
        S = self.env['sb.doc.series'].sudo()
        prefix = 'BR-R/' if doc_type == 'receipt' else 'BR-P/'
        return S.search([('company_id', '=', self.company_id.id), ('doc_type', '=', doc_type), ('prefix', '=', prefix)], limit=1) or S.create({
            'name': 'Bank reconciliation (%s)' % ('money in' if doc_type == 'receipt' else 'money out'), 'company_id': self.company_id.id,
            'doc_type': doc_type, 'prefix': prefix, 'padding': 4, 'is_default': False})

    def action_finish(self):
        self.ensure_one()
        self._sb_check_open()
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('finish a bank reconciliation'))
        cur = self.currency_id
        if self.late_line_ids:
            raise UserError(self.env._('%s entries on this bank account were posted after the items were loaded. Press Load '
                                       'items first.', len(self.late_line_ids)))
        expected, source = self._sb_expected_opening()
        if cur.compare_amounts(self.opening_balance, expected):
            raise UserError(self.env._('The opening balance %(opening)s is not %(expected)s, %(origin)s. Press Load items to '
                                       'bring it up to date.', opening='{:,.2f}'.format(self.opening_balance),
                                       expected='{:,.2f}'.format(expected), origin=source))
        # the statement ending balance is the opening balance plus what the bank cleared
        cleared = self.line_ids.filtered('cleared')
        ending = expected + sum(cleared.mapped('amount'))
        if cur.compare_amounts(self.statement_balance, ending):
            raise UserError(self.env._('The difference is %s. Tick the items the bank has cleared (or post charges / interest) '
                                       'until it is zero.', '{:,.2f}'.format(self.statement_balance - ending)))
        # the proof to the books, rounded as the reconciliation report shows it
        if cur.round(self.carried_open) and not cur.compare_amounts(self.unexplained, self.carried_open):
            raise UserError(self.env._(
                '%(diff)s is not explained: it is the difference carried from the reconciliation %(rec)s, not something in '
                'this statement. Press "Clear carried difference" to book it to the bank charges account, then finish.',
                diff='{:,.2f}'.format(self.carried_open), rec=self.carry_from_id.name))
        if cur.round(self.unexplained):
            raise UserError(self.env._(
                'The statement balance with the outstanding items comes to %(rec)s, the books to %(gl)s: %(diff)s is not '
                'explained. Usually an item ticked twice, or a bank charge or rounding not yet in the books: book it (or '
                'correct the entry), then finish.', rec='{:,.2f}'.format(self.reconciled_balance),
                gl='{:,.2f}'.format(self.gl_balance), diff='{:,.2f}'.format(self.unexplained)))
        cleared = self._sb_clear_cheques(cleared)
        controlled(cleared.move_line_id).write({'sb_cleared': True, 'sb_rec_id': self.id})
        controlled((self.line_ids - cleared).move_line_id).write({'sb_cleared': False, 'sb_rec_id': False})
        controlled(self).write({'state': 'done', 'finished_at': fields.Datetime.now()})
        self._sb_clear_history()
        self.message_post(body=self.env._('Reconciled: statement balance %s, %s items cleared, %s outstanding.',
                                          '{:,.2f}'.format(self.statement_balance), len(self.line_ids.filtered('cleared')),
                                          len(self.line_ids.filtered(lambda l: not l.cleared))))
        return True

    def _sb_clear_cheques(self, cleared):
        """Issued cheques ticked as presented: cleared as from Cheques (outstanding cheques -> this bank) on the day the
        statement shows them; the bank line of the clearing takes the cheque's place, ticked. Returns the cleared items."""
        Item = self.env['sb.bank.rec.line']
        for item in cleared.filtered('cheque_row_id'):
            row = item.cheque_row_id
            day = min(item.stmt_ids.mapped('date') or [self.date])
            self.env['sb.cheque.wizard'].create({'row_id': row.id, 'action': 'clear', 'date': day, 'account_id': self.account_id.id,
                                                 'reference': row.reference}).action_confirm()
            bank_line = row.settle_voucher_id.move_id.line_ids.filtered(lambda l: l.account_id == self.account_id)
            new = Item.create([{'rec_id': self.id, 'move_line_id': l.id, 'cleared': True} for l in bank_line])
            item.stmt_ids.write({'matched_line_id': new[:1].id})
            cleared = cleared - item | new
            item.unlink()
        return cleared

    def action_reopen(self):
        self.ensure_one()
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('reopen a bank reconciliation'))
        later = self.search([('account_id', '=', self.account_id.id), ('state', '=', 'done'), ('date', '>', self.date)], limit=1)
        if later:
            raise UserError(self.env._('Reopen the later reconciliation of %s first.', later.date))
        lock = self.company_id._get_user_fiscal_lock_date(self._sb_journal())
        if lock and self.date <= lock:
            raise UserError(self.env._(
                'The statement of %(date)s is in a closed period (the books are closed through %(lock)s), so this '
                'reconciliation stays as it is. A difference it left is cleared on the next reconciliation: "Clear carried '
                'difference".', date=self.date.strftime('%d %b %Y'), lock=lock.strftime('%d %b %Y')))
        controlled(self.env['account.move.line'].search([('sb_rec_id', '=', self.id),
                                                         ('move_id', 'not in', self.carry_voucher_ids.move_id.ids)])).write(
            {'sb_cleared': False, 'sb_rec_id': False})
        controlled(self).write({'state': 'draft'})
        self.message_post(body=self.env._('Reopened.'))
        return True

    def _sb_journal(self):
        return self.env['account.journal'].search([('default_account_id', '=', self.account_id.id)], limit=1) \
            or self.env['account.journal']

    def _sb_first_open_day(self):
        """The statement date, or the first open day when the statement's period is closed."""
        lock = self.company_id._get_user_fiscal_lock_date(self._sb_journal())
        return self.date if not lock or self.date > lock else lock + timedelta(days=1)

    def action_clear_carried(self):
        """Clear the difference carried from the previous reconciliation, as Sage does: one adjusting entry between this
        bank account and the bank charges account, dated in this statement's period (or the first open day), cleared at
        once and never loaded as an item."""
        self.ensure_one()
        self._sb_check_open()
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('clear a carried reconciliation difference'))
        cur = self.currency_id
        amount = cur.round(self.carried_open)
        if not amount:
            raise UserError(self.env._('There is no difference carried from the previous reconciliation.'))
        account = self.charges_account_id or self.company_id.sb_bank_charges_account_id
        if not account:
            raise UserError(self.env._('Choose the charges account (Charges and interest tab), then clear the difference.'))
        prev = self.carry_from_id
        # books above the bank (amount > 0): money out of the bank; books below it: money in
        doc_type = 'payment' if amount > 0 else 'receipt'
        memo = self.env._('Reconciliation difference carried from %(rec)s, cleared on %(this)s', rec=prev.name, this=self.name)
        v = self.env['sb.voucher'].create({
            'voucher_type': doc_type, 'company_id': self.company_id.id, 'date': self._sb_first_open_day(),
            'sb_series_id': self._br_series(doc_type).id, 'memo': memo,
            'cash_line_ids': [Command.create({'account_id': self.account_id.id, 'method': 'transfer'})],
            'line_ids': [Command.create({'account_id': account.id, 'name': self.env._('Reconciliation difference'),
                                         'amount': abs(amount)})]})
        v.action_post()
        self.carry_voucher_ids |= v
        # in the books, never on a statement: settled by this reconciliation, not an item to tick
        controlled(self._sb_carry_lines()).write({'sb_cleared': True, 'sb_rec_id': self.id})
        self.line_ids.filtered(lambda l: l.move_line_id.move_id == v.move_id).unlink()
        body = self.env._('Difference of %(amt)s carried from %(rec)s cleared by %(voucher)s (%(account)s).',
                          amt='{:,.2f}'.format(amount), rec=prev.name, voucher=v.name, account=account.display_name)
        self.message_post(body=body)
        prev.message_post(body=body)
        v.message_post(body=body)
        return True

    def action_print(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.report', 'name': 'Account Reconciliation',
                'context': {'report': 'bank_rec', 'catalog_key': 'bank_rec', 'ui': {'mode': 'custom'},
                            'options': {'rec_id': self.id, 'date_from': fields.Date.to_string(self.date - timedelta(days=30)),
                                        'date_to': fields.Date.to_string(self.date), '_title': 'Account Reconciliation'}}}

    # ------------------------------------------------------------------ statement parsing
    def _parse_statement(self, data, filename):
        rows = []
        if filename.lower().endswith('.pdf') or data[:5] == b'%PDF-':
            return self._parse_pdf(data)
        if filename.lower().endswith(('.xlsx', '.xlsm')):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            ws = wb.worksheets[0]
            rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        elif filename.lower().endswith('.xls'):
            import xlrd
            book = xlrd.open_workbook(file_contents=data)
            sh = book.sheet_by_index(0)
            rows = [sh.row_values(i) for i in range(sh.nrows)]
        else:
            text = data.decode('utf-8-sig', errors='replace')
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=',;\t') if text.strip() else csv.excel
            rows = list(csv.reader(io.StringIO(text), dialect))
        # find the header row
        header_idx, cols = None, {}
        for i, r in enumerate(rows[:30]):
            labels = [str(c or '').strip().lower() for c in r]
            found = self._map_columns(labels)
            if 'date' in found and ('amount' in found or 'debit' in found or 'credit' in found):
                header_idx, cols = i, found
                break
        if header_idx is None:
            return []
        out = []
        group_date = None  # statements that print the date once above its transactions (Dashen)
        for r in rows[header_idx + 1:]:
            def get(key):
                i = cols.get(key)
                return r[i] if i is not None and i < len(r) else None
            first = str(r[0] if r else '').strip()
            d = self._to_date(get('date'))
            if first and not any(str(c or '').strip() for c in r[1:]):
                group_date = self._to_date(first) or group_date
                continue
            if not d and not first and group_date:
                d = group_date
            if not d:
                continue
            if 'amount' in cols:
                amount = self._to_float(get('amount'))
            else:
                amount = abs(self._to_float(get('credit'))) - abs(self._to_float(get('debit')))  # some banks print debits negative
            if not amount:
                continue
            bal = get('balance')
            out.append({'date': d, 'description': str(get('description') or '').strip()[:250],
                        'reference': str(get('reference') or '').strip().strip('="')[:64], 'amount': amount,
                        'balance': self._to_float(bal) if bal not in (None, '') else None})
        return out

    # ------------------------------------------------------------------ PDF statements
    _PDF_DATE = re.compile(r'\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}-[A-Za-z]{3}-\d{2,4}|\d{4}-\d{2}-\d{2})\b')
    _PDF_MONEY = re.compile(r'(?<![\d/.-])-?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)')
    _PDF_OPENING = re.compile(r'(?:BBF|opening balance|balance brought forward|brought forward)\s*:?\s*(?:ETB)?\s*(-?[\d,]+\.\d{2})', re.I)
    _PDF_OUT_WORDS = ('fee', 'charge', 'vat', 'value added tax', 'tax', 'withdraw', 'cheque', 'debit', 'transfer to', 'payment to')

    def _parse_pdf(self, data):
        """Bank statements as PDF (Awash, Abay, Oromia International ...). Every transaction line carries a date and the
        running balance, so each line's amount and direction come from the change in the balance: no column positions,
        which differ from bank to bank and from page to page."""
        lines = self._pdf_lines(data)
        text = '\n'.join(lines)
        m = self._PDF_OPENING.search(text)
        prev = self._to_float(m.group(1)) if m else None
        # day/month or month/day: decided by the dates of the whole document
        nums = [tuple(int(x) for x in re.split(r'[/.-]', d)[:2]) for d in self._PDF_DATE.findall(text) if re.match(r'\d+[/.-]\d+[/.-]', d)]
        month_first = any(b > 12 for a, b in nums) and not any(a > 12 for a, b in nums)
        out, cur = [], None
        for ln in lines:
            dm = self._PDF_DATE.search(ln)
            money = [self._to_float(x) for x in self._PDF_MONEY.findall(ln)]
            if not dm or len(money) < 2 or re.search(r'total|balance as at|closing', ln, re.I):
                if cur is not None and not self._PDF_DATE.search(ln) and len(cur['description']) < 200:
                    cur['description'] = (cur['description'] + ' ' + ln).strip()
                continue
            d = self._pdf_date(dm.group(1), month_first)
            if not d:
                continue
            amount = balance = None
            if prev is not None:
                for b in reversed(money):  # the running balance: usually the last figure
                    for a in money:
                        if a is b or not a:
                            continue
                        if abs(prev + a - b) < 0.01:
                            amount, balance = a, b
                        elif abs(prev - a - b) < 0.01:
                            amount, balance = -a, b
                        if amount is not None:
                            break
                    if amount is not None:
                        break
            if amount is None and prev is not None:
                # the amount printed on another row (Awash's debits): the change in the running balance is the amount
                balance = money[-1]
                amount = round(balance - prev, 2)
            if amount is None:
                # first line without an opening balance: the largest figure is the balance, the direction from the words
                balance = max(money)
                a = max((x for x in money if x != balance and x), default=0.0)
                amount = -a if any(w in ln.lower() for w in self._PDF_OUT_WORDS) else a
            if not amount:
                prev = balance
                continue
            desc = self._PDF_MONEY.sub('', self._PDF_DATE.sub('', ln))
            ref = re.search(r'\b[A-Z0-9]{10,}\b', desc)
            cur = {'date': d, 'description': re.sub(r'\s+', ' ', desc).strip()[:250], 'reference': ref.group(0)[:64] if ref else '',
                   'amount': amount, 'balance': balance}
            out.append(cur)
            prev = balance
        return out

    def _pdf_lines(self, data):
        """The statement's text, one printed row per line: pieces of text are put back together by their height on the
        page (some banks' PDFs store each column separately)."""
        try:
            from pdfminer.high_level import extract_pages
            from pdfminer.layout import LAParams, LTTextContainer, LTTextLine
        except ImportError:
            extract_pages = None
        if extract_pages:
            lines = []
            for page in extract_pages(io.BytesIO(data), laparams=LAParams(line_margin=0.2, all_texts=True)):
                pieces = []
                def walk(obj):
                    if isinstance(obj, LTTextLine):
                        if obj.get_text().strip():
                            pieces.append((obj.y0, obj.x0, obj.get_text().strip()))
                    elif isinstance(obj, LTTextContainer) or hasattr(obj, '__iter__'):
                        for o in obj:
                            walk(o)
                walk(page)
                rows = []
                for y, x, t in sorted(pieces, key=lambda p: (-p[0], p[1])):
                    if rows and abs(rows[-1][0] - y) <= 3:
                        rows[-1][1].append((x, t))
                    else:
                        rows.append([y, [(x, t)]])
                lines += [' '.join(t for _x, t in sorted(r[1])) for r in rows]
            return lines
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        lines = []
        for page in PdfReader(io.BytesIO(data)).pages:
            lines += [ln.strip() for ln in (page.extract_text() or '').splitlines() if ln.strip()]
        return lines

    def _pdf_date(self, s, month_first):
        for fmt in (('%m/%d/%Y', '%m-%d-%Y', '%m.%d.%Y', '%m/%d/%y') if month_first else ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y')) + \
                ('%d-%b-%y', '%d-%b-%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _map_columns(self, labels):
        rules = [
            ('date', ('value date', 'transaction date', 'posting date', 'date')),
            ('description', ('description', 'narration', 'narrative', 'particulars', 'details', 'remark', 'memo')),
            ('reference', ('reference', 'ref', 'cheque', 'check', 'chq', 'document', 'transaction id')),
            ('debit', ('debit', 'withdrawal', 'paid out', 'money out', 'dr')),
            ('credit', ('credit', 'deposit', 'paid in', 'money in', 'cr')),
            ('amount', ('amount',)),
            ('balance', ('balance', 'running balance')),
        ]
        found = {}
        for key, words in rules:
            for i, lab in enumerate(labels):
                if i in found.values():
                    continue
                if any(lab == w or lab.startswith(w + ' ') or lab.endswith(' ' + w) or (len(w) > 3 and w in lab) for w in words):
                    found[key] = i
                    break
        return found

    def _to_date(self, v):
        if not v:
            return None
        if isinstance(v, datetime):
            return v.date()
        if hasattr(v, 'year') and hasattr(v, 'month'):
            return v
        if isinstance(v, float):  # xls serial date
            try:
                import xlrd
                return datetime(*xlrd.xldate_as_tuple(v, 0)[:3]).date()
            except Exception:
                return None
        s = re.sub(r'^(\d{1,2})(st|nd|rd|th)\b', r'\1', str(v).strip())  # 8th July 2026
        for text in (s, s[:11].strip()):
            for fmt in DATE_FORMATS:
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    continue
        return None

    def _to_float(self, v):
        if v in (None, ''):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(',', '').replace('ETB', '').replace('Br', '').strip()
        neg = s.startswith('(') and s.endswith(')')
        s = re.sub(r'[^0-9.\-]', '', s)
        try:
            f = float(s) if s not in ('', '-', '.') else 0.0
        except ValueError:
            return 0.0
        return -f if neg else f


class SbBankRecLine(models.Model):
    _name = 'sb.bank.rec.line'
    _description = 'Bank Reconciliation Item'
    _order = 'date, id'

    rec_id = fields.Many2one('sb.bank.rec', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='rec_id.currency_id')
    move_line_id = fields.Many2one('account.move.line', required=True, ondelete='cascade')
    cheque_row_id = fields.Many2one('sb.voucher.cash', string='Issued cheque', ondelete='cascade',
                                    help='A cheque drawn on this bank not yet presented: ticked, it is cleared on Finish.')
    date = fields.Date(related='move_line_id.date', store=True)
    move_name = fields.Char(related='move_line_id.move_id.name', string='Voucher')
    ref = fields.Char(related='move_line_id.move_id.ref', string='Reference')
    name = fields.Char(related='move_line_id.name', string='Description')
    partner_id = fields.Many2one(related='move_line_id.partner_id')
    amount = fields.Monetary(compute='_compute_amount', store=True, string='Amount')
    deposit = fields.Monetary(compute='_compute_amount', store=True)
    withdrawal = fields.Monetary(compute='_compute_amount', store=True)
    cleared = fields.Boolean()
    stmt_ids = fields.One2many('sb.bank.rec.stmt', 'matched_line_id', string='Matched statement line')

    @api.model_create_multi
    def create(self, vals_list):
        self.env['sb.bank.rec'].browse([v.get('rec_id') for v in vals_list])._sb_check_open()
        return super().create(vals_list)

    def write(self, vals):
        self.rec_id._sb_check_open()
        return super().write(vals)

    def unlink(self):
        # a ledger line deleted under a draft reconciliation takes its item with it; a finished one is protected above
        self.rec_id._sb_check_open()
        return super().unlink()

    @api.depends('move_line_id.balance')
    def _compute_amount(self):
        for l in self:
            b = l.move_line_id.balance
            l.amount = b
            l.deposit = b if b > 0 else 0.0
            l.withdrawal = -b if b < 0 else 0.0


class SbBankRecStmt(models.Model):
    _name = 'sb.bank.rec.stmt'
    _description = 'Imported Statement Line'
    _order = 'date, id'

    rec_id = fields.Many2one('sb.bank.rec', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='rec_id.currency_id')
    date = fields.Date(required=True)
    description = fields.Char()
    reference = fields.Char()
    amount = fields.Monetary(help='Positive = money into the bank, negative = money out.')
    matched_line_id = fields.Many2one('sb.bank.rec.line', string='Matched to', domain="[('rec_id', '=', rec_id)]")
    account_id = fields.Many2one('account.account', string='Post unmatched to',
                                 help='For "Create vouchers": the account to post this line to (default: bank suspense).')
    status = fields.Char(compute='_compute_status')

    @api.model_create_multi
    def create(self, vals_list):
        self.env['sb.bank.rec'].browse([v.get('rec_id') for v in vals_list])._sb_check_open()
        return super().create(vals_list)

    def write(self, vals):
        self.rec_id._sb_check_open()
        return super().write(vals)

    def unlink(self):
        self.rec_id._sb_check_open()
        return super().unlink()

    @api.depends('matched_line_id')
    def _compute_status(self):
        for s in self:
            s.status = 'Matched' if s.matched_line_id else 'Not in the books'
