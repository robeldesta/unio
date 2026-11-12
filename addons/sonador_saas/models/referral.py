"""Referrals: every company has a code and a link (unio.et/signup.html?ref=CODE). A company that signs up with it gets
a discount on its first paid months; the company that referred it earns free months when the newcomer starts paying.
Accountants (partners) keep their commission on res.partner (referrer_id), with a code of their own.

Settings: saas.ref_discount_pct (20), saas.ref_discount_months (3), saas.ref_reward_months (1)."""
import json
import secrets

from odoo import api, fields, models


def _new_code(name):
    base = ''.join(c for c in (name or '').upper() if c.isalnum())[:6] or 'UNIO'
    return '%s%s' % (base, secrets.token_hex(2).upper())


class SaasTenantReferral(models.Model):
    _inherit = 'saas.tenant'

    referral_code = fields.Char(index=True, copy=False, readonly=True)
    referred_by_id = fields.Many2one('saas.tenant', 'Referred by', index=True, tracking=True)
    referral_ids = fields.One2many('saas.tenant', 'referred_by_id', 'Companies referred')
    credit_months = fields.Integer('Free months earned', tracking=True, help='Months the company does not pay, earned by referring others.')
    paid_months = fields.Integer('Months paid', readonly=True)
    subscribed_users = fields.Integer('Users subscribed', readonly=True, help='Users of the last confirmed payment.')
    ref_rewarded = fields.Boolean(readonly=True, help='Its referrer already got the reward for it.')

    _uniq_ref_code = models.Constraint('unique(referral_code)', 'Referral codes are unique.')

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            v.setdefault('referral_code', _new_code(v.get('subdomain') or v.get('name')))
        return super().create(vals_list)

    @api.model
    def _ensure_codes(self):
        for t in self.sudo().search([('referral_code', '=', False)]):
            t.referral_code = _new_code(t.subdomain)
        return True

    # ------------------------------------------------------------------ settings
    def _ref_settings(self):
        P = self.env['ir.config_parameter'].sudo()
        return {'pct': float(P.get_param('saas.ref_discount_pct', '20')), 'months': int(P.get_param('saas.ref_discount_months', '3')),
                'reward': int(P.get_param('saas.ref_reward_months', '1'))}

    # ------------------------------------------------------------------ what a company pays this month
    def price_now(self, users):
        """The plan's price, less the newcomer discount (first months) or nothing at all when a free month is earned."""
        self.ensure_one()
        price = dict(self.plan_id.price_for(users, self.currency))
        s = self._ref_settings()
        price['notes'] = []
        if self.credit_months > 0 and price['total']:
            price['free_month'] = True
            price['notes'].append('Free month earned by referring others')
            price['full'], price['total'] = price['total'], 0.0
        elif self.referred_by_id and self.paid_months < s['months'] and s['pct'] and price['total']:
            digits = 2 if self.currency == 'USD' else 0
            full = price['total']
            price['total'] = round(full * (1 - s['pct'] / 100.0), digits)
            price['full'] = full
            price['notes'].append('%g%% welcome discount (referred by %s), month %d of %d' % (s['pct'], self.referred_by_id.name, self.paid_months + 1, s['months']))
        return price

    def _month_paid(self, months=1, credits_used=0):
        """After a confirmed payment (or a free month used): count it, move the paid-until date, reward the referrer once."""
        s = self._ref_settings()
        for t in self:
            used = credits_used or (1 if self.env.context.get('free_month') else 0)
            if used:
                t.credit_months = max(t.credit_months - used, 0)
            t.paid_months += months
            t._extend(months)
            if t.referred_by_id and not t.ref_rewarded and not self.env.context.get('free_month'):
                ref = t.referred_by_id
                ref.credit_months += s['reward']
                t.ref_rewarded = True
                ref.message_post(body=self.env._('%(c)s, which you referred, started paying: %(n)s free month(s) earned.', c=t.name, n=s['reward']))
                ref._push_referral()
                self.env['saas.service'].sudo()._notify_owner(ref, self.env._('You earned a free month'), self.env._(
                    '%(c)s, which signed up with your referral link, has started its subscription. Your next %(n)s month(s) of Unio are free. Thank you!',
                    c=t.name, n=s['reward']))
            t._push_referral()

    # ------------------------------------------------------------------ the company's own Refer page (pushed into its database)
    def _referral_stats(self):
        self.ensure_one()
        s = self._ref_settings()
        site = (self.env['ir.config_parameter'].sudo().get_param('saas.site_url') or 'https://unio.et').rstrip('/')
        return {'code': self.referral_code, 'link': '%s/signup.html?ref=%s' % (site, self.referral_code),
                'signed_up': len(self.referral_ids), 'paying': len(self.referral_ids.filtered('ref_rewarded')),
                'credit_months': self.credit_months, 'offer_pct': s['pct'], 'offer_months': s['months'], 'reward_months': s['reward'],
                'companies': [{'name': r.name, 'paying': r.ref_rewarded, 'since': fields.Date.to_string(r.create_date.date())} for r in self.referral_ids[:50]]}

    def _push_referral(self):
        from .provision import _tenant_env
        for t in self.filtered(lambda x: x.state in ('trial', 'active', 'overdue', 'suspended')):
            try:
                cr, env = _tenant_env(t.subdomain)
            except Exception:  # noqa: BLE001  the company's database is not reachable now: next night
                continue
            try:
                env['ir.config_parameter'].set_param('saas.referral', json.dumps(t._referral_stats()))
                # where the help panel finds the articles and support (the platform database, same server)
                env['ir.config_parameter'].set_param('saas.platform_db', self.env.cr.dbname)
                env['ir.config_parameter'].set_param('saas.paid_until', fields.Date.to_string(t.paid_until) if t.paid_until else '')
                env['ir.config_parameter'].set_param('saas.billing', t.billing or 'monthly')
                env['ir.config_parameter'].set_param('saas.plan_state', t.state)
                if 'unio.brand' in env:
                    env['unio.brand'].apply()  # with every app loaded, every email template gets the Unio name
                cr.commit()
                env.registry.signal_changes()  # running workers drop their cached settings
            finally:
                cr.close()

    @api.model
    def _cron_push_referrals(self):
        self._ensure_codes()
        self.sudo().search([('state', 'in', ('trial', 'active', 'overdue', 'suspended'))])._push_referral()

    # ------------------------------------------------------------------ the website asks about a code
    @api.model
    def referral_offer(self, code):
        code = (code or '').strip().upper()
        t = self.sudo().search([('referral_code', '=', code)], limit=1) if code else self.browse()
        if not t:
            return {'valid': False}
        s = self._ref_settings()
        return {'valid': True, 'by': t.name, 'pct': s['pct'], 'months': s['months']}
