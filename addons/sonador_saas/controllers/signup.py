"""Public endpoints of the cloud service: the website's sign-up form, the web-address check and the price calculator."""
import time
from collections import defaultdict, deque

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

_HITS = defaultdict(deque)  # ip -> sign-up times (per worker; a proxy limit sits in front in production)


def _cors(resp):
    origin = request.httprequest.headers.get('Origin')
    allowed = (request.env['ir.config_parameter'].sudo().get_param('saas.site_origins') or '').split(',')
    if origin and origin.strip() in [o.strip() for o in allowed if o.strip()]:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp


def _json(data, status=200):
    return _cors(request.make_json_response(data, status=status))


class SaasSignup(http.Controller):

    @http.route('/unio/check', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def check(self, name='', **kw):
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        T = request.env['saas.tenant'].sudo()
        try:
            sub = T.clean_subdomain(name)
        except ValidationError as e:
            return _json({'ok': False, 'message': str(e.args[0])})
        taken = not request.env['saas.signup'].sudo().address_free(sub)
        return _json({'ok': not taken, 'subdomain': sub, 'message': '' if not taken else 'Taken: choose another web address.'})

    @http.route('/unio/price', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def price(self, users='1', currency='USD', plan='standard', **kw):
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        p = request.env['saas.plan'].sudo().search([('code', '=', plan)], limit=1)
        if not p:
            return _json({'ok': False}, 404)
        return _json(dict(p.price_for(max(int(users or 1), 1), currency.upper()), ok=True))

    @http.route('/unio/form', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def form(self, **kw):
        """What the sign-up form needs before it is sent: its signed token, the robot check's site key."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        return _json(dict(request.env['saas.signup'].sudo().form_token(), ok=True))

    def _ip(self):
        # behind nginx on the same host: the address it saw (proxy_mode is off, so read the header nginx sets)
        h = request.httprequest.headers
        return (h.get('X-Real-IP') or (h.get('X-Forwarded-For') or '').split(',')[0].strip() or request.httprequest.remote_addr or '?')[:64]

    def _guard(self):
        ip = self._ip()
        hits = _HITS[ip]
        now = time.time()
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= 20:
            return ip, _json({'ok': False, 'message': 'Too many requests from your network; try again in an hour.'}, 429)
        hits.append(now)
        return ip, None

    def _data(self, kw):
        return request.get_json_data() if request.httprequest.is_json else kw

    @http.route('/unio/signup', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def signup(self, **kw):
        """Step 1: reserve the address and email the confirmation link."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        ip, refused = self._guard()
        if refused:
            return refused
        try:
            res = request.env['saas.signup'].sudo().start(self._data(kw), ip)
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/resend', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def resend(self, **kw):
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        ip, refused = self._guard()
        if refused:
            return refused
        data = self._data(kw)
        try:
            res = request.env['saas.signup'].sudo().resend(data.get('email'), data.get('subdomain'))
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/verify', type='http', auth='public', methods=['GET', 'POST', 'OPTIONS'], csrf=False)
    def verify(self, t='', **kw):
        """Step 2: the link from the email. GET shows what will be made; POST with the password makes it."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        S = request.env['saas.signup'].sudo()
        try:
            if request.httprequest.method == 'GET':
                return _json(dict(S.peek(t), ok=True))
            ip, refused = self._guard()
            if refused:
                return refused
            data = self._data(kw)
            res = S.confirm(data.get('t'), data.get('password'))
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/subscribe', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def subscribe(self, **kw):
        """A customer asks to subscribe (online payment is not open yet): we invoice them and they pay by transfer."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        ip, refused = self._guard()
        if refused:
            return refused
        data = self._data(kw)
        try:
            res = request.env['saas.service'].sudo().subscribe_request(data.get('subdomain'), data.get('email'), data.get('users'), data.get('note'),
                                                                       data.get('period') or 'monthly')
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/pay', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def pay(self, c='', u='1', p='monthly', **kw):
        """The telebirr QR (and bank details) for the first month of a company."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        try:
            res = request.env['saas.payment'].sudo().qr_for(c, u, p)
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/paid', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def paid(self, **kw):
        """The payer sends the telebirr transaction number: checked against telebirr's receipt, or by hand."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        ip, refused = self._guard()
        if refused:
            return refused
        data = self._data(kw)
        try:
            res = request.env['saas.payment'].sudo().report(data.get('subdomain'), data.get('email'), data.get('users'), data.get('reference'),
                                                            data.get('period') or 'monthly')
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/checkout', type='http', auth='public', methods=['POST', 'OPTIONS'], csrf=False)
    def checkout(self, **kw):
        """Start paying on telebirr's checkout page."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        ip, refused = self._guard()
        if refused:
            return refused
        data = self._data(kw)
        try:
            P = request.env['saas.payment'].sudo()
            start = P.checkout_chapa if data.get('provider') == 'chapa' else P.checkout
            res = start(data.get('subdomain'), data.get('email'), data.get('users'), data.get('period') or 'monthly')
        except (UserError, ValidationError) as e:
            return _json({'ok': False, 'message': str(e.args[0])}, 400)
        return _json(dict(res, ok=True))

    @http.route('/unio/pay_status', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def pay_status(self, o='', **kw):
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        return _json(dict(request.env['saas.payment'].sudo().settle(o), ok=True))

    @http.route('/unio/telebirr/notify', type='http', auth='public', methods=['POST'], csrf=False)
    def telebirr_notify(self, **kw):
        """telebirr's notice that an order changed. Its content is not trusted: telebirr is asked about the order."""
        try:
            body = request.get_json_data() if request.httprequest.is_json else kw
        except ValueError:
            body = kw
        order = (body or {}).get('merch_order_id') or ((body or {}).get('biz_content') or {}).get('merch_order_id')
        res = request.env['saas.payment'].sudo().settle(order) if order else {'state': 'unknown'}
        return request.make_json_response({'code': 0, 'msg': 'success', 'state': res.get('state')})

    @http.route('/unio/chapa/notify', type='http', auth='public', methods=['GET', 'POST'], csrf=False)
    def chapa_notify(self, **kw):
        """Chapa's callback for a transaction. Not trusted as such: Chapa's verify API is asked."""
        try:
            body = request.get_json_data() if request.httprequest.is_json else kw
        except ValueError:
            body = kw
        ref = (body or {}).get('trx_ref') or (body or {}).get('tx_ref') or kw.get('trx_ref') or kw.get('tx_ref')
        res = request.env['saas.payment'].sudo().settle(ref) if ref else {'state': 'unknown'}
        return request.make_json_response({'ok': True, 'state': res.get('state')})

    @http.route('/unio/ref', type='http', auth='public', methods=['GET', 'OPTIONS'], csrf=False)
    def ref(self, code='', **kw):
        """The website asks what a referral code offers (shown on the sign-up page)."""
        if request.httprequest.method == 'OPTIONS':
            return _json({})
        return _json(dict(request.env['saas.tenant'].sudo().referral_offer(code), ok=True))
