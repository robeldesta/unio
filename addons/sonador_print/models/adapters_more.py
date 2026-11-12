"""Payslips and customer / supplier statements as designed documents."""
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class SbPrintAdaptersMore(models.AbstractModel):
    _inherit = 'sb.print'

    # ------------------------------------------------------------------ payslip (one employee in one payroll)
    def _doc_sb_payroll_line(self, l, kind):
        run = l.run_id
        cur = run.currency_id or run.company_id.currency_id
        e = l.employee_id
        rows = self.env['sb.report'].sudo().payslip_doc(run, l)['rows']
        lines = []
        for r in rows:
            v = r.get('values') or {}
            if r.get('kind') == 'line':
                lines.append({'desc': v.get('item', ''), 'earn': self._money(v['earn'], cur) if v.get('earn') else '',
                              'ded': self._money(v['ded'], cur) if v.get('ded') else ''})
        notes = [r['values']['item'] for r in rows if r.get('kind') == 'note' and r.get('values', {}).get('item')]
        deductions = sum((r.get('values') or {}).get('ded') or 0.0 for r in rows if r.get('kind') == 'line')
        meta = [('Period', '%s - %s' % (fields.Date.to_string(run.date_from), fields.Date.to_string(run.date))),
                ('Pay date', fields.Date.to_string(run.pay_date) if run.pay_date else ''), ('Paid days', '%g' % l.pay_days),
                ('Position', e.job or ''), ('Pension no.', e.pension_no or ''), ('Salary account', e.bank_account or '')]
        return {'record': l, 'number': run.name, 'date': run.pay_date or run.date, 'currency': cur, 'title': 'Payslip',
                'status_mark': 'DRAFT' if run.state == 'draft' else '', 'notes': '\n'.join(notes), 'fiscal': [], 'qr': False,
                'party': {'label': 'Employee', 'name': e.name, 'tin': e.tin or '', 'address': '', 'phone': ''},
                'meta': [(a, b) for a, b in meta if b],
                'columns': [{'key': 'desc', 'label': 'Pay item', 'align': 'left'}, {'key': 'earn', 'label': 'Earnings', 'align': 'right'},
                            {'key': 'ded', 'label': 'Deductions', 'align': 'right'}],
                'lines': lines,
                'totals': [('Gross pay', self._money(l.gross, cur), False), ('Total deductions', self._money(deductions, cur), False),
                           ('Net pay', self._money(l.net, cur), True)],
                'amount': l.net}

    # ------------------------------------------------------------------ statement of account (customer or supplier)
    def _doc_res_partner(self, p, kind):
        ctx = self.env.context
        company = self.env.company
        cur = company.currency_id
        today = fields.Date.context_today(self)
        date_to = fields.Date.to_date(ctx.get('sb_statement_to')) or today
        date_from = fields.Date.to_date(ctx.get('sb_statement_from')) or date_to.replace(day=1) - relativedelta(months=2)
        supplier = ctx.get('sb_statement_type') == 'supplier'
        acc_type = 'liability_payable' if supplier else 'asset_receivable'
        sign = -1 if supplier else 1
        AML = self.env['account.move.line'].sudo()
        base = [('partner_id', 'child_of', p.commercial_partner_id.id), ('account_id.account_type', '=', acc_type),
                ('parent_state', '=', 'posted'), ('company_id', '=', company.id)]
        opening = sign * sum(AML.search(base + [('date', '<', date_from)]).mapped('balance'))
        lines = [{'date': fields.Date.to_string(date_from), 'doc': 'Balance brought forward', 'ref': '', 'due': '', 'debit': '', 'credit': '',
                  'bal': self._money(opening, cur)}]
        bal = opening
        # one row per document (a receipt settling several invoices is one payment); same day: charges before payments
        docs = {}
        for m in AML.search(base + [('date', '>=', date_from), ('date', '<=', date_to)], order='date, move_name, id'):
            r = docs.setdefault(m.move_id.id, {'move': m.move_id, 'date': m.date, 'amt': 0.0, 'due': m.date_maturity})
            r['amt'] += sign * m.balance
        for r in sorted(docs.values(), key=lambda r: (r['date'], r['amt'] < 0, r['move'].name or '')):
            amt = r['amt']
            if abs(amt) < 0.005:
                continue
            bal += amt
            lines.append({'date': fields.Date.to_string(r['date']), 'doc': r['move'].name, 'ref': r['move'].ref or '',
                          'due': fields.Date.to_string(r['due']) if r['due'] and amt > 0 and r['due'] != r['date'] else '',
                          'debit': self._money(amt, cur) if amt > 0 else '', 'credit': self._money(-amt, cur) if amt < 0 else '',
                          'bal': self._money(bal, cur)})
        # ageing of what is still open at the statement date, by due date
        buckets = [('Not yet due', None, 0), ('1-30 days', 1, 30), ('31-60 days', 31, 60), ('61-90 days', 61, 90), ('Over 90 days', 91, 10 ** 6)]
        aged = dict.fromkeys([b[0] for b in buckets], 0.0)
        for m in AML.search(base + [('date', '<=', date_to), ('reconciled', '=', False)]):
            late = (date_to - (m.date_maturity or m.date)).days
            label = next(b[0] for b in buckets if (b[1] is None and late <= 0) or (b[1] is not None and b[1] <= late <= b[2]))
            aged[label] += sign * m.amount_residual
        ageing = ' · '.join('%s %s' % (k, self._money(v, cur)) for k, v in aged.items() if abs(v) > 0.005)
        return {'record': p, 'number': '%s to %s' % (fields.Date.to_string(date_from), fields.Date.to_string(date_to)), 'date': date_to,
                'currency': cur, 'title': 'Supplier statement' if supplier else 'Statement of account', 'status_mark': '',
                'notes': ('Ageing\n' + ageing) if ageing else '', 'fiscal': [], 'qr': False,
                'party': self._party(p, 'Supplier' if supplier else 'Customer'),
                'meta': [('Statement date', fields.Date.to_string(date_to))] + ([('Credit limit', self._money(p.credit_limit, cur))]
                                                                                 if not supplier and getattr(p, 'credit_limit', 0) else []),
                'columns': [{'key': 'date', 'label': 'Date', 'align': 'left'}, {'key': 'doc', 'label': 'Document', 'align': 'left'},
                            {'key': 'ref', 'label': 'Reference', 'align': 'left'}, {'key': 'due', 'label': 'Due', 'align': 'left'},
                            {'key': 'debit', 'label': 'Charges' if not supplier else 'Bills', 'align': 'right'},
                            {'key': 'credit', 'label': 'Payments', 'align': 'right'}, {'key': 'bal', 'label': 'Balance', 'align': 'right'}],
                'lines': lines,
                'totals': [('Opening balance', self._money(opening, cur), False), ('Balance due %s' % cur.name if not supplier else 'Balance owed %s' % cur.name,
                                                                                   self._money(bal, cur), True)],
                'amount': None}
