"""Unio's money in Sonador Systems' own books. HQ keeps no books: the company's accounts are in its own Unio system
(setting saas.books_db, e.g. "sonador"), like every customer's. HQ writes into it:

- every confirmed subscription payment: a receipt from the subscriber into the account the money arrived in (Telebirr,
  Chapa or the bank), held as the customer's advance, and the sales invoice for it, VAT included for a customer in our
  own country. In Ethiopia a sales invoice's number is the fiscal receipt or the Ministry e-invoice reference, so the
  invoice waits as a draft for that number; elsewhere it is posted at once. The gateway's fee, when one is set, is a
  payment from that same account;
- on the 1st of each month: last month's Anthropic cost for Unio AI, as a draft payment to check against the card.

Everything carries the job "Unio" (Projects), so Unio's own profit and loss is the job's report, inside one set of books
for the one company."""
import logging
from contextlib import contextmanager
from datetime import timedelta

import odoo
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# where each way of paying lands: (former number, name) of the money account in the books
MONEY_ACCOUNTS = {'telebirr': ('211010', 'Telebirr merchant wallet'), 'chapa': ('211011', 'Chapa balance'), 'bank': ('211001', 'Bank')}
INCOME = '110300'  # Hosting and subscription revenue
FEES = ('626900', 'Payment gateway fees')
FEE_PCT = {'chapa': 2.5}  # telebirr charges the payer; a bank transfer costs us nothing
AI_COST = ('626910', 'AI services (Anthropic)')


class SaasPayment(models.Model):
    _inherit = 'saas.payment'

    books_ref = fields.Char('In the books', readonly=True, copy=False, help='The cash sale made for this payment in the company books.')


