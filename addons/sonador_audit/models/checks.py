"""What an internal auditor looks at on a visit: what was entered or changed in the books since the last visit, worked
out from the books when the visit starts (and again on Refresh). Each check lists the records behind it; the auditor
marks it In order, Issue found (and raises a finding) or Not applicable. Nothing here changes the books."""
import json
from collections import defaultdict
from datetime import timedelta

from odoo import fields, models

MONEY_OUT = ('payment', 'cash_purchase')
LARGE_DEFAULT = 50000.0
MAX_ITEMS = 200


def _money(v):
    return '{:,.2f}'.format(v or 0.0)


class AuditCheck(models.Model):
    _inherit = 'sb.audit.check'

    # ------------------------------------------------------------------ building the checks of a visit
    def _sb_checks_for(self, visit):
        """[(key, vals)] for the visit, in the order the auditor walks through them."""
        env = self.env
        company = visit.company_id
        since = visit.since
        V = env['sb.voucher'].with_company(company).sudo()
        # vouchers brought over from the previous system are left out: they were entered in one go when moving
        base = [('company_id', '=', company.id)] + ([('sb_migrated', '=', False)] if 'sb_migrated' in V._fields else [])
        large = float(env['ir.config_parameter'].sudo().get_param('sonador_audit.large_payment', LARGE_DEFAULT))
        out = []

        def vouchers(vs, fmt=None):
            lines = []
            for v in vs[:MAX_ITEMS]:
                lines.append(fmt(v) if fmt else '%s · %s · %s · %s' % (
                    v.name, v.date.strftime('%d %b %Y') if v.date else '', v.partner_id.name or v.memo or '',
                    _money(v.amount_total)))
            if len(vs) > MAX_ITEMS:
                lines.append('... and %d more: Open shows them all' % (len(vs) - MAX_ITEMS))
            return {'count': len(vs), 'amount': sum(vs.mapped('amount_total')), 'res_model': 'sb.voucher',
                    'res_ids': json.dumps(vs.ids), 'detail': '\n'.join(lines)}

        # 1. voided and 2. reopened: from the voucher history (who did it and when)
        moved = self._sb_state_changes(company, since)
        voided = V.browse([r for r, new, _u, _d in moved if new == 'void']).exists()
        reopened = V.browse(list({r for r, new, _u, _d in moved if new == 'draft'})).exists()
        who = {(r, new): (u, d) for r, new, u, d in moved}

        def by(v, new):
            u, d = who.get((v.id, new), ('', None))
            return '%s · %s · %s · %s by %s on %s' % (v.name, v.partner_id.name or v.memo or '', _money(v.amount_total),
                                                     'voided' if new == 'void' else 'reopened', u,
                                                     d.strftime('%d %b %H:%M') if d else '')
        out.append(('voided', dict(vouchers(voided, lambda v: by(v, 'void')), area='journals', title='Vouchers voided',
                                   why='A void takes an entry out of the books. Ask why, and who agreed to it.')))
        out.append(('reopened', dict(vouchers(reopened, lambda v: by(v, 'draft')), area='journals',
                                     title='Posted vouchers reopened and changed',
                                     why='The number stays the same but the amounts may not. Compare with the paper voucher.')))
        # 3. journals
        jv = V.search(base + [('voucher_type', '=', 'journal'), ('state', '=', 'posted'), ('create_date', '>=', since)], order='date')
        out.append(('journals', dict(vouchers(jv, lambda v: '%s · %s · %s · %s' % (
            v.name, v.date.strftime('%d %b %Y'), (v.memo or v.reference or '')[:80], _money(v.total_debit))),
            amount=sum(jv.mapped('total_debit')), area='journals', title='Journal vouchers posted',
            why='Journals move amounts between accounts with no cash changing hands: the usual place for mistakes '
                'and for hiding them. Each needs a reason and support.')))
        # 4. backdated
        new = V.search(base + [('state', '=', 'posted'), ('create_date', '>=', since)], order='date')
        back = new.filtered(lambda v: v.date and v.create_date and (v.create_date.date() - v.date).days > 7)
        out.append(('backdated', dict(vouchers(back, lambda v: '%s · dated %s, entered %s · %s · %s' % (
            v.name, v.date.strftime('%d %b'), v.create_date.strftime('%d %b'), v.partner_id.name or v.memo or '',
            _money(v.amount_total))), area='controls', title='Entered more than a week after their date',
            why='Late entries can change months already reported or taxes already filed.')))
        # 5. large payments
        pay = V.search(base + [('voucher_type', 'in', MONEY_OUT), ('state', '=', 'posted'), ('create_date', '>=', since),
                               ('amount_total', '>=', large)], order='amount_total desc')
        out.append(('large', dict(vouchers(pay), area='purchases', title='Payments of %s or more' % _money(large).replace('.00', ''),
                                  why='Look at the approval, the invoice and who received the money.')))
        # 6. round amounts
        rnd = V.search(base + [('voucher_type', 'in', MONEY_OUT), ('state', '=', 'posted'), ('create_date', '>=', since),
                               ('amount_total', '>=', 5000)]).filtered(lambda v: abs(v.amount_total % 1000) < 0.005)
        out.append(('round', dict(vouchers(rnd), area='purchases', title='Payments in round thousands',
                                  why='Real invoices rarely come to round figures. Round payments are often advances or estimates.')))
        # 7. new vendors paid
        paid = V.search(base + [('voucher_type', 'in', MONEY_OUT), ('state', '=', 'posted'), ('create_date', '>=', since)])
        older = set(V.search([('company_id', '=', company.id), ('partner_id', 'in', paid.partner_id.ids), ('state', '=', 'posted'),
                              ('create_date', '<', since)]).partner_id.ids)
        paid_new = paid.filtered(lambda v: v.partner_id and v.partner_id.id not in older)
        out.append(('new_vendors', dict(vouchers(paid_new), area='purchases', title='Payments to new vendors',
                                        why='Paid for the first time since the last visit. Fake suppliers start this way: '
                                            'check the TIN, the address and who added them.')))
        # 8. possible duplicates: same partner and amount within 7 days
        recent = V.search(base + [('voucher_type', 'in', MONEY_OUT + ('bill',)), ('state', '=', 'posted'),
                                  ('date', '>=', (since - timedelta(days=30)).date())], order='date')
        groups = defaultdict(list)
        for v in recent:
            if v.partner_id and v.amount_total:
                groups[(v.partner_id.id, round(v.amount_total, 2), v.voucher_type == 'bill')].append(v)
        dup = V.browse()
        for vs in groups.values():
            for a, b in zip(vs, vs[1:]):
                if (b.date - a.date).days <= 7 and (b.create_date >= since or a.create_date >= since):
                    dup |= a | b
        out.append(('duplicates', dict(vouchers(dup), area='purchases', title='Possible duplicate payments or bills',
                                       why='Same vendor, same amount, within a week of each other.')))
        # 9. weekend entries
        wk = new.filtered(lambda v: fields.Datetime.context_timestamp(v, v.create_date).weekday() == 6)
        out.append(('sunday', dict(vouchers(wk, lambda v: '%s · entered Sunday %s by %s · %s' % (
            v.name, fields.Datetime.context_timestamp(v, v.create_date).strftime('%d %b %H:%M'), v.create_uid.name,
            _money(v.amount_total))), area='controls', title='Entered on a Sunday',
            why='Not wrong in itself, but work outside office hours is worth a question.')))
        # 10. old drafts
        drafts = V.search(base + [('state', '=', 'draft'), ('create_date', '<', fields.Datetime.now() - timedelta(days=7))])
        out.append(('drafts', dict(vouchers(drafts), area='controls', title='Vouchers not posted for over a week',
                                   why='Unposted vouchers are missing from every report. Post or delete them.')))
        # 11. banks: below zero, not reconciled
        banks = env['account.journal'].sudo().search([('company_id', '=', company.id), ('type', '=', 'bank')]).default_account_id
        cash = env['account.journal'].sudo().search([('company_id', '=', company.id), ('type', '=', 'cash')]).default_account_id
        neg = self._sb_below_zero(company, banks | cash, since.date())
        out.append(('below_zero', {'area': 'cash', 'title': 'Bank or cash below zero in the books',
                                   'why': 'A bank cannot go below zero (unless overdrawn by agreement). In the books it '
                                          'means money out was recorded before money in, or something is missing.',
                                   'count': len(neg), 'amount': sum(abs(x[2]) for x in neg), 'res_model': 'account.account',
                                   'res_ids': json.dumps(sorted({x[0].id for x in neg})),
                                   'detail': '\n'.join('%s · lowest %s on %s' % (a.name, _money(b), d.strftime('%d %b %Y'))
                                                       for a, d, b in neg)}))
        Rec = env['sb.bank.rec'].sudo()
        stale, lines = [], []
        cutoff = fields.Date.context_today(self).replace(day=1) - timedelta(days=1)  # end of last month
        for a in banks:
            last = Rec.search([('account_id', '=', a.id), ('state', '=', 'done')], order='date desc', limit=1)
            if not last or last.date < cutoff - timedelta(days=5):
                stale.append(a.id)
                lines.append('%s · %s' % (a.name, 'last reconciled to %s' % last.date.strftime('%d %b %Y') if last else 'never reconciled'))
        out.append(('bank_rec', {'area': 'cash', 'title': 'Banks not reconciled to last month end',
                                 'why': 'Without a reconciliation nobody has proved the books agree with the bank.',
                                 'count': len(stale), 'res_model': 'account.account', 'res_ids': json.dumps(stale),
                                 'detail': '\n'.join(lines)}))
        # 12. cash count, one per cash account
        for a in cash:
            bal = self._sb_balance(a)
            out.append(('cash:%d' % a.id, {'area': 'cash', 'title': 'Count the cash: %s' % a.name, 'account_id': a.id,
                                           'why': 'Count the notes and coins in the box and enter the total. '
                                                  'The difference to the books shows at once.',
                                           'book_balance': bal, 'count': 0, 'res_model': 'account.account',
                                           'res_ids': json.dumps([a.id])}))
        return out

    def _sb_state_changes(self, company, since):
        """(voucher id, new state, user name, when) for vouchers voided or reopened since."""
        self.env.cr.execute("""
            SELECT m.res_id, t.new_value_char, p.name, m.date
              FROM mail_tracking_value t
              JOIN mail_message m ON m.id = t.mail_message_id
              JOIN ir_model_fields f ON f.id = t.field_id
              JOIN sb_voucher v ON v.id = m.res_id
              LEFT JOIN res_partner p ON p.id = m.author_id
             WHERE m.model = 'sb.voucher' AND f.name = 'state' AND f.model = 'sb.voucher' AND v.company_id = %s
               AND m.date >= %s AND t.old_value_char = 'Posted' AND t.new_value_char IN ('Void', 'Not posted')
             ORDER BY m.date""", (company.id, since))
        return [(r, 'void' if new == 'Void' else 'draft', u or '', d) for r, new, u, d in self.env.cr.fetchall()]

    def _sb_balance(self, account):
        self.env.cr.execute("""SELECT COALESCE(SUM(balance), 0) FROM account_move_line
                                WHERE account_id = %s AND parent_state = 'posted'""", (account.id,))
        return self.env.cr.fetchone()[0]

    def _sb_below_zero(self, company, accounts, since):
        """[(account, day, lowest balance)] for accounts whose end-of-day balance went below zero since."""
        out = []
        for a in accounts:
            self.env.cr.execute("""
                SELECT date, SUM(SUM(balance)) OVER (ORDER BY date) FROM account_move_line
                 WHERE account_id = %s AND parent_state = 'posted' AND company_id = %s GROUP BY date ORDER BY date""",
                                (a.id, company.id))
            low = None
            for d, b in self.env.cr.fetchall():
                if d >= since and b < -0.005 and (low is None or b < low[1]):
                    low = (d, b)
            if low:
                out.append((a, low[0], low[1]))
        return out
