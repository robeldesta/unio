"""Unio AI from inside a customer system. Every AI feature (assistant, Studio, help answers) goes through here to the
Unio gateway, which holds the key and keeps each company within its weekly allowance: on the same server through the
platform database, on a server of its own over HTTPS with the token Unio HQ gave it."""
import logging
import time
import uuid
from contextlib import contextmanager

import requests

import odoo
from odoo import api, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class UnioAi(models.AbstractModel):
    _name = 'unio.ai'
    _description = 'Unio AI'

    @contextmanager
    def _platform(self):
        db = self.env['ir.config_parameter'].sudo().get_param('saas.platform_db')
        with odoo.modules.registry.Registry(db).cursor() as cr:
            yield api.Environment(cr, odoo.SUPERUSER_ID, {})

    def _remote(self):
        P = self.env['ir.config_parameter'].sudo()
        url, token = P.get_param('saas.ai_gateway_url'), P.get_param('saas.ai_gateway_token')
        return (url.rstrip('/'), token) if url and token else (None, None)

    def _post(self, what, body, tries=1):
        """A question carries its own id: when the answer is lost on the way back (a dropped connection), it is asked
        again with the same id and Unio gives the same answer without charging it twice."""
        url, token = self._remote()
        r = None
        for n in range(tries):
            try:
                r = requests.post('%s/saas/ai/v1/%s' % (url, what), json=body, timeout=(15, 200),
                                  headers={'Authorization': 'Bearer %s' % token})
                break
            except requests.RequestException:
                _logger.warning('Unio AI: no answer on try %s of %s', n + 1, tries, exc_info=True)
                if n + 1 < tries:
                    time.sleep(3 * (n + 1))
        if r is None:
            raise UserError(self.env._('Unio AI could not be reached just now. Try again in a minute.'))
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise UserError(data.get('error') or self.env._('Unio AI could not answer just now.'))
        return data

    @api.model
    def ask(self, feature, payload):
        """One call to the model for this company and the person asking."""
        P = self.env['ir.config_parameter'].sudo()
        user = self.env.user.login
        if 'saas.ai' in self.env:  # Unio HQ itself: the gateway is here
            return self.env['saas.ai'].sudo().call(self.env.cr.dbname, feature, payload, user)
        if P.get_param('saas.platform_db'):
            with self._platform() as penv:
                if 'saas.ai' not in penv:
                    raise UserError(self.env._('Unio AI is not switched on yet.'))
                return penv['saas.ai'].call(self.env.cr.dbname, feature, payload, user)
        if self._remote()[0]:
            return self._post('call', {'feature': feature, 'payload': payload, 'user': user, 'request_id': uuid.uuid4().hex}, tries=3)
        raise UserError(self.env._('Unio AI is not switched on for this system.'))

    @api.model
    def meter(self):
        """This week's allowance for the company and the person: for the meter beside the AI."""
        P = self.env['ir.config_parameter'].sudo()
        try:
            if 'saas.ai' in self.env:
                return self.env['saas.ai'].sudo().status(self.env.cr.dbname, self.env.user.login)
            if P.get_param('saas.platform_db'):
                with self._platform() as penv:
                    return penv['saas.ai'].status(self.env.cr.dbname, self.env.user.login) if 'saas.ai' in penv else {'enabled': False}
            if self._remote()[0]:
                return self._post('status', {'user': self.env.user.login})
        except Exception:  # noqa: BLE001 - the meter never stops the screen, but says why AI shows as off
            _logger.warning('Unio AI shows as off: the allowance check failed', exc_info=True)
            return {'enabled': False}
        return {'enabled': False}

    @api.model
    def enabled(self):
        return bool(self.meter().get('enabled'))
