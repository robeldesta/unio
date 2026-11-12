"""Posted entries stay posted.

A posted journal entry is changed only through the app's own flows: Post, Edit and Void on the voucher, the opening
balances screen, the Year-End Wizard. Nobody can reset an entry to draft, cancel it or change its lines outside them (the
accounting manager neither: reverse it instead), and nobody can touch a year-end closing entry (reopen the year in the
wizard). An entry that was ever posted is never deleted.

Posting and reversing outside the vouchers (Odoo's journal entry screen and its Reverse button) take the Accountant role;
the entry of a voucher is posted and reversed only by the voucher itself (Post and Void), so the voucher and its entry
always agree. An entry left to post itself on its date (Odoo's auto-post) is posted by the system only if everyone who
wrote it may post entries; scheduling one takes the Accountant role too. A new entry starts as a draft. An entry dated more
than 60 days ahead is posted by an Accountant only, and nothing is posted beyond the end of the next fiscal year.

An entry cancelled after it was posted is kept like a posted one: it cannot be changed or deleted, and "posted before" is
set by posting alone.

A posted or void voucher changes only through Edit, Post and Void: a direct change is refused except for the few fields
that do not touch the books (the withholding receipt number). The flags the flows keep (a voucher's status, its entry,
being edited, an opening balance, a year-end closing entry) are set by the flows alone. A voucher reopened with Edit is
still in the books: only users who may edit posted vouchers (Accountant and up) change it, post it again or discard the
changes, and the chatter names who opened and who finished the edit.

The history of entries, vouchers and the other financial documents (stock documents, bank reconciliations, payments,
payroll runs, assets, the chart of accounts: chatter messages and tracked changes), and of lock date changes on the
company, cannot be edited or deleted either.

Every other field of a posted entry and its lines is frozen as well (price, quantity, product, label, due date, payment
reference, analytic distribution ...), except the technical fields Odoo keeps up to date (reconciliation, payment state,
sending) and the Accountant's "Reviewed" mark, which is written to the chatter. Entry numbers are given by the journal or
the voucher, never typed, and a posted entry's number is used once in the company (JV-157 and jv/0157 are one number).

Settlement is kept by Odoo's reconciliation alone: an entry's residual and payment status, a line's reconciled flag,
residual and matching number, and the reconciliation links themselves are written only while the reconciliation (or the
recompute, posting, hashing) runs on the server, never by a client. Items on customer / vendor accounts are matched
within one customer or vendor and one account; matching across customers or vendors takes the Accountant role and is
written in the chatter. The numbering and hash of a posted entry are kept by posting; the withholding details filled in
after posting by an Accountant, written in the chatter. A void voucher's reason stays as it was given.

Unreconciling takes the Accountant role and is written in the chatter of both entries. A message in the history of an
audited document carries the author and date of whoever writes it (a client cannot claim another author or an old date),
and the files attached to a posted entry or voucher stay on file: nobody deletes, replaces or moves them outside the
app's flows (the accounting manager neither). A voucher dated in a closed period cannot be opened with Edit.

Flows pass a context value no client request can carry (a Python object), so a user cannot claim one over RPC.
"""
import contextvars
import re
from contextlib import contextmanager
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date

from .ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian
from .period import LOCK_FIELDS, YEAR_END
from .series import _ascii_text
from .client_guard import server_side

MOVE_FLOW = object()
POSTING = object()
AUTO_POST = object()  # Odoo's auto-post cron is running

# changing these on a posted entry changes the ledger
MOVE_FIELDS = ('date', 'journal_id', 'line_ids', 'invoice_line_ids', 'name', 'currency_id', 'partner_id', 'move_type',
               'company_id', 'invoice_date', 'sb_closing', 'ref', 'narration', 'reversed_entry_id', 'posted_before')
LINE_FIELDS = ('account_id', 'partner_id', 'debit', 'credit', 'balance', 'amount_currency', 'currency_id', 'tax_ids',
               'tax_line_id', 'tax_tag_ids', 'journal_id', 'date', 'move_id', 'company_id')
# Everything else on a posted entry and its lines is frozen too (quantity, price, product, label, due date, payment
# reference, analytic distribution ...), except the technical fields Odoo and the app keep up to date on a posted entry:
# reconciliation and payment state, sending and printing, hashing, the links to the documents that settle it, the chatter.
MOVE_TECH = {
    'access_token', 'activity_ids', 'message_follower_ids', 'message_ids', 'message_main_attachment_id', 'website_message_ids',
    'audit_trail_message_ids', 'attachment_ids', 'amount_residual', 'amount_residual_signed', 'payment_state', 'is_move_sent',
    'sending_data', 'invoice_pdf_report_file', 'ubl_cii_xml_file', 'matched_payment_ids', 'payment_ids', 'reversal_move_ids',
    'tax_cash_basis_created_move_ids', 'exchange_diff_partial_ids', 'statement_line_ids', 'asset_depreciation_ids', 'asset_ids',
    'stock_move_ids', 'transaction_ids', 'inalterable_hash', 'secure_sequence_number', 'made_sequence_gap', 'currency_reval_date',
    'adjusting_entries_move_ids', 'adjusting_entry_origin_move_ids', 'wip_production_ids', 'is_manually_modified',
    'sequence_number', 'sequence_prefix', 'write_date', 'write_uid',
    # withholding details of entries brought over from the previous system, filled in for the declaration
    'sb_wht_base', 'sb_wht_receipt_no', 'sb_wht_receipt_date',
    # handled by their own rules above
    'state', 'posted_before', 'sb_closing',
}
# "Reviewed": an Accountant's mark on a posted entry, written to its chatter
MOVE_REVIEW = ('checked',)
LINE_TECH = {
    'full_reconcile_id', 'matching_number', 'reconciled', 'amount_residual', 'amount_residual_currency', 'matched_debit_ids',
    'matched_credit_ids', 'reconcile_model_id', 'analytic_line_ids', 'is_imported', 'no_followup', 'write_date', 'write_uid',
    'sb_cleared', 'sb_rec_id', 'sb_recost_move_id',  # bank reconciliation, inventory cost recalculation
}
# Of those, what Odoo and the app write themselves inside their flows (Register Payment, Send, the bank reconciliation, the
# revaluation, asset creation, posting): never by a client writing the entry or its lines directly (client_guard)
MOVE_SERVER = {
    'is_move_sent', 'sending_data', 'invoice_pdf_report_file', 'ubl_cii_xml_file', 'matched_payment_ids', 'payment_ids',
    'reversal_move_ids', 'tax_cash_basis_created_move_ids', 'exchange_diff_partial_ids', 'statement_line_ids',
    'asset_depreciation_ids', 'asset_ids', 'stock_move_ids', 'transaction_ids', 'currency_reval_date',
    'adjusting_entries_move_ids', 'adjusting_entry_origin_move_ids', 'wip_production_ids', 'access_token',
}
LINE_SERVER = LINE_TECH - {'no_followup', 'write_date', 'write_uid'}
# copies of the entry's own fields kept on each line (stored related fields): they follow the entry and are never written
# on a line (a line marked posted on a draft entry, or draft on a posted one, would leave or join the books)
LINE_FROM_MOVE = ('parent_state', 'move_name', 'date', 'journal_id', 'company_id', 'company_currency_id', 'invoice_date',
                  'ref', 'payment_id', 'statement_line_id', 'statement_id')

