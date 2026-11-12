import base64
import json

from odoo import http
from odoo.http import request
from odoo.tools.misc import file_open

from odoo.addons.web.controllers.webmanifest import WebManifest


class UnioManifest(WebManifest):
    """The installable phone / desktop app is called Unio and wears its icon."""

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        manifest.update(background_color='#FFFFFF', theme_color='#1B2240')
        manifest['icons'] = [{'src': '/sonador_unio_brand/static/img/unio-icon-%s.png' % s, 'sizes': '%sx%s' % (s, s), 'type': 'image/png'}
                             for s in (192, 512)]
        return manifest

    def _icon_path(self):
        return 'sonador_unio_brand/static/img/unio-icon-192.png'


class UnioLogo(http.Controller):

    @http.route('/unio/login_logo', type='http', auth='public', readonly=True)
    def login_logo(self, **kw):
        """On the sign-in page: the company's logo once it set one, the Unio logo until then."""
        company = request.env['res.company'].sudo().search([], limit=1, order='id')
        if company.logo and not request.env['unio.brand'].sudo().is_placeholder(company.logo):
            return request.redirect('/web/binary/company_logo', local=True)
        with file_open('sonador_unio_brand/static/img/unio_wordmark.svg', 'rb') as f:
            return request.make_response(f.read(), headers=[('Content-Type', 'image/svg+xml'), ('Cache-Control', 'public, max-age=3600')])
