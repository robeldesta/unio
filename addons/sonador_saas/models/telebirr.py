"""telebirr C2B Web Checkout: an order with telebirr, the customer pays on telebirr's own checkout page, telebirr
calls our notify URL, and we ask telebirr for the order's status before believing it.

Settings (ir.config_parameter, platform database): saas.telebirr_base_url, saas.telebirr_web_url,
saas.telebirr_fabric_app_id, saas.telebirr_app_secret, saas.telebirr_merchant_app_id, saas.telebirr_merchant_code,
saas.telebirr_private_key (PEM). Requests are signed SHA256withRSA/PSS over the sorted fields, as telebirr's demo does."""
import base64
import logging
import re
import secrets
import string
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_logger = logging.getLogger(__name__)

EXCLUDE = {'sign', 'sign_type', 'header', 'refund_info', 'openType', 'raw_request', 'biz_content'}


class Telebirr:
    def __init__(self, base_url, web_url, fabric_app_id, app_secret, merchant_app_id, merchant_code, private_key, verify_tls=False):
        self.base_url = base_url.rstrip('/')
        self.web_url = web_url
        self.fabric_app_id = fabric_app_id
        self.app_secret = app_secret
        self.merchant_app_id = merchant_app_id
        self.merchant_code = merchant_code
        key = private_key.strip()
        if 'BEGIN' not in key:
            # the developer portal shows the key as bare base64: give it its PEM frame
            import textwrap
            key = '-----BEGIN PRIVATE KEY-----\n%s\n-----END PRIVATE KEY-----\n' % '\n'.join(textwrap.wrap(''.join(key.split()), 64))
        self.key = serialization.load_pem_private_key(key.encode(), password=None)
        self.verify_tls = verify_tls  # telebirr's gateway certificates do not validate everywhere (their demo turns it off too)

    @classmethod
    def from_params(cls, P):
        g = lambda k: (P.get_param('saas.telebirr_%s' % k) or '').strip()
        if not all(g(k) for k in ('base_url', 'web_url', 'fabric_app_id', 'app_secret', 'merchant_app_id', 'merchant_code', 'private_key')):
            return None
        return cls(g('base_url'), g('web_url'), g('fabric_app_id'), g('app_secret'), g('merchant_app_id'), g('merchant_code'), g('private_key'))

    # ------------------------------------------------------------------ signing
    @staticmethod
    def sign_string(req):
        """The string telebirr signs: every field (and those inside biz_content) but a few, sorted, key=value&..."""
        fields = {k: v for k, v in req.items() if k not in EXCLUDE}
        fields.update({k: v for k, v in (req.get('biz_content') or {}).items() if k not in EXCLUDE})
        return '&'.join('%s=%s' % (k, fields[k]) for k in sorted(fields))

    def sign(self, req):
        sig = self.key.sign(self.sign_string(req).encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        return base64.b64encode(sig).decode()

    @staticmethod
    def nonce():
        return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(32))

    # ------------------------------------------------------------------ calls
    def token(self):
        r = requests.post(self.base_url + '/payment/v1/token', json={'appSecret': self.app_secret},
                          headers={'X-APP-Key': self.fabric_app_id}, timeout=20, verify=self.verify_tls)
        r.raise_for_status()
        return r.json()['token']

    def _call(self, path, method, biz):
        req = {'timestamp': str(int(time.time())), 'nonce_str': self.nonce(), 'method': method, 'version': '1.0', 'biz_content': biz}
        req['sign'] = self.sign(req)
        req['sign_type'] = 'SHA256WithRSA'
        r = requests.post(self.base_url + path, json=req, timeout=30, verify=self.verify_tls,
                          headers={'X-APP-Key': self.fabric_app_id, 'Authorization': self.token()})
        data = r.json()
        if data.get('result') not in (None, 'SUCCESS') and data.get('code') not in (None, '0'):
            _logger.warning('telebirr %s: %s', method, data)
        return data

    PAID = {'PAY_SUCCESS', 'COMPLETED', 'Completed', 'SUCCESS', 'TRADE_SUCCESS', 'PAID'}

    def preorder(self, order_id, title, amount, notify_url, redirect_url):
        """A telebirr order; returns its prepay_id (None and the reply when refused). telebirr refuses an order
        without a return address, and very small amounts."""
        biz = {'notify_url': notify_url, 'appid': self.merchant_app_id, 'merch_code': self.merchant_code, 'merch_order_id': order_id,
               # telebirr refuses titles with other characters than letters, digits and spaces
               'trade_type': 'Checkout', 'title': re.sub(r'[^A-Za-z0-9 ]+', ' ', title).strip()[:64],
               'total_amount': ('%.2f' % amount).rstrip('0').rstrip('.'),
               'trans_currency': 'ETB', 'timeout_express': '120m'}
        biz['redirect_url'] = redirect_url
        data = self._call('/payment/v1/merchant/preOrder', 'payment.preorder', biz)
        return (data.get('biz_content') or {}).get('prepay_id'), data

    def checkout_url(self, prepay_id):
        m = {'appid': self.merchant_app_id, 'merch_code': self.merchant_code, 'nonce_str': self.nonce(), 'prepay_id': prepay_id,
             'timestamp': str(int(time.time()))}
        sign = self.sign(m)
        raw = '&'.join('%s=%s' % (k, m[k]) for k in sorted(m))
        from urllib.parse import quote
        return '%s%s&sign=%s&sign_type=SHA256WithRSA&version=1.0&trade_type=Checkout' % (self.web_url, raw, quote(sign, safe=''))

    def query(self, order_id):
        """telebirr's own word on an order: its trade_status ('Completed' when paid)."""
        data = self._call('/payment/v1/merchant/queryOrder', 'payment.queryorder',
                          {'appid': self.merchant_app_id, 'merch_code': self.merchant_code, 'merch_order_id': order_id})
        return data.get('biz_content') or {}, data

    def is_paid(self, order_id, amount=None):
        biz, _data = self.query(order_id)
        status = biz.get('order_status') or biz.get('trade_status') or ''
        paid = status in self.PAID
        if paid and amount is not None and biz.get('total_amount'):
            paid = float(biz['total_amount']) + 0.005 >= amount
        return paid, status