# set by the app's flows only (Post, Edit, Discard, Void, the opening balances screen, the Year-End Wizard)
VOUCHER_FLAGS = ('state', 'move_id', 'linked_voucher_id', 'sb_editing', 'sb_edit_snapshot', 'sb_edit_uid', 'sb_migrated',
                 'sb_ob_kind', 'sb_ob_period')
# what can still be changed directly on a posted or void voucher: nothing the books show
VOUCHER_FREE = ('wht_certificate_no', 'wht_certificate_date', 'sb_void_reason')
# settlement: kept by Odoo's reconciliation (receipts, payments, bank reconciliation, Match Old Items, credit notes) and its
# recompute, never written by a client: an invoice marked paid by hand would leave the open items with nothing in the books
MOVE_SETTLE = ('amount_residual', 'amount_residual_signed', 'payment_state')
LINE_SETTLE = ('reconciled', 'amount_residual', 'amount_residual_currency', 'matching_number', 'full_reconcile_id',
               'matched_debit_ids', 'matched_credit_ids')
# numbering and inalterability of a posted entry: kept by posting and hashing alone
MOVE_SEQ = ('inalterable_hash', 'secure_sequence_number', 'made_sequence_gap', 'is_manually_modified', 'sequence_number',
            'sequence_prefix')
# withholding details filled in after posting (entries brought over): Accountant and up, written to the chatter
MOVE_WHT = ('sb_wht_base', 'sb_wht_receipt_no', 'sb_wht_receipt_date')
PARTNER_ACCOUNT_TYPES = ('asset_receivable', 'liability_payable')
VOUCHER_CHILDREN = ('sb.voucher.line', 'sb.voucher.cash', 'sb.voucher.apply')
# an entry dated further ahead than this is probably a typing error (2062 for 2026): the user is warned, and only an
# Accountant posts it
FUTURE_DAYS = 60

AUDITED_MODELS = ('account.move', 'sb.voucher', 'sb.stock.voucher', 'sb.bank.rec', 'account.payment', 'sb.payroll.run',
                  'account.asset.asset', 'account.account')
COMPANY_AUDIT_FIELDS = {f for f, _label in LOCK_FIELDS} | {'restrictive_audit_trail'}
MESSAGE_CONTENT = {'body', 'subject', 'tracking_value_ids', 'attachment_ids', 'model', 'res_id', 'message_type',
                   'subtype_id', 'author_id', 'email_from', 'date', 'partner_ids', 'record_name', 'parent_id', 'reply_to',
                   'record_alias_domain_id', 'message_id'}


def norm_name(name):
    """A document number as compared across the books: Latin capitals and digits only, no leading zeros
    (JV-0157, jv/157 and JV 157 are one number)."""
    text = re.sub(r'[^A-Z0-9]', '', _ascii_text(name).upper())
    return re.sub(r'(^|\D)0+(?=\d)', r'\1', text)


# the same in SQL, on account_move.name
NORM_SQL = "regexp_replace(regexp_replace(upper(name), '[^A-Z0-9]', '', 'g'), '(^|[^0-9])0+([0-9])', '\\1\\2', 'g')"


def earlier_system(journal):
    """An earlier system's journal (code X..., filled by the nightly sync)."""
    return bool(journal) and journal.type == 'general' and (journal.code or '').upper().startswith('X')


def controlled(records):
    """`records` with the app's controlled-flow flag: posted entries reached through them may be reset and rewritten."""
    return records.with_context(sb_move_flow=MOVE_FLOW)


def in_flow(env):
    return env.su or env.context.get('sb_move_flow') is MOVE_FLOW or env.context.get('sb_year_end') is YEAR_END


# Odoo's reconciliation machinery is running (server side, in this thread): it alone writes the settlement fields and the
# reconciliation links. A client request cannot set it: it is not in the context.
_SETTLING = contextvars.ContextVar('sb_settling', default=0)


@contextmanager
def settling():
    token = _SETTLING.set(_SETTLING.get() + 1)
    try:
        yield
    finally:
        _SETTLING.reset(token)


def may_settle(env):
    return in_flow(env) or env.context.get('sb_posting') is POSTING or _SETTLING.get() > 0


def _settle_refused(env, what):
    return UserError(env._('%s is kept by the reconciliation itself (a receipt or payment applied, the bank reconciliation, '
                           'Match Old Items, a credit note): it cannot be written directly.', what))


def is_accountant(user):
    """May post journal entries directly and edit posted vouchers."""
    return user._is_superuser() or user.has_group('sonador_books.group_sb_accountant')


def latest_date(env):
    """The last date anything may be posted on: the end of the next fiscal year."""
    today = fields.Date.context_today(env['res.users'])
    return fiscal_periods(fiscal_year_of(today) + 1)[-1][3]


def _defaults(env, vals, names):
    """The values of `names` a create would set, from `vals` or from default_<name> in the context."""
    return {f: vals[f] if f in vals else env.context['default_' + f] for f in names
            if f in vals or 'default_' + f in env.context}


def _kept(records, name):
    """Whether writing `name` changes what is kept: a stored field, or one written through to others (Odoo's own
    "dirty" markers, computed and not stored, change nothing)."""
    field = records._fields.get(name)
    return bool(field and (field.store or field.inverse or field.related))


def _will_change(record, vals, name):
    """Whether writing `vals` changes `name` on `record` (a list of lines always counts as a change)."""
    field = record._fields.get(name)
    if not field or field.type == 'one2many':
        return True
    return record.env['account.move']._field_will_change(record, vals, name)


