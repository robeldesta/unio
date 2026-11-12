"""Paying by the month or by the year ("pay 10 months, get 12"), the date a company is paid until, and what happens
around it: reminders before the end, overdue after it, read-only after a grace period (nothing is ever deleted).

Settings: saas.annual_pay_months (10), saas.renew_grace_days (14)."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class SaasTenantBilling(models.Model):
    _inherit = 'saas.tenant'

    billing = fields.Selection([('monthly', 'Monthly'), ('yearly', 'Yearly')], default='monthly', tracking=True)
    paid_until = fields.Date(tracking=True, help='The subscription is paid up to this date.')
    renew_reminded = fields.Date(readonly=True, help='Last renewal reminder sent.')

    def _extend(self, months):
        today = fields.Date.context_today(self)
        for t in self:
            start = t.paid_until if t.paid_until and t.paid_until >= today else today
            t.paid_until = start + relativedelta(months=months)
            if months >= 12:
                t.billing = 'yearly'
            elif months == 1 and t.billing != 'yearly':
                t.billing = 'monthly'

    def price_for_period(self, users, period='monthly'):
        """What the company pays for a month or a year, our VAT added on top for a company in our own country
        (prices are before VAT: 999 a user is 1,148.85 with 15% VAT)."""
        return self._with_vat(self._price_for_period_net(users, period))

    def _with_vat(self, p):
        self.ensure_one()
        company = self.env.company
        tax = company.account_sale_tax_id
        rate = tax.amount if tax and tax.amount_type == 'percent' and not tax.price_include else 0.0
        if self.country != (company.country_id.code or 'ET') or not rate or not p.get('total'):
            p.update(net=p.get('total', 0.0), vat=0.0, vat_rate=0.0)
            return p
        net = p['total']
        vat = round(net * rate / 100.0, 2)
        p.update(net=net, vat=vat, vat_rate=rate, total=round(net + vat, 2))
        p.setdefault('notes', []).append('%g%% VAT %s included' % (rate, '{:,.2f}'.format(vat)))
        return p

    def _price_for_period_net(self, users, period='monthly'):
        """What the company pays for a month, or for a year (pay 10, get 12), with referral discounts and free months."""
        self.ensure_one()
        if period != 'yearly':
            p = self.price_now(users)
            p.update(period='monthly', months=1, credits_used=1 if p.get('free_month') else 0)
            return p
        base = dict(self.plan_id.price_for(users, self.currency))
        full = base['total']
        digits = 2 if self.currency == 'USD' else 0
        s = self._ref_settings()
        pay_months = int(self.env['ir.config_parameter'].sudo().get_param('saas.annual_pay_months', '10'))
        credits = min(self.credit_months, pay_months)
        paying = pay_months - credits
        disc_months = min(max(s['months'] - self.paid_months, 0), paying) if self.referred_by_id and s['pct'] else 0
        total = round(full * paying - full * s['pct'] / 100.0 * disc_months, digits)
        year = round(full * 12, digits)
        notes = ['Pay %d months, get 12' % pay_months]
        if credits:
            notes.append('%d free month(s) earned by referrals used' % credits)
        if disc_months:
            notes.append('%g%% welcome discount on %d month(s)' % (s['pct'], disc_months))
        base.update(period='yearly', months=12, total=total, full=year, saving=round(year - total, digits), notes=notes,
                    credits_used=credits, free_month=False, monthly=full)
        return base

    @api.model
    def _ensure_paid_until(self):
        """Companies that paid before paid-until dates existed: from their last confirmed payment."""
        Pay = self.env['saas.payment'].sudo()
        for t in self.sudo().search([('state', 'in', ('active', 'overdue')), ('paid_until', '=', False)]):
            last = Pay.search([('tenant_id', '=', t.id), ('state', '=', 'confirmed')], order='id desc', limit=1)
            start = last.create_date.date() if last else fields.Date.context_today(self)
            t.paid_until = start + relativedelta(months=last.months or 1 if last else 1)
        return True

    # ------------------------------------------------------------------ renewals (nightly)
    @api.model
    def _renewals(self):
        today = fields.Date.context_today(self)
        grace = int(self.env['ir.config_parameter'].sudo().get_param('saas.renew_grace_days', '14'))
        S = self.env['saas.service'].sudo()
        site = (self.env['ir.config_parameter'].sudo().get_param('saas.site_url') or 'https://unio.et').rstrip('/')
        for t in self.sudo().search([('state', 'in', ('active', 'overdue')), ('paid_until', '!=', False)]):
            left = (t.paid_until - today).days
            link = '%s/subscribe.html?c=%s&u=%s&p=%s' % (site, t.subdomain, max(t.subscribed_users or 1, 1), t.billing or 'monthly')
            if left in (7, 1) and t.renew_reminded != today:
                S._notify_owner(t, self.env._('Your Unio subscription renews in %s day(s)', left), self.env._(
                    'Your subscription for %(c)s is paid until %(d)s. Renew here to keep everything running: %(l)s', c=t.name, d=t.paid_until, l=link))
                t.renew_reminded = today
            elif left < 0 and t.state == 'active':
                t.state = 'overdue'
                S._set_lock(t, False)
                S._notify_owner(t, self.env._('Your Unio subscription has ended'), self.env._(
                    'The subscription for %(c)s ended on %(d)s. Everything keeps working for %(g)s more days; renew here: %(l)s',
                    c=t.name, d=t.paid_until, g=grace, l=link))
            elif left < -grace and t.state == 'overdue':
                t.state = 'suspended'
                S._set_lock(t, True)
                t.message_post(body=self.env._('Not renewed within %s days: read-only until payment (nothing deleted).', grace))
        return True
