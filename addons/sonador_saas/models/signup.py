"""Sign-up in two steps: the form only reserves the web address and emails a link; the system is made when the link
is opened and the owner chooses a password there (no password is ever stored or sent before that).

Robots are kept out by: a hidden field, a signed form token (too fast or too old is refused), limits per address and
per email kept in the database, throwaway email domains, a domain that cannot receive mail, and Cloudflare Turnstile
when its keys are set (saas.turnstile_site_key / saas.turnstile_secret)."""
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import timedelta

import requests
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

TOKEN_HOURS = 48
FORM_MIN_SECONDS = 4          # a person needs longer than this to fill in the form
FORM_MAX_SECONDS = 4 * 3600
LIMITS = {'ip_hour': 5, 'ip_day': 15, 'email_day': 3, 'resend_minutes': 1, 'resend_total': 5}
DISPOSABLE = {
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com', '10minutemail.com', '10minutemail.net', 'tempmail.com',
    'temp-mail.org', 'tempmail.net', 'yopmail.com', 'yopmail.net', 'trashmail.com', 'getnada.com', 'nada.email', 'dispostable.com',
    'maildrop.cc', 'mohmal.com', 'throwawaymail.com', 'fakeinbox.com', 'mintemail.com', 'mailnesia.com', 'emailondeck.com',
    'tempail.com', 'tempr.email', 'discard.email', 'spambog.com', 'moakt.com', 'burnermail.io', 'mailpoof.com', 'inboxkitten.com',
    'mail.tm', 'tmpmail.org', 'tmpmail.net', 'linshiyouxiang.net', 'emailfake.com', 'crazymailing.com', 'mytemp.email', 'trbvm.com',
}


