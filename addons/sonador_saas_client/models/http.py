"""What the top bar needs to know at start (is help there, is Unio AI on, are files allowed) comes with the page itself
instead of in separate calls; the answers are kept a few minutes per server process."""
import time

from odoo import models
from odoo.http import request

_CACHE = {}  # dbname -> (expires, flags)
TTL = 300


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        if request and request.session.uid:
            info['unio'] = self._unio_flags()
        return info

    @classmethod
    def _unio_flags_compute(cls, env):
        flags = {'help': False, 'help_ai': False, 'ai': False, 'ai_files': False}
        try:
            flags['help'] = env['saas.help'].available()
            if flags['help']:
                flags['help_ai'] = bool(env['saas.help'].ai_on())
        except Exception:  # noqa: BLE001 - the page never waits on help
            pass
        try:
            m = env['unio.ai'].meter()
            flags['ai'], flags['ai_files'] = bool(m.get('enabled')), m.get('files', True) is not False
        except Exception:  # noqa: BLE001
            pass
        return flags

    def _unio_flags(self):
        db = self.env.cr.dbname
        hit = _CACHE.get(db)
        if hit and hit[0] > time.time():
            return hit[1]
        flags = self._unio_flags_compute(self.env)
        _CACHE[db] = (time.time() + TTL, flags)
        return flags
