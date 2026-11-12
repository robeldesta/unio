"""What a client request (the web client, JSON-RPC, XML-RPC) may ask of the server.

Odoo's accounting code skips its own checks when the context says so (check_move_validity=False lets an entry stay
unbalanced, skip_readonly_check writes readonly fields, tracking_disable leaves no history ...). Those switches are meant
for the server's own flows: a client request never carries them, they are taken out of its context on the way in.

The server also keeps some fields of a posted entry up to date itself (sent, the payments and statement lines linked to
it, the assets it created, the revaluation date): it writes them inside its flows (Register Payment, Send, the
revaluation), never because a client asked to write them directly. `server_side` tells the two apart: a direct write or
create by a client on journal entries or items is not the server's own flow, nor is code run as a user with no request
at all (a shell or script acting as that user); a button, a wizard or a cron is.
"""
import contextvars

from odoo import models
from odoo.http import request
from odoo.service import model as service_model

# context keys that switch off checks: server flows only
CLIENT_STRIP = frozenset({
    'check_move_validity', 'skip_invoice_sync', 'skip_invoice_line_sync', 'skip_account_move_synchronization',
    'skip_readonly_check', 'skip_is_manually_modified', 'tracking_disable', 'mail_notrack', 'force_delete',
    'install_mode', 'import_file', 'disable_abnormal_invoice_detection', 'move_reverse_cancel', 'no_new_invoice',
    'sb_settling', 'sb_move_flow', 'sb_posting', 'sb_year_end', 'sb_autopost', 'bypass_audit',
})
# methods by which a client writes data straight into records
CLIENT_WRITES = frozenset({'write', 'create', 'web_save', 'web_save_multi', 'load', 'unlink', 'copy', 'copy_data',
                           'web_create', 'name_create', 'update'})
JOURNAL_MODELS = ('account.move', 'account.move.line')

# (model, method) of the client call running in this thread, or None
_CLIENT_CALL = contextvars.ContextVar('sb_client_call', default=None)


def _clean(context):
    if context and any(k in context for k in CLIENT_STRIP):
        return {k: v for k, v in context.items() if k not in CLIENT_STRIP}
    return context


def client_writing(models_=JOURNAL_MODELS):
    """Whether a client is writing or creating records of `models_` directly (write, create, web_save ...)."""
    call = _CLIENT_CALL.get()
    return bool(call) and call[0] in models_ and call[1] in CLIENT_WRITES


def server_side(env):
    """Whether the code running is the server's own flow (see the module docstring)."""
    if _CLIENT_CALL.get():
        return not client_writing()
    return bool(request) or env.su or env.user._is_superuser()


_odoo_call_kw = getattr(service_model.call_kw, '_sb_original', service_model.call_kw)


def call_kw(model, name, args, kwargs):
    if 'sb.voucher' not in model.env.registry:  # a database without this app
        return _odoo_call_kw(model, name, args, kwargs)
    context = kwargs.get('context')
    cleaned = _clean(context)
    if cleaned is not context:
        kwargs = dict(kwargs, context=cleaned)
    token = _CLIENT_CALL.set((model._name, name))
    try:
        return _odoo_call_kw(model, name, args, kwargs)
    finally:
        _CLIENT_CALL.reset(token)


call_kw._sb_original = _odoo_call_kw
# XML-RPC / JSON-RPC (execute_kw) and the web client (/web/dataset/call_kw, call_button) all go through call_kw
service_model.call_kw = call_kw
try:
    from odoo.addons.web.controllers import dataset as _web_dataset
    _web_dataset.call_kw = call_kw
except ImportError:  # pragma: no cover
    pass


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _dispatch(cls, endpoint):
        # any other route: the request's context and a "context" parameter lose the same switches
        if request and request.db:
            ctx = request.env.context
            cleaned = _clean(ctx)
            if cleaned is not ctx:
                request.update_env(context=cleaned)
            params = getattr(request, 'params', None)
            if isinstance(params, dict) and isinstance(params.get('context'), dict):
                params['context'] = _clean(params['context'])
        return super()._dispatch(endpoint)
