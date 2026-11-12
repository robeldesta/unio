import csv
import io
from collections import defaultdict

from odoo import http
from odoo.http import content_disposition, request


class SonadorPayroll(http.Controller):

    @http.route('/sonador_payroll/bank/<int:run_id>', type='http', auth='user')
    def bank_file(self, run_id, **kw):
        """Salary transfer list per bank: one CSV with a section for each bank (account, name, amount)."""
        if not request.env['sb.people']._is_hr():
            return request.not_found()
        run = request.env['sb.payroll.run'].sudo().browse(run_id).exists()
        if not run:
            return request.not_found()
        by_bank = defaultdict(list)
        for l in run.line_ids.filtered(lambda l: l.net > 0):
            acc = l.hr_employee_id.primary_bank_account_id if l.hr_employee_id else False
            bank = acc.bank_id.name if acc and acc.bank_id else ('No bank account' if not acc else 'Bank not set')
            by_bank[bank].append((acc.acc_number if acc else '', l.employee_id.name, round(l.net, 2)))
        out = io.StringIO()
        w = csv.writer(out)
        for bank in sorted(by_bank):
            rows = by_bank[bank]
            w.writerow([bank, run.name, '%d transfers' % len(rows), '%.2f' % sum(r[2] for r in rows)])
            w.writerow(['Account number', 'Name', 'Amount', 'Reference'])
            for acc, name, amt in sorted(rows, key=lambda r: r[1]):
                w.writerow([acc, name, '%.2f' % amt, run.name])
            w.writerow([])
        return request.make_response(out.getvalue().encode('utf-8-sig'), headers=[
            ('Content-Type', 'text/csv'), ('Content-Disposition', content_disposition('%s - bank transfers.csv' % run.name))])

    @http.route('/sonador_payroll/file/<int:run_id>/<string:kind>', type='http', auth='user')
    def run_file(self, run_id, kind, **kw):
        """finance: workbook by department; bank: workbook for the bank; letter: transfer letter (PDF); ot: overtime sheet."""
        if not request.env['sb.people']._is_hr():
            return request.not_found()
        run = request.env['sb.payroll.run'].sudo().browse(run_id).exists()
        X = request.env['sb.payroll.export'].sudo()
        if not run or kind not in ('finance', 'bank', 'letter', 'ot'):
            return request.not_found()
        if kind == 'letter':
            return request.make_response(X.letter_pdf(run), headers=[
                ('Content-Type', 'application/pdf'),
                ('Content-Disposition', content_disposition('%s - bank letter %s.pdf' % (run.name, run._reference()), disposition_type='inline'))])
        data = {'finance': X.finance_xlsx, 'bank': X.bank_xlsx, 'ot': X.ot_xlsx}[kind](run)
        title = {'finance': 'payroll by department', 'bank': 'for bank', 'ot': 'overtime sheet'}[kind]
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('%s - %s.xlsx' % (run.name, title)))])

    @http.route('/sonador_payroll/payslip/<int:line_id>', type='http', auth='user')
    def payslip(self, line_id, **kw):
        """One payslip: HR, or the employee themself once the payroll is posted."""
        line = request.env['sb.payroll.line'].sudo().browse(line_id).exists()
        if not line:
            return request.not_found()
        me = request.env['sb.people']._me()
        if not request.env['sb.people']._is_hr() and (line.hr_employee_id != me or line.run_id.state not in ('posted', 'paid')):
            return request.not_found()
        R = request.env['sb.report'].sudo()
        pdf = R.render_pdf(R.payslip_doc(line.run_id, line))
        return request.make_response(pdf, headers=[('Content-Type', 'application/pdf'),
                                                   ('Content-Disposition', content_disposition('Payslip %s %s.pdf' % (line.employee_id.name, line.run_id.name), disposition_type='inline'))])
