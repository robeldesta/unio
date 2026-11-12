"""Buttons and saves wait at most a few seconds for a record another user is posting: never a two-minute spinner.

Postgres waits for a row lock as long as the other transaction holds it (another user's post, an idle session); the
request ran until the server's time limit. Here a button or a save waits LOCK_WAIT, then the user reads a plain message."""
from psycopg2 import errors

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.web.controllers.dataset import DataSet

LOCK_WAIT = '12s'
WRITES = ('web_save', 'write', 'create', 'unlink', 'action_archive', 'action_unarchive')


def _busy_message(env):
    return env._('Another user is posting or changing the same records at this moment. Wait a few seconds and try '
                 'again; nothing was saved.')


class PostingDataSet(DataSet):

    def _sb_guarded(self, call):
        request.env.cr.execute("SET LOCAL lock_timeout = '%s'" % LOCK_WAIT)
        try:
            result = call()
            request.env.flush_all()  # the writes run here, inside the guard, not after the request returns
            return result
        except errors.LockNotAvailable as e:
            raise UserError(_busy_message(request.env)) from e

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        return self._sb_guarded(lambda: super(PostingDataSet, self).call_button(model, method, args, kwargs, path=path))

    @http.route()
    def call_kw(self, model, method, args, kwargs, path=None):
        if method not in WRITES:
            return super().call_kw(model, method, args, kwargs, path=path)
        return self._sb_guarded(lambda: super(PostingDataSet, self).call_kw(model, method, args, kwargs, path=path))
