"""Month-end close, with its checklist: "Close through here" first shows, on the same screen, what is still open in the
periods being closed (drafts, bank accounts not reconciled, depreciation not run, goods received not billed, VAT and
withholding returns, suspense and clearing balances). The user then closes, or closes anyway; the lock is logged with
what was still open."""
import re

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date

from ..models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

ICONS = {'ok': ('fa-check-circle', '#1E8E5A'), 'review': ('fa-exclamation-circle', '#B7791F'), 'info': ('fa-info-circle', '#5D8DA8')}


class SbPeriodWizard(models.TransientModel):
    _inherit = 'sb.period.wizard'

    @api.model
    def _sb_open(self):
        """Accounting Periods opens on a saved screen, so its buttons act on the first click."""
        return self.create({})._reopen()

    close_to = fields.Date('Close through', readonly=True)
    close_name = fields.Char(readonly=True)
    close_checklist = fields.Html(compute='_compute_close_checklist', sanitize=False)
    # reopening asks why, on the same screen; the reason goes into the period close log
    reopen_from = fields.Date('Reopen from', readonly=True)
    reopen_name = fields.Char(readonly=True)
    reopen_reason = fields.Char('Reason for reopening')
    close_open_count = fields.Integer(compute='_compute_close_checklist')

    @api.depends('close_to', 'company_id')
    def _compute_close_checklist(self):
        for w in self:
            if not w.close_to:
                w.close_checklist, w.close_open_count = False, 0
                continue
            checks = w._sb_close_checks(w.close_to)
            w.close_open_count = len([c for c in checks if c[0] == 'review'])
            rows = [Markup('<tr><td style="padding:.3rem .6rem"><i class="fa %s" style="color:%s"></i></td>'
                           '<td style="padding:.3rem .6rem">%s</td><td style="padding:.3rem .6rem;text-align:right">%s</td>'
                           '<td style="padding:.3rem .6rem;color:#6B7A85">%s</td></tr>') % (
                ICONS[status][0], ICONS[status][1], label, count or '', note) for status, label, count, note in checks]
            w.close_checklist = Markup('<table style="width:100%%">%s</table>') % Markup('').join(rows)

    def _sb_close_checks(self, end):
        """[(status, check, count, note)] for closing the books through `end` (from the day after the current lock)."""
        self.ensure_one()
        company = self.company_id
        lock = company.fiscalyear_lock_date
        start = fields.Date.add(lock, days=1) if lock else False
        span = [('date', '<=', end)] + ([('date', '>=', start)] if start else [])
        cur = company.currency_id
        money = lambda x: '{:,.2f}'.format(x or 0.0)  # noqa: E731
        out = []

        # 1. drafts dated in the periods
        drafts = self.env['sb.voucher'].search([('company_id', '=', company.id), ('state', '=', 'draft')] + span)
        names = drafts.mapped(lambda v: v.name if v.name not in (False, 'New', '/') else '%s %s' % (
            dict(v._fields['voucher_type']._description_selection(self.env)).get(v.voucher_type), v.partner_id.name or ''))
        if 'sb.stock.voucher' in self.env:
            stock = self.env['sb.stock.voucher'].search([('company_id', '=', company.id), ('state', '=', 'draft')] + span)
            drafts_count = len(drafts) + len(stock)
            names += stock.mapped(lambda v: v.name if v.name not in (False, '/') else v.display_name)
        else:
            drafts_count = len(drafts)
        moves = self.env['account.move'].search_count([('company_id', '=', company.id), ('state', '=', 'draft')] + span)
        drafts_count += moves
        out.append(('review' if drafts_count else 'ok', self.env._('Vouchers and entries not posted'), drafts_count,
                    self.env._('Post or delete them: %s', ', '.join(n.strip() for n in names[:8]) + (' …' if len(names) > 8 else ''))
                    if drafts_count else self.env._('None')))

        # 2. bank accounts reconciled to the period end
        # the banks: the bank journals' accounts and every account ever reconciled (banks kept without a journal of their own)
        banks = self.env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'bank')]).default_account_id
        banks |= self.env['sb.bank.rec'].search([('company_id', '=', company.id)]).account_id
        AML = self.env['account.move.line']
        used = AML._read_group([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('account_id', 'in', banks.ids),
                                ('date', '<=', end)], ['account_id'], ['__count'])
        not_rec = []
        for account, _count in used:
            # reconciled through the period end: the account's last finished reconciliation reaches it and proves to the
            # books, with no difference carried into it still to clear
            last = self.env['sb.bank.rec'].search([('company_id', '=', company.id), ('account_id', '=', account.id),
                                                   ('state', '=', 'done')], order='date desc, id desc', limit=1)
            if not last or last.date < end:
                not_rec.append(account.display_name)
            elif not cur.is_zero(last.unexplained) or not cur.is_zero(last.carried_open):
                not_rec.append(self.env._('%(account)s (%(amt)s not explained in the reconciliation of %(date)s)',
                                          account=account.display_name, amt=money(last.unexplained or last.carried_open),
                                          date=last.date.strftime('%d %b %Y')))
        out.append(('review' if not_rec else 'ok', self.env._('Bank accounts reconciled to %s', format_ethiopian(end)), len(not_rec),
                    self.env._('Reconcile: %s', '; '.join(not_rec[:6]) + (' …' if len(not_rec) > 6 else ''))
                    if not_rec else self.env._('All reconciled')))

        # 3. depreciation run
        if 'account.asset.depreciation.line' in self.env:
            lines = self.env['account.asset.depreciation.line'].search([
                ('asset_id.company_id', '=', company.id), ('asset_id.state', '=', 'open'), ('move_check', '=', False),
                ('depreciation_date', '<=', end)])
            out.append(('review' if lines else 'ok', self.env._('Depreciation posted'), len(lines),
                        self.env._('%(n)s depreciation lines up to this date are not posted (%(amt)s): run depreciation',
                                   n=len(lines), amt=money(sum(lines.mapped('amount')))) if lines else self.env._('Up to date')))

        # 4. goods received not billed: GRNs posted to the goods received not invoiced account and not billed by the date
        # (a GRN that credited the supplier directly, or a migrated receipt, is already in the payables)
        grni = company.sb_grni_account_id if 'sb_grni_account_id' in company._fields else False
        if grni:
            grns = self.env['sb.stock.voucher'].search([('company_id', '=', company.id), ('voucher_type', '=', 'grn'),
                                                        ('state', '=', 'posted'), ('date', '<=', end),
                                                        ('move_id.line_ids.account_id', '=', grni.id)])
            grns = grns.filtered(lambda v: not any(b.state == 'posted' and b.date <= end for b in v.bill_ids | v.bill_many_ids))
            value = -sum(grns.move_id.line_ids.filtered(lambda a: a.account_id == grni).mapped('balance'))
            out.append(('review' if grns else 'ok', self.env._('Goods received, not billed'), len(grns),
                        self.env._('%(amt)s waiting for the supplier\'s bill (%(docs)s): enter the bills, or accrue them',
                                   amt=money(value), docs=', '.join(grns[:6].mapped('name')) +
                                   (' …' if len(grns) > 6 else '')) if grns else self.env._('None')))

        # 5. VAT and withholding returns
        taxes = AML._read_group([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('tax_line_id', '!=', False)] + span,
                                ['tax_line_id'], ['balance:sum'])
        output_vat = -sum(b for t, b in taxes if t.type_tax_use == 'sale')
        input_vat = sum(b for t, b in taxes if t.type_tax_use == 'purchase')
        wht_note = ''
        wht_acc = company.sb_wht_payable_account_id
        if wht_acc:
            on_wht = [('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('account_id', '=', wht_acc.id)]
            withheld = AML._read_group(on_wht + span, [], ['credit:sum'])[0][0] or 0.0
            to_pay = -(AML._read_group(on_wht + [('date', '<=', end)], [], ['balance:sum'])[0][0] or 0.0)
            wht_note = self.env._('; withholding withheld %s', money(withheld))
            if cur.compare_amounts(to_pay, 0.0) >= 0:
                wht_note += self.env._(', to pay %s', money(to_pay))
            else:
                wht_note += self.env._(', but %(acc)s shows %(amt)s more paid than withheld: check it',
                                       acc=wht_acc.display_name, amt=money(-to_pay))
        filed = company.tax_lock_date and company.tax_lock_date >= end
        out.append(('ok' if filed else 'review', self.env._('VAT and withholding returns'), '',
                    self.env._('Returns filed: tax periods locked through %s', format_ethiopian(company.tax_lock_date)) if filed else
                    self.env._('Output VAT %(out)s, input VAT %(inp)s, net %(net)s%(wht)s. File the returns, then lock the tax period.',
                               out=money(output_vat), inp=money(input_vat), net=money(output_vat - input_vat), wht=wht_note)))

        # 6. suspense and clearing accounts (as the year-end checklist)
        accounts = self.env['account.account'].with_company(company).search([
            ('company_ids', 'in', company.id), '|', ('name', 'ilike', 'suspense'), ('name', 'ilike', 'clearing')])
        accounts |= company.account_journal_suspense_account_id
        res = AML._read_group([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('date', '<=', end),
                               ('account_id', 'in', accounts.ids)], ['account_id'], ['balance:sum'])
        open_ = [(acc, bal) for acc, bal in res if not cur.is_zero(bal or 0.0)]
        out.append(('review' if open_ else 'ok', self.env._('Suspense and clearing accounts'), len(open_),
                    self.env._('Clear them: %s', '; '.join('%s %s' % (acc.display_name, money(bal)) for acc, bal in open_[:6]))
                    if open_ else self.env._('All at zero')))
        return [(s, escape(label), count, escape(note)) for s, label, count, note in out]

    def action_close_confirm(self):
        """Close the books through the period shown in the checklist."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can close periods.'))
        if not self.close_to:
            return self._reopen()
        self._sb_check_close_to(self.close_to)
        all_checks = self._sb_close_checks(self.close_to)
        # a period is closed only over posted work: a draft dated in it could never be posted afterwards
        if all_checks and all_checks[0][0] == 'review':
            raise UserError(self.env._('%(n)s vouchers, entries or stock documents dated up to %(date)s are not posted. %(what)s, '
                                       'then close the period.', n=all_checks[0][2], date=format_date(self.env, self.close_to),
                                       what=str(all_checks[0][3])))
        checks = [c for c in all_checks if c[0] == 'review']
        note = ('Closed with open checks: %s' % '; '.join(str(c[1]) for c in checks)) if checks else 'Closed, checklist clear'
        self.company_id.sudo().with_context(sb_lock_note=note).fiscalyear_lock_date = self.close_to
        self.write({'close_to': False, 'close_name': False})
        return self._reopen()

    def _sb_last_ended(self):
        """End of the last accounting period that has ended (today is not in it)."""
        today = fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        ends = [end for year in (fy - 1, fy) for _no, _name, _start, end in fiscal_periods(year) if end < today]
        return max(ends)

    def _sb_check_close_to(self, close_to):
        """The books are closed through the end of a period that has ended, later than they are closed now. Closing a
        period that has not ended would stop posting in it; an earlier date is a reopening, which asks for its reason."""
        last = self._sb_last_ended()
        if close_to > last:
            raise UserError(self.env._(
                'The books can be closed only through a period that has ended: %(date)s (%(ec)s) is after the end of the last '
                'ended period, %(last)s (%(last_ec)s).', date=close_to, ec=format_ethiopian(close_to), last=last,
                last_ec=format_ethiopian(last)))
        if not any(end == close_to for year in (fiscal_year_of(close_to),) for _n, _m, _s, end in fiscal_periods(year)):
            raise UserError(self.env._('%(date)s is not the last day of an accounting period: close through a period in the '
                                       'list.', date=close_to))
        lock = self.company_id.fiscalyear_lock_date
        if lock and close_to < lock:
            raise UserError(self.env._(
                'The books are closed through %(lock)s (%(ec)s). Closing through an earlier date reopens periods: use Reopen '
                'on the period, with the reason.', lock=format_date(self.env, lock), ec=format_ethiopian(lock)))
        if lock and close_to == lock:
            raise UserError(self.env._('The books are already closed through %(lock)s (%(ec)s).', lock=format_date(self.env, lock),
                                       ec=format_ethiopian(lock)))

    def action_close_cancel(self):
        self.write({'close_to': False, 'close_name': False, 'reopen_from': False, 'reopen_name': False, 'reopen_reason': False})
        return self._reopen()

    def action_reopen_confirm(self):
        """Reopen the period shown (and every later one), with the reason logged."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can reopen periods.'))
        if not self.reopen_from:
            return self._reopen()
        if not (self.reopen_reason or '').strip():
            raise UserError(self.env._('Enter the reason for reopening %s: it is kept in the period close log.',
                                       self.reopen_name or format_date(self.env, self.reopen_from)))
        lock = self.company_id.fiscalyear_lock_date
        if not lock or self.reopen_from > lock:
            raise UserError(self.env._('%(date)s is not closed: nothing to reopen.', date=self.reopen_from))
        if not any(start == self.reopen_from for _n, _m, start, _e in fiscal_periods(fiscal_year_of(self.reopen_from))):
            raise UserError(self.env._('%(date)s is not the first day of an accounting period: reopen a period in the list.',
                                       date=self.reopen_from))
        line = self.line_ids.filtered(lambda l: l.date_from == self.reopen_from)[:1]
        if line:
            line._sb_check_reopen()
        self.company_id.sudo().with_context(sb_lock_note=self.env._('Reopened: %s', self.reopen_reason.strip())) \
            .fiscalyear_lock_date = fields.Date.subtract(self.reopen_from, days=1)
        self.write({'reopen_from': False, 'reopen_name': False, 'reopen_reason': False})
        return self._reopen()


class SbPeriodWizardLine(models.TransientModel):
    _inherit = 'sb.period.wizard.line'

    def action_close_through(self):
        """Show the month-end checklist for closing through this period; closing is confirmed from it."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(self.env._('Only accounting administrators can close periods.'))
        self.wizard_id._sb_check_close_to(self.date_to)
        # "Meskerem 2019 (10 Oct 2026)": the month's name carries its year already
        # (Hamle 2017 opens fiscal year 2018: no "Hamle 2017 2018")
        name = self.name if re.search(r'\b\d{4}\b', self.name or '') else '%s %s' % (self.name, self.fy_label)
        self.wizard_id.write({'close_to': self.date_to, 'close_name': '%s (%s)' % (name, format_date(self.env, self.date_to)),
                              'reopen_from': False, 'reopen_name': False, 'reopen_reason': False})
        return self.wizard_id._reopen()