class SaasBooks(models.AbstractModel):
    _name = 'saas.books'
    _description = "Unio's money in the company books"

    @contextmanager
    def _env(self):
        """The company books: another database on this server (committed at the end), or this one."""
        db = self.env['ir.config_parameter'].sudo().get_param('saas.books_db')
        if not db:
            raise UserError(self.env._('Set which system holds the company books (setting saas.books_db).'))
        if db == self.env.cr.dbname:
            yield self.env(su=True)
            return
        with odoo.modules.registry.Registry(db).cursor() as cr:
            yield api.Environment(cr, odoo.SUPERUSER_ID, {})

    # ------------------------------------------------------------------ what the books need
    def _account(self, env, legacy, name, kind):
        A = env['account.account']
        acc = A._sb_find(legacy)[:1]
        if not acc:
            acc = A.create({'code': A._sb_new_code(legacy, env.company), 'sb_legacy_code': legacy, 'name': name, 'account_type': kind,
                            'company_ids': [Command.set(env.company.ids)]})
            env.flush_all()
        return acc

    def _job(self, env):
        if 'sb.job' not in env:
            return env['ir.model'].browse()
        J = env['sb.job']
        job = J.search([('name', '=', 'Unio')], limit=1)
        if not job:
            job = J.create({'name': 'Unio', 'kind': 'internal', 'state': 'active'})
        return job

    def _partner(self, env, tenant):
        P = env['res.partner']
        ref = 'UNIO-%s' % tenant.subdomain
        partner = P.search([('ref', '=', ref)], limit=1)
        if not partner:
            country = env['res.country'].search([('code', '=', tenant.country)], limit=1)
            partner = P.create({'name': tenant.name, 'ref': ref, 'email': tenant.email, 'phone': tenant.phone or False,
                                'is_company': True, 'country_id': country.id or False, 'customer_rank': 1})
        return partner

    def _line(self, job, vals):
        if job:
            vals['job_id'] = job.id
        return Command.create(vals)

    # ------------------------------------------------------------------ a confirmed payment
    @api.model
    def book_payment(self, payment):
        """A receipt and its invoice per confirmed payment (skipped if already booked). Returns the numbers."""
        p = payment.sudo()
        if p.books_ref or not p.amount:
            return p.books_ref
        t = p.tenant_id
        method = p.method if p.method in MONEY_ACCOUNTS else 'bank'
        with self._env() as env:
            company = env.company
            legacy, name = MONEY_ACCOUNTS[method]
            money = self._account(env, legacy, name, 'asset_cash')
            income = self._account(env, INCOME, 'Hosting and subscription revenue', 'income')
            job = self._job(env)
            partner = self._partner(env, t)
            cur = env['res.currency'].with_context(active_test=False).search([('name', '=', p.currency or 'ETB')], limit=1) or company.currency_id
            # the amount paid is the full price; for a customer in our own country it includes our VAT
            domestic = t.country == (company.country_id.code or 'ET')
            tax = company.account_sale_tax_id if domestic else env['account.tax']
            rate = tax.amount / 100.0 if tax and tax.amount_type == 'percent' and not tax.price_include else 0.0
            net = round(p.amount / (1 + rate), 2)
            label = self.env._('Unio subscription, %(n)s users, %(m)s month(s) (%(r)s)', n=p.users or 1, m=p.months or 1, r=p.reference or p.method)
            V = env['sb.voucher']
            ref = 'UNIO-PAY-%s' % p.id
            today = fields.Date.context_today(self)
            foreign = {'doc_currency_id': cur.id} if cur != company.currency_id else {}
            if foreign and not cur.active:
                cur.active = True  # a customer abroad pays in its own currency
            # the money: in the books at once, as the customer's advance until the invoice is posted
            advance = company.sb_customer_advance_account_id
            if not advance:
                raise UserError(self.env._('Set the customer advances account in the Accounting settings of the company books.'))
            r = V.create({'voucher_type': 'receipt', 'partner_id': partner.id, 'date': today, 'reference': ref, 'memo': label,
                          'cash_account_id': money.id,
                          'line_ids': [self._line(job, {'name': label, 'account_id': advance.id, 'partner_id': partner.id, 'amount': p.amount})]})
            r.action_post()
            # the invoice: in Ethiopia its number is the fiscal receipt or the Ministry e-invoice reference, so it waits
            # as a draft for that number; once posted, the advance above is applied to it
            inv = V.create(dict({'voucher_type': 'sale', 'partner_id': partner.id, 'date': today, 'reference': ref,
                                 'memo': self.env._('%s. Paid: receipt %s.', label, r.name),
                                 'line_ids': [self._line(job, {'name': label, 'quantity': 1, 'price_unit': net, 'account_id': income.id,
                                                               'tax_ids': [Command.set(tax.ids)]})]}, **foreign))
            fiscal = 'sb_fiscal_only' in inv._fields and inv.sb_fiscal_only
            if not fiscal:
                inv.action_post()
            number = r.name if fiscal else '%s, %s' % (r.name, inv.name)
            # the gateway's fee, when one is set for this way of paying (e.g. saas.fee_chapa_pct = 2.5)
            # Chapa keeps 2.5% of the amount paid, its own 15% VAT included (input VAT we claim back); telebirr charges
            # the payer, so nothing comes off for it
            pct = float(self.env['ir.config_parameter'].sudo().get_param('saas.fee_%s_pct' % method, FEE_PCT.get(method, 0)) or 0)
            if pct:
                fee = round(p.amount * pct / 100.0, 2)
                fee_vat = round(fee * rate / (1 + rate), 2) if rate else 0.0
                fees = self._account(env, FEES[0], FEES[1], 'expense')
                lines = [self._line(job, {'name': self.env._('%(m)s fee %(p)s%%', m=method.title(), p=pct), 'account_id': fees.id, 'amount': fee - fee_vat})]
                if fee_vat:
                    vat_in = self._account(env, '221200', 'VAT Receivable on Purchases', 'asset_current')
                    lines.append(self._line(job, {'name': self.env._('Input VAT on the %s fee', method.title()), 'account_id': vat_in.id, 'amount': fee_vat}))
                f = V.create({'voucher_type': 'payment', 'date': today, 'reference': 'UNIO-FEE-%s' % p.id, 'cash_account_id': money.id,
                              'memo': self.env._('%(m)s fee on %(n)s', m=method.title(), n=r.name), 'line_ids': lines})
                f.action_post()
        p.books_ref = number
        return number

    # ------------------------------------------------------------------ AI cost, monthly
    @api.model
    def book_ai_month(self, month_start=None):
        """Last month's Unio AI cost (Anthropic, in US dollars) as a draft payment in the books, in birr at the day's rate."""
        today = fields.Date.context_today(self)
        start = fields.Date.to_date(month_start) if month_start else (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        usd = round(sum(self.env['saas.ai.usage'].sudo().search([('date', '>=', start), ('date', '<', end)]).mapped('cost_usd')), 2)
        if usd < 0.01:
            return False
        ref = 'UNIO-AI-%s' % start.strftime('%Y%m')
        with self._env() as env:
            V = env['sb.voucher']
            if V.search_count([('reference', '=', ref)]):
                return False
            usd_cur = env['res.currency'].with_context(active_test=False).search([('name', '=', 'USD')], limit=1)
            company = env.company
            etb = usd_cur._convert(usd, company.currency_id, company, today) if usd_cur else usd
            acc = self._account(env, AI_COST[0], AI_COST[1], 'expense')
            bank = self._account(env, *MONEY_ACCOUNTS['bank'], 'asset_cash')
            partner = env['res.partner'].search([('name', '=', 'Anthropic')], limit=1) or env['res.partner'].create({'name': 'Anthropic', 'is_company': True})
            label = self.env._('Unio AI, %(m)s: US$ %(u)s at the day\'s rate (check against the card statement)', m=start.strftime('%B %Y'), u=usd)
            v = V.create({'voucher_type': 'payment', 'partner_id': partner.id, 'date': today, 'reference': ref, 'memo': label,
                          'cash_account_id': bank.id, 'line_ids': [self._line(self._job(env), {'name': label, 'account_id': acc.id, 'amount': round(etb, 2)})]})
            return v.name

    @api.model
    def _cron_monthly(self):
        try:
            self.book_ai_month()
        except Exception as e:  # noqa: BLE001 - the books may be closed or unset; HQ keeps working
            _logger.warning('Unio AI cost not booked: %s', e)
