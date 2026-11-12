"""Approval confirmations: before an approval step goes through, the person reads the step's text, ticks the box and
signs (drawn with mouse, touchpad or finger), and, where the step says so, types a phrase or re-enters their password.

The engine is generic: a "point" names a method of any model (a button such as purchase.order.button_approve, or a
service call such as sb.leave.service.decide) and the text to show. Points are wrapped when the registry loads; nothing
in the apps themselves changes. A company switch (default off) and a per-text switch decide whether anything is asked.

Every confirmation is stored for good: who, when, which document and action, the exact text and its version, the drawn
signature with a SHA-256 seal over image, text, user and time, IP address, browser and a reference to the session. The
table refuses changes and deletions for everyone, the ORM and the database (a trigger) alike.

How another module plugs in (e.g. payment requests):
  1. Data, no Python: a sb.confirm.text (code, title, body, control tick/phrase/password, fresh_signature) and a
     sb.confirm.point per step: model, method (the button, e.g. "action_finance_check"), optional domain, group_id /
     not_group_id, amount_field (for the typed phrase threshold). The method is wrapped at registry load; on a web call
     the person sees the text, ticks, signs, and the same call runs again. Nothing else to write.
  2. Service calls: record_model + record_arg (position of the document id) + only_if_arg (e.g. approve=True).
  3. Code paths that are not a single method: call self.env['sb.confirm.point'].require(records, 'method_name')
     before acting; it raises ConfirmationRequired the same way.
  4. In Me, a kind's _ap_<kind>_decide calling the wrapped method gets the text inline in the approval card, and in
     Telegram through the signing page, with no extra code.
Confirmations are only asked on interactive calls (a web request, or context sb_confirm_force=True as the Telegram
bot sets); crons, imports and shells are never stopped."""
import base64
import functools
import hashlib
import json
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.http import request
from odoo.tools import consteq
from odoo.tools.misc import hmac as odoo_hmac
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

FRESH_MINUTES = 15          # a confirmation lets the action through for this long (the same call run again)
MAX_SIGNATURE = 600 * 1024  # bytes of the drawn image
CHAT_SIGNATURE_HOURS = 8    # a signature drawn in a chat bot's page may be used again for this long (not for always-draw texts)
CONTROLS = [('tick', 'Tick box and signature'), ('phrase', 'Tick, signature, and the typed phrase above an amount'),
            ('password', 'Tick, signature, and password')]
_SKIP = frozenset({'create', 'write', 'unlink', 'read', 'search', 'browse', 'copy', 'name_create', 'default_get', 'fields_get'})


class ConfirmationRequired(UserError):
    """Raised instead of running a step that needs a confirmation first; args[1] carries the text to show."""

    def __init__(self, message, payload):
        super().__init__(message)
        self.args = (message, payload)


def _meta():
    """IP address, browser and a session reference of the current web request (never the session key itself)."""
    out = {'ip': False, 'agent': False, 'session': False}
    try:
        if request and getattr(request, 'httprequest', None):
            hr = request.httprequest
            ip = (hr.environ.get('HTTP_X_FORWARDED_FOR') or hr.remote_addr or '').split(',')[0].strip()
            out['ip'] = ip or False
            out['agent'] = (hr.user_agent.string if hr.user_agent else '')[:500] or False
            sid = getattr(request.session, 'sid', None)
            if sid:
                out['session'] = 'web:' + hashlib.sha256(sid.encode()).hexdigest()[:24]
    except RuntimeError:
        pass
    return out


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_confirm_on = fields.Boolean('Approval confirmations on', default=False,
                                   help='Off: no confirmation text is asked anywhere. On: each text that is switched on is asked '
                                        'before its approval step.')
    sb_confirm_amount = fields.Monetary('Typed phrase above', currency_field='currency_id', default=100000.0,
                                        help='Steps with a typed phrase ask for it when the document is above this amount.')
    sb_confirm_reuse_signature = fields.Boolean('Reuse a signature within a session', default=False,
                                                help='On: after signing once, people may tick "use the signature I drew earlier in '
                                                     'this session" instead of drawing again (unless the text always asks a new '
                                                     'one). Off: every confirmation is signed by hand.')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sb_confirm_on = fields.Boolean(related='company_id.sb_confirm_on', readonly=False)
    sb_confirm_amount = fields.Monetary(related='company_id.sb_confirm_amount', readonly=False, currency_field='sb_confirm_currency_id')
    sb_confirm_reuse_signature = fields.Boolean(related='company_id.sb_confirm_reuse_signature', readonly=False)
    sb_confirm_currency_id = fields.Many2one(related='company_id.currency_id', string='Confirmation currency')


