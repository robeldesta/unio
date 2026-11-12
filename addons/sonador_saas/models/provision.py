"""Sign-up and the nightly jobs of the cloud service.

Sign-up copies the country template database (tpl_et, tpl_ug, tpl_ke: built once with tools/new_company.sh, then
locked so nobody can open them as a site), gives the copy a new identity (database id and secret), renames the
company, turns the template's administrator into the customer's owner with the password they chose, and starts
the trial. Nothing in a customer database is shared with another one."""
import logging

from markupsafe import Markup
from datetime import timedelta

import odoo
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.service import db as dbs

_logger = logging.getLogger(__name__)

# groups that make a user a paid user (office work); everyone else is self-service (leave, payslips, expenses, approvals)
PAID_GROUPS = ['base.group_system', 'sonador_books.group_sb_clerk', 'sonador_books.group_sb_accountant', 'sonador_books.group_sb_manager',
               'sonador_books.group_sb_auditor', 'sonador_books.group_sb_hr', 'sonador_sales.group_sb_sales_user',
               'sonador_inventory.group_sb_store_user', 'sonador_purchase.group_sb_purchase_user', 'sonador_crm.group_crm_user',
               'sonador_factory.group_operator', 'sonador_factory.group_manager', 'sonador_project.group_user', 'sonador_project.group_manager',
               'sonador_fleet.group_user', 'sonador_fleet.group_manager']


def _db_exists(name):
    """Also true for the locked templates (Odoo's own check connects to the database, which they refuse)."""
    db = odoo.sql_db.db_connect('postgres')
    with db.cursor() as cr:
        cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        return bool(cr.fetchone())


def _copy_database(template, name):
    """Copy a template into a new customer database (Odoo's database manager is off on the service, as it should be):
    the database itself, a new identity (database id and secret), and the files."""
    import os
    import shutil

    from odoo.tools import SQL
    db = odoo.sql_db.db_connect('postgres')
    with db.cursor() as cr:
        cr._cnx.autocommit = True
        cr.execute(SQL("CREATE DATABASE %s ENCODING 'unicode' TEMPLATE %s", SQL.identifier(name), SQL.identifier(template)))
    registry = odoo.modules.registry.Registry.new(name)
    with registry.cursor() as cr:
        env = api.Environment(cr, odoo.api.SUPERUSER_ID, {})
        env['ir.config_parameter'].init(force=True)  # new database.uuid and database.secret: never shared with the template
    src, dst = odoo.tools.config.filestore(template), odoo.tools.config.filestore(name)
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copytree(src, dst)


def _tenant_env(dbname):
    """A superuser environment on a customer database (the caller closes the cursor)."""
    registry = odoo.modules.registry.Registry(dbname)
    cr = registry.cursor()
    return cr, api.Environment(cr, odoo.api.SUPERUSER_ID, {})


