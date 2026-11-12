import io
import json

from odoo import http
from odoo.http import content_disposition, request


def visible_rows(doc, expanded):
    """Rows the user sees on screen: every ancestor expanded."""
    parents = {r['id']: r.get('parent') for r in doc['rows']}
    out = []
    for row in doc['rows']:
        p, ok, depth = row.get('parent'), True, 0
        while p and depth < 20:
            depth += 1
            if p not in expanded:
                ok = False
                break
            p = parents.get(p)
        if ok:
            out.append(row)
    return out


class SonadorReports(http.Controller):

    @http.route('/sonador_books/report/export', type='http', auth='user', methods=['POST'], csrf=True)
    def export(self, report, fmt='xlsx', options='{}', expanded='[]', **kw):
        opts = json.loads(options or '{}')
        exp = json.loads(expanded or '[]')
        # the report's own access rule (payroll reports: HR only), before anything is computed or rendered
        request.env['sb.report']._check_report_access(report, request.env['sb.report']._options(opts))
        doc = request.env['sb.report'].get_report(report, opts, exp)
        doc['rows'] = visible_rows(doc, set(exp))
        hidden = set(opts.get('hidden_cols') or [])
        if hidden:
            doc['columns'] = [c for i, c in enumerate(doc['columns']) if i == 0 or c['key'] not in hidden]
        base = '%s %s' % (doc['title'], doc['subtitle'].split('|')[0].strip())
        if fmt == 'pdf':
            pdf = request.env['sb.report'].render_pdf(doc)
            disposition = 'inline' if kw.get('inline') else 'attachment'
            return request.make_response(pdf, headers=[
                ('Content-Type', 'application/pdf'),
                ('Content-Disposition', content_disposition(base + '.pdf', disposition_type=disposition))])
        data = request.env['sb.report'].render_xlsx([doc])
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(base + '.xlsx'))])

    def _xlsx(self, doc):
        return request.env['sb.report'].render_xlsx([doc])


class SonadorBackups(http.Controller):

    @http.route('/sonador_books/backup/<string:name>', type='http', auth='user')
    def download_backup(self, name, **kw):
        import os
        if not request.env.user.has_group('base.group_system'):
            return request.not_found()
        folder = request.env['sb.maintenance']._backup_dir()
        full = os.path.join(folder, os.path.basename(name))
        if not name.endswith('.zip') or not os.path.isfile(full):
            return request.not_found()
        with open(full, 'rb') as fh:
            data = fh.read()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/zip'), ('Content-Disposition', content_disposition(os.path.basename(full)))])


class SonadorPacks(http.Controller):

    @http.route('/sonador_books/pack/<int:pack_id>/<string:fmt>', type='http', auth='user')
    def pack(self, pack_id, fmt='pdf', **kw):
        pack = request.env['sb.report.pack'].browse(pack_id).exists()
        if not pack:
            return request.not_found()
        data, filename, mimetype = pack.render(fmt)
        disposition = 'inline' if kw.get('inline') else 'attachment'
        return request.make_response(data, headers=[('Content-Type', mimetype),
                                                    ('Content-Disposition', content_disposition(filename, disposition_type=disposition))])


class SonadorPayslips(http.Controller):

    @http.route('/sonador_books/payslips/<int:run_id>', type='http', auth='user')
    def payslips(self, run_id, **kw):
        R = request.env['sb.report']
        R._check_report_access('payslip')
        run = request.env['sb.payroll.run'].browse(run_id).exists()
        if not run:
            return request.not_found()
        pdf = R.render_pdf(R.payslip_doc(run))
        return request.make_response(pdf, headers=[('Content-Type', 'application/pdf'),
                                                   ('Content-Disposition', content_disposition('%s.pdf' % run.name, disposition_type='inline'))])