class SbConfirmText(models.Model):
    _name = 'sb.confirm.text'
    _description = 'Confirmation text'
    _order = 'step_no, sequence, id'

    name = fields.Char('Title', required=True)
    code = fields.Char(required=True, index=True)
    step_no = fields.Integer('Step', help='Number of the step in the company\'s list of approval steps.')
    step = fields.Char('Approval step')
    who = fields.Char('Who confirms')
    sequence = fields.Integer(default=10)
    body = fields.Text('Text', required=True, help='The exact text people confirm. Changing it makes a new version: '
                                                    'everyone confirms the new version from then on.')
    version = fields.Integer(default=1, readonly=True, required=True)
    legal_state = fields.Selection([('draft', 'Draft for legal review'), ('approved', 'Approved by legal')],
                                   'Legal status', default='draft', required=True)
    enabled = fields.Boolean('On', default=False, help='Ask this text before its approval steps (when confirmations are on '
                                                       'for the company).')
    control = fields.Selection(CONTROLS, default='tick', required=True)
    phrase = fields.Char('Phrase to type', default='I CONFIRM')
    fresh_signature = fields.Boolean('Always draw a new signature',
                                     help='Off: a signature drawn earlier in the same login session may be used again.')
    company_id = fields.Many2one('res.company', 'Company', help='Empty: every company.')
    version_ids = fields.One2many('sb.confirm.text.version', 'text_id', 'Versions')
    point_ids = fields.One2many('sb.confirm.point', 'text_id', 'Where it is asked', context={'active_test': False})
    point_count = fields.Integer(compute='_compute_counts', string='Hooked steps')
    confirmation_count = fields.Integer(compute='_compute_counts', string='Confirmations')

    _code_unique = models.Constraint('unique(code, company_id)', 'Each text has its own code.')

    def _compute_counts(self):
        for t in self:
            t.point_count = len(t.point_ids.filtered('active'))
            t.confirmation_count = self.env['sb.confirmation'].sudo().search_count([('text_id', '=', t.id)])

    @api.model_create_multi
    def create(self, vals_list):
        texts = super().create(vals_list)
        for t in texts:
            t._snapshot()
        return texts

    def write(self, vals):
        changed = {'name', 'body'} & set(vals)
        if changed:
            for t in self:
                if any(vals[f] != t[f] for f in changed):
                    super(SbConfirmText, t).write(dict(vals, version=t.version + 1))
                    t._snapshot()
                else:
                    super(SbConfirmText, t).write(vals)
            return True
        return super().write(vals)

    def _snapshot(self):
        self.env['sb.confirm.text.version'].sudo().create({'text_id': self.id, 'version': self.version, 'name': self.name,
                                                           'body': self.body})

    def action_switch(self):
        for t in self:
            t.enabled = not t.enabled
        return True

    def action_confirmations(self):
        return {'type': 'ir.actions.act_window', 'name': self.env._('Confirmations'), 'res_model': 'sb.confirmation',
                'view_mode': 'list,form', 'domain': [('text_id', 'in', self.ids)]}


