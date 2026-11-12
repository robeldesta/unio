"""Foreign currencies: daily exchange rates for every active currency (fetched, or typed), and the month-end
revaluation of open foreign-currency items (IAS 21.23): customer and vendor balances and foreign-currency bank and
cash accounts are restated at the closing rate; the difference goes to exchange gain or loss and reverses on the
first day of the next month, so the realised difference is booked when the item is settled."""
import json
import logging
from collections import defaultdict
from datetime import timedelta
from urllib.request import Request, urlopen

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from .ethiopian_calendar import format_ethiopian

_logger = logging.getLogger(__name__)
SOURCE = 'https://open.er-api.com/v6/latest/%s'  # free daily reference rates, every ISO currency (UGX, ETB, KES, USD ...)


class SbFx(models.AbstractModel):
    _name = 'sb.fx'
    _description = 'Foreign currencies (screen)'

    def _require(self):
        if not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise AccessError(self.env._('Exchange rates and revaluation are kept by the accountants.'))

    def _foreign(self, company):
        return self.env['res.currency'].search([('active', '=', True), ('id', '!=', company.currency_id.id)])

    # ------------------------------------------------------------------ rates
    @api.model
    def _fetch(self, base):
        req = Request(SOURCE % base, headers={'User-Agent': 'Odoo'})
        with urlopen(req, timeout=20) as r:  # noqa: S310 (fixed https source)
            data = json.loads(r.read().decode())
        if data.get('result') != 'success':
            raise UserError(self.env._('The rate service did not answer (%s).', data.get('error-type') or data.get('result')))
        return data['rates']

    @api.model
    def update_rates(self, company=None, day=None):
        """Today's rate of every active foreign currency, per unit of the company currency (Odoo's convention)."""
        companies = company or self.env['res.company'].search([])
        done = []
        for c in companies:
            if self.env['ir.config_parameter'].sudo().get_param('sonador.fx_auto', 'True') == 'False':
                continue
            cur = self._foreign(c)
            if not cur:
                continue
            rates = self._fetch(c.currency_id.name)
            d = day or fields.Date.context_today(self)
            R = self.env['res.currency.rate'].sudo()
            for x in cur:
                r = rates.get(x.name)
                if not r:
                    continue
                row = R.search([('currency_id', '=', x.id), ('company_id', '=', c.id), ('name', '=', d)], limit=1)
                if row:
                    row.rate = r
                else:
                    R.create({'currency_id': x.id, 'company_id': c.id, 'name': d, 'rate': r})
                done.append('%s %s' % (x.name, round(1 / r, 4)))
        return done

    @api.model
    def _cron_rates(self):
        try:
            self.update_rates()
        except Exception as e:  # noqa: BLE001 - a missed day keeps yesterday's rate
            _logger.warning('Exchange rates not updated: %s', e)

    # ------------------------------------------------------------------ revaluation
    def _closing_rate(self, cur, company, day):
        return cur._get_conversion_rate(cur, company.currency_id, company, day)

    @api.model
    def revaluation(self, day):
        """What restating every open foreign-currency item at the closing rate of `day` would book."""
        c = self.env.company
        day = fields.Date.to_date(day)
        cc = c.currency_id
        AML = self.env['account.move.line']
        items = []
        # customer and vendor balances, per account, partner and currency
        lines = AML.search([('company_id', '=', c.id), ('parent_state', '=', 'posted'), ('date', '<=', day),
                            ('currency_id', '!=', cc.id), ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable'))])
        # open as at `day`: settlements made after it do not count
        settled, settled_cur = defaultdict(float), defaultdict(float)
        for p in self.env['account.partial.reconcile'].search(['|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids),
                                                               ('max_date', '<=', day)]):
            settled[p.debit_move_id.id] += p.amount
            settled[p.credit_move_id.id] -= p.amount
            settled_cur[p.debit_move_id.id] += p.debit_amount_currency
            settled_cur[p.credit_move_id.id] -= p.credit_amount_currency
        group = defaultdict(lambda: [0.0, 0.0])
        for l in lines:
            g = group[(l.account_id, l.partner_id.commercial_partner_id, l.currency_id)]
            g[0] += l.amount_currency - settled_cur.get(l.id, 0.0)
            g[1] += l.balance - settled.get(l.id, 0.0)
        for (acc, partner, cur), (amt_cur, booked) in group.items():
            if abs(amt_cur) < 0.005:
                continue
            rate = self._closing_rate(cur, c, day)
            now = cc.round(amt_cur * rate)
            items.append({'account_id': acc.id, 'account': acc.display_name, 'partner_id': partner.id, 'partner': partner.name or '',
                          'currency': cur.name, 'amount_currency': amt_cur, 'rate': rate, 'booked': booked, 'restated': now, 'diff': cc.round(now - booked)})
        # foreign-currency bank and cash accounts
        for acc in self.env['account.account'].search([('company_ids', 'in', c.id), ('account_type', '=', 'asset_cash'), ('currency_id', '!=', False),
                                                       ('currency_id', '!=', cc.id)]):
            res = AML._read_group([('account_id', '=', acc.id), ('parent_state', '=', 'posted'), ('date', '<=', day)], [], ['amount_currency:sum', 'balance:sum'])
            amt_cur, booked = res[0][0] or 0.0, res[0][1] or 0.0
            if abs(amt_cur) < 0.005 and abs(booked) < 0.5:
                continue
            rate = self._closing_rate(acc.currency_id, c, day)
            now = cc.round(amt_cur * rate)
            items.append({'account_id': acc.id, 'account': acc.display_name, 'partner_id': False, 'partner': '', 'currency': acc.currency_id.name,
                          'amount_currency': amt_cur, 'rate': rate, 'booked': booked, 'restated': now, 'diff': cc.round(now - booked)})
        items = [i for i in items if abs(i['diff']) >= 0.5]
        ref = 'FX-REVAL-%s' % day.strftime('%Y%m%d')
        posted = self.env['account.move'].search([('company_id', '=', c.id), ('ref', '=', ref), ('state', '=', 'posted')], limit=1)
        return {'date': fields.Date.to_string(day), 'date_ec': format_ethiopian(day), 'items': items, 'total': sum(i['diff'] for i in items),
                'posted': posted.name or False, 'posted_id': posted.id or False}

    @api.model
    def post_revaluation(self, day):
        """Book the revaluation on `day` and its reversal on the next day, in the exchange difference journal. Each line keeps
        its foreign currency with no foreign amount (only the value in the company currency changes), as Odoo's own
        exchange differences do; that is what a foreign-currency bank account requires."""
        self._require()
        c = self.env.company
        day = fields.Date.to_date(day)
        data = self.revaluation(day)
        if data['posted']:
            raise UserError(self.env._('The revaluation of %s is already posted (%s).', format_ethiopian(fields.Date.to_date(data['date'])), data['posted']))
        if not data['items']:
            raise UserError(self.env._('Nothing to revalue on %s.', format_ethiopian(fields.Date.to_date(data['date']))))
        gain = c.income_currency_exchange_account_id
        loss = c.expense_currency_exchange_account_id
        journal = c.currency_exchange_journal_id
        if not gain or not loss or not journal:
            raise UserError(self.env._('Set the exchange difference journal and the exchange gain and loss accounts of the company first.'))
        Cur = self.env['res.currency']
        lines, net = [], 0.0
        for i in data['items']:
            d = i['diff']
            net += d
            label = self.env._('Revaluation %(cur)s %(amt)s at %(rate)s', cur=i['currency'], amt='{:,.2f}'.format(i['amount_currency']),
                               rate='{:,.4f}'.format(i['rate']))
            lines.append({'account_id': i['account_id'], 'partner_id': i['partner_id'] or False, 'name': label,
                          'currency_id': Cur.search([('name', '=', i['currency'])], limit=1).id, 'amount_currency': 0.0,
                          'debit': d if d > 0 else 0.0, 'credit': -d if d < 0 else 0.0})
        if net:
            lines.append({'account_id': (gain if net > 0 else loss).id, 'name': self.env._('Unrealised exchange difference'),
                          'debit': -net if net < 0 else 0.0, 'credit': net if net > 0 else 0.0})
        M = self.env['account.move'].sudo()
        ref = 'FX-REVAL-%s' % day.strftime('%Y%m%d')
        m = M.create({'move_type': 'entry', 'journal_id': journal.id, 'date': day, 'ref': ref,
                      'line_ids': [Command.create(l) for l in lines]})
        m.action_post()
        rev = M.create({'move_type': 'entry', 'journal_id': journal.id, 'date': day + timedelta(days=1), 'ref': ref + '-R',
                        'line_ids': [Command.create(dict(l, name=self.env._('Reversal: %s', l['name']), debit=l['credit'], credit=l['debit'])) for l in lines]})
        rev.action_post()
        m.message_post(body=self.env._('Revaluation at %(d)s, reversed by %(r)s on the next day.', d=format_ethiopian(fields.Date.to_date(data['date'])), r=rev.name))
        return self.revaluation(day)

    # ------------------------------------------------------------------ screen
    @api.model
    def ctx(self):
        self._require()
        c = self.env.company
        today = fields.Date.context_today(self)
        R = self.env['res.currency.rate']
        cur = []
        for x in self._foreign(c):
            hist = R.search([('currency_id', '=', x.id), ('company_id', 'in', (c.id, False))], order='name desc', limit=10)
            cur.append({'id': x.id, 'name': x.name, 'symbol': x.symbol,
                        'history': [{'id': h.id, 'date': fields.Date.to_string(h.name), 'per_unit': round(1 / h.rate, 4) if h.rate else 0} for h in hist]})
        last_month_end = today.replace(day=1) - timedelta(days=1)
        return {'company': c.name, 'currency': c.currency_id.name, 'currencies': cur, 'auto': self.env['ir.config_parameter'].sudo().get_param('sonador.fx_auto', 'True') != 'False',
                'month_end': fields.Date.to_string(last_month_end), 'today': fields.Date.to_string(today)}

    @api.model
    def set_rate(self, currency_id, day, per_unit):
        """A rate typed by the accountant: company currency per one unit of the foreign currency (e.g. 3,650 UGX per USD)."""
        self._require()
        per_unit = float(per_unit or 0)
        if per_unit <= 0:
            raise UserError(self.env._('Enter how many %s one unit is worth.', self.env.company.currency_id.name))
        c = self.env.company
        R = self.env['res.currency.rate'].sudo()  # accountants keep rates; Odoo reserves the model for administrators
        row = R.search([('currency_id', '=', int(currency_id)), ('company_id', '=', c.id), ('name', '=', day)], limit=1)
        vals = {'rate': 1.0 / per_unit}
        if row:
            row.write(vals)
        else:
            R.create(dict(vals, currency_id=int(currency_id), company_id=c.id, name=day))
        return self.ctx()

    @api.model
    def fetch_now(self):
        self._require()
        self.update_rates(self.env.company)
        return self.ctx()

    @api.model
    def set_auto(self, on):
        self._require()
        self.env['ir.config_parameter'].sudo().set_param('sonador.fx_auto', 'True' if on else 'False')
        return self.ctx()
