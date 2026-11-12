from odoo import models

from ..controllers.app_path import to_app


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _post_dispatch(cls, response):
        super()._post_dispatch(response)
        # the server's own redirects into the system (after login, "View" buttons in emails...) go to /app
        loc = response.headers.get('Location') if hasattr(response, 'headers') else None
        if loc:
            new = to_app(loc).replace('redirect=%2Fodoo', 'redirect=%2Fapp')
            if new != loc:
                response.headers['Location'] = new
