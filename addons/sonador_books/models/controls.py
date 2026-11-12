"""Overdraft control: a cash or bank account can be kept from going below zero (or below an agreed overdraft limit).
Checked when a voucher is posted; the whole posting is refused, so nothing half-posts."""
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .voucher import VOUCHER_JOURNALS


class AccountAccount(models.Model):
    _inherit = 'account.account'

    sb_overdraft_control = fields.Boolean('Stop payments that overdraw it',
                                          help='Cash and bank accounts: a payment, transfer or cash purchase that would take the balance '
                                               'below minus the overdraft limit is refused.')
    sb_overdraft_limit = fields.Float('Overdraft limit', digits='Account',
                                      help='Agreed overdraft (0 = the balance may not go below zero).')
    sb_custodian_id = fields.Many2one('res.users', 'Kept by',
                                      help='Cash boxes: the person who holds the cash. Their petty cash vouchers pay from this box '
                                           'by default.')


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_series_open = fields.Boolean(compute='_compute_sb_series_open',
                                    help='The series numbering this voucher takes any number typed (open numbering).')

    @api.depends('sb_series_id', 'voucher_type', 'company_id')
    def _compute_sb_series_open(self):
        Series = self.env['sb.doc.series'].sudo()
        for v in self:
            series = v.voucher_type and Series._for(v.voucher_type, company=v.company_id or self.env.company, series=v.sb_series_id)
            v.sb_series_open = bool(series and series.sb_open_numbering)

    def _sb_paid_from_my_box(self, user=None):
        """A payment paid only from cash boxes this user keeps (Kept by): the keeper posts it without the Clerk role. Cash
        moved to another cash or bank account is a transfer, not a payment from the box."""
        self.ensure_one()
        user = user or self.env.user
        boxes = self.cash_line_ids.account_id | (self.cash_account_id if not self.cash_line_ids else self.env['account.account'])
        return self.voucher_type == 'payment' and bool(boxes) \
            and all(a.account_type == 'asset_cash' and a.sb_custodian_id == user for a in boxes) \
            and not any(l.account_id.account_type == 'asset_cash' for l in self.line_ids) \
            and not getattr(self, 'sb_setoff', False)

    def _sb_require_post(self):
        if not self.env.su and self.voucher_type == 'payment' and self._sb_paid_from_my_box():
            return  # the petty cash keeper posts the payments from their own box
        return super()._sb_require_post()

    def _default_cash_account(self, vtype=None):
        vtype = vtype or self.env.context.get('default_voucher_type')
        user = self.env.user
        if vtype == 'payment' and not user.has_group('sonador_books.group_sb_clerk') \
                and user.has_group('sonador_books.group_sb_petty_cash'):
            box = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('account_type', '=', 'asset_cash'),
                                                      ('sb_custodian_id', '=', user.id)], order='code', limit=1)
            if box:
                return box  # the keeper pays from the box they keep
        return super()._default_cash_account(vtype)

    def action_post(self):
        res = super().action_post()
        AML = self.env['account.move.line']
        for v in self.filtered(lambda x: x.state == 'posted' and x.move_id):
            for acc in v.move_id.line_ids.filtered(lambda l: l.credit > 0 and l.account_id.account_type == 'asset_cash'
                                                   and l.account_id.sb_overdraft_control).account_id:
                bal = AML._read_group([('account_id', '=', acc.id), ('parent_state', '=', 'posted'), ('company_id', '=', v.company_id.id)],
                                      [], ['balance:sum'])[0][0] or 0.0
                if v.company_id.currency_id.compare_amounts(bal, -(acc.sb_overdraft_limit or 0.0)) < 0:
                    raise ValidationError(_('%(acc)s would go to %(bal)s (overdraft limit %(lim)s): %(v)s is not posted. Move money into the '
                                            'account first, or raise its overdraft limit.', acc=acc.display_name,
                                            bal='{:,.2f}'.format(bal), lim='{:,.2f}'.format(acc.sb_overdraft_limit or 0.0), v=v.display_name))
        return res


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _must_check_constrains_date_sequence(self):
        if self.env.context.get('sb_open_numbering'):
            return False  # typed on an open-numbering series: any text, not a sequence of the journal
        if self.id and 'sb.voucher' in self.env:
            v = self.env['sb.voucher'].sudo().search([('move_id', '=', self.id)], limit=1)
            if v.sb_series_id.sb_open_numbering:
                return False
        return super()._must_check_constrains_date_sequence()

    def _get_starting_sequence(self):
        """Every journal numbers by fiscal year (BILL/2025/0001), like the vouchers: Odoo numbers bills and journals by month."""
        seq = super()._get_starting_sequence()
        parts = seq.split('/')
        if len(parts) == 4 and len(parts[2]) == 2 and parts[2].isdigit():  # CODE/YEAR/MM/0000 -> CODE/YEAR/0000
            seq = '/'.join([parts[0], parts[1], parts[3]])
        if (self.company_id or self.env.company).sb_number_without_year and len(seq.split('/')) >= 3:
            seq = '%s/%s' % (seq.split('/')[0], seq.split('/')[-1])  # no year in any number: STK/0001, counting on
        # in a journal the vouchers number (JV, CPV, INV ...), an entry made outside the vouchers is marked E (JV/26-27/E0001),
        # so it never takes a number of the voucher series (JV/26-27/0001)
        if (self.journal_id.code or '') in set(VOUCHER_JOURNALS.values()):
            seq = re.sub(r'(\d+)$', r'E\1', seq)
        return seq

    def _get_next_sequence_format(self):
        """An entry Odoo numbers takes the next number of its journal in the entry's fiscal year (CODE/26-27/0001),
        whatever the journal's earlier entries look like (numbered by month, brought over with another system's numbers:
        Odoo would carry on from those). The earlier systems' own journals (X...) are filled by the nightly sync only."""
        journal = self.journal_id
        if not journal or not self.date or (journal.code or '').upper().startswith('X'):
            return super()._get_next_sequence_format()
        start = self._get_starting_sequence()
        format_string, format_values = self._get_sequence_format_param(start)
        prefix = re.sub(r'\d+$', '', start)
        self.flush_model(['name', 'sequence_prefix', 'sequence_number', 'journal_id'])
        self.env.cr.execute('SELECT max(sequence_number) FROM account_move WHERE journal_id = %s AND sequence_prefix = %s '
                            'AND id != %s', (journal.id, prefix, self._origin.id or 0))
        format_values['seq'] = self.env.cr.fetchone()[0] or 0
        return format_string, format_values
