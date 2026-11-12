"""Which tours this system can show (the screen each starts on must exist and be open to the person), and a record of
tours started, finished and skipped. On Unio cloud the record goes to the platform, so HQ sees which tours help."""
import logging

import odoo
from odoo import api, models

_logger = logging.getLogger(__name__)


class UnioTour(models.AbstractModel):
    _name = 'unio.tour'
    _description = 'Guided tours'

    @api.model
    def available(self, starts):
        """starts: {tour key: xmlid of the first screen or False}. Returns the keys this person can take."""
        out = []
        for key, xmlid in (starts or {}).items():
            if not xmlid:
                out.append(key)
                continue
            act = self.env.ref(xmlid, raise_if_not_found=False)
            if not act:
                continue
            try:
                act.check_access('read')
            except Exception:
                continue
            menus = self.env['ir.ui.menu'].sudo().search([('action', '=', '%s,%d' % (act._name, act.id))])
            if menus and not menus.with_user(self.env.user)._filter_visible_menus():
                continue
            out.append(key)
        return out

    @api.model
    def event(self, key, step, event):
        P = self.env['ir.config_parameter'].sudo()
        db, sub = P.get_param('saas.platform_db'), P.get_param('saas.tenant')
        if not db or event not in ('start', 'done', 'skip', 'offer', 'decline'):
            return True
        try:
            with odoo.modules.registry.Registry(db).cursor() as cr:
                penv = api.Environment(cr, odoo.SUPERUSER_ID, {})
                if 'saas.tour.event' in penv:
                    penv['saas.tour.event'].log(sub, str(key)[:40], int(step or 0), event, self.env.user.login)
        except Exception:
            _logger.info('tour event not recorded', exc_info=True)
        return True