class SaasService(models.AbstractModel):
    _name = 'saas.service'
    _description = 'Cloud service operations'

    # ------------------------------------------------------------------ sign-up
    @api.model
    def signup(self, vals):
        """vals: company, subdomain, country, email, password, name, phone, industry, users, referrer_code.
        Returns {'url', 'tenant_id'}."""
        T = self.env['saas.tenant'].sudo()
        company = (vals.get('company') or '').strip()
        email = (vals.get('email') or '').strip().lower()
        password = vals.get('password') or ''
        country = (vals.get('country') or 'ET').upper()
        if not company or '@' not in email:
            raise ValidationError(self.env._('Enter the company name and a valid email.'))
        if len(password) < 8:
            raise ValidationError(self.env._('Choose a password of at least 8 characters.'))
        if country not in ('ET', 'UG', 'KE'):
            raise ValidationError(self.env._('That country is not available yet.'))
        sub = T.clean_subdomain(vals.get('subdomain') or company)
        stale = T.search([('subdomain', '=', sub), ('state', '=', 'failed')])
        if stale and not _db_exists(sub):
            stale.subdomain = '%s-failed-%d' % (sub, stale.id)  # a failed attempt never got a database: free its address
        if T.search_count([('subdomain', '=', sub)]) or _db_exists(sub):
            raise ValidationError(self.env._('The web address "%s" is taken; choose another.', sub))
        template = 'tpl_%s' % country.lower()
        if not _db_exists(template):
            raise UserError(self.env._('Sign-up for %s is not open yet.', country))
        plan = self.env['saas.plan'].sudo().search([('code', '=', vals.get('plan') or 'standard')], limit=1) or \
            self.env['saas.plan'].sudo().search([], limit=1)
        partner = self.env['res.partner'].sudo().create({'name': company, 'email': email, 'phone': vals.get('phone') or False,
                                                         'is_company': True, 'customer_rank': 1,
                                                         'country_id': self.env['res.country'].search([('code', '=', country)], limit=1).id})
        referrer = T.search([('referral_code', '=', (vals.get('referrer_code') or '').strip().upper())], limit=1) if vals.get('referrer_code') else T
        tenant = T.create({'name': company, 'subdomain': sub, 'country': country, 'plan_id': plan.id, 'partner_id': partner.id,
                           'referred_by_id': referrer.id or False,
                           'email': email, 'phone': vals.get('phone'), 'contact_name': vals.get('name'),
                           'industry': vals.get('industry') or 'other', 'state': 'trial',
                           'trial_end': fields.Date.context_today(self) + timedelta(days=plan.trial_days)})
        # the database is created outside this transaction: commit the record first so a failure leaves a trace
        self.env.cr.commit()
        try:
            _copy_database(template, sub)
            cr, env = _tenant_env(sub)
            try:
                self._prepare(env, tenant, vals)
                cr.commit()
            finally:
                cr.close()
        except Exception as e:  # noqa: BLE001
            _logger.exception('Sign-up of %s failed', sub)
            tenant.write({'state': 'failed', 'note': str(e)[:500]})
            self.env.cr.commit()
            raise UserError(self.env._('We could not create your system just now. We have been alerted; please try again in a few minutes.'))
        self._request_address(sub)
        tenant._push_referral()
        if referrer:
            referrer._push_referral()
        tenant.message_post(body=self.env._('Signed up: %(c)s at %(u)s, trial until %(d)s.', c=company, u=tenant.url, d=tenant.trial_end))
        return {'url': tenant.url, 'tenant_id': tenant.id, 'subdomain': sub}

    @api.model
    def _request_address(self, sub):
        """On the server: ask the host for this address's certificate and HTTPS site (a file in the shared queue folder)."""
        import os
        folder = self.env['ir.config_parameter'].sudo().get_param('saas.cert_queue')
        if folder and os.path.isdir(folder):
            open(os.path.join(folder, sub), 'w').close()

    @api.model
    def _prepare(self, env, tenant, vals):
        """Inside the new customer database."""
        c = env.ref('base.main_company')
        c.write({'name': tenant.name, 'email': tenant.email, 'phone': tenant.phone or False})
        c.partner_id.write({'name': tenant.name})
        owner = env.ref('base.user_admin')
        owner.write({'login': tenant.email, 'email': tenant.email, 'name': vals.get('name') or tenant.name, 'password': vals['password']})
        P = env['ir.config_parameter']
        P.set_param('web.base.url', tenant.url)
        P.set_param('web.base.url.freeze', 'True')
        P.set_param('auth_signup.invitation_scope', 'b2b')  # no public sign-up inside a customer system
        P.set_param('saas.tenant', tenant.subdomain)
        P.set_param('saas.trial_end', fields.Date.to_string(tenant.trial_end))
        if 'saas.apps' in env:
            env['saas.apps'].apply_industry(tenant.industry or 'other')  # only the apps this kind of business uses show

    # ------------------------------------------------------------------ subscription requests
    @api.model
    def subscribe_request(self, subdomain, email, users, note=None, period='monthly'):
        T = self.env['saas.tenant'].sudo()
        email = (email or '').strip().lower()
        t = T.search([('subdomain', '=', (subdomain or '').strip().lower())], limit=1)
        if not t or (t.email or '').lower() != email:
            raise ValidationError(self.env._('Use the email you signed up with (the owner of %s).', subdomain or 'the system'))
        users = max(int(users or 1), 1)
        price = t.price_for_period(users, period)
        if price.get('free_month'):
            # a free month earned by referring: nothing to pay, the subscription starts now
            if t.state in ('trial', 'suspended', 'overdue'):
                t.state = 'active'
                self._set_lock(t, False)
            t.with_context(free_month=True)._month_paid(1, 1)
            t.message_post(body=self.env._('Subscription started on a free month earned by referrals (%s users).', users))
            return {'company': t.name, 'users': users, 'total': 0.0, 'currency': t.currency, 'free_month': True}
        t.message_post(body=self.env._('Subscription requested (%(per)s) for %(n)s users: %(total)s %(cur)s. %(notes)s %(note)s',
                                       per=price.get('period', 'monthly'), n=users, total=price['total'], cur=t.currency,
                                       notes='; '.join(price.get('notes') or []), note=note or ''),
                       subject=self.env._('Subscription request: %s', t.name))
        P = self.env['ir.config_parameter'].sudo()
        body = Markup('<p>Hello,</p><p>We received your request to subscribe <b>%s</b> for %s users (%s %s a month). '
                      'We will email you the invoice within one working day. Your system stays as it is while the payment is on its way.</p>'
                      '<p>Unio</p>') % (t.name, users, '{:,.2f}'.format(price['total']), t.currency)
        self.env['mail.mail'].sudo().create({'subject': 'Your Unio subscription request', 'email_from': P.get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
                                             'email_to': t.email, 'body_html': body, 'auto_delete': True}).send(raise_exception=False)
        sales = P.get_param('saas.sales_email')
        if sales:
            self.env['mail.mail'].sudo().create({'subject': 'Subscription request: %s (%s users)' % (t.name, users), 'email_to': sales,
                                                 'email_from': P.get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
                                                 'body_html': Markup('<p>%s (%s) asks to subscribe: %s users, %s %s a month.</p>') % (
                                                     t.name, t.url, users, '{:,.2f}'.format(price['total']), t.currency)}).send(raise_exception=False)
        return {'company': t.name, 'users': users, 'total': price['total'], 'currency': t.currency}

    # ------------------------------------------------------------------ nightly
    @api.model
    def count_users(self, tenants=None):
        tenants = tenants or self.env['saas.tenant'].sudo().search([('state', 'in', ('trial', 'active', 'overdue', 'suspended'))])
        today = fields.Date.context_today(self)
        for t in tenants:
            try:
                cr, env = _tenant_env(t.subdomain)
            except Exception as e:  # noqa: BLE001
                _logger.warning('Cannot open %s: %s', t.subdomain, e)
                continue
            try:
                users = env['res.users'].search([('share', '=', False), ('active', '=', True), ('id', '!=', odoo.api.SUPERUSER_ID)])
                paid_groups = env['res.groups']
                for x in PAID_GROUPS:
                    g = env.ref(x, raise_if_not_found=False)
                    if g:
                        paid_groups |= g
                paid = users.filtered(lambda u: u.all_group_ids & paid_groups) if t.plan_id.light_users_free else users
            finally:
                cr.close()
            t.write({'paid_users': len(paid), 'light_users': len(users) - len(paid), 'counted_on': fields.Datetime.now()})
            usage = self.env['saas.usage'].sudo()
            row = usage.search([('tenant_id', '=', t.id), ('date', '=', today)], limit=1)
            vals = {'paid_users': t.paid_users, 'light_users': t.light_users}
            if row:
                row.write(vals)
            else:
                usage.create(dict(vals, tenant_id=t.id, date=today))
        return True

    @api.model
    def _cron_nightly(self):
        self.count_users()
        self.trials()
        self.env['saas.tenant']._renewals()
        self.env['saas.tenant']._cron_push_referrals()

    @api.model
    def _notify_owner(self, tenant, subject, text):
        P = self.env['ir.config_parameter'].sudo()
        self.env['mail.mail'].sudo().create({
            'subject': subject, 'email_to': tenant.email, 'email_from': P.get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
            'body_html': Markup('<div style="font-family:Arial,sans-serif;color:#1B2240"><p>Hello,</p><p>%s</p><p>Unio</p></div>') % text,
            'auto_delete': True}).send(raise_exception=False)

    @api.model
    def trials(self):
        """Trial reminders and expiry: a trial that ends without a subscription turns read-only (nothing is deleted)."""
        today = fields.Date.context_today(self)
        for t in self.env['saas.tenant'].sudo().search([('state', '=', 'trial'), ('trial_end', '!=', False)]):
            left = (t.trial_end - today).days
            if left in (23, 10, 3):
                t.message_post(body=self.env._('Trial reminder: %d days left.', left), partner_ids=t.partner_id.ids,
                               subject=self.env._('Your trial ends in %d days', left))
            elif left < 0:
                t.state = 'suspended'
                self._set_lock(t, True)
                t.message_post(body=self.env._('Trial ended: the system is read-only until the subscription starts.'))
        return True

    @api.model
    def _set_lock(self, tenant, locked):
        try:
            cr, env = _tenant_env(tenant.subdomain)
        except Exception:  # noqa: BLE001
            return
        try:
            env['ir.config_parameter'].set_param('saas.locked', 'True' if locked else 'False')
            env['ir.config_parameter'].set_param('saas.plan_state', tenant.state)
            cr.commit()
            env.registry.signal_changes()  # running workers drop their cached settings on their next request
        finally:
            cr.close()

    # ------------------------------------------------------------------ billing (manual payments for now)
    @api.model
    def invoice_month(self, day=None):
        """One invoice per active customer for the users counted, priced by its plan in its currency."""
        day = fields.Date.to_date(day) if day else fields.Date.context_today(self)
        V = self.env['sb.voucher'].sudo()
        made = V
        income = self.env['account.account']._sb_find('110000')[:1]
        for t in self.env['saas.tenant'].sudo().search([('state', 'in', ('active', 'overdue'))]):
            ref = 'UNIO-%s-%s' % (t.subdomain, day.strftime('%Y%m'))
            if V.search_count([('reference', '=', ref)]):
                continue
            price = t.plan_id.price_for(t.paid_users, t.currency)
            if not price['total']:
                continue
            cur = self.env['res.currency'].with_context(active_test=False).search([('name', '=', t.currency)], limit=1)
            if cur and not cur.active:
                cur.active = True  # bill each customer in its own currency
            # VAT: a customer in our own country pays our VAT; one abroad buys an exported service (no VAT here)
            domestic = t.country == self.env.company.country_id.code
            taxes = self.env.company.account_sale_tax_id if domestic else self.env['account.tax']
            v = V.create({'voucher_type': 'sale', 'partner_id': t.partner_id.id, 'date': day, 'reference': ref, 'saas_tenant_id': t.id,
                          'doc_currency_id': cur.id or self.env.company.currency_id.id,
                          'memo': self.env._('Subscription %(m)s: %(n)s users', m=day.strftime('%B %Y'), n=price['users']),
                          'line_ids': [(0, 0, {'name': self.env._('%(p)s plan, %(n)s users at %(e)s (%(d)s%% volume discount)', p=t.plan_id.name,
                                                                   n=price['users'], e=price['each'], d=price['discount']),
                                               'quantity': price['users'], 'price_unit': price['each'], 'account_id': income.id,
                                               'tax_ids': [(6, 0, taxes.ids)]})]})
            v.action_post()
            made |= v
        return made
