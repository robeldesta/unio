"""Paying the subscription with telebirr: a QR for the exact amount, made from the merchant's own telebirr QR (EMVCo
format: only the amount and the checksum change), and the payment recorded with its telebirr transaction number.
The payment is confirmed automatically when telebirr's public receipt shows it (to us, for the amount, not used
before), otherwise with one click in the Cloud app. telebirr's merchant API (paid notices) plugs in here later."""
import base64
import io
import logging
import re

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


def _tlv(tag, value):
    return '%s%02d%s' % (tag, len(value), value)


def _parse(payload):
    out, i = [], 0
    while i + 4 <= len(payload):
        tag, ln = payload[i:i + 2], int(payload[i + 2:i + 4])
        out.append((tag, payload[i + 4:i + 4 + ln]))
        i += 4 + ln
    return out


def _crc16(data):
    crc = 0xFFFF
    for b in data.encode():
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return '%04X' % crc


def emv_with_amount(template, amount):
    """The merchant's QR payload with this amount (tag 54) and a new checksum (tag 63)."""
    fields_ = [(t, v) for t, v in _parse(template) if t not in ('54', '63')]
    amt = ('%.2f' % amount).rstrip('0').rstrip('.')
    out, placed = '', False
    for t, v in fields_:
        if not placed and t > '54':
            out += _tlv('54', amt)
            placed = True
        out += _tlv(t, v)
    if not placed:
        out += _tlv('54', amt)
    out += '6304'
    return out + _crc16(out)


def emv_info(payload):
    """What a payer sees: the merchant's name and its account (telebirr short code)."""
    d = dict(_parse(payload))
    acc = dict(_parse(d.get('28', '')))
    return {'name': d.get('59', ''), 'city': d.get('60', ''), 'code': acc.get('02', ''), 'network': acc.get('01', ''),
            'valid': _crc16(payload[:-4]) == payload[-4:]}


