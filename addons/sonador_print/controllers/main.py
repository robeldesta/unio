"""Payslip downloads print in the company's payslip design."""
from odoo import http
from odoo.http import content_disposition, request

from odoo.addons.sonador_books.controllers.main import SonadorPayslips
from odoo.addons.sonador_payroll.controllers.main import SonadorPayroll


def _pdf_response(pdf, filename):
    return request.make_response(pdf, headers=[('Content-Type', 'application/pdf'),
                                               ('Content-Disposition', content_disposition(filename, disposition_type='inline'))])


class DesignedPayslips(SonadorPayslips):

    @http.route()
    def payslips(self, run_id, **kw):
        run = request.env['sb.payroll.run'].browse(run_id).exists()
        if not run:
            return request.not_found()
        run.check_access('read')
        return _pdf_response(request.env['sb.print'].pdf(run.line_ids), '%s.pdf' % run.name)


class DesignedPayslip(SonadorPayroll):

    @http.route()
    def payslip(self, line_id, **kw):
        """One payslip: HR, or the employee themself once the payroll is posted (as before)."""
        line = request.env['sb.payroll.line'].sudo().browse(line_id).exists()
        if not line:
            return request.not_found()
        me = request.env['sb.people']._me()
        if not request.env['sb.people']._is_hr() and (line.hr_employee_id != me or line.run_id.state not in ('posted', 'paid')):
            return request.not_found()
        return _pdf_response(request.env['sb.print'].sudo().pdf(line), 'Payslip %s %s.pdf' % (line.employee_id.name, line.run_id.name))
