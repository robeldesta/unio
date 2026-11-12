"""Chapa: one checkout for telebirr, CBE Birr, M-Pesa, other wallets and cards. We start a transaction, the payer
pays on Chapa's page, Chapa calls our callback, and we ask Chapa's verify API before believing anything.
Setting: saas.chapa_secret_key (CHASECK_TEST-... in test mode, CHASECK-... live)."""
import requests

API = 'https://api.chapa.co/v1'


class Chapa:
    def __init__(self, secret_key):
        self.headers = {'Authorization': 'Bearer %s' % secret_key, 'Content-Type': 'application/json'}

    @classmethod
    def from_params(cls, P):
        key = (P.get_param('saas.chapa_secret_key') or '').strip()
        return cls(key) if key else None

    def initialize(self, tx_ref, amount, currency, email, first_name, last_name, callback_url, return_url, title, description):
        r = requests.post(API + '/transaction/initialize', headers=self.headers, timeout=30, json={
            'amount': ('%.2f' % amount), 'currency': currency, 'email': email, 'first_name': first_name[:50] or 'Customer',
            'last_name': last_name[:50] or 'Unio', 'tx_ref': tx_ref, 'callback_url': callback_url, 'return_url': return_url,
            'customization': {'title': title[:16], 'description': description[:50]}})
        data = r.json()
        return ((data.get('data') or {}).get('checkout_url') if data.get('status') == 'success' else None), data

    def verify(self, tx_ref):
        r = requests.get(API + '/transaction/verify/%s' % tx_ref, headers=self.headers, timeout=30)
        data = r.json()
        return data.get('data') or {}, data