class SaasPayment(models.Model):
    _name = 'saas.payment'
    _description = 'Subscription payment'
    _order = 'id desc'
    _inherit = ['mail.thread']

    tenant_id = fields.Many2one('saas.tenant', required=True, ondelete='cascade', index=True)
    amount = fields.Float(required=True)
    currency = fields.Char(default='ETB')
    users = fields.Integer()
    months = fields.Integer(default=1, help='Months the payment covers (12 for a year).')
    credits_used = fields.Integer(help='Free months (earned by referrals) this payment uses.')
    method = fields.Selection([('telebirr', 'telebirr'), ('chapa', 'Chapa'), ('bank', 'Bank transfer')], default='telebirr', required=True)
    reference = fields.Char('Transaction number', index=True)
    payer = fields.Char()
    state = fields.Selection([('waiting', 'To check'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected')], default='waiting', tracking=True, index=True)
    checked = fields.Char('Check', help='What the automatic check of the telebirr receipt found.')
    confirmed_by = fields.Selection([('receipt', 'telebirr receipt'), ('person', 'By hand'), ('api', 'telebirr notice'), ('chapa', 'Chapa')])

    prepay_id = fields.Char(readonly=True, help='telebirr checkout order')

    _uniq_ref = models.Constraint('unique(method, reference)', 'This transaction number was already used.')

    # ------------------------------------------------------------------ telebirr checkout (merchant API)
    def _api(self):
        from .telebirr import Telebirr
        return Telebirr.from_params(self.env['ir.config_parameter'].sudo())

    @api.model
    def checkout(self, subdomain, email, users, period='monthly'):
        """Start a payment on telebirr's own checkout page; returns its address."""
        tb = self._api()
        if not tb:
            raise UserError(self.env._('Online payment is not switched on yet: scan the QR code instead.'))
        t = self.env['saas.tenant'].sudo().search([('subdomain', '=', (subdomain or '').strip().lower())], limit=1)
        if not t or (t.email or '').lower() != (email or '').strip().lower():
            raise ValidationError(self.env._('Use the email you signed up with (the owner of %s).', subdomain or 'the system'))
        if t.currency != 'ETB':
            raise ValidationError(self.env._('telebirr takes payments in birr; pay by bank transfer instead.'))
        users = max(int(users or 1), 1)
        price = t.price_for_period(users, period)
        pay = self.sudo().create({'tenant_id': t.id, 'amount': price['total'], 'currency': 'ETB', 'users': users, 'method': 'telebirr',
                                  'months': price.get('months', 1), 'credits_used': price.get('credits_used', 0), 'payer': email, 'reference': 'UNIO%s%d' % (fields.Datetime.now().strftime('%y%m%d%H%M%S'), t.id)})
        P = self.env['ir.config_parameter'].sudo()
        api_root = P.get_param('saas.api_url') or 'https://my.unio.et'
        site = (P.get_param('saas.site_url') or 'https://unio.et').rstrip('/')
        prepay, data = tb.preorder(pay.reference, 'Unio subscription %s (%s users)' % (t.name, users), price['total'],
                                   '%s/unio/telebirr/notify' % api_root, '%s/subscribe.html?o=%s' % (site, pay.reference))
        if not prepay:
            pay.write({'state': 'rejected', 'checked': ('telebirr refused the order: %s' % (data.get('msg') or data))[:200]})
            raise UserError(self.env._('telebirr could not start the payment just now. Scan the QR code instead, or try again in a minute.'))
        pay.prepay_id = prepay
        return {'url': tb.checkout_url(prepay), 'order': pay.reference}

    def _chapa(self):
        from .chapa import Chapa
        return Chapa.from_params(self.env['ir.config_parameter'].sudo())

    @api.model
    def checkout_chapa(self, subdomain, email, users, period='monthly'):
        """Start a payment on Chapa's page (telebirr, CBE Birr, M-Pesa, cards...); returns its address."""
        ch = self._chapa()
        if not ch:
            raise UserError(self.env._('Online payment is not switched on yet: scan the QR code instead.'))
        t = self.env['saas.tenant'].sudo().search([('subdomain', '=', (subdomain or '').strip().lower())], limit=1)
        if not t or (t.email or '').lower() != (email or '').strip().lower():
            raise ValidationError(self.env._('Use the email you signed up with (the owner of %s).', subdomain or 'the system'))
        users = max(int(users or 1), 1)
        price = t.price_for_period(users, period)
        cur = t.currency if t.currency in ('ETB', 'USD') else 'USD'
        if cur != t.currency:
            price = dict(t.price_for_period(users, period), total=t.plan_id.price_for(users, 'USD')['total'] * (price.get('months') == 12 and 10 or 1))
        pay = self.sudo().create({'tenant_id': t.id, 'amount': price['total'], 'currency': cur, 'users': users, 'method': 'chapa',
                                  'months': price.get('months', 1), 'credits_used': price.get('credits_used', 0), 'payer': email, 'reference': 'unio-%s-%s' % (t.subdomain[:20], fields.Datetime.now().strftime('%y%m%d%H%M%S'))})
        P = self.env['ir.config_parameter'].sudo()
        api_root = P.get_param('saas.api_url') or 'https://my.unio.et'
        site = (P.get_param('saas.site_url') or 'https://unio.et').rstrip('/')
        name = (t.contact_name or t.name or '').split()
        url, data = ch.initialize(pay.reference, price['total'], cur, t.email, name[0] if name else t.name, ' '.join(name[1:]) or t.name,
                                  '%s/unio/chapa/notify' % api_root, '%s/subscribe.html?o=%s' % (site, pay.reference),
                                  'Unio', 'Unio subscription %s users' % users)
        if not url:
            pay.write({'state': 'rejected', 'checked': ('Chapa refused: %s' % (data.get('message') or data))[:200]})
            raise UserError(self.env._('The payment page could not be opened just now. Scan the QR code instead, or try again in a minute.'))
        pay.prepay_id = 'chapa'
        return {'url': url, 'order': pay.reference}

    @api.model
    def settle(self, order):
        pay = self.sudo().search([('reference', '=', order or ''), ('method', '=', 'chapa')], limit=1)
        if pay:
            return self._settle_chapa(pay)
        return self._settle_telebirr(order)

    def _settle_chapa(self, pay):
        if pay.state == 'waiting':
            ch = self._chapa()
            if ch:
                try:
                    tx, _data = ch.verify(pay.reference)
                except Exception as e:  # noqa: BLE001  Chapa unreachable: asked again later
                    tx = {'status': 'unreachable (%s)' % type(e).__name__}
                status = tx.get('status') or 'unknown'
                pay.checked = 'Chapa: %s%s' % (status, (' by %s' % tx.get('method')) if tx.get('method') else '')
                if status == 'success' and float(tx.get('amount') or 0) + 0.005 >= pay.amount and (tx.get('currency') or pay.currency) == pay.currency:
                    pay.write({'state': 'confirmed', 'confirmed_by': 'chapa'})
                    pay._activate()
        return {'state': pay.state, 'company': pay.tenant_id.name}

    @api.model
    def _settle_telebirr(self, order):
        """Ask telebirr about an order (after its notice, or when the payer comes back) and confirm it when paid."""
        pay = self.sudo().search([('method', '=', 'telebirr'), ('reference', '=', order or '')], limit=1)
        if not pay:
            return {'state': 'unknown'}
        if pay.state == 'waiting' and pay.prepay_id:
            tb = self._api()
            if tb:
                try:
                    paid, status = tb.is_paid(pay.reference, pay.amount)
                except Exception as e:  # noqa: BLE001  telebirr unreachable: stays waiting, asked again later
                    paid, status = False, 'unreachable (%s)' % type(e).__name__
                pay.checked = 'telebirr: %s' % status
                if paid:
                    pay.write({'state': 'confirmed', 'confirmed_by': 'api'})
                    pay._activate()
        return {'state': pay.state, 'company': pay.tenant_id.name}

    @api.model
    def _cron_settle(self):
        """Payments started on telebirr in the last two days that are still open: ask telebirr again."""
        for p in self.sudo().search([('state', '=', 'waiting'), ('prepay_id', '!=', False),  # telebirr and Chapa orders
                                     ('create_date', '>', fields.Datetime.now() - __import__('datetime').timedelta(days=2))]):
            self.settle(p.reference)

    # ------------------------------------------------------------------ QR
    @api.model
    def qr_for(self, subdomain, users, period='monthly'):
        """The telebirr QR for the first month of a customer company (ETB only)."""
        P = self.env['ir.config_parameter'].sudo()
        template = P.get_param('saas.telebirr_qr')
        t = self.env['saas.tenant'].sudo().search([('subdomain', '=', (subdomain or '').strip().lower())], limit=1)
        if not t:
            raise ValidationError(self.env._('Open this page from the "Subscribe" button in your system.'))
        users = max(int(users or 1), 1)
        price = t.price_for_period(users, period)
        out = {'company': t.name, 'users': users, 'total': price['total'], 'currency': t.currency, 'notes': price.get('notes', []),
               'period': price.get('period', 'monthly'), 'saving': price.get('saving', 0), 'monthly': price.get('monthly'),
               'full': price.get('full'), 'free_month': price.get('free_month', False),
               'bank': P.get_param('saas.bank_details') or '', 'checkout': bool(self._api()) and t.currency == 'ETB',
               'chapa': bool(self._chapa())}
        if template and t.currency == 'ETB' and price['total']:
            import qrcode
            payload = emv_with_amount(template, price['total'])
            img = qrcode.make(payload, box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            out.update(qr='data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode(), merchant=emv_info(payload))
        return out

    # ------------------------------------------------------------------ the payer says it is paid
    @api.model
    def report(self, subdomain, email, users, reference, period='monthly'):
        T = self.env['saas.tenant'].sudo()
        t = T.search([('subdomain', '=', (subdomain or '').strip().lower())], limit=1)
        if not t or (t.email or '').lower() != (email or '').strip().lower():
            raise ValidationError(self.env._('Use the email you signed up with (the owner of %s).', subdomain or 'the system'))
        ref = re.sub(r'[^A-Za-z0-9]', '', reference or '').upper()
        if not 6 <= len(ref) <= 20:
            raise ValidationError(self.env._('Enter the transaction number from the telebirr message (letters and digits, e.g. CJK7AB12CD).'))
        if self.sudo().search_count([('method', '=', 'telebirr'), ('reference', '=', ref)]):
            raise ValidationError(self.env._('This transaction number was already sent.'))
        users = max(int(users or 1), 1)
        price = t.price_for_period(users, period)
        pay = self.sudo().create({'tenant_id': t.id, 'amount': price['total'], 'currency': t.currency, 'users': users,
                                  'method': 'telebirr', 'reference': ref, 'payer': email,
                                  'months': price.get('months', 1), 'credits_used': price.get('credits_used', 0)})
        pay._check_receipt()
        t.message_post(body=self.env._('telebirr payment reported: %(r)s for %(a)s %(c)s (%(n)s users). %(s)s',
                                       r=ref, a=price['total'], c=t.currency, n=users, s=dict(pay._fields['state'].selection)[pay.state]))
        sales = self.env['ir.config_parameter'].sudo().get_param('saas.sales_email')
        if sales and pay.state == 'waiting':
            self.env['mail.mail'].sudo().create({
                'subject': 'telebirr payment to check: %s %s %s' % (t.name, price['total'], t.currency), 'email_to': sales,
                'email_from': self.env['ir.config_parameter'].sudo().get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
                'body_html': '<p>%s reports telebirr transaction <b>%s</b> for %s %s (%s users). Confirm it in Cloud, Payments.</p>' % (
                    t.name, ref, price['total'], t.currency, users)}).send(raise_exception=False)
        return {'state': pay.state, 'company': t.name}

    def _check_receipt(self):
        """telebirr's public receipt for the transaction: paid to us, at least the amount -> confirmed."""
        self.ensure_one()
        P = self.env['ir.config_parameter'].sudo()
        url = (P.get_param('saas.telebirr_receipt_url') or 'https://transactioninfo.ethiotelecom.et/receipt/%s') % self.reference
        template = P.get_param('saas.telebirr_qr') or ''
        info = emv_info(template) if template else {}
        try:
            html = requests.get(url, timeout=12).text
        except requests.RequestException as e:
            self.checked = 'Receipt not reachable (%s): check by hand.' % type(e).__name__
            return False
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        to_us = bool(info.get('name')) and info['name'].split()[0].lower() in text.lower() or (info.get('code') and info['code'] in text)
        amounts = [float(a.replace(',', '')) for a in re.findall(r'(\d[\d,]*\.\d{2})\s*(?:Birr|ETB)', text, re.I)]
        if to_us and amounts and max(amounts) + 0.005 >= self.amount:
            self.write({'checked': 'Receipt shows %.2f ETB to %s.' % (max(amounts), info.get('name')), 'state': 'confirmed', 'confirmed_by': 'receipt'})
            self._activate()
            return True
        self.checked = 'Receipt %s: %s' % ('found but not matching' if amounts else 'not found', ', '.join('%.2f' % a for a in amounts))[:200]
        return False

    # ------------------------------------------------------------------ in the Cloud app
    def action_confirm(self):
        for p in self.filtered(lambda x: x.state != 'confirmed'):
            p.write({'state': 'confirmed', 'confirmed_by': p.confirmed_by or 'person'})
            p._activate()
        return True

    def action_reject(self):
        self.filtered(lambda x: x.state == 'waiting').write({'state': 'rejected'})
        return True

    def _activate(self):
        """Paid: the company is subscribed (and no longer read-only if its trial had ended)."""
        S = self.env['saas.service'].sudo()
        for p in self:
            t = p.tenant_id
            if t.state in ('trial', 'suspended', 'overdue'):
                t.state = 'active'
                S._set_lock(t, False)
            if p.users:
                t.subscribed_users = p.users
            t._month_paid(p.months or 1, p.credits_used or 0)
            t.message_post(body=self.env._('Payment confirmed: %(a)s %(c)s (%(r)s). Subscription active.', a=p.amount, c=p.currency, r=p.reference))
            # into the company books (Sonador Systems' own Unio system); a problem there never holds up the customer
            try:
                with self.env.cr.savepoint():
                    number = self.env['saas.books'].book_payment(p)
                if number:
                    t.message_post(body=self.env._('Booked in the company books as %s.', number))
            except Exception as e:  # noqa: BLE001
                t.message_post(body=self.env._('Not booked in the company books yet: %s', e))