def future_warning(env, day):
    """A warning (not a refusal) for a date far ahead, or None."""
    today = fields.Date.context_today(env['res.users'])
    if day and day > today + timedelta(days=FUTURE_DAYS):
        return {'title': env._('Date far ahead'),
                'message': env._('%(date)s (%(ec)s) is %(n)s days from today. Check the year before posting.',
                                 date=day, ec=format_ethiopian(day), n=(day - today).days)}
    return None


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _sb_ever_posted(self):
        return self.filtered(lambda m: m.posted_before or (m.name and m.name != '/'))

    def _sb_locked(self):
        """Posted entries, and entries cancelled after they were posted: both change through the flows only."""
        return self.filtered(lambda m: m.state == 'posted' or (m.state == 'cancel' and m.posted_before))

    def _sb_documents(self):
        """{entry: the document it is the entry of} for the entries of these that belong to a voucher (and the reversals of
        those): such an entry is posted, edited and reversed only through its document. Modules add their documents."""
        moves = self | self.reversed_entry_id
        docs = {}
        for v in self.env['sb.voucher'].sudo().search([('move_id', 'in', moves.ids)]):
            docs[v.move_id] = v
        for move in self:
            if move not in docs and move.reversed_entry_id in docs:
                docs[move] = docs[move.reversed_entry_id]
        return {m: d for m, d in docs.items() if m in self}

    def _sb_check_change(self, labels=None):
        """Refuse to change these posted entries outside the app's flows (see the module docstring)."""
        posted = self._sb_locked()
        if not posted:
            return
        ctx = self.env.context
        closing = posted.filtered('sb_closing')
        if closing and ctx.get('sb_year_end') is not YEAR_END:
            raise UserError(self.env._('%s is a year-end closing entry. To change it, reopen the year in Setup > Year-End Closing.',
                                       ', '.join(closing.mapped('name'))))
        if in_flow(self.env):
            return
        docs = posted._sb_documents()
        if docs:
            raise UserError(self.env._('Posted entries cannot be changed. Void %s to reverse it (or Edit it).',
                                       ', '.join(sorted({d.display_name for d in docs.values()}))))
        raise UserError(self.env._('Posted entries cannot be changed: %(names)s%(what)s. Reverse the entry instead (Reverse '
                                   'button or a journal voucher), then enter it again.', names=', '.join(posted.mapped('name')),
                                   what=' (%s)' % ', '.join(labels) if labels else ''))

    def _sb_check_post(self):
        """Who may post these entries (see the module docstring). The auto-post cron runs as the system: there, the people
        who wrote the entry must be allowed to post it."""
        auto = self.env.context.get('sb_autopost') is AUTO_POST
        if in_flow(self.env) and not auto:
            return
        docs = self._sb_documents()
        if docs:
            raise UserError(self.env._('%s is posted from its voucher only: open the voucher and post it there.',
                                       ', '.join(sorted({d.display_name for d in docs.values()}))))
        if auto:
            for move in self:
                writers = (move.create_uid | move.write_uid | move.line_ids.create_uid | move.line_ids.write_uid).sudo()
                cannot = writers.filtered(lambda u: not is_accountant(u))
                if cannot:
                    raise UserError(self.env._(
                        '%(entry)s was entered or changed by %(users)s, who cannot post journal entries: it is not posted '
                        'automatically. An Accountant must check it and post it.',
                        entry=move.name if move.name and move.name != '/' else move.ref or self.env._('This entry'),
                        users=', '.join(cannot.mapped('name'))))
            return
        if not is_accountant(self.env.user):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can post journal entries directly. '
                                       'Enter a voucher instead.'))

    def _sb_check_dates(self):
        """Nothing is posted beyond the end of the next fiscal year; an entry more than FUTURE_DAYS ahead only by an
        Accountant (a date like 2062 for 2026 is a typing error). The system's own posting (scripts, the cron) is exempt."""
        user = self.env.user
        if user._is_superuser():
            return
        today = fields.Date.context_today(user)
        last = latest_date(self.env)
        for move in self.filtered(lambda m: m.date and m.date > today + timedelta(days=FUTURE_DAYS)):
            what = move.name if move.name and move.name != '/' else move.ref or self.env._('This entry')
            if move.date > last:
                raise UserError(self.env._(
                    '%(entry)s is dated %(date)s (%(ec)s), after the end of next fiscal year (%(last)s). Check the year.',
                    entry=what, date=move.date, ec=format_ethiopian(move.date), last=last))
            if not is_accountant(user):
                raise UserError(self.env._(
                    '%(entry)s is dated %(date)s (%(ec)s), %(n)s days from today. Check the date: only an Accountant can post '
                    'this far ahead.', entry=what, date=move.date, ec=format_ethiopian(move.date), n=(move.date - today).days))

    def _sb_check_flags(self, vals_list):
        """sb_closing is set by the Year-End Wizard alone."""
        if self.env.su or self.env.context.get('sb_year_end') is YEAR_END:
            return
        if any(v.get('sb_closing') for v in vals_list) or self.env.context.get('default_sb_closing'):
            raise UserError(self.env._('Only Year-End Closing marks an entry as a year-end closing entry.'))

    def _sb_check_auto_post(self, vals_list):
        """Scheduling an entry to post itself is posting it: Accountant and up."""
        if in_flow(self.env) or self.env.context.get('sb_posting') is POSTING or is_accountant(self.env.user):
            return
        if any(_defaults(self.env, v, ['auto_post']).get('auto_post') not in (None, False, 'no') for v in vals_list):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can set a journal entry to post '
                                       'automatically.'))

    def _sb_check_typed_name(self, vals_list):
        """An entry is numbered by its journal when it is posted (or by its voucher's series): a number typed by hand could
        repeat another document's (JV-157 in the payments journal). The app's flows name their entries themselves."""
        if in_flow(self.env) or self.env.context.get('sb_posting') is POSTING:
            return
        if any(_defaults(self.env, v, ['name']).get('name') not in (None, False, '', '/') for v in vals_list):
            raise UserError(self.env._('Entry numbers are given by the journal when the entry is posted (or by the voucher): '
                                       'they cannot be typed in. Leave the number empty; put your own reference in Reference.'))

    def _sb_check_unique_name(self):
        """A posted entry's number is used once in the company's books, whatever the journal: JV-157, jv/0157 and JV 157 are
        the same number."""
        named = self.filtered(lambda m: m.state == 'posted' and m.name and m.name != '/')
        if not named:
            return
        wanted = {m.id: norm_name(m.name) for m in named}
        self.flush_model(['name', 'state', 'company_id'])
        self.env.cr.execute('SELECT id, name, company_id, norm FROM (SELECT id, name, company_id, %s AS norm FROM account_move '
                            "WHERE state = 'posted' AND company_id = ANY(%%s)) x WHERE norm = ANY(%%s)" % NORM_SQL,
                            (named.company_id.ids, list(set(wanted.values()))))
        rows = self.env.cr.fetchall()
        for move in named:
            other = next((r for r in rows if r[3] == wanted[move.id] and r[2] == move.company_id.id and r[0] != move.id), None)
            if other:
                doc = self.env['sb.voucher'].sudo().search([('move_id', '=', other[0])], limit=1)
                raise UserError(self.env._(
                    '%(name)s is already the number of %(other)s%(doc)s. A document number is used once in the books: '
                    'leave the number to the journal, or use the next number of the series.', name=move.name, other=other[1],
                    doc=' (%s)' % doc.display_name if doc and doc.name != other[1] else ''))

    def _sb_name_taken(self, name):
        """Whether a posted entry of this company already carries `name` (compared as norm_name does)."""
        self.ensure_one()
        self.env.cr.execute("SELECT 1 FROM account_move WHERE state = 'posted' AND company_id = %%s AND %s = %%s LIMIT 1" % NORM_SQL,
                            (self.company_id.id, norm_name(name)))
        return bool(self.env.cr.fetchone())

    def _sb_check_frozen(self, vals):
        """Every field of a posted entry is frozen outside the flows, except the technical ones (MOVE_TECH) and the
        Accountant's review mark (MOVE_REVIEW)."""
        locked = self._sb_locked()
        if not locked or in_flow(self.env) or self.env.context.get('sb_posting') is POSTING:
            return
        names = [f for f in vals if f not in MOVE_TECH and f not in MOVE_FIELDS and f != 'auto_post' and _kept(self, f)
                 and not f.startswith(('message_', 'activity_'))]
        changing = [f for f in names if any(self._field_will_change(m, vals, f) for m in locked)]
        review = [f for f in changing if f in MOVE_REVIEW]
        frozen = [f for f in changing if f not in MOVE_REVIEW]
        if frozen:
            locked._sb_check_change([self._fields[f].string for f in frozen])
        if review and not is_accountant(self.env.user):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can mark posted entries as reviewed.'))

    def _sb_check_server_fields(self, vals):
        """What Odoo keeps up to date on a posted entry inside its flows (sent, linked payments, statement lines, assets,
        revaluation date ...) is not written by a client writing the entry directly."""
        names = [f for f in MOVE_SERVER if f in vals and f in self._fields]
        if not names or may_settle(self.env) or server_side(self.env):
            return
        locked = self._sb_locked()
        changing = [f for f in names if any(_will_change(m, vals, f) for m in locked)]
        if changing:
            raise UserError(self.env._('%(f)s of a posted entry is kept by the app itself (payments, sending, bank '
                                       'statements, assets, revaluation): it cannot be written directly (%(e)s).',
                                       f=', '.join(self._fields[f].string for f in changing),
                                       e=', '.join(locked.mapped('name'))))

    def _sb_check_settle_write(self, vals):
        """Settlement (residual, payment status) and the numbering / hash of a posted entry change through Odoo's own
        machinery only; the withholding details of a posted entry by an Accountant, written to its chatter."""
        if may_settle(self.env):
            return
        settle = [f for f in MOVE_SETTLE if f in vals and any(self._field_will_change(m, vals, f) for m in self)]
        if settle:
            raise _settle_refused(self.env, ', '.join(self._fields[f].string for f in settle))
        locked = self._sb_locked()
        seq = [f for f in MOVE_SEQ if f in vals and f in self._fields and any(self._field_will_change(m, vals, f) for m in locked)]
        if seq:
            raise UserError(self.env._('%(f)s of a posted entry is kept by posting (numbering, inalterability hash): it cannot '
                                       'be written directly (%(e)s).', f=', '.join(self._fields[f].string for f in seq),
                                       e=', '.join(locked.mapped('name'))))
        if any(f in vals for f in MOVE_WHT) and not is_accountant(self.env.user) and any(
                self._field_will_change(m, vals, f) for m in locked for f in MOVE_WHT if f in vals):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can fill in the withholding details '
                                       'of a posted entry.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not may_settle(self.env):
            for vals in vals_list:
                bad = [f for f in MOVE_SETTLE if f in vals] + [f for f in ('inalterable_hash', 'secure_sequence_number')
                                                               if vals.get(f)]
                if bad:
                    raise _settle_refused(self.env, ', '.join(self._fields[f].string for f in bad))
        self._sb_check_flags(vals_list)
        self._sb_check_auto_post(vals_list)
        self._sb_check_typed_name(vals_list)
        if not in_flow(self.env):
            for vals in vals_list:
                given = _defaults(self.env, vals, ['state', 'posted_before'])
                if given.get('state') not in (None, False, 'draft') or given.get('posted_before'):
                    raise UserError(self.env._('A new journal entry starts as a draft: create it, then post it (or enter a '
                                               'voucher).'))
        moves = super().create(vals_list)
        moves._sb_check_journal_use()
        return moves

    def _sb_check_journal_use(self):
        """The earlier systems' journals (X...) are filled by the nightly sync alone, and the Cash Sales journal by Cash Sale
        vouchers alone (each takes the cash register's number): nobody enters an entry there by hand."""
        if in_flow(self.env) or self.env.user._is_superuser():
            return
        for move in self:
            if earlier_system(move.journal_id):
                raise UserError(self.env._(
                    '%(j)s holds the entries brought over from the earlier system by the nightly sync: new entries go to '
                    'the Journal Vouchers journal (or are entered as a voucher).', j=move.journal_id.display_name))
            if (move.journal_id.code or '').upper() == 'CS':
                raise UserError(self.env._(
                    '%(j)s takes Cash Sale vouchers only (each carries its cash register number): enter a Cash Sale voucher, '
                    'or a journal voucher for a correction.', j=move.journal_id.display_name))

    @api.model
    def _sb_reversal_journal(self, company):
        """Where the reversal of an entry brought over from the earlier system goes: the Journal Vouchers journal."""
        Journal = self.env['account.journal'].sudo()
        dom = [('company_id', '=', company.id), ('type', '=', 'general')]
        return Journal.search(dom + [('code', '=', 'JV')], limit=1) or Journal.search(dom, order='sequence, id').filtered(
            lambda j: not earlier_system(j) and j.code not in ('CS', 'OB', 'CABA', 'EXCH'))[:1]

    def button_draft(self):
        self._sb_check_change()
        return super().button_draft()

    def button_cancel(self):
        self._sb_check_change()
        return super().button_cancel()

    def write(self, vals):
        if 'sb_closing' in vals and any(self._field_will_change(m, vals, 'sb_closing') for m in self):
            self._sb_check_flags([vals])
        if 'auto_post' in vals:
            self._sb_check_auto_post([vals])
        posting = self.env.context.get('sb_posting') is POSTING
        if 'posted_before' in vals and not in_flow(self.env) and not (posting and vals['posted_before']) and \
                any(self._field_will_change(m, vals, 'posted_before') for m in self):
            raise UserError(self.env._('"Posted before" is set by posting the entry, not by hand.'))
        if any(f in vals for f in MOVE_FIELDS):
            moving = self._sb_locked().filtered(lambda m: any(self._field_will_change(m, vals, f) for f in MOVE_FIELDS
                                                              if f in vals and not (posting and f == 'posted_before')))
            moving._sb_check_change()
        if vals.get('state') in ('draft', 'cancel'):
            self._sb_locked().filtered(lambda m: m.state != vals['state'])._sb_check_change()
        if vals.get('state') == 'posted' and not posting:
            self.filtered(lambda m: m.state != 'posted')._sb_check_post()
        if 'name' in vals and any(self._field_will_change(m, vals, 'name') for m in self):
            self._sb_check_typed_name([{'name': vals['name']}])
        self._sb_check_settle_write(vals)
        self._sb_check_server_fields(vals)
        self._sb_check_frozen(vals)
        logged = MOVE_REVIEW + MOVE_WHT
        review = {m.id: {f: m[f] for f in logged if f in vals} for m in self._sb_locked()} \
            if any(f in vals for f in logged) and not self.env.su else {}
        res = super().write(vals)
        if 'journal_id' in vals:
            self._sb_check_journal_use()
        for move in self.filtered(lambda m: m.id in review):
            changed = [(self._fields[f].string, old, move[f]) for f, old in review[move.id].items() if old != move[f]]
            if changed:
                move.sudo().message_post(author_id=self.env.user.partner_id.id, body=self.env._(
                    '%(what)s by %(user)s.', user=self.env.user.name,
                    what='; '.join('%s: %s -> %s' % c for c in changed)))
        if 'name' in vals and not self.env.su:
            self._sb_check_unique_name()
        return res

    def unlink(self):
        posted = self._sb_ever_posted()
        if posted.filtered('sb_closing'):
            raise UserError(self.env._('A year-end closing entry cannot be deleted. Reopen the year in Setup > Year-End Closing.'))
        # the opening balances screen is the one flow that takes an entry out (an opening item removed before go-live)
        if posted and self.env.context.get('sb_move_flow') is not MOVE_FLOW:
            raise UserError(self.env._('%s was posted and cannot be deleted. Reverse it (void the voucher) instead.',
                                       ', '.join(posted.mapped(lambda m: m.name if m.name and m.name != '/' else str(m.id)))))
        return super().unlink()

    def _post(self, soft=True):
        """Odoo moves an entry dated in a closed period to the first open date without a word. Refuse it instead,
        as the voucher screen does. Who may post: see _sb_check_post."""
        today = fields.Date.context_today(self)
        self._sb_check_post()
        self._sb_check_dates()
        for move in self:
            if soft and move.date and move.date > today:
                continue  # posted later, on its date, by Odoo
            locks = move._get_violated_lock_dates(move.date, move._affect_tax_report()) if move.date else []
            if locks:
                lock, field = locks[-1]
                what = dict(LOCK_FIELDS).get(field, field)
                raise UserError(self.env._(
                    '%(entry)s is dated %(date)s, in a closed period (%(what)s locked through %(lock)s, %(ec)s). '
                    'Change the date or reopen the period.',
                    entry=move.name if move.name and move.name != '/' else move.ref or self.env._('This entry'),
                    date=format_date(self.env, move.date), what=what, lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
        res = super(AccountMove, self.with_context(sb_posting=POSTING))._post(soft=soft)
        if not self.env.su:
            res._sb_check_unique_name()
        for move in res:
            if future_warning(self.env, move.date) and not self.env.su:
                move.message_post(body=self.env._('Posted with a date %s days ahead of today.', (move.date - today).days))
        return res

    @api.model
    def _autopost_draft_entries(self, batch_size=100):
        # the cron runs as the system: the entries' writers must be allowed to post them (_sb_check_post)
        return super(AccountMove, self.with_context(sb_autopost=AUTO_POST))._autopost_draft_entries(batch_size=batch_size)

    def _reverse_moves(self, default_values_list=None, cancel=False):
        """A year-end closing entry is reversed by the Year-End Wizard alone (reopening the year), the entry of a voucher by
        the voucher's Void alone."""
        closing = self.filtered('sb_closing')
        if closing and self.env.context.get('sb_year_end') is not YEAR_END:
            raise UserError(self.env._('%s is a year-end closing entry. To undo it, reopen the year in Setup > Year-End Closing.',
                                       ', '.join(closing.mapped('name'))))
        if not in_flow(self.env):
            docs = self._sb_documents()
            if docs:
                raise UserError(self.env._('%s: void the voucher to reverse its entry (a reversed entry would leave the voucher '
                                           'posted).', ', '.join(sorted({d.display_name for d in docs.values()}))))
        if not in_flow(self.env) and not self.env.user._is_superuser() and any(earlier_system(m.journal_id) for m in self):
            # an entry brought over from the earlier system is reversed in the Journal Vouchers journal, numbered there
            default_values_list = [dict(v) for v in default_values_list] if default_values_list else [{} for _m in self]
            for move, values in zip(self, default_values_list):
                journal = self.env['account.journal'].browse(values.get('journal_id')) if values.get('journal_id') else move.journal_id
                if earlier_system(journal):
                    target = self._sb_reversal_journal(move.company_id)
                    if not target:
                        raise UserError(self.env._('There is no Journal Vouchers journal to reverse %s in.', move.name))
                    values['journal_id'] = target.id
        return super()._reverse_moves(default_values_list, cancel=cancel)

    def _hash_moves(self, **kwargs):
        with settling():
            return super()._hash_moves(**kwargs)

    def action_toggle_block_payment(self):
        with settling():
            return super().action_toggle_block_payment()

    @api.onchange('date')
    def _onchange_sb_future_date(self):
        if warning := future_warning(self.env, self.date):
            return {'warning': warning}


class AccountMoveReversal(models.TransientModel):
    _inherit = 'account.move.reversal'

    def reverse_moves(self, is_modify=False):
        """Reversing a posted entry takes the same role as voiding a voucher."""
        self.env['sb.voucher']._sb_require('group_sb_accountant', self.env._('reverse posted entries'))
        # Odoo would move a reversal dated in a closed period to the first open date without a word: refuse it instead
        for move in self.move_ids:
            day = self.date or move.date
            locks = move._get_violated_lock_dates(day, move._affect_tax_report()) if day else []
            if locks:
                lock, field = locks[-1]
                raise UserError(self.env._(
                    'The reversal of %(entry)s would be dated %(date)s, in a closed period (%(what)s locked through %(lock)s, '
                    '%(ec)s). Choose a date in an open period.', entry=move.name, date=day,
                    what=dict(LOCK_FIELDS).get(field, field), lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
        return super().reverse_moves(is_modify=is_modify)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _sb_locked(self):
        # the entry's own state, never the copy kept on the line (parent_state)
        return self.filtered(lambda l: l.move_id.state == 'posted' or (l.move_id.state == 'cancel' and l.move_id.posted_before))

    def _sb_check_move_copies(self, vals):
        """The entry's fields copied on its lines (state, number, date, journal ...) follow the entry: a line cannot be
        given another value than its entry's."""
        names = [f for f in LINE_FROM_MOVE if f in vals and f in self._fields]
        if not names or self.env.user._is_superuser():
            return
        for line in self.sudo():
            for f in names:
                field = self._fields[f]
                source = line
                for part in field.related.split('.') if isinstance(field.related, str) else field.related:
                    source = source[part]
                try:
                    same = field.convert_to_cache(vals[f], line) == field.convert_to_cache(source, line)
                except (ValueError, TypeError):
                    same = False
                if not same:
                    raise UserError(self.env._(
                        '%(field)s of a journal item is its entry\'s (%(entry)s): it cannot be written on the item.',
                        field=field.string, entry=line.move_id.name or line.move_id.display_name))

    def _sb_check_server_fields(self, vals):
        """Reconciliation links, analytic lines, bank reconciliation and recosting marks of posted items are written by
        Odoo and the app inside their flows, not by a client writing the items."""
        names = [f for f in LINE_SERVER if f in vals]
        if not names or may_settle(self.env) or server_side(self.env):
            return
        locked = self._sb_locked()
        changing = [f for f in names if any(_will_change(l, vals, f) for l in locked)]
        if changing:
            raise UserError(self.env._('%(f)s of a posted journal item is kept by the app itself (reconciliation, bank '
                                       'reconciliation, analytics): it cannot be written directly (%(e)s).',
                                       f=', '.join(self._fields[f].string for f in changing),
                                       e=', '.join(locked.move_id.mapped('name'))))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user._is_superuser():
            # the entry's fields copied on the line come from the entry (move_id), whatever a request gives
            vals_list = [{k: v for k, v in vals.items() if k not in LINE_FROM_MOVE} for vals in vals_list]
        if not may_settle(self.env):
            for vals in vals_list:
                bad = [f for f in LINE_SETTLE if f in vals and vals[f] and not (
                    f == 'matching_number' and str(vals[f]).startswith('I'))]  # 'I…': Odoo's import pairing, matched on posting
                bad += [f for f in ('amount_residual', 'amount_residual_currency') if f in vals and f not in bad]
                if bad:
                    raise _settle_refused(self.env, ', '.join(self._fields[f].string for f in bad))
        move_ids = {v['move_id'] for v in vals_list if v.get('move_id')}
        if move_ids:
            self.env['account.move'].browse(move_ids)._sb_locked()._sb_check_change()
        return super().create(vals_list)

    def write(self, vals):
        self._sb_check_move_copies(vals)
        if not may_settle(self.env):
            settle = [f for f in LINE_SETTLE if f in vals and any(_will_change(l, vals, f) for l in self)]
            if settle:
                raise _settle_refused(self.env, ', '.join(self._fields[f].string for f in settle))
        self._sb_check_server_fields(vals)
        if any(f in vals for f in LINE_FIELDS):
            changing = self._sb_locked().filtered(lambda l: any(
                self.env['account.move']._field_will_change(l, vals, f) for f in LINE_FIELDS if f in vals))
            changing.move_id._sb_check_change()
        # everything else but the technical fields (reconciliation, bank reconciliation): price, quantity, product, label ...
        if not in_flow(self.env) and self.env.context.get('sb_posting') is not POSTING:
            names = [f for f in vals if f not in LINE_TECH and f not in LINE_FIELDS and _kept(self, f)]
            locked = self._sb_locked() if names else self.browse()
            frozen = [f for f in names if any(self.env['account.move']._field_will_change(l, vals, f) for l in locked)]
            if frozen:
                locked.move_id._sb_check_change([self._fields[f].string for f in frozen])
        return super().write(vals)

    def unlink(self):
        self._sb_locked().move_id._sb_check_change()
        return super().unlink()

    @api.model
    def _sb_plan_lines(self, plan):
        lines = self.env['account.move.line']
        for item in plan:
            lines |= self._sb_plan_lines(item) if isinstance(item, (list, tuple)) else item
        return lines

    def _reconcile_plan(self, reconciliation_plan):
        """Every reconciliation (reconcile(), payments, bank statements, the manual matching screen) goes through here.
        Items on customer / vendor accounts are matched within one customer or vendor: matching across them takes the
        Accountant role and is written in the chatter of the entries. The links and the settlement fields are written by
        this machinery alone (see settling)."""
        crossed = []
        for item in reconciliation_plan:
            lines = self._sb_plan_lines([item])
            if len(lines.account_id) > 1:
                raise UserError(self.env._('Items of different accounts cannot be matched: %s.',
                                           ', '.join(lines.account_id.mapped('display_name'))))
            on_partner = lines.filtered(lambda l: l.account_id.account_type in PARTNER_ACCOUNT_TYPES)
            if len({l.partner_id.commercial_partner_id.id for l in on_partner}) > 1:
                crossed.append(on_partner)
        if crossed and not self.env.su:
            if not is_accountant(self.env.user):
                raise UserError(self.env._(
                    'Items of different customers / vendors cannot be matched (%s): apply a receipt or payment to its own '
                    'customer\'s or vendor\'s items. Moving a balance from one to another takes an Accountant and a journal '
                    'voucher.', '; '.join(', '.join(sorted({l.partner_id.display_name or self.env._('no partner')
                                                              for l in c})) for c in crossed)))
        with settling():
            res = super()._reconcile_plan(reconciliation_plan)
        if crossed and not self.env.su:
            for lines in crossed:
                note = self.env._('Matched across customers / vendors by %(user)s: %(items)s.', user=self.env.user.name,
                                  items='; '.join('%s %s %s' % (l.move_id.name, l.partner_id.display_name or '-',
                                                                '{:,.2f}'.format(l.balance)) for l in lines))
                for move in lines.move_id:
                    move.sudo().message_post(body=note, author_id=self.env.user.partner_id.id)
        return res


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def _sb_check_edit_rights(self):
        """A voucher reopened with Edit is still in the books: it is changed, posted again or discarded by those who may edit
        posted vouchers."""
        editing = self.filtered('sb_editing')
        if editing and not self.env.su and not is_accountant(self.env.user):
            raise UserError(self.env._('%s is being edited (a posted voucher reopened with Edit): only users with the '
                                       'Accountant role (or higher) can change it, post it or discard the changes.',
                                       ', '.join(editing.mapped('name'))))

    def _sb_check_not_closing(self):
        closing = self.filtered(lambda v: v.state == 'posted' and v.move_id.sb_closing)
        if closing and self.env.context.get('sb_year_end') is not YEAR_END:
            raise UserError(self.env._('%s is a year-end closing voucher. To undo it, reopen the year in Setup > Year-End Closing.',
                                       ', '.join(closing.mapped('name'))))

    @api.model
    def _sb_free_fields(self):
        """Fields of a posted or void voucher that can be changed directly (they do not touch the books)."""
        return set(VOUCHER_FREE) | {f for f in self._fields if f.startswith(('message_', 'activity_'))}

    def _sb_check_locked(self, fields_changed):
        """A posted or void voucher changes through Edit, Post and Void only."""
        if in_flow(self.env):
            return
        locked = self.filtered(lambda v: v.state in ('posted', 'void'))
        blocked = set(fields_changed) - self._sb_free_fields()
        if 'sb_void_reason' in fields_changed and locked.filtered(lambda v: v.state == 'void'):
            blocked.add('sb_void_reason')  # written before voiding; once void, it stays as it was given
            locked = locked.filtered(lambda v: v.state == 'void') if blocked == {'sb_void_reason'} else locked
        if locked and blocked:
            labels = sorted({self._fields[f].string for f in blocked if f in self._fields})
            raise UserError(self.env._('%(v)s is %(state)s and cannot be changed directly (%(fields)s). Use Edit or Void.',
                                       v=', '.join(locked.mapped('name')),
                                       state=self.env._('void') if all(v.state == 'void' for v in locked) else self.env._('posted'),
                                       fields=', '.join(labels)))

    @api.model_create_multi
    def create(self, vals_list):
        if not in_flow(self.env):
            for vals in vals_list:
                flags = {f: v for f, v in _defaults(self.env, vals, VOUCHER_FLAGS).items() if v and v != 'draft'}
                if flags:
                    raise UserError(self.env._('A new voucher starts as not posted: %s is set by posting, editing or voiding it.',
                                               ', '.join(sorted(self._fields[f].string for f in flags))))
        return super().create(vals_list)

    def write(self, vals):
        if not in_flow(self.env):
            changing = [f for f in vals if any(_will_change(v, vals, f) for v in self)] if self else list(vals)
            flags = [f for f in changing if f in VOUCHER_FLAGS]
            if flags:
                raise UserError(self.env._('%s is changed only by posting, editing or voiding the voucher.',
                                           ', '.join(sorted(self._fields[f].string for f in flags))))
            self._sb_check_locked(changing)
            if set(changing) - self._sb_free_fields():
                self._sb_check_edit_rights()
        wht = [f for f in ('wht_certificate_no', 'wht_certificate_date') if f in vals]
        before = {}
        if wht and not in_flow(self.env):
            done = self.filtered(lambda v: v.state in ('posted', 'void'))
            before = {v.id: {f: v[f] for f in wht} for v in done}
            if done and not is_accountant(self.env.user) and any(_will_change(v, vals, f) for v in done for f in wht):
                raise UserError(self.env._('The withholding receipt of a posted voucher is changed by an Accountant (or '
                                           'higher) only.'))
        res = super().write(vals)
        for v in self.filtered(lambda x: x.id in before):
            changed = ['%s: %s -> %s' % (self._fields[f].string, old or '-', v[f] or '-') for f, old in before[v.id].items()
                       if old != v[f]]
            if changed:
                v.sudo().message_post(author_id=self.env.user.partner_id.id,
                                      body=self.env._('%(what)s by %(user)s.', what='; '.join(changed), user=self.env.user.name))
        return res

    def action_post(self):
        self._sb_check_edit_rights()
        return super(SbVoucher, controlled(self)).action_post()

    def action_edit(self):
        self._sb_check_not_closing()
        for v in self.filtered(lambda x: x.state == 'posted' and x.move_id.date):
            locks = v.move_id._get_violated_lock_dates(v.move_id.date, v.move_id._affect_tax_report())
            if locks:
                lock, field = locks[-1]
                raise UserError(self.env._(
                    '%(v)s is dated %(date)s, in a closed period (%(what)s locked through %(lock)s, %(ec)s): it cannot be '
                    'edited. Void it (the reversal is dated in the open period) and enter it again, or have the period '
                    'reopened first.', v=v.name, date=format_date(self.env, v.date), what=dict(LOCK_FIELDS).get(field, field), lock=format_date(self.env, lock),
                    ec=format_ethiopian(lock)))
        return super(SbVoucher, controlled(self)).action_edit()

    def action_discard_edit(self):
        self._sb_check_edit_rights()
        return super(SbVoucher, controlled(self)).action_discard_edit()

    def action_void(self):
        self._sb_check_not_closing()
        editing = self.filtered('sb_editing')
        if editing:
            raise UserError(self.env._('%s is being edited: discard or post the edit first, then void it.',
                                       ', '.join(editing.mapped('name'))))
        return super(SbVoucher, controlled(self)).action_void()

    @api.onchange('date')
    def _onchange_sb_future_date(self):
        if warning := future_warning(self.env, self.date):
            return {'warning': warning}


class SbVoucherChild(models.AbstractModel):
    """Lines, deposit / payment rows and applied invoices of a posted or void voucher change with the voucher only."""
    _name = 'sb.voucher.child.guard'
    _description = 'Voucher row guard'

    def _sb_check_voucher_open(self, vouchers):
        if in_flow(self.env):
            return
        locked = vouchers.filtered(lambda v: v.state in ('posted', 'void'))
        if locked:
            raise UserError(self.env._('%s is posted: its lines change only through Edit (or Void).',
                                       ', '.join(locked.mapped('name'))))
        vouchers._sb_check_edit_rights()

    @api.model_create_multi
    def create(self, vals_list):
        self._sb_check_voucher_open(self.env['sb.voucher'].browse({v['voucher_id'] for v in vals_list if v.get('voucher_id')}))
        return super().create(vals_list)

    def write(self, vals):
        if vals:
            self._sb_check_voucher_open(self.voucher_id | self.env['sb.voucher'].browse(vals.get('voucher_id') or []))
        return super().write(vals)

    def unlink(self):
        self._sb_check_voucher_open(self.voucher_id)
        return super().unlink()


class SbVoucherLine(models.Model):
    _name = 'sb.voucher.line'
    _inherit = ['sb.voucher.line', 'sb.voucher.child.guard']


class SbVoucherCash(models.Model):
    _name = 'sb.voucher.cash'
    _inherit = ['sb.voucher.cash', 'sb.voucher.child.guard']


class SbVoucherApply(models.Model):
    _name = 'sb.voucher.apply'
    _inherit = ['sb.voucher.apply', 'sb.voucher.child.guard']


class SbBeginning(models.AbstractModel):
    _inherit = 'sb.beginning'

    # the opening balances screen is a controlled flow (Accountant and up, checked by each save): it writes opening vouchers

    @api.model
    def bb_accounts_save(self, key, amounts):
        return super(SbBeginning, controlled(self)).bb_accounts_save(key, amounts)

    @api.model
    def bb_partner_save(self, kind, partner_id, rows):
        return super(SbBeginning, controlled(self)).bb_partner_save(kind, partner_id, rows)

    @api.model
    def merge_into_opening(self):
        return super(SbBeginning, controlled(self)).merge_into_opening()

    @api.model
    def _adopt(self):
        return super(SbBeginning, controlled(self))._adopt()

    @api.model
    def _repost(self, voucher, lines):
        return super(SbBeginning, controlled(self))._repost(controlled(voucher), lines)

    @api.model
    def _remove(self, voucher):
        # an opening item taken out of the opening balances screen goes with its entry and the entry's history
        from odoo.addons.account.models.mail_message import bypass_token
        flow = controlled(self).with_context(bypass_audit=bypass_token)
        return super(SbBeginning, flow)._remove(voucher.with_env(flow.env))


# ---------------------------------------------------------------------- audit trail
class MailMessage(models.Model):
    _inherit = 'mail.message'

    def _sb_audited(self):
        """Messages that are part of the accounting audit trail."""
        return self.sudo().filtered(lambda m: m.model in AUDITED_MODELS or (
            m.model == 'res.company' and COMPANY_AUDIT_FIELDS & set(m.tracking_value_ids.field_id.mapped('name'))))

    @api.model_create_multi
    def create(self, vals_list):
        """A message in the history of an entry or voucher is written by whoever writes it, when they write it: the author
        and the date given by a client (Administrator, 1 Jan 2024) are replaced. Odoo's own messages (tracking, mail
        received, the app's flows) are posted by the system and keep theirs."""
        if not in_flow(self.env):
            partner = self.env.user.partner_id
            default_model = self.env.context.get('default_model')
            for vals in vals_list:
                if (vals.get('model') or default_model) in AUDITED_MODELS + ('res.company',):
                    for name in ('email_from', 'author_guest_id', 'create_date', 'create_uid', 'write_date', 'write_uid'):
                        vals.pop(name, None)
                    vals.update(author_id=partner.id, date=fields.Datetime.now())
            self = self.with_context({k: v for k, v in self.env.context.items()  # noqa: PLW0642
                                      if k not in ('default_author_id', 'default_email_from', 'default_date')})
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and MESSAGE_CONTENT & set(vals) and (self._sb_audited() or vals.get('model') in AUDITED_MODELS):
            raise UserError(self.env._('The history of accounting entries and vouchers cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self._sb_audited():
            raise UserError(self.env._('The history of accounting entries and vouchers cannot be deleted.'))
        return super().unlink()


class MailTrackingValue(models.Model):
    _inherit = 'mail.tracking.value'

    def write(self, vals):
        if not self.env.su and self.sudo().mail_message_id._sb_audited():
            raise UserError(self.env._('The history of accounting entries and vouchers cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.sudo().mail_message_id._sb_audited():
            raise UserError(self.env._('The history of accounting entries and vouchers cannot be deleted.'))
        return super().unlink()


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _check_can_update_message_content(self, messages):
        # the chatter's Edit and Delete run as superuser after this check
        if self._name in AUDITED_MODELS or messages._sb_audited():
            raise UserError(self.env._('The history of accounting entries and vouchers cannot be changed or deleted.'))
        return super()._check_can_update_message_content(messages)


# ---------------------------------------------------------------------- attachments of posted documents
ATTACHMENT_CONTENT = {'datas', 'raw', 'db_datas', 'store_fname', 'url', 'type', 'res_model', 'res_id', 'res_field', 'name',
                      'checksum', 'file_size', 'mimetype'}


def in_books(record):
    """Whether `record` (an audited document) is in the books: posted, voided, or posted once."""
    if record._name == 'account.move':
        return record.state == 'posted' or record.posted_before
    if record._name == 'sb.voucher':
        return record.state in ('posted', 'void') or record.sb_editing
    return 'state' in record._fields and record.state not in (False, 'draft')


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _sb_on_posted(self):
        """Attachments of entries, vouchers and the other audited documents once they are in the books."""
        found = self.browse()
        for att in self.sudo():
            if att.res_model in AUDITED_MODELS and att.res_id and att.res_model in self.env:
                record = self.env[att.res_model].sudo().browse(att.res_id).exists()
                if record and in_books(record):
                    found |= att
        return found

    def unlink(self):
        """The documents attached to a posted entry or voucher (the supplier's invoice, the signed receipt) are part of its
        record: nobody deletes them, the accounting manager neither. Only the app's flows (an opening item taken out of
        the opening balances) and the system do. A route deleting the file in sudo for the user who asked (the chatter's
        delete, /mail/attachment/delete) is that user: sudo alone is not a flow here."""
        if not (self.env.context.get('sb_move_flow') is MOVE_FLOW or self.env.context.get('sb_year_end') is YEAR_END
                or self.env.user._is_superuser()):
            posted = self._sb_on_posted()
            if posted:
                raise UserError(self.env._('%s is attached to a posted entry or voucher and stays on file. Attach a corrected '
                                           'document next to it instead.', ', '.join(posted.mapped('name'))))
        return super().unlink()

    def write(self, vals):
        if not in_flow(self.env) and ATTACHMENT_CONTENT & set(vals):
            posted = self._sb_on_posted()
            if posted:
                raise UserError(self.env._('%s is attached to a posted entry or voucher: it cannot be replaced, renamed or '
                                           'moved. Attach a corrected document next to it instead.',
                                           ', '.join(posted.mapped('name'))))
        return super().write(vals)


# ---------------------------------------------------------------------- unreconciling
class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        if not may_settle(self.env):
            raise _settle_refused(self.env, self.env._('A reconciliation link'))
        return super().create(vals_list)

    def write(self, vals):
        if not may_settle(self.env):
            raise _settle_refused(self.env, self.env._('A reconciliation link'))
        return super().write(vals)

    def unlink(self):
        """Unreconciling posted items reopens invoices and bills that were paid: Accountant and up (the app's flows, such as
        Edit and Void on a voucher, do it as part of their own check). It is written in the chatter of both entries."""
        if not self:
            return super().unlink()
        if not in_flow(self.env) and not is_accountant(self.env.user):
            raise UserError(self.env._('Only users with the Accountant role (or higher) can unreconcile posted items.'))
        notes = []
        for partial in self.sudo():
            debit, credit = partial.debit_move_id, partial.credit_move_id
            amount = partial.company_currency_id.format(partial.amount) if partial.company_currency_id else partial.amount
            for move, other in ((debit.move_id, credit.move_id), (credit.move_id, debit.move_id)):
                if move.state == 'posted':
                    notes.append((move, self.env._('Unreconciled from %(other)s (%(account)s, %(amount)s) by %(user)s.',
                                                   other=other.name or other.display_name, account=debit.account_id.display_name,
                                                   amount=amount, user=self.env.user.name)))
        with settling():
            res = super().unlink()
        author = self.env.user.partner_id.id
        for move, note in notes:
            if move.exists():
                move.sudo().message_post(body=note, author_id=author)
        return res


class AccountFullReconcile(models.Model):
    _inherit = 'account.full.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        if not may_settle(self.env):
            raise _settle_refused(self.env, self.env._('A full reconciliation'))
        return super().create(vals_list)

    def write(self, vals):
        if not may_settle(self.env):
            raise _settle_refused(self.env, self.env._('A full reconciliation'))
        return super().write(vals)

    def unlink(self):
        if self and not in_flow(self.env) and not is_accountant(self.env.user) and not _SETTLING.get():
            raise UserError(self.env._('Only users with the Accountant role (or higher) can unreconcile posted items.'))
        with settling():
            return super().unlink()
