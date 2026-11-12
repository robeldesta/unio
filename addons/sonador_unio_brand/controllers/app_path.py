"""The system lives at /app (company.unio.et/app), not /odoo. /odoo links (old emails, bookmarks) still work: they open
the system and the address bar switches to /app. Redirects the server sends to /odoo/... are rewritten to /app/..."""
import re

from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.home import Home

ODOO = re.compile(r'^(?P<base>(?:https?://[^/]+)?)/odoo(?=/|\?|#|$)')


def to_app(url):
    return ODOO.sub(r'\g<base>/app', url) if url else url


class AppHome(Home):

    @http.route(['/web', '/odoo', '/odoo/<path:subpath>', '/scoped_app/<path:subpath>', '/app', '/app/<path:subpath>'],
                type='http', auth='none', readonly=Home._web_client_readonly)
    def web_client(self, s_action=None, **kw):
        return super().web_client(s_action=s_action, **kw)

    def _login_redirect(self, uid, redirect=None):
        return to_app(super()._login_redirect(uid, redirect=redirect))