class SbConfirmTextVersion(models.Model):
    _name = 'sb.confirm.text.version'
    _description = 'Confirmation text version'
    _order = 'text_id, version desc'

    text_id = fields.Many2one('sb.confirm.text', 'Confirmation text', required=True, ondelete='cascade', index=True)
    version = fields.Integer(required=True)
    name = fields.Char('Title')
    body = fields.Text('Text')
    date = fields.Datetime(default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', 'Changed by', default=lambda s: s.env.user)

    def write(self, vals):
        raise UserError(self.env._('A version of a confirmation text cannot be changed: edit the text to make a new version.'))


class SbConfirmPoint(models.Model):
    """Where a text is asked: a method of a model, optionally only for some records, people or arguments."""
    _name = 'sb.confirm.point'
    _description = 'Confirmation step'
    _order = 'sequence, id'

    name = fields.Char('Action', required=True, help='How the action is named in the confirmation, e.g. "Approve purchase order".')
    text_id = fields.Many2one('sb.confirm.text', 'Text', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    model = fields.Char('Model', required=True, help='Technical name of the model whose method is the approval step.')
    method = fields.Char('Method', required=True)
    record_model = fields.Char('Document model', help='When the method is a service call: the model of the document it acts on.')
    record_arg = fields.Integer('Document id argument', default=-1,
                                help='-1: the records the method runs on. 0, 1 ...: the position of the argument holding the '
                                     'document id; with no document (a new request), the confirmation is for the action.')
    only_if_arg = fields.Integer('Only when argument is true', default=-1,
                                 help='-1: always. 0, 1 ...: ask only when this positional argument is true (e.g. approve=True).')
    domain = fields.Char('Only for documents', default='[]', help='Domain the document must match, e.g. [("amount_total", ">=", 500000)].')
    group_id = fields.Many2one('res.groups', 'Only for people with role')
    not_group_id = fields.Many2one('res.groups', 'Not for people with role')
    amount_field = fields.Char('Amount field', help='Field holding the document amount, for the typed phrase threshold.')
    note = fields.Char()

    @api.constrains('model', 'method', 'domain')
    def _check_point(self):
        for p in self:
            if p.method in _SKIP or p.method.startswith('_'):
                raise ValidationError(self.env._('%s cannot carry a confirmation.', p.method))
            try:
                safe_eval(p.domain or '[]')
            except Exception:
                raise ValidationError(self.env._('The document condition is not a valid domain.'))

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        self._reload()
        return res

    def write(self, vals):
        res = super().write(vals)
        if {'model', 'method', 'active'} & set(vals):
            self._reload()
        return res

    def _reload(self):
        self.env['sb.confirm.point']._install_hooks()
        # other workers pick the new method up when they next reload the registry
        self.env.registry.registry_invalidated = True

    # ------------------------------------------------------------------ hooks
    def _register_hook(self):
        super()._register_hook()
        try:
            self._install_hooks()
        except Exception:
            _logger.exception('Confirmation hooks not installed')

    @api.model
    def _install_hooks(self):
        self.env.cr.execute("SELECT to_regclass('sb_confirm_point')")
        if not self.env.cr.fetchone()[0]:
            return
        self.env.cr.execute('SELECT DISTINCT model, method FROM sb_confirm_point WHERE active')
        for model, method in self.env.cr.fetchall():
            if model not in self.env.registry or method in _SKIP:
                continue
            cls = type(self.env[model])
            original = getattr(cls, method, None)
            if not callable(original) or getattr(original, '_sb_confirm', False):
                continue
            setattr(cls, method, _wrap(original, method))
            _logger.info('Confirmation asked before %s.%s', model, method)

    # ------------------------------------------------------------------ the gate
    @api.model
    def _gate(self, records, method, args, kwargs, original):
        env = records.env
        if env.uid == SUPERUSER_ID or not (request or env.context.get('sb_confirm_force')):
            return
        company = env.company
        if not company.sb_confirm_on:
            return
        points = self.sudo().search([('model', '=', records._name), ('method', '=', method), ('text_id.enabled', '=', True),
                                     '|', ('text_id.company_id', '=', False), ('text_id.company_id', '=', company.id)])
        user = env.user
        for p in points:
            if p.group_id and p.group_id not in user.all_group_ids:
                continue
            if p.not_group_id and p.not_group_id in user.all_group_ids:
                continue
            if p.only_if_arg >= 0 and not (len(args) > p.only_if_arg and args[p.only_if_arg]):
                continue
            docs = p._documents(records, args)
            if docs is not None and not docs and not records and p.record_arg < 0:
                docs = None    # a service call with no document named: the confirmation is for the action
            if docs is None:   # an action without a document yet (a new request)
                if not p._fresh(False, 0):
                    raise ConfirmationRequired(p.text_id.name, p._payload(records, False, method, args, kwargs, original))
                continue
            pending = docs.filtered(lambda d: not p._fresh(d._name, d.id))
            if pending:
                raise ConfirmationRequired(p.text_id.name, p._payload(records, pending, method, args, kwargs, original))

    @api.model
    def require(self, records, method, args=(), kwargs=None):
        """Ask the confirmations set on records' model and method, from code (see the module docstring, 3.)."""
        original = getattr(type(records), method, None)
        self._gate(records, method, tuple(args), kwargs or {}, getattr(original, '__wrapped__', original))

    def _documents(self, records, args):
        if self.record_arg < 0:
            docs = records.sudo()
        else:
            if len(args) <= self.record_arg or not args[self.record_arg]:
                return None
            val = args[self.record_arg]
            ids = [int(i) for i in (val if isinstance(val, (list, tuple)) else [val])]
            docs = self.env[self.record_model or records._name].sudo().browse(ids).exists()
        dom = safe_eval(self.domain or '[]')
        return docs.filtered_domain(dom) if dom else docs

    def _fresh(self, model, res_id):
        return bool(self.env['sb.confirmation'].sudo().search_count([
            ('user_id', '=', self.env.uid), ('point_id', '=', self.id), ('text_version', '=', self.text_id.version),
            ('res_model', '=', model or False), ('res_id', '=', res_id or 0),
            ('date', '>=', fields.Datetime.now() - timedelta(minutes=FRESH_MINUTES))]))

    def _amount(self, docs):
        if not docs or not self.amount_field or self.amount_field not in docs._fields:
            return 0.0
        return max(docs.mapped(self.amount_field) or [0.0])

    def _payload(self, records, docs, method, args, kwargs, original):
        t = self.text_id
        company = self.env.company
        amount = self._amount(docs) if docs else 0.0
        needs_phrase = t.control == 'phrase' and amount >= (company.sb_confirm_amount or 0.0)
        token = self._sign({'p': self.id, 'v': t.version, 'm': docs._name if docs else False, 'ids': docs.ids if docs else [],
                            'u': self.env.uid, 'a': amount})
        replay = None
        try:
            call_args = list(args) if getattr(original, '_api_model', False) else [records.ids] + list(args)
            replay = {'model': records._name, 'method': method, 'args': call_args, 'kwargs': kwargs}
            json.dumps(replay)
        except (TypeError, ValueError):
            replay = None
        doc_label = ', '.join('%s %s' % (self.env['ir.model']._get(d._name).name, d.display_name) for d in docs[:5]) if docs else self.name
        return {'token': token, 'title': t.name, 'version': t.version, 'text': t.body, 'action': self.name, 'document': doc_label,
                'needs_phrase': needs_phrase, 'phrase': t.phrase or 'I CONFIRM', 'phrase_amount': company.sb_confirm_amount,
                'needs_password': t.control == 'password', 'needs_signature': True,
                'reuse_signature': self.env.company.sb_confirm_reuse_signature and (not t.fresh_signature)
                                   and self.env['sb.confirmation']._session_signature() is not False,
                'replay': replay}

    def _sign(self, data):
        msg = json.dumps(data, sort_keys=True)
        sig = odoo_hmac(self.env(su=True), 'sb_confirm', msg)
        return base64.urlsafe_b64encode(msg.encode()).decode() + '.' + sig

    @api.model
    def _unsign(self, token):
        try:
            b, sig = token.rsplit('.', 1)
            msg = base64.urlsafe_b64decode(b.encode()).decode()
        except Exception:
            raise UserError(self.env._('This confirmation is not valid. Start the approval again.'))
        if not consteq(odoo_hmac(self.env(su=True), 'sb_confirm', msg), sig):
            raise UserError(self.env._('This confirmation is not valid. Start the approval again.'))
        return json.loads(msg)


def _wrap(original, method):
    @functools.wraps(original)
    def wrapped(self, *args, **kwargs):
        if self.env.context.get('sb_confirm_inside') or 'sb.confirm.point' not in self.env:
            return original(self, *args, **kwargs)
        self.env['sb.confirm.point']._gate(self, method, args, kwargs, original)
        # what the step calls inside is part of the same confirmed action
        return original(self.with_context(sb_confirm_inside=True), *args, **kwargs)
    wrapped._sb_confirm = True
    return wrapped


class SbConfirmation(models.Model):
    _name = 'sb.confirmation'
    _description = 'Approval confirmation'
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char('Reference', readonly=True)
    date = fields.Datetime('Date and time', required=True, readonly=True, index=True)
    user_id = fields.Many2one('res.users', 'User', required=True, readonly=True, index=True, ondelete='restrict')
    user_name = fields.Char('Name', readonly=True)
    login = fields.Char(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True, ondelete='restrict')
    res_model = fields.Char('Document model', readonly=True, index=True)
    res_id = fields.Integer('Document id', readonly=True, index=True)
    doc_type = fields.Char('Document type', readonly=True)
    doc_number = fields.Char('Document number', readonly=True)
    action = fields.Char('Action confirmed', readonly=True)
    method = fields.Char(readonly=True)
    point_id = fields.Many2one('sb.confirm.point', 'Step', readonly=True, ondelete='restrict', index=True)
    text_id = fields.Many2one('sb.confirm.text', 'Text', readonly=True, ondelete='restrict', index=True)
    text_title = fields.Char('Text title', readonly=True)
    text_version = fields.Integer('Text version', readonly=True)
    text_copy = fields.Text('Exact text accepted', readonly=True)
    ticked = fields.Boolean('Box ticked', readonly=True)
    phrase_typed = fields.Char('Phrase typed', readonly=True)
    password_checked = fields.Boolean('Password re-entered', readonly=True)
    amount = fields.Float(readonly=True)
    signature = fields.Binary('Signature', attachment=False, readonly=True)
    signature_reused = fields.Boolean('Signature drawn earlier in the session', readonly=True)
    seal = fields.Char('SHA-256 seal', readonly=True, help='SHA-256 over the signature image, the text, the user and the time: '
                                                           'any change to them no longer matches.')
    seal_ok = fields.Boolean('Seal matches', compute='_compute_seal_ok')
    ip_address = fields.Char('IP address', readonly=True)
    user_agent = fields.Char('Browser', readonly=True)
    session_ref = fields.Char('Session', readonly=True, help='A one-way reference to the login session (not the session key).')
    channel = fields.Selection([('web', 'Web'), ('telegram', 'Telegram')], default='web', readonly=True)
    telegram_ref = fields.Char('Telegram account', readonly=True)
    note = fields.Char('Note', readonly=True, help='E.g. why no password was asked (a verified chat sign-in instead).')

    def init(self):
        # the database itself refuses any change or deletion, whoever asks
        self.env.cr.execute("""
            CREATE OR REPLACE FUNCTION sb_confirmation_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Approval confirmations cannot be changed or deleted'; END; $$ LANGUAGE plpgsql;
            DROP TRIGGER IF EXISTS sb_confirmation_immutable ON sb_confirmation;
            CREATE TRIGGER sb_confirmation_immutable BEFORE UPDATE OR DELETE ON sb_confirmation
                FOR EACH ROW EXECUTE FUNCTION sb_confirmation_immutable();
        """)

    @api.model
    def _seal(self, image_b64, text, user_id, date_str):
        h = hashlib.sha256()
        for part in (base64.b64decode(image_b64 or b''), (text or '').encode(), str(user_id).encode(), date_str.encode()):
            h.update(hashlib.sha256(part).digest())
        return h.hexdigest()

    def _compute_seal_ok(self):
        for c in self:
            c.seal_ok = bool(c.seal) and c.seal == self._seal(c.signature, c.text_copy, c.user_id.id,
                                                              fields.Datetime.to_string(c.date))

    def write(self, vals):
        raise UserError(self.env._('Approval confirmations cannot be changed.'))

    def unlink(self):
        raise UserError(self.env._('Approval confirmations cannot be deleted.'))

    @api.model
    def _session_signature(self, session_ref=None):
        """The signature this person drew earlier in this login session, if any."""
        session_ref = session_ref or _meta()['session']
        if not session_ref:
            return False
        dom = [('user_id', '=', self.env.uid), ('session_ref', '=', session_ref), ('signature', '!=', False),
               ('signature_reused', '=', False)]
        if not session_ref.startswith('web:'):
            # a chat account is not a login session that ends: a signature drawn there serves for 8 hours
            dom.append(('date', '>=', fields.Datetime.now() - timedelta(hours=CHAT_SIGNATURE_HOURS)))
        last = self.sudo().search(dom, limit=1)
        return last.signature if last else False

    @api.model
    def accept(self, token, ticked, phrase='', password='', signature=False, reuse_signature=False, channel='web', telegram_ref=False):
        """Record the confirmation the person just gave (one per document). Called by the confirmation screen."""
        data = self.env['sb.confirm.point']._unsign(token)
        if data.get('u') != self.env.uid:
            raise UserError(self.env._('This confirmation was opened by someone else.'))
        point = self.env['sb.confirm.point'].sudo().browse(data['p']).exists()
        if not point:
            raise UserError(self.env._('This approval step is no longer asked.'))
        text = point.text_id
        if text.version != data['v']:
            raise UserError(self.env._('The text changed while you were reading it. Read the new version and confirm again.'))
        if not ticked:
            raise UserError(self.env._('Tick the box to confirm.'))
        company = self.env.company
        if text.control == 'phrase' and (data.get('a') or 0.0) >= (company.sb_confirm_amount or 0.0):
            if (phrase or '').strip().upper() != (text.phrase or 'I CONFIRM').strip().upper():
                raise UserError(self.env._('Type %s exactly to confirm.', text.phrase or 'I CONFIRM'))
        pw_ok = False
        waiver = self._password_waiver(channel, telegram_ref) if text.control == 'password' else False
        if text.control == 'password' and not waiver:
            if channel != 'web':
                raise UserError(self.env._('This step needs your password: approve it in the web app.'))
            if not password:
                raise UserError(self.env._('Enter your password to confirm.'))
            try:
                self.env.user._check_credentials({'type': 'password', 'password': password}, {'interactive': True})
            except AccessDenied:
                raise UserError(self.env._('The password is not right.'))
            pw_ok = True
        meta = _meta()
        if channel == 'telegram':
            # the session is the chat account (its id), whatever the username shows that day
            meta = {'ip': False, 'agent': 'Telegram', 'session': 'telegram:%s' % ((telegram_ref or '').split(' ')[0])}
        reused = False
        if signature and ',' in signature[:100]:
            signature = signature.split(',', 1)[1]   # a data URL from the pad
        if not signature and reuse_signature and not text.fresh_signature and self.env.company.sb_confirm_reuse_signature:
            signature = self._session_signature(meta['session'])
            reused = bool(signature)
        if not signature:
            raise UserError(self.env._('Sign in the box to confirm.'))
        try:
            raw = base64.b64decode(signature, validate=True)
        except Exception:
            raise UserError(self.env._('The signature could not be read. Sign again.'))
        if len(raw) > MAX_SIGNATURE or not raw.startswith(b'\x89PNG'):
            raise UserError(self.env._('The signature could not be read. Sign again.'))
        if len(raw) < 900 and not reused:
            raise UserError(self.env._('The signature is empty. Sign in the box.'))
        now = fields.Datetime.now().replace(microsecond=0)
        now_s = fields.Datetime.to_string(now)
        user = self.env.user
        model = data.get('m') or False
        docs = self.env[model].sudo().browse(data.get('ids') or []).exists() if model else []
        rows = []
        for d in (docs or [False]):
            rows.append({
                'name': '%s/%s' % (text.code, now.strftime('%Y%m%d%H%M%S')), 'date': now, 'user_id': user.id, 'user_name': user.name,
                'login': user.login, 'company_id': company.id, 'res_model': d._name if d else False, 'res_id': d.id if d else 0,
                'doc_type': self.env['ir.model']._get(d._name).name if d else False, 'doc_number': d.display_name if d else False,
                'action': point.name, 'method': '%s.%s' % (point.model, point.method), 'point_id': point.id, 'text_id': text.id,
                'text_title': text.name, 'text_version': text.version, 'text_copy': text.body, 'ticked': True,
                'phrase_typed': (phrase or '').strip() or False, 'password_checked': pw_ok, 'amount': data.get('a') or 0.0,
                'signature': signature, 'signature_reused': reused,
                'seal': self._seal(signature, text.body, user.id, now_s),
                'ip_address': meta['ip'], 'user_agent': meta['agent'], 'session_ref': meta['session'],
                'channel': channel, 'telegram_ref': telegram_ref or False, 'note': waiver or False})
        recs = self.sudo().create(rows)
        return {'ids': recs.ids}

    @api.model
    def _password_waiver(self, channel, telegram_ref=False):
        """A step that asks for the password may accept another proof of identity on some channels (a chat bot module
        returns the note recorded with the confirmation, e.g. a verified chat sign-in). Default: never."""
        return False

    @api.model
    def last_for(self, model, res_id):
        """Confirmations given on a document, for its printout's approval footer."""
        return self.sudo().search([('res_model', '=', model), ('res_id', '=', res_id)], order='date')
