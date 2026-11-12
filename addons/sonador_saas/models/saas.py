"""The cloud service's own records: plans (price per user, progressive discount, trial), customer companies (one
database each) and their monthly user counts. Monthly invoices are ordinary sales invoices in these books."""
import re
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

COUNTRIES = [('ET', 'Ethiopia'), ('UG', 'Uganda'), ('KE', 'Kenya')]
CURRENCY = {'ET': 'ETB', 'UG': 'UGX', 'KE': 'KES'}
STATES = [('trial', 'Trial'), ('active', 'Active'), ('overdue', 'Payment overdue'), ('suspended', 'Suspended (read-only)'),
          ('cancelled', 'Cancelled'), ('failed', 'Sign-up failed')]
RESERVED = {'www', 'my', 'app', 'api', 'admin', 'mail', 'help', 'docs', 'status', 'blog', 'support', 'sales', 'billing', 'test',
            'demo', 'static', 'cdn', 'postgres', 'template0', 'template1'}


class SaasPlan(models.Model):
    _name = 'saas.plan'
    _description = 'Subscription plan'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    price_usd = fields.Float('Price per user (USD)', default=5.99)
    price_etb = fields.Float('Price per user (ETB)', default=999)
    price_ugx = fields.Float('Price per user (UGX)', default=21900)
    price_kes = fields.Float('Price per user (KES)', default=775)
    discount_step = fields.Float('Discount per extra user (%)', default=5.0)
    discount_cap = fields.Float('Largest discount (%)', default=30.0)
    free_users = fields.Integer('Users free of charge', default=0, help='Free plan: this many users at no charge.')
    max_users = fields.Integer('Most users (0 = no limit)')
    trial_days = fields.Integer(default=30)
    light_users_free = fields.Boolean('Self-service users free', default=True,
                                      help='Employees who only request leave, see payslips, claim expenses or approve are not charged.')
    note = fields.Text()

    def price_for(self, users, currency='USD'):
        """Per-user price and total for a number of paid users: every user gets step % off per user after the first,
        never more than the cap."""
        self.ensure_one()
        base = {'USD': self.price_usd, 'ETB': self.price_etb, 'UGX': self.price_ugx, 'KES': self.price_kes}.get(currency, self.price_usd)
        n = max(int(users) - self.free_users, 0)
        if n <= 0:
            return {'users': 0, 'each': 0.0, 'total': 0.0, 'discount': 0.0, 'currency': currency}
        disc = min(self.discount_step * (n - 1), self.discount_cap)
        each = base * (1 - disc / 100.0)
        digits = 2 if currency == 'USD' else 0
        each = round(each, digits)
        return {'users': n, 'each': each, 'total': round(each * n, digits), 'discount': disc, 'currency': currency}


class SaasTenant(models.Model):
    _name = 'saas.tenant'
    _description = 'Customer company (cloud)'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    name = fields.Char('Company', required=True, tracking=True)
    subdomain = fields.Char(required=True, index=True, copy=False, help='Its web address and database name: <subdomain>.<domain>.')
    country = fields.Selection(COUNTRIES, required=True, default='ET')
    currency = fields.Char(compute='_compute_currency', store=True)
    plan_id = fields.Many2one('saas.plan', required=True, ondelete='restrict', tracking=True)
    partner_id = fields.Many2one('res.partner', 'Billed to', tracking=True)
    referrer_id = fields.Many2one('res.partner', 'Accountant / partner', help='Earns the partner commission on this subscription.')
    contact_name = fields.Char()
    email = fields.Char(required=True)
    phone = fields.Char()
    industry = fields.Selection([('manufacturing', 'Manufacturing'), ('trading', 'Trading and import'), ('services', 'Services'),
                                 ('construction', 'Construction'), ('retail', 'Retail'), ('school', 'School'), ('other', 'Other')], default='other')
    state = fields.Selection(STATES, default='trial', required=True, tracking=True, index=True)
    trial_end = fields.Date(tracking=True)
    paid_users = fields.Integer('Paid users', readonly=True)
    light_users = fields.Integer('Self-service users', readonly=True)
    counted_on = fields.Datetime(readonly=True)
    monthly_amount = fields.Float(compute='_compute_amount', string='Monthly amount')
    url = fields.Char(compute='_compute_url')
    usage_ids = fields.One2many('saas.usage', 'tenant_id')
    invoice_ids = fields.One2many('sb.voucher', 'saas_tenant_id', string='Invoices')
    note = fields.Text()

    _uniq_sub = models.Constraint('unique(subdomain)', 'That web address is taken.')

    @api.depends('country')
    def _compute_currency(self):
        for t in self:
            t.currency = CURRENCY.get(t.country, 'USD')

    @api.depends('plan_id', 'paid_users', 'currency')
    def _compute_amount(self):
        for t in self:
            t.monthly_amount = t.plan_id.price_for(t.paid_users, t.currency)['total'] if t.plan_id else 0.0

    def _compute_url(self):
        domain = self.env['ir.config_parameter'].sudo().get_param('saas.domain', 'unio.localhost:8569')
        scheme = 'http' if 'localhost' in domain else 'https'
        for t in self:
            t.url = '%s://%s.%s' % (scheme, t.subdomain, domain)

    @api.model
    def clean_subdomain(self, text):
        s = re.sub(r'[^a-z0-9]+', '', (text or '').lower())[:30]
        if len(s) < 3:
            raise ValidationError(self.env._('The web address needs at least 3 letters or digits.'))
        if s in RESERVED or s.startswith('tpl'):
            raise ValidationError(self.env._('"%s" is reserved; choose another web address.', s))
        return s


class SaasUsage(models.Model):
    _name = 'saas.usage'
    _description = 'Users counted'
    _order = 'date desc'

    tenant_id = fields.Many2one('saas.tenant', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True)
    paid_users = fields.Integer()
    light_users = fields.Integer()


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    saas_tenant_id = fields.Many2one('saas.tenant', 'Cloud subscription', index=True, copy=False)
