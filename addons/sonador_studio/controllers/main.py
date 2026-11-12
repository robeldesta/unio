from odoo import http
from odoo.http import content_disposition, request


class StudioExport(http.Controller):

    @http.route('/sonador_studio/export/<int:app_id>', type='http', auth='user')
    def export(self, app_id, **kw):
        name, data = request.env['sb.studio.export'].export_app(app_id)
        return request.make_response(data, headers=[('Content-Type', 'application/zip'), ('Content-Disposition', content_disposition(name))])
