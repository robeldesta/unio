"""Chapa checkout for a shop: telebirr, CBE Birr, M-Pesa, other wallets and cards on one page. The key is the shop's
own (Website > Shop and contact), so the money goes straight to the shop's Chapa account; Unio never holds it.
We start a transaction, the buyer pays on Chapa's page, and we ask Chapa's verify API before believing anything."""
import re

import requests

API = 'https://api.chapa.co/v1'


def _clean(text, n):
    """Chapa accepts letters, digits, spaces and a few signs in titles and descriptions."""
    return re.sub(r'[^A-Za-z0-9 .,_-]+', ' ', text or '').strip()[:n] or 'Order'


class Chapa:
    def __init__(self, secret_key):
        self.key = (secret_key or '').strip()
        self.headers = {'Authorization': 'Bearer %s' % self.key, 'Content-Type': 'application/json'}

    @property
    def test(self):
        return 'TEST' in self.key.upper()

    def check(self):
        """True when Chapa accepts the key (listing the banks needs a valid secret key)."""
        try:
            r = requests.get(API + '/banks', headers=self.headers, timeout=20)
            return r.status_code == 200
        except Exception:
            return False

    def initialize(self, tx_ref, amount, email, first_name, last_name, phone, callback_url, return_url, title, description):
        body = {'amount': '%.2f' % amount, 'currency': 'ETB', 'tx_ref': tx_ref, 'callback_url': callback_url, 'return_url': return_url,
                'first_name': (first_name or 'Customer')[:50], 'last_name': (last_name or '-')[:50],
                'customization': {'title': _clean(title, 16), 'description': _clean(description, 50)}}
        if email:
            body['email'] = email
        digits = re.sub(r'\D', '', phone or '')
        if len(digits) >= 9:
            body['phone_number'] = '0' + digits[-9:]
        r = requests.post(API + '/transaction/initialize', headers=self.headers, timeout=30, json=body)
        data = r.json()
        return ((data.get('data') or {}).get('checkout_url') if data.get('status') == 'success' else None), data

    def verify(self, tx_ref):
        r = requests.get(API + '/transaction/verify/%s' % tx_ref, headers=self.headers, timeout=30)
        data = r.json()
        return data.get('data') or {}, data