class SaasSignup(models.Model):
    _name = 'saas.signup'
    _description = 'Sign-up waiting for its email to be confirmed'
    _order = 'id desc'

    company = fields.Char(required=True)
    subdomain = fields.Char(required=True, index=True)
    country = fields.Char(default='ET')
    industry = fields.Char()
    plan = fields.Char()
    users = fields.Integer()
    name = fields.Char()
    email = fields.Char(required=True, index=True)
    phone = fields.Char()
    referrer_code = fields.Char()
    ip = fields.Char(index=True)
    token_hash = fields.Char(index=True)
    expires = fields.Datetime()
    state = fields.Selection([('pending', 'Waiting for the email'), ('done', 'System created'), ('expired', 'Expired')],
                             default='pending', index=True)
    sent_count = fields.Integer()
    last_sent = fields.Datetime()
    tenant_id = fields.Many2one('saas.tenant', ondelete='set null')

    # ------------------------------------------------------------------ guards
    def _secret(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.secret')

    @api.model
    def form_token(self):
        """A token the form sends back: when it was shown, signed, so robots cannot skip the form or reuse an old one."""
        ts = str(int(time.time()))
        sig = hmac.new(self._secret().encode(), ts.encode(), hashlib.sha256).hexdigest()[:32]
        P = self.env['ir.config_parameter'].sudo()
        return {'form_token': '%s.%s' % (ts, sig), 'turnstile_site_key': P.get_param('saas.turnstile_site_key') or ''}

    def _check_form_token(self, token):
        try:
            ts, sig = (token or '').split('.')
            good = hmac.new(self._secret().encode(), ts.encode(), hashlib.sha256).hexdigest()[:32]
            age = time.time() - int(ts)
        except ValueError:
            age, sig, good = -1, 'x', 'y'
        if not hmac.compare_digest(sig, good) or age > FORM_MAX_SECONDS:
            raise ValidationError(self.env._('The form has expired: reload the page and try again.'))
        if age < FORM_MIN_SECONDS:
            raise ValidationError(self.env._('That was quick! Please check the form and send it again.'))

    def _check_turnstile(self, response, ip):
        secret = self.env['ir.config_parameter'].sudo().get_param('saas.turnstile_secret')
        if not secret:
            return
        try:
            r = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', timeout=10,
                              data={'secret': secret, 'response': response or '', 'remoteip': ip}).json()
        except requests.RequestException:
            _logger.warning('Turnstile could not be reached; sign-up let through on the other checks')
            return
        if not r.get('success'):
            raise ValidationError(self.env._('Please confirm you are not a robot.'))

    def _check_limits(self, ip, email):
        now = fields.Datetime.now()
        S = self.sudo()
        if S.search_count([('ip', '=', ip), ('create_date', '>', now - timedelta(hours=1))]) >= LIMITS['ip_hour'] or \
                S.search_count([('ip', '=', ip), ('create_date', '>', now - timedelta(days=1))]) >= LIMITS['ip_day']:
            raise ValidationError(self.env._('Too many sign-ups from your network; please try again later.'))
        if S.search_count([('email', '=', email), ('create_date', '>', now - timedelta(days=1))]) >= LIMITS['email_day']:
            raise ValidationError(self.env._('This email was used for several sign-ups today: look for our email, or try again tomorrow.'))

    def _check_email(self, email):
        if not re.fullmatch(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}", email):
            raise ValidationError(self.env._('Enter a valid email address.'))
        domain = email.split('@')[1]
        if domain in DISPOSABLE or any(domain.endswith('.' + d) for d in DISPOSABLE):
            raise ValidationError(self.env._('Please use your work or personal email, not a temporary one.'))
        if not self._domain_takes_mail(domain):
            raise ValidationError(self.env._('The email domain "%s" cannot receive mail: check the address.', domain))

    def _domain_takes_mail(self, domain):
        """Does the domain have a mail server (MX) or at least an address? Asked by DNS over HTTPS; if DNS cannot be asked, yes."""
        if self.env['ir.config_parameter'].sudo().get_param('saas.skip_mx_check'):
            return True
        try:
            for rtype in ('MX', 'A'):
                r = requests.get('https://cloudflare-dns.com/dns-query', params={'name': domain, 'type': rtype}, timeout=6,
                                 headers={'accept': 'application/dns-json'}).json()
                if r.get('Status') == 3:  # NXDOMAIN: the domain does not exist
                    return False
                if r.get('Answer'):
                    return True
            return False
        except (requests.RequestException, ValueError):
            return True

    # ------------------------------------------------------------------ step 1: the form
    @api.model
    def start(self, vals, ip):
        """Check everything, reserve the address, email the link. Nothing is created yet."""
        if vals.get('website'):  # the hidden field only robots fill in
            raise ValidationError(self.env._('Rejected.'))
        self._check_form_token(vals.get('form_token'))
        self._check_turnstile(vals.get('turnstile'), ip)
        T = self.env['saas.tenant'].sudo()
        company = (vals.get('company') or '').strip()[:120]
        email = (vals.get('email') or '').strip().lower()
        country = (vals.get('country') or 'ET').upper()
        if not company:
            raise ValidationError(self.env._('Enter the company name.'))
        self._check_email(email)
        if country not in ('ET', 'UG', 'KE'):
            raise ValidationError(self.env._('That country is not available yet.'))
        self._check_limits(ip, email)
        sub = T.clean_subdomain(vals.get('subdomain') or company)
        if not self.address_free(sub, email):
            raise ValidationError(self.env._('The web address "%s" is taken; choose another.', sub))
        # a new attempt for the same address and email replaces the one before
        self.sudo().search([('subdomain', '=', sub), ('email', '=', email), ('state', '=', 'pending')]).write({'state': 'expired'})
        token = secrets.token_urlsafe(32)
        rec = self.sudo().create({
            'company': company, 'subdomain': sub, 'country': country, 'industry': vals.get('industry') or 'other',
            'plan': vals.get('plan') or 'standard', 'users': int(vals.get('users') or 1), 'name': (vals.get('name') or '')[:120],
            'email': email, 'phone': (vals.get('phone') or '')[:40], 'referrer_code': vals.get('referrer_code'), 'ip': ip,
            'token_hash': self._hash(token), 'expires': fields.Datetime.now() + timedelta(hours=TOKEN_HOURS)})
        if self.env['ir.config_parameter'].sudo().get_param('saas.confirm_email', 'True') == 'True':
            rec._send(token)
            return {'pending': True, 'email': email, 'subdomain': sub, 'signup_id': rec.id}
        # email confirmation switched off (until outgoing mail works): straight on to choosing the password
        return {'pending': True, 'email': email, 'subdomain': sub, 'signup_id': rec.id, 'token': token}

    @api.model
    def address_free(self, sub, email=None):
        from .provision import _db_exists
        if self.env['saas.tenant'].sudo().search_count([('subdomain', '=', sub)]) or _db_exists(sub):
            return False
        held = self.sudo().search([('subdomain', '=', sub), ('state', '=', 'pending'), ('expires', '>', fields.Datetime.now())])
        return not held or (email and all(h.email == email for h in held))

    def _hash(self, token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _link(self, token):
        site = self.env['ir.config_parameter'].sudo().get_param('saas.site_url') or 'https://unio.et'
        return '%s/verify.html?t=%s' % (site.rstrip('/'), token)

    def _send(self, token):
        self.ensure_one()
        P = self.env['ir.config_parameter'].sudo()
        sender = P.get_param('saas.mail_from') or 'Unio <noreply@unio.et>'
        link = self._link(token)
        name = escape(self.name or '')
        body = Markup('''<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;color:#1B2240">
<div style="font-size:24px;font-weight:800;letter-spacing:-.3px;margin:8px 0 18px"><img src="%s/assets/unio_mail.png" width="32" height="23" alt="" style="vertical-align:-3px;margin-right:6px"/>unio</div>
<p style="font-size:16px">Hello %s,</p>
<p style="font-size:15px;line-height:1.5">Confirm your email to create <b>%s</b> at <b>%s</b>. You will choose your password on the next page.</p>
<p style="margin:26px 0"><a href="%s" style="background:#2D6BFF;color:#fff;text-decoration:none;padding:13px 22px;border-radius:10px;font-weight:700;display:inline-block">Confirm and create my system</a></p>
<p style="font-size:13px;color:#6B7286;line-height:1.5">The link works for %d hours. If you did not ask for this, ignore this email: nothing is created without it.</p>
<p style="font-size:12px;color:#9AA0B2;word-break:break-all">%s</p></div>''') % (
            (P.get_param('saas.site_url') or 'https://unio.et').rstrip('/'), name or 'there', escape(self.company), escape('%s.%s' % (self.subdomain, P.get_param('saas.domain') or 'unio.et')), link, TOKEN_HOURS, link)
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Confirm your email to create %s' % self.company, 'email_from': sender, 'email_to': self.email,
            'body_html': body, 'auto_delete': True})
        mail.send(raise_exception=False)
        self.write({'sent_count': self.sent_count + 1, 'last_sent': fields.Datetime.now()})

    @api.model
    def resend(self, email, subdomain):
        rec = self.sudo().search([('email', '=', (email or '').strip().lower()), ('subdomain', '=', subdomain), ('state', '=', 'pending'),
                                  ('expires', '>', fields.Datetime.now())], limit=1)
        if not rec:
            raise ValidationError(self.env._('This sign-up has expired: please fill in the form again.'))
        if rec.sent_count >= LIMITS['resend_total'] or (rec.last_sent and fields.Datetime.now() - rec.last_sent < timedelta(minutes=LIMITS['resend_minutes'])):
            raise ValidationError(self.env._('We just sent it: please wait a minute and look in your spam folder too.'))
        token = secrets.token_urlsafe(32)
        rec.token_hash = self._hash(token)  # the earlier link stops working
        rec._send(token)
        return {'pending': True, 'email': rec.email}

    # ------------------------------------------------------------------ step 2: the link
    def _by_token(self, token):
        rec = self.sudo().search([('token_hash', '=', self._hash(token or '')), ('state', 'in', ('pending', 'done'))], limit=1)
        if not rec or (rec.state == 'pending' and rec.expires < fields.Datetime.now()):
            raise ValidationError(self.env._('This link has expired or was replaced by a newer one. Please sign up again.'))
        return rec

    @api.model
    def peek(self, token):
        rec = self._by_token(token)
        out = {'company': rec.company, 'subdomain': rec.subdomain, 'email': rec.email, 'done': rec.state == 'done'}
        if rec.state == 'done' and rec.tenant_id:
            out['url'] = rec.tenant_id.url
        return out

    @api.model
    def confirm(self, token, password):
        """The email is confirmed: make the system with the password chosen now."""
        rec = self._by_token(token)
        if rec.state == 'done':
            return {'url': rec.tenant_id.url, 'subdomain': rec.subdomain}
        if len(password or '') < 8:
            raise ValidationError(self.env._('Choose a password of at least 8 characters.'))
        if not self.address_free(rec.subdomain, rec.email):
            raise ValidationError(self.env._('The web address "%s" was taken in the meantime. Please sign up again with another.', rec.subdomain))
        res = self.env['saas.service'].sudo().signup({
            'company': rec.company, 'subdomain': rec.subdomain, 'country': rec.country, 'email': rec.email, 'password': password,
            'name': rec.name, 'phone': rec.phone, 'industry': rec.industry, 'users': rec.users, 'plan': rec.plan,
            'referrer_code': rec.referrer_code, '_verified': True})
        rec.write({'state': 'done', 'tenant_id': res['tenant_id']})
        return res

    @api.autovacuum
    def _gc_expired(self):
        self.sudo().search([('state', '=', 'pending'), ('expires', '<', fields.Datetime.now())]).write({'state': 'expired'})
        self.sudo().search([('state', '=', 'expired'), ('create_date', '<', fields.Datetime.now() - timedelta(days=30))]).unlink()
